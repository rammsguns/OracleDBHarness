"""What a commit is allowed to claim.

A commit has three possible answers, not two, and the harness has to keep them
apart. It succeeded; it was refused, in which case the work is still pending on a
session the user can still reach; or the connection died with the COMMIT in flight,
in which case Oracle may already have made the transaction durable. The last case is
reported as ``outcome_unknown``, written to the audit trail as such, and the session
is retired so nothing can commit or retry on it.

These run against the local stand-in and exercise the harness code, not Oracle. See
docs/compatibility.md.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import audit_events, execute, live_connection, open_worksheet


def test_a_successful_commit_is_durable_and_audited(client: TestClient, developer, targets) -> None:
    profile_id = targets["development"]["id"]
    session = open_worksheet(client, developer, profile_id)
    execute(client, developer, session, "UPDATE employees SET salary = 11 WHERE employee_id = 100")

    response = client.post(f"/api/v1/worksheets/{session}/commit", headers=developer)
    assert response.status_code == 200, response.text
    assert response.json() == {"committed": True, "hadOpenTransaction": True}

    events = audit_events(client, "worksheet.commit")
    assert [event.outcome for event in events] == ["succeeded"]

    # The session survives a commit and the change is visible to a separate session.
    reader = open_worksheet(client, developer, profile_id)
    rows = execute(
        client, developer, reader, "SELECT salary FROM employees WHERE employee_id = 100"
    )["outcome"]["resultSet"]["rows"]
    assert rows == [[11]]


def test_a_refused_commit_leaves_the_work_pending_on_a_usable_session(
    client: TestClient, developer, targets
) -> None:
    """A definite failure is a failure. The user keeps the session and the choice."""

    profile_id = targets["development"]["id"]
    session = open_worksheet(client, developer, profile_id)
    execute(client, developer, session, "UPDATE employees SET salary = 12 WHERE employee_id = 100")
    live_connection(client, session).fail_next_commit()

    response = client.post(f"/api/v1/worksheets/{session}/commit", headers=developer)
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "oracle_error"
    assert error["oracleCode"] == "ORA-02290"

    events = audit_events(client, "worksheet.commit")
    assert [event.outcome for event in events] == ["failed"]

    # Nothing was retired, and the transaction is still the user's to resolve.
    described = client.get(f"/api/v1/worksheets/{session}", headers=developer)
    assert described.status_code == 200, described.text
    assert described.json()["session"]["transactionOpen"] is True

    rolled_back = client.post(f"/api/v1/worksheets/{session}/rollback", headers=developer)
    assert rolled_back.status_code == 200, rolled_back.text


def test_a_commit_whose_answer_is_lost_is_reported_as_outcome_unknown(
    client: TestClient, developer, targets
) -> None:
    """The one case that must never be called success or failure."""

    profile_id = targets["development"]["id"]
    session = open_worksheet(client, developer, profile_id)
    execute(client, developer, session, "UPDATE employees SET salary = 13 WHERE employee_id = 100")
    live_connection(client, session).fail_next_commit(lost=True)

    response = client.post(f"/api/v1/worksheets/{session}/commit", headers=developer)
    assert response.status_code == 502, response.text
    error = response.json()["error"]
    assert error["code"] == "outcome_unknown"
    assert error["retryable"] is False
    assert error["detail"]["verificationRequired"] is True
    assert error["detail"]["sessionId"] == session
    assert error["detail"]["hadOpenTransaction"] is True
    assert "verify" in error["message"].lower()


def test_a_lost_commit_is_recorded_in_the_audit_trail(
    client: TestClient, developer, targets
) -> None:
    profile_id = targets["development"]["id"]
    session = open_worksheet(client, developer, profile_id)
    execute(client, developer, session, "UPDATE employees SET salary = 14 WHERE employee_id = 100")
    live_connection(client, session).fail_next_commit(lost=True)
    client.post(f"/api/v1/worksheets/{session}/commit", headers=developer)

    events = audit_events(client, "worksheet.commit")
    assert [event.outcome for event in events] == ["outcome_unknown"]
    detail = events[0].detail
    assert detail["verificationRequired"] is True
    assert detail["error"]["code"] == "outcome_unknown"
    assert events[0].risk_class == "persistent_write"


def test_a_session_that_lost_its_commit_is_retired_not_reused(
    client: TestClient, developer, targets
) -> None:
    """Nothing may commit, roll back or run on the connection again."""

    profile_id = targets["development"]["id"]
    session = open_worksheet(client, developer, profile_id)
    execute(client, developer, session, "UPDATE employees SET salary = 15 WHERE employee_id = 100")
    live_connection(client, session).fail_next_commit(lost=True)
    client.post(f"/api/v1/worksheets/{session}/commit", headers=developer)

    assert client.get("/api/v1/worksheets", headers=developer).json()["sessions"] == []
    for path, method in (
        (f"/api/v1/worksheets/{session}/commit", client.post),
        (f"/api/v1/worksheets/{session}/rollback", client.post),
        (f"/api/v1/worksheets/{session}", client.get),
    ):
        assert method(path, headers=developer).status_code == 409

    retried = execute(
        client, developer, session, "UPDATE employees SET salary = 15 WHERE employee_id = 100"
    )
    assert retried["error"]["code"] == "session_expired"

    # The durable session record says why it went away, so the trail is complete.
    from harness_api.models import WorksheetSessionRecord

    factory = client.app.state.harness.session_factory
    with factory() as db:
        record = db.get(WorksheetSessionRecord, session)
        assert record is not None
        assert record.closed_at is not None
        assert record.close_reason == "connection lost during commit"


VALID_BODY = """\
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


def _lose_the_next_one_shot_commit(client: TestClient) -> None:
    """Arm the next connection the engine opens so its commit is lost.

    Compilation, runbooks and diagnostics each open their own connection and commit
    it themselves, so the failure has to be armed as that connection is created
    rather than on a worksheet session the test already holds.
    """

    backend = client.app.state.harness.execution._backend

    def connect(spec, _original=backend.connect):
        connection = _original(spec)
        backend.connect = _original
        connection.fail_next_commit(lost=True)
        return connection

    backend.connect = connect


def _latest_execution(client: TestClient, operation_id: str):
    from sqlalchemy import select

    from harness_api.models import Execution

    factory = client.app.state.harness.session_factory
    with factory() as db:
        return db.scalars(
            select(Execution)
            .where(Execution.operation_id == operation_id)
            .order_by(Execution.started_at.desc())
        ).first()


def test_a_lost_one_shot_commit_is_not_recorded_as_a_failed_execution(
    client: TestClient, developer, targets
) -> None:
    """The uncertainty has to survive the trip out of the engine.

    A one-shot statement commits after the engine has settled its outcome, so a
    commit whose answer never comes back arrives at the execution record as a raised
    error rather than as a state. Writing it down as ``failed`` would tell the next
    reader the work did not happen, and invite a retry that applies it twice.
    """

    _lose_the_next_one_shot_commit(client)
    response = client.post(
        "/api/v1/plsql/compile",
        headers=developer,
        json={"profileId": targets["development"]["id"], "source": VALID_BODY},
    )
    assert response.status_code == 502, response.text
    error = response.json()["error"]
    assert error["code"] == "outcome_unknown"
    assert error["retryable"] is False
    assert error["detail"]["statementCompleted"] is True
    assert error["detail"]["verificationRequired"] is True

    record = _latest_execution(client, "plsql.compile")
    assert record is not None
    assert record.state == "outcome_unknown"
    assert record.error_code == "outcome_unknown"
    assert record.verification_json["verificationRequired"] is True
    assert record.finished_at is not None
