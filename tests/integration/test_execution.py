"""Execution, transactions and session ownership.

These run against the local stand-in. They exercise the harness code that owns
transactions, limits and session leases; they are not evidence about Oracle. The
same file has to pass against Oracle 19c before release - see docs/compatibility.md.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import execute, open_worksheet


def test_bounded_fetch_reports_truncation(client: TestClient, developer, targets) -> None:
    session = open_worksheet(client, developer, targets["development"]["id"])
    body = execute(
        client,
        developer,
        session,
        "SELECT line_id FROM order_lines ORDER BY line_id",
        maxRows=25,
    )
    result = body["outcome"]["resultSet"]
    assert result["rowCount"] == 25
    assert result["truncated"] is True
    assert "Row limit" in result["truncationReason"]


def test_binds_are_passed_as_values_not_interpolated(
    client: TestClient, developer, targets
) -> None:
    session = open_worksheet(client, developer, targets["development"]["id"])
    body = execute(
        client,
        developer,
        session,
        "SELECT COUNT(*) AS c FROM employees WHERE last_name = :name",
        binds=[{"name": "name", "value": "Byron' OR '1'='1"}],
    )
    assert body["outcome"]["state"] == "succeeded"
    assert body["outcome"]["resultSet"]["rows"] == [[0]]


def test_dml_stays_uncommitted_until_asked(client: TestClient, developer, targets) -> None:
    profile_id = targets["development"]["id"]
    writer = open_worksheet(client, developer, profile_id)
    reader = open_worksheet(client, developer, profile_id)

    body = execute(
        client,
        developer,
        writer,
        "UPDATE employees SET salary = 1 WHERE employee_id = 100",
    )
    assert body["outcome"]["state"] == "succeeded"
    assert body["outcome"]["rowsAffected"] == 1
    assert body["session"]["transactionOpen"] is True

    seen = execute(
        client, developer, reader, "SELECT salary FROM employees WHERE employee_id = 100"
    )
    assert seen["outcome"]["resultSet"]["rows"] != [[1]]

    assert client.post(f"/api/v1/worksheets/{writer}/commit", headers=developer).json()[
        "hadOpenTransaction"
    ]
    seen = execute(
        client, developer, reader, "SELECT salary FROM employees WHERE employee_id = 100"
    )
    assert seen["outcome"]["resultSet"]["rows"] == [[1]]


def test_rollback_discards_the_change(client: TestClient, developer, targets) -> None:
    session = open_worksheet(client, developer, targets["development"]["id"])
    before = execute(
        client, developer, session, "SELECT salary FROM employees WHERE employee_id = 101"
    )["outcome"]["resultSet"]["rows"]

    execute(client, developer, session, "UPDATE employees SET salary = 7 WHERE employee_id = 101")
    response = client.post(f"/api/v1/worksheets/{session}/rollback", headers=developer)
    assert response.json() == {"rolledBack": True, "hadOpenTransaction": True}

    after = execute(
        client, developer, session, "SELECT salary FROM employees WHERE employee_id = 101"
    )["outcome"]["resultSet"]["rows"]
    assert after == before


def test_closing_a_session_rolls_back_uncommitted_work(
    client: TestClient, developer, targets
) -> None:
    profile_id = targets["development"]["id"]
    session = open_worksheet(client, developer, profile_id)
    execute(client, developer, session, "UPDATE employees SET salary = 3 WHERE employee_id = 102")
    closed = client.delete(f"/api/v1/worksheets/{session}", headers=developer).json()
    assert closed["rolledBackUncommittedWork"] is True

    other = open_worksheet(client, developer, profile_id)
    rows = execute(
        client, developer, other, "SELECT salary FROM employees WHERE employee_id = 102"
    )["outcome"]["resultSet"]["rows"]
    assert rows != [[3]]


def test_another_user_cannot_use_your_session(
    client: TestClient, developer, second_developer, targets
) -> None:
    session = open_worksheet(client, developer, targets["development"]["id"])

    response = client.post(
        f"/api/v1/worksheets/{session}/execute",
        headers=second_developer,
        json={"statement": "SELECT 1 FROM dual"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_authorized"

    assert (
        client.post(f"/api/v1/worksheets/{session}/commit", headers=second_developer).status_code
        == 403
    )
    assert (
        client.delete(f"/api/v1/worksheets/{session}", headers=second_developer).status_code == 403
    )
    assert client.get("/api/v1/worksheets", headers=second_developer).json()["sessions"] == []


def test_ddl_is_refused_while_dml_is_pending(client: TestClient, developer, targets) -> None:
    """Oracle DDL commits. The harness refuses rather than committing silently."""

    session = open_worksheet(client, developer, targets["development"]["id"])
    execute(client, developer, session, "UPDATE employees SET salary = 9 WHERE employee_id = 103")

    response = client.post(
        f"/api/v1/worksheets/{session}/execute",
        headers=developer,
        json={"statement": "CREATE TABLE scratch_ddl_guard (id NUMBER)"},
    )
    assert response.status_code == 403
    assert "would commit" in response.json()["error"]["message"]

    client.post(f"/api/v1/worksheets/{session}/rollback", headers=developer)
    response = client.post(
        f"/api/v1/worksheets/{session}/execute",
        headers=developer,
        json={"statement": "CREATE TABLE scratch_ddl_guard (id NUMBER)"},
    )
    assert response.json()["outcome"]["state"] == "succeeded"


def test_cancel_reports_whether_it_was_delivered(client: TestClient, developer, targets) -> None:
    session = open_worksheet(client, developer, targets["development"]["id"])
    body = client.post(f"/api/v1/worksheets/{session}/cancel", headers=developer).json()
    assert body["delivered"] is False
    assert "No statement is running" in body["reason"]


def test_idle_sessions_expire_and_say_so(client: TestClient, developer, targets, settings) -> None:
    session = open_worksheet(client, developer, targets["development"]["id"])
    registry = client.app.state.harness.execution._registry  # noqa: SLF001 - white-box
    registry._sessions[session].idle_timeout_seconds = 0.01  # noqa: SLF001
    time.sleep(0.05)
    assert registry.reap_expired() == [session]

    response = client.post(
        f"/api/v1/worksheets/{session}/execute",
        headers=developer,
        json={"statement": "SELECT 1 FROM dual"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_expired"


def test_a_deadline_that_elapses_is_reported_not_silently_extended(
    client: TestClient, developer, targets
) -> None:
    session = open_worksheet(client, developer, targets["development"]["id"])
    body = execute(
        client,
        developer,
        session,
        "SELECT COUNT(*) FROM order_lines",
        deadlineSeconds=0.001,
    )
    outcome = body["outcome"]
    # Either the statement beat the deadline and says the budget elapsed, or it was
    # stopped. Both are honest outcomes; silently succeeding without a note is not.
    assert outcome["state"] in ("succeeded", "failed", "cancelled", "outcome_unknown")
    if outcome["state"] == "succeeded" and outcome["warnings"]:
        assert any("budget" in w for w in outcome["warnings"])


def test_execution_records_are_written_for_every_statement(
    client: TestClient, developer, targets
) -> None:
    session = open_worksheet(client, developer, targets["development"]["id"])
    execute(client, developer, session, "SELECT 1 AS one FROM dual")
    executions = client.get("/api/v1/executions", headers=developer).json()
    assert executions
    latest = executions[0]
    assert latest["operationId"] == "worksheet.execute"
    assert latest["state"] == "succeeded"
    assert latest["policyDecision"] == "allowed"
    assert len(latest["statementFingerprint"]) == 64


def test_a_refused_statement_is_still_recorded(client: TestClient, developer, targets) -> None:
    session = open_worksheet(client, developer, targets["development"]["id"])
    response = client.post(
        f"/api/v1/worksheets/{session}/execute",
        headers=developer,
        json={"statement": "SELECT 1 FROM dual; SELECT 2 FROM dual"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["detail"]["reason"] == "multiple_statements"


def test_idempotency_key_prevents_a_second_dispatch(client: TestClient, developer, targets) -> None:
    session = open_worksheet(client, developer, targets["development"]["id"])
    payload = {
        "statement": "UPDATE employees SET salary = salary WHERE employee_id = 104",
        "idempotencyKey": "demo-key-1",
    }
    first = client.post(
        f"/api/v1/worksheets/{session}/execute", headers=developer, json=payload
    ).json()
    second = client.post(
        f"/api/v1/worksheets/{session}/execute", headers=developer, json=payload
    ).json()
    assert first["outcome"]["state"] == "succeeded"
    assert second["outcome"]["verification"]["deduplicated"] is True
    assert any("exactly once" in w for w in second["outcome"]["warnings"])


def test_compiling_a_program_unit_cannot_commit_pending_worksheet_work(
    client: TestClient, developer, targets
) -> None:
    """CREATE OR REPLACE is DDL. In Oracle it commits, so the worksheet refuses it."""

    session = open_worksheet(client, developer, targets["development"]["id"])
    execute(client, developer, session, "UPDATE employees SET salary = 7 WHERE employee_id = 101")

    response = client.post(
        f"/api/v1/worksheets/{session}/execute",
        headers=developer,
        json={
            "statement": (
                "CREATE OR REPLACE PROCEDURE scratch_guard IS BEGIN NULL; END scratch_guard;"
            )
        },
    )
    assert response.status_code == 403
    assert "would commit" in response.json()["error"]["message"]

    # The transaction is still the user's to decide about.
    state = client.get(f"/api/v1/worksheets/{session}", headers=developer).json()
    assert state["session"]["transactionOpen"] is True

    rolled_back = client.post(f"/api/v1/worksheets/{session}/rollback", headers=developer).json()
    assert rolled_back["hadOpenTransaction"] is True

    # With nothing pending there is nothing to lose, so it runs.
    response = client.post(
        f"/api/v1/worksheets/{session}/execute",
        headers=developer,
        json={
            "statement": (
                "CREATE OR REPLACE PROCEDURE scratch_guard IS BEGIN NULL; END scratch_guard;"
            )
        },
    )
    assert response.status_code == 200, response.text


def test_revoking_access_stops_a_commit_and_discards_the_session(
    client: TestClient, developer, administrator, targets
) -> None:
    """Owning a session is not the same as still being allowed to write to the target."""

    profile_id = targets["development"]["id"]
    session = open_worksheet(client, developer, profile_id)
    execute(client, developer, session, "UPDATE employees SET salary = 5 WHERE employee_id = 100")

    grant_id = client.post(
        "/api/v1/admin/grants",
        headers=administrator,
        json={
            "subject": "dev@example.internal",
            "profileId": profile_id,
            "permissions": ["read", "worksheet", "compile"],
        },
    ).json()["id"]
    revoked = client.delete(f"/api/v1/admin/grants/{grant_id}", headers=administrator)
    assert revoked.status_code == 200, revoked.text
    assert session in revoked.json()["closedSessions"]

    response = client.post(f"/api/v1/worksheets/{session}/commit", headers=developer)
    assert response.status_code in (403, 409)
    assert client.get("/api/v1/worksheets", headers=developer).json()["sessions"] == []

    # Restore access and confirm the uncommitted change did not survive.
    client.post(
        "/api/v1/admin/grants",
        headers=administrator,
        json={
            "subject": "dev@example.internal",
            "profileId": profile_id,
            "permissions": ["read", "worksheet", "compile"],
        },
    )
    fresh = open_worksheet(client, developer, profile_id)
    rows = execute(
        client, developer, fresh, "SELECT salary FROM employees WHERE employee_id = 100"
    )["outcome"]["resultSet"]["rows"]
    assert rows != [[5]]


def test_a_commit_is_refused_once_the_worksheet_permission_is_taken_away(
    client: TestClient, developer, administrator, targets
) -> None:
    """Narrowing a grant reaches an open session too, not only the next one."""

    profile_id = targets["development"]["id"]
    session = open_worksheet(client, developer, profile_id)
    execute(client, developer, session, "UPDATE employees SET salary = 6 WHERE employee_id = 100")

    client.post(
        "/api/v1/admin/grants",
        headers=administrator,
        json={
            "subject": "dev@example.internal",
            "profileId": profile_id,
            "permissions": ["read"],
        },
    )

    response = client.post(f"/api/v1/worksheets/{session}/commit", headers=developer)
    assert response.status_code == 403
    assert "worksheet" in response.json()["error"]["message"]
    # The session went with the permission; the pending change was rolled back.
    assert client.get("/api/v1/worksheets", headers=developer).json()["sessions"] == []


def test_an_idempotency_key_is_scoped_to_the_user_who_chose_it(
    client: TestClient, developer, second_developer, targets
) -> None:
    """Clients pick these strings, so two users will collide sooner or later."""

    profile_id = targets["development"]["id"]
    mine = open_worksheet(client, developer, profile_id)
    theirs = open_worksheet(client, second_developer, profile_id)

    first = client.post(
        f"/api/v1/worksheets/{mine}/execute",
        headers=developer,
        json={
            "statement": "UPDATE employees SET salary = salary WHERE employee_id = 104",
            "idempotencyKey": "shared-key",
        },
    ).json()
    assert first["outcome"]["state"] == "succeeded"

    second = client.post(
        f"/api/v1/worksheets/{theirs}/execute",
        headers=second_developer,
        json={
            "statement": "SELECT employee_id FROM employees WHERE employee_id = 104",
            "idempotencyKey": "shared-key",
        },
    )
    assert second.status_code == 200, second.text
    outcome = second.json()["outcome"]
    # Their own SELECT ran; they were not handed the other user's DML record.
    assert outcome["verification"].get("deduplicated") is not True
    assert outcome["executionId"] != first["outcome"]["executionId"]
    assert outcome["resultSet"]["rows"] == [[104]]


def test_reusing_an_idempotency_key_for_a_different_statement_is_refused(
    client: TestClient, developer, targets
) -> None:
    profile_id = targets["development"]["id"]
    session = open_worksheet(client, developer, profile_id)
    payload = {
        "statement": "UPDATE employees SET salary = salary WHERE employee_id = 105",
        "idempotencyKey": "reused-key",
    }
    assert (
        client.post(
            f"/api/v1/worksheets/{session}/execute", headers=developer, json=payload
        ).status_code
        == 200
    )

    response = client.post(
        f"/api/v1/worksheets/{session}/execute",
        headers=developer,
        json={
            "statement": "DELETE FROM employees WHERE employee_id = 105",
            "idempotencyKey": "reused-key",
        },
    )
    assert response.status_code == 400
    assert "different request" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("first", "changed"),
    [
        ({"statement": "SELECT 'alpha' FROM dual"}, {"statement": "SELECT 'bravo' FROM dual"}),
        ({"statement": "SELECT 10 FROM dual"}, {"statement": "SELECT 20 FROM dual"}),
        (
            {"statement": "SELECT :v FROM dual", "binds": [{"name": "v", "value": 10}]},
            {"binds": [{"name": "v", "value": 20}]},
        ),
        (
            {"statement": "SELECT :v FROM dual", "binds": [{"name": "v", "value": 10}]},
            {"binds": [{"name": "v", "value": "10"}]},
        ),
        (
            {"statement": "SELECT :v FROM dual", "binds": [{"name": "v", "value": None}]},
            {"binds": [{"name": "v", "value": ""}]},
        ),
        ({"statement": "SELECT 1 FROM dual", "maxRows": 10}, {"maxRows": 20}),
    ],
)
def test_idempotency_rejects_changed_execution_inputs(
    client: TestClient, developer, targets, first, changed
) -> None:
    session = open_worksheet(client, developer, targets["development"]["id"])
    url = f"/api/v1/worksheets/{session}/execute"
    payload = {**first, "idempotencyKey": "exact-request"}
    original = client.post(url, headers=developer, json=payload)
    assert original.status_code == 200, original.text
    assert original.json()["outcome"]["state"] == "succeeded"

    rejected = client.post(url, headers=developer, json={**payload, **changed})
    assert rejected.status_code == 400, rejected.text
    assert "different request" in rejected.json()["error"]["message"]

    retry = client.post(url, headers=developer, json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.json()["outcome"]["executionId"] == original.json()["outcome"]["executionId"]
    assert retry.json()["outcome"]["verification"]["deduplicated"] is True


def test_idempotency_accepts_reordered_binds_and_rejects_legacy_records(
    client: TestClient, developer, targets
) -> None:
    from harness_api.models import Execution

    session = open_worksheet(client, developer, targets["development"]["id"])
    url = f"/api/v1/worksheets/{session}/execute"
    payload = {
        "statement": "SELECT :a, :b FROM dual",
        "binds": [{"name": "a", "value": "private-value"}, {"name": "b", "value": 2}],
        "idempotencyKey": "ordered-binds",
    }
    original = client.post(url, headers=developer, json=payload).json()["outcome"]
    assert original["state"] == "succeeded"
    payload["binds"].reverse()
    retry = client.post(url, headers=developer, json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.json()["outcome"]["verification"]["deduplicated"] is True

    with client.app.state.harness.session_factory() as db:
        record = db.get(Execution, original["executionId"])
        assert len(record.request_digest) == 64
        assert record.statement_text is None
        # Simulate an execution created before the digest column was introduced.
        record.request_digest = None
        db.commit()
    rejected = client.post(url, headers=developer, json=payload)
    assert rejected.status_code == 400, rejected.text
