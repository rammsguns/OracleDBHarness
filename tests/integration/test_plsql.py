"""The PL/SQL workspace: inspect a broken unit, repair it, compile, run a test block."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import execute, open_worksheet

# These tests compile over the seeded package. Against Oracle that outlives the test.
pytestmark = pytest.mark.usefixtures("restore_seeded_package")

# Defines every subprogram the seeded specification declares, emit_lines included:
# Oracle refuses a body that leaves one out (PLS-00323), which the stand-in does not
# check.
REPAIRED_BODY = """\
CREATE OR REPLACE PACKAGE BODY employee_report AS
  FUNCTION headcount(p_department_id IN NUMBER) RETURN NUMBER IS
    l_count NUMBER;
  BEGIN
    SELECT COUNT(*) INTO l_count FROM employees WHERE department_id = p_department_id;
    RETURN l_count;
  END headcount;
  PROCEDURE report_department(p_department_id IN NUMBER) IS
  BEGIN
    DBMS_OUTPUT.PUT_LINE('headcount=' || headcount(p_department_id));
  END report_department;
  PROCEDURE emit_lines(p_count IN NUMBER, p_width IN NUMBER DEFAULT 40) IS
  BEGIN
    FOR i IN 1 .. p_count LOOP
      DBMS_OUTPUT.PUT_LINE(LPAD(TO_CHAR(i), p_width, '.'));
    END LOOP;
  END emit_lines;
END employee_report;
"""

STILL_BROKEN_BODY = REPAIRED_BODY.replace("FROM employees", "FROM employee")


def _detail(client: TestClient, headers, profile_id: str) -> dict:
    return client.get(
        f"/api/v1/targets/{profile_id}/objects/detail",
        headers=headers,
        params={
            "owner": "HARNESS_APP",
            "objectName": "EMPLOYEE_REPORT",
            "objectType": "PACKAGE BODY",
        },
    ).json()


def test_seeded_invalid_package_shows_line_level_errors(
    client: TestClient, developer, targets
) -> None:
    detail = _detail(client, developer, targets["development"]["id"])
    errors = detail["panels"]["errors"]
    assert errors["available"] is True
    assert errors["rows"], errors
    line, position, text = errors["rows"][0][0], errors["rows"][0][1], errors["rows"][0][2]
    assert line == 5
    assert position > 0
    assert "ORA-00942" in text

    status = detail["panels"]["status"]
    assert any(row[3] == "INVALID" for row in status["rows"])

    source = detail["panels"]["source"]
    assert source["available"] is True
    assert len(source["rows"]) == 18


def test_a_still_broken_body_compiles_with_reported_errors(
    client: TestClient, developer, targets
) -> None:
    response = client.post(
        "/api/v1/plsql/compile",
        headers=developer,
        json={"profileId": targets["development"]["id"], "source": STILL_BROKEN_BODY},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["compiled"] is False
    assert body["errors"], body
    assert body["outcome"]["verification"]["compiled"] is False


def test_repairing_and_compiling_makes_the_object_valid(
    client: TestClient, developer, targets
) -> None:
    profile_id = targets["development"]["id"]
    response = client.post(
        "/api/v1/plsql/compile",
        headers=developer,
        json={"profileId": profile_id, "source": REPAIRED_BODY},
    )
    body = response.json()
    assert body["compiled"] is True, body
    assert body["errors"] == []
    assert body["outcome"]["verification"]["compiled"] is True

    detail = _detail(client, developer, profile_id)
    assert detail["panels"]["errors"]["rows"] == []
    assert any(row[3] == "VALID" for row in detail["panels"]["status"]["rows"])


def test_compilation_does_not_commit_worksheet_work(client: TestClient, developer, targets) -> None:
    """Compilation runs in its own session, so pending DML stays pending."""

    profile_id = targets["development"]["id"]
    session = open_worksheet(client, developer, profile_id)
    execute(client, developer, session, "UPDATE employees SET salary = 55 WHERE employee_id = 100")

    client.post(
        "/api/v1/plsql/compile",
        headers=developer,
        json={"profileId": profile_id, "source": REPAIRED_BODY},
    )

    state = client.get(f"/api/v1/worksheets/{session}", headers=developer).json()
    assert state["session"]["transactionOpen"] is True

    client.post(f"/api/v1/worksheets/{session}/rollback", headers=developer)
    rows = execute(
        client, developer, session, "SELECT salary FROM employees WHERE employee_id = 100"
    )["outcome"]["resultSet"]["rows"]
    assert rows != [[55]]


def test_an_anonymous_block_returns_bounded_dbms_output(
    client: TestClient, developer, targets
) -> None:
    session = open_worksheet(client, developer, targets["development"]["id"])
    body = execute(
        client,
        developer,
        session,
        "BEGIN\n  DBMS_OUTPUT.PUT_LINE('checked ' || 'employee_report');\nEND;",
    )
    outcome = body["outcome"]
    assert outcome["state"] == "succeeded"
    assert outcome["dbmsOutput"] == ["checked employee_report"]
    assert outcome["dbmsOutputTruncated"] is False


def test_compile_endpoint_refuses_anything_that_is_not_a_program_unit(
    client: TestClient, developer, targets
) -> None:
    response = client.post(
        "/api/v1/plsql/compile",
        headers=developer,
        json={
            "profileId": targets["development"]["id"],
            "source": "SELECT 1 FROM dual",
        },
    )
    assert response.status_code == 400
    assert "CREATE OR REPLACE" in response.json()["error"]["message"]


def test_an_account_without_the_compile_permission_is_refused(
    client: TestClient, dba, targets
) -> None:
    """The DBA account holds read, worksheet and runbook here, but not compile."""

    response = client.post(
        "/api/v1/plsql/compile",
        headers=dba,
        json={"profileId": targets["development"]["id"], "source": REPAIRED_BODY},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "policy_refused"
