"""Restarting the API over a store that already holds interrupted work.

The unit suite checks the reconciliation decision in isolation. This one checks what an
operator actually lives through: the process goes away mid-statement, a new one starts
against the same metadata store, and the questions are whether the database gets touched
again, what the API now says about that statement, and whether anybody is given a
procedure to follow.

A killed process is simulated by abandoning the application without shutting its
execution service down. That leaves exactly the durable state a SIGKILL leaves, because
a process that dies writes nothing on its way out: no closed sessions, no finished
execution records, no clean-stop marker.

These run against the local stand-in and exercise the harness code. That an abandoned
Oracle connection really does roll back is a separate, Oracle-side claim; see
``tests/qualification/test_connection_loss.py`` and docs/compatibility.md.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from harness_api.app import create_app
from harness_api.config import Settings
from harness_api.db import build_engine, build_session_factory
from harness_api.models import AuditEvent, Execution, WorksheetSessionRecord, utcnow
from harness_api.recovery import RECONCILE_SESSION
from harness_worker.types import ExecutionState
from tests.conftest import auth, live_connection, open_worksheet

DEAD_RUNTIME = "rt_a_process_that_died"


@pytest.fixture
def doomed(settings: Settings) -> Iterator[Callable[[], TestClient]]:
    """Start applications whose process dies rather than stopping.

    A killed process runs no shutdown hook and has none of its threads left, so nothing
    closes its worksheet sessions, finishes its execution records or marks its runtime
    stopped. Suppressing all of that is what leaves the metadata store in the state a
    SIGKILL leaves it in, which is the state reconciliation has to work from.

    The client is deliberately never exited inside the test: exiting drains the request
    portal, which would let a statement still in flight run to completion -- the opposite
    of what is being tested. Teardown handles it, after the test has finished looking at
    what the restart made of the wreckage.
    """

    clients: list[TestClient] = []

    def start() -> TestClient:
        app = create_app(settings)
        client = TestClient(app)
        client.__enter__()
        service = app.state.harness.execution
        service._reaper_stop.set()  # noqa: SLF001 - a dead process has no threads
        service.shutdown = lambda: None  # type: ignore[method-assign]
        clients.append(client)
        return client

    yield start

    for client in clients:
        try:
            client.__exit__(None, None, None)
        except Exception:  # noqa: BLE001, PERF203 - teardown of an abandoned process
            pass


def _developer(client: TestClient) -> dict[str, str]:
    return auth(client, "dev@example.internal", ["developer"])


def _development_target(client: TestClient, headers: dict[str, str]) -> str:
    targets = client.get("/api/v1/targets", headers=headers).json()
    return next(target["id"] for target in targets if target["name"] == "development")


def _store(settings: Settings) -> Any:
    """A session factory over the same metadata store, outside any application."""

    return build_session_factory(build_engine(settings))


def _executions(settings: Settings) -> list[Execution]:
    factory = _store(settings)
    with factory() as db:
        return list(db.scalars(select(Execution).order_by(Execution.started_at)).all())


def _plant_interrupted_write(settings: Settings, profile_id: str, execution_id: str) -> None:
    """An execution record as a process killed mid-UPDATE would have left it.

    Planted rather than produced by a real interruption, because these three checks are
    about what the API and the operator do with such a record afterwards. The record is
    produced for real in ``test_a_statement_in_flight_when_the_process_died_is_not_rerun``.
    """

    factory = _store(settings)
    with factory() as db:
        db.add(
            Execution(
                id=execution_id,
                user_id="",
                profile_id=profile_id,
                operation_id="worksheet.execute",
                statement_kind="dml",
                risk_class="persistent_write",
                statement_fingerprint="a" * 64,
                state=ExecutionState.RUNNING.value,
                owner_id=DEAD_RUNTIME,
                dispatched_at=utcnow(),
            )
        )
        db.commit()


@pytest.mark.stand_in_only
def test_a_statement_in_flight_when_the_process_died_is_not_rerun(
    settings: Settings, seeded: dict, doomed: Callable[[], TestClient]
) -> None:
    """The restart must not touch the database on the interrupted statement's behalf.

    The evidence is the row the interrupted UPDATE would have changed. The first process
    was killed with the statement in flight; the second must not complete it. A restart
    that replayed the write would leave 4242 behind.
    """

    reached_the_backend = threading.Event()
    let_it_finish = threading.Event()
    try:
        client = doomed()
        headers = _developer(client)
        session = open_worksheet(client, headers, _development_target(client, headers))

        # Hold the statement inside the leased connection so it is genuinely in flight at
        # the moment the process is abandoned: dispatched, holding the session lock, no
        # outcome. Only the statement under test blocks.
        connection = live_connection(client, session)
        inner = connection.execute

        def blocking_execute(statement: str, *args: Any, **kwargs: Any) -> Any:
            if "4242" in statement:
                reached_the_backend.set()
                let_it_finish.wait(30.0)
            return inner(statement, *args, **kwargs)

        connection.execute = blocking_execute

        def run_the_write() -> None:
            try:
                client.post(
                    f"/api/v1/worksheets/{session}/execute",
                    headers=headers,
                    json={
                        "statement": "UPDATE employees SET salary = 4242 WHERE employee_id = 100"
                    },
                )
            except Exception:  # noqa: BLE001 - the process is going away; nobody reads this
                pass

        # A daemon thread, abandoned along with the process it belongs to.
        writer = threading.Thread(target=run_the_write, daemon=True)
        writer.start()
        assert reached_the_backend.wait(10.0), "the write never reached the backend"

        # What the store holds at the moment of death: dispatched, and not finished.
        (in_flight,) = _executions(settings)
        interrupted_id = in_flight.id
        assert in_flight.state == ExecutionState.RUNNING.value
        assert in_flight.dispatched_at is not None

        # A new process over the same store. Reconciliation runs during its startup.
        second = create_app(settings)
        with TestClient(second) as fresh_client:
            fresh_headers = _developer(fresh_client)
            reconciled = {row.id: row for row in _executions(settings)}[interrupted_id]
            assert reconciled.state == ExecutionState.OUTCOME_UNKNOWN.value
            assert reconciled.verification_json["verificationRequired"] is True
            assert reconciled.error_code == "interrupted"

            # The database is the evidence: nothing reran the UPDATE.
            fresh_session = open_worksheet(
                fresh_client, fresh_headers, _development_target(fresh_client, fresh_headers)
            )
            response = fresh_client.post(
                f"/api/v1/worksheets/{fresh_session}/execute",
                headers=fresh_headers,
                json={"statement": "SELECT salary FROM employees WHERE employee_id = 100"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["outcome"]["resultSet"]["rows"] != [[4242]], (
                "the restart reran the interrupted UPDATE"
            )
    finally:
        let_it_finish.set()

    # The abandoned worker now finishes, inside a process that no longer owns the store.
    # Its late answer must not overwrite the verdict the restart already recorded and an
    # operator may already be acting on.
    writer.join(10.0)
    assert not writer.is_alive(), "the abandoned write never finished, so nothing was proved"
    late = {row.id: row.state for row in _executions(settings)}[interrupted_id]
    assert late == ExecutionState.OUTCOME_UNKNOWN.value, (
        f"a late completion overwrote the reconciled record with {late!r}"
    )


def test_the_restart_tells_the_owner_the_outcome_is_unknown(
    settings: Settings, seeded: dict
) -> None:
    """What a user sees when they reload after a restart.

    Not ``failed``, which invites a retry that would apply the write twice, and not
    ``succeeded``. The record says unknown and says verification is needed.
    """

    first = create_app(settings)
    with TestClient(first) as client:
        headers = _developer(client)
        profile_id = _development_target(client, headers)
    _plant_interrupted_write(settings, profile_id, "exe_interrupted_write")

    second = create_app(settings)
    with TestClient(second) as client:
        administrator = auth(client, "admin@example.internal", ["administrator"])
        report = client.get("/api/v1/admin/reconciliation", headers=administrator)
        assert report.status_code == 200, report.text
        body = report.json()

    assert "exe_interrupted_write" in body["outcomeUnknown"]
    assert body["needsVerification"] == 1
    assert body["procedure"], "an operator is handed no procedure to follow"
    outstanding = {item["id"]: item for item in body["outstanding"]}
    assert outstanding["exe_interrupted_write"]["state"] == "outcome_unknown"
    assert "unknown" in outstanding["exe_interrupted_write"]["errorMessage"]


def test_an_operator_records_a_verification_without_rewriting_what_happened(
    settings: Settings, seeded: dict
) -> None:
    """Recording a finding clears the queue and keeps the uncertainty.

    The state stays ``outcome_unknown``, because the harness never observed the outcome
    and a human's later check is a different kind of fact from one the harness saw for
    itself. Both are kept, which is what makes the trail readable a month later.
    """

    first = create_app(settings)
    with TestClient(first) as client:
        headers = _developer(client)
        profile_id = _development_target(client, headers)
    _plant_interrupted_write(settings, profile_id, "exe_needs_check")

    second = create_app(settings)
    with TestClient(second) as client:
        administrator = auth(client, "admin@example.internal", ["administrator"])
        recorded = client.post(
            "/api/v1/admin/executions/exe_needs_check/verification",
            headers=administrator,
            json={
                "finding": "not_applied",
                "note": "Salary unchanged at that key; the UPDATE never landed.",
            },
        )
        assert recorded.status_code == 200, recorded.text
        assert recorded.json()["state"] == "outcome_unknown"

        after = client.get("/api/v1/admin/reconciliation", headers=administrator).json()
        assert after["outstanding"] == []
        assert after["needsVerification"] == 1, "the restart still reports what it found"

        trail = client.get("/api/v1/audit", headers=administrator).json()
        verified = [e for e in trail if e["operationId"] == "system.restart.verified"]
        assert [e["outcome"] for e in verified] == ["not_applied"]
        assert verified[0]["executionId"] == "exe_needs_check"

    with _store(settings)() as db:
        row = db.get(Execution, "exe_needs_check")
        assert row is not None
        assert row.state == ExecutionState.OUTCOME_UNKNOWN.value
        assert row.verification_json["operatorVerification"]["finding"] == "not_applied"


def test_a_finding_is_refused_for_an_execution_with_a_known_outcome(
    client: TestClient, developer: dict[str, str], targets: dict
) -> None:
    """Only an uncertain execution takes a finding.

    A write the harness watched succeed does not need verifying, and accepting a finding
    against one would let an administrator annotate history that was never in doubt.
    """

    session = open_worksheet(client, developer, targets["development"]["id"])
    response = client.post(
        f"/api/v1/worksheets/{session}/execute",
        headers=developer,
        json={"statement": "UPDATE employees SET salary = 77 WHERE employee_id = 100"},
    )
    assert response.status_code == 200, response.text
    execution_id = response.json()["outcome"]["executionId"]

    administrator = auth(client, "admin@example.internal", ["administrator"])
    refused = client.post(
        f"/api/v1/admin/executions/{execution_id}/verification",
        headers=administrator,
        json={"finding": "applied"},
    )
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["code"] == "invalid_request"
    assert refused.json()["error"]["detail"]["state"] == "succeeded"


def test_a_nonsense_finding_is_refused(client: TestClient) -> None:
    administrator = auth(client, "admin@example.internal", ["administrator"])
    refused = client.post(
        "/api/v1/admin/executions/exe_whatever/verification",
        headers=administrator,
        json={"finding": "probably fine"},
    )
    assert refused.status_code == 400, refused.text
    assert "applied" in refused.json()["error"]["message"]


def test_the_reconciliation_report_is_restricted_to_administrators(
    client: TestClient, developer: dict[str, str]
) -> None:
    refused = client.get("/api/v1/admin/reconciliation", headers=developer)
    assert refused.status_code == 403, refused.text


def test_a_worksheet_session_does_not_survive_a_restart(
    settings: Settings, seeded: dict, doomed: Callable[[], TestClient]
) -> None:
    """The lease died with the process that held it, and the record says so.

    An open record left behind would show a user a session nothing can reach and that no
    idle reaper would ever collect, because the reaper only knows sessions in memory.
    """

    client = doomed()
    headers = _developer(client)
    session_id = open_worksheet(client, headers, _development_target(client, headers))

    second = create_app(settings)
    with TestClient(second) as client:
        headers = _developer(client)
        assert client.get("/api/v1/worksheets", headers=headers).json()["sessions"] == []
        gone = client.get(f"/api/v1/worksheets/{session_id}", headers=headers)
        assert gone.status_code in (404, 409), gone.text

    with _store(settings)() as db:
        row = db.get(WorksheetSessionRecord, session_id)
        assert row is not None
        assert row.closed_at is not None
        assert row.close_reason == "process restart"
        event = db.scalars(
            select(AuditEvent).where(AuditEvent.operation_id == RECONCILE_SESSION)
        ).one()
        assert event.detail["sessionId"] == session_id
        assert event.outcome == "closed"


def test_a_clean_restart_reconciles_nothing(settings: Settings, seeded: dict) -> None:
    """An orderly shutdown leaves nothing to resolve, and the report says so rather than
    inventing activity. A startup that always reported work would make the real thing
    impossible to notice."""

    first = create_app(settings)
    with TestClient(first) as client:
        headers = _developer(client)
        session = open_worksheet(client, headers, _development_target(client, headers))
        client.post(
            f"/api/v1/worksheets/{session}/execute",
            headers=headers,
            json={"statement": "SELECT employee_id FROM employees WHERE employee_id = 100"},
        )
        client.delete(f"/api/v1/worksheets/{session}", headers=headers)

    second = create_app(settings)
    with TestClient(second) as client:
        administrator = auth(client, "admin@example.internal", ["administrator"])
        body = client.get("/api/v1/admin/reconciliation", headers=administrator).json()

    assert body["executionsResolved"] == 0
    assert body["sessionsClosed"] == []
    assert body["outstanding"] == []
    assert body["procedure"] == []
    assert "nothing interrupted" in body["summary"]
