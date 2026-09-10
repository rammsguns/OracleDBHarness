"""The six MVP workflows, end to end.

Each test walks one of the workflows in MVP_PLAN.md, "MVP outcome", through the same
API the console and the IDE adapters use. They run against the local stand-in, so
they demonstrate the workflows are wired together; they are not Oracle evidence.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.conftest import execute, open_worksheet

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
END employee_report;
"""


def test_workflow_1_register_verify_and_inspect_a_connection(
    client: TestClient, administrator, developer, targets
) -> None:
    """Register a connection, verify identity and permissions, inspect its schemas."""

    created = client.post(
        "/api/v1/admin/targets",
        headers=administrator,
        json={
            "name": "workflow-1",
            "environment": "development",
            "host": "localhost",
            "port": 1521,
            "serviceName": "WORKFLOW1",
            "username": "harness_app",
            "defaultSchema": "HARNESS_APP",
            "secretReference": "harness-app",
            "worksheetsEnabled": True,
        },
    )
    assert created.status_code == 201, created.text
    profile_id = created.json()["id"]

    granted = client.post(
        "/api/v1/admin/grants",
        headers=administrator,
        json={
            "subject": "dev@example.internal",
            "profileId": profile_id,
            "permissions": ["read", "worksheet", "compile"],
        },
    )
    assert granted.status_code == 201, granted.text

    verified = client.post(f"/api/v1/targets/{profile_id}/test", headers=developer).json()
    assert verified["connected"] is True
    assert verified["identity"]["databaseName"] == "WORKFLOW1"
    assert verified["identity"]["version"].startswith("19.")
    assert all(c["checkedAt"] for c in verified["capabilities"])

    schemas = client.get(f"/api/v1/targets/{profile_id}/schemas", headers=developer).json()
    assert schemas["available"] is True
    assert any("HARNESS_APP" in str(row) for row in schemas["rows"])

    objects = client.get(
        f"/api/v1/targets/{profile_id}/objects",
        headers=developer,
        params={"owner": "HARNESS_APP", "limit": 3},
    ).json()
    assert len(objects["rows"]) == 3
    assert objects["hasMore"] is True


def test_workflow_2_run_parameterized_sql_and_control_the_transaction(
    client: TestClient, developer, targets
) -> None:
    profile_id = targets["development"]["id"]
    session = open_worksheet(client, developer, profile_id)

    read = execute(
        client,
        developer,
        session,
        "SELECT last_name, salary FROM employees WHERE department_id = :dept ORDER BY salary DESC",
        binds=[{"name": "dept", "value": 20}],
        maxRows=2,
    )
    assert read["outcome"]["state"] == "succeeded"
    assert read["outcome"]["resultSet"]["truncated"] is True
    assert read["outcome"]["elapsedMs"] >= 0

    write = execute(
        client,
        developer,
        session,
        "UPDATE employees SET salary = salary + :raise WHERE department_id = :dept",
        binds=[{"name": "raise", "value": 100}, {"name": "dept", "value": 20}],
    )
    assert write["outcome"]["rowsAffected"] == 3
    assert write["session"]["transactionOpen"] is True

    client.post(f"/api/v1/worksheets/{session}/rollback", headers=developer)
    client.post(f"/api/v1/worksheets/{session}/commit", headers=developer)
    after = execute(
        client,
        developer,
        session,
        "SELECT SUM(salary) AS total FROM employees WHERE department_id = 20",
    )
    assert after["outcome"]["state"] == "succeeded"

    history = client.get("/api/v1/executions", headers=developer).json()
    assert len(history) >= 3
    assert {h["state"] for h in history} == {"succeeded"}


def test_workflow_3_edit_compile_and_test_a_plsql_package(
    client: TestClient, developer, targets
) -> None:
    profile_id = targets["development"]["id"]

    detail = client.get(
        f"/api/v1/targets/{profile_id}/objects/detail",
        headers=developer,
        params={
            "owner": "HARNESS_APP",
            "objectName": "EMPLOYEE_REPORT",
            "objectType": "PACKAGE BODY",
        },
    ).json()
    assert detail["panels"]["errors"]["rows"][0][0] == 5

    compiled = client.post(
        "/api/v1/plsql/compile",
        headers=developer,
        json={"profileId": profile_id, "source": REPAIRED_BODY},
    ).json()
    assert compiled["compiled"] is True
    assert compiled["errors"] == []

    session = open_worksheet(client, developer, profile_id)
    tested = execute(
        client,
        developer,
        session,
        "BEGIN\n  DBMS_OUTPUT.PUT_LINE('headcount=' || "
        "(SELECT COUNT(*) FROM employees WHERE department_id = 20));\nEND;",
    )
    assert tested["outcome"]["dbmsOutput"] == ["headcount=3"]


def test_workflow_4_investigate_a_slow_statement(client: TestClient, developer, targets) -> None:
    profile_id = targets["development"]["id"]
    slow = (
        "SELECT order_id, SUM(quantity * unit_price) FROM order_lines "
        "WHERE product_id = :product_id GROUP BY order_id"
    )

    cursors = client.get(
        f"/api/v1/tuning/{profile_id}/cursors",
        headers=developer,
        params={"textFilter": "order_lines"},
    ).json()
    assert cursors["kind"] == "measured"
    sql_id = cursors["rows"][0][0]

    detail = client.get(f"/api/v1/tuning/{profile_id}/cursors/{sql_id}", headers=developer).json()
    assert detail["plan"]["available"] is True

    estimated = client.post(
        "/api/v1/tuning/explain",
        headers=developer,
        json={"profileId": profile_id, "statement": slow.replace(":product_id", "3")},
    ).json()
    assert estimated["kind"] == "estimated"

    before = client.post(
        "/api/v1/tuning/observations",
        headers=developer,
        json={
            "profileId": profile_id,
            "label": "full scan",
            "sqlId": sql_id,
            "measured": {"elapsedMs": 880, "bufferGets": 918300},
            "estimated": {"cost": estimated["rows"][0][9]},
            "context": {"dataSetLabel": "fixture-v1"},
        },
    ).json()
    after = client.post(
        "/api/v1/tuning/observations",
        headers=developer,
        json={
            "profileId": profile_id,
            "label": "indexed",
            "sqlId": sql_id,
            "measured": {"elapsedMs": 120, "bufferGets": 4100},
            "estimated": {"cost": 3},
            "context": {"dataSetLabel": "fixture-v1"},
        },
    ).json()

    comparison = client.get(
        "/api/v1/tuning/observations/compare",
        headers=developer,
        params={"beforeId": before["id"], "afterId": after["id"]},
    ).json()
    assert comparison["conclusionPermitted"] is True
    assert comparison["measuredDeltas"]["elapsedMs"]["delta"] == -760
    assert "estimatedBefore" in comparison and "measuredDeltas" in comparison


def test_workflow_5_dba_overview_and_a_reviewed_maintenance_action(
    client: TestClient, dba, targets
) -> None:
    profile_id = targets["development"]["id"]

    overview = client.get(f"/api/v1/targets/{profile_id}/dba/overview", headers=dba).json()
    assert overview["unavailablePanels"] == []
    blocking = overview["panels"]["blocking"]
    assert blocking["available"] is True
    assert blocking["rows"], "the seeded blocking scenario is visible"
    assert overview["panels"]["schedulerFailures"]["rows"], "a failed job is visible"
    assert overview["panels"]["invalidObjects"]["rows"], "the invalid package is visible"
    assert "AWR" in overview["note"]

    preview = client.post(
        "/api/v1/runbooks/runbook.gather_table_stats/preview",
        headers=dba,
        json={
            "profileId": profile_id,
            "parameters": {"owner": "HARNESS_APP", "table_name": "ORDER_LINES"},
        },
    ).json()
    assert preview["ready"] is True
    assert preview["willChangeDatabase"] is True

    run = client.post(
        "/api/v1/runbooks/runbook.gather_table_stats/run",
        headers=dba,
        json={
            "profileId": profile_id,
            "parameters": {"owner": "HARNESS_APP", "table_name": "ORDER_LINES"},
            "confirm": True,
        },
    ).json()
    assert run["outcome"] == "succeeded"
    assert run["verification"]["observed"] is True
    assert run["verification"]["rows"][0][2] == 4000


def test_workflow_6_ide_copilot_explains_a_selection_and_proposes_a_reviewed_diff(
    client: TestClient, administrator, targets
) -> None:
    """The DataForge path: an adapter credential, scoped context, a reviewed diff."""

    created = client.post(
        "/api/v1/admin/integrations",
        headers=administrator,
        json={"name": "dataforge-e2e", "kind": "dataforge", "scopes": ["copilot:assist"]},
    ).json()
    adapter = {"Authorization": f"Bearer {created['token']}"}

    capabilities = client.get("/api/v1/integrations/capabilities", headers=adapter).json()
    assert capabilities["enabled"] is True
    assert capabilities["executesDatabaseOperations"] is False

    source = "SELECT COUNT(*) INTO l_count FROM employee WHERE department_id = p_department_id;"
    payload = {
        "action": "diagnose",
        "targetReference": f"dataforge:{created['id']}:conn-9:HARNESS_APP",
        "userMessage": "Why will this not compile?",
        "databaseVersion": "19.3.0.0.0",
        "actorReference": "dataforge-user-17",
        "attachments": [
            {"category": "selected_source", "name": "selection", "content": source},
            {
                "category": "error_text",
                "name": "compiler output",
                "content": "PL/SQL: ORA-00942: table or view does not exist",
            },
        ],
        "editor": {"editorId": "df-buffer-3", "revision": "12", "text": source},
    }

    events: list[tuple[str, dict]] = []
    with client.stream(
        "POST", "/api/v1/copilot/requests", headers=adapter, json=payload
    ) as response:
        assert response.status_code == 200
        name = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                events.append((name, json.loads(line[6:])))

    answer = "".join(data["text"] for n, data in events if n == "delta")
    assert "ORA-00942" in answer
    proposal = next(data for n, data in events if n == "proposal")
    assert proposal["baseRevision"] == "12"
    assert proposal["appliesToEditorOnly"] is True

    applied = client.post(
        f"/api/v1/copilot/proposals/{proposal['proposalId']}/apply-check",
        headers=adapter,
        json={
            "editorId": "df-buffer-3",
            "revision": "12",
            "currentText": source,
            "targetReference": payload["targetReference"],
            "actorReference": "dataforge-user-17",
        },
    ).json()
    assert applied["canApply"] is True
    assert applied["executesDatabaseOperations"] is False

    # The adapter still cannot execute anything against a harness target.
    assert (
        client.post(
            "/api/v1/worksheets",
            headers=adapter,
            json={"profileId": targets["development"]["id"]},
        ).status_code
        == 401
    )
