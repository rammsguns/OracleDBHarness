"""Killing the API process for real, at the four moments restart recovery has to handle.

``test_restart.py`` abandons an application inside the test process, which leaves the
metadata store as a dead process would but keeps its database connection alive. Here the
API runs in its own interpreter and is ended by the operating system (see
``tests/process_death``), so the database sees what it sees when a pilot host's API
process crashes: a client that vanished mid-conversation.

Every scenario asserts the same three things, from three independent places:

* **The persisted record** - read from the metadata store directly, and through the
  administrator's reconciliation report on a freshly started process.
* **The database effect** - read through a separate session, only once that session has
  seen the dead one cleaned up. Reading earlier would read a transaction in progress.
* **No replay and no false success** - exactly one execution record for the statement,
  audited ``redispatched: false``, and no success recorded for anything whose answer was
  never seen.

Against the stand-in this is a deterministic rehearsal of the machinery and of the
harness's reconciliation after a real OS kill. It is not evidence about Oracle, and it
writes none. With ``HARNESS_QUAL_*`` configured the same scenarios run against Oracle,
check that the killed process really was using the Oracle driver, and write their
observations to a report beside the qualification report.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from harness_api.config import Settings
from harness_api.db import build_engine, build_session_factory
from harness_api.models import AuditEvent, ConnectionProfile, Execution, WorksheetSessionRecord
from harness_api.recovery import RECONCILE_EXECUTION, VERIFIED_OPERATION_ID
from harness_worker.backend import create_backend
from harness_worker.backend.base import ConnectionSpec
from harness_worker.statement import fingerprint, prepare
from harness_worker.types import ExecutionState
from tests import oracle_config
from tests.process_death.harness import (
    EMPLOYEE_ID,
    KILL_METHOD,
    ApiProcess,
    Cleanup,
    Observer,
    confirm_backend,
    fire_and_abandon,
    start_api,
)
from tests.qualification.evidence import Evidence

# A tautology no other statement in the suites carries, so a barrier stops on the
# scenario's statement and on nothing the API runs for itself.
MARK = "'process-death' = 'process-death'"
READ = f"SELECT salary FROM employees WHERE employee_id = {EMPLOYEE_ID} AND {MARK}"


def _write(salary: int) -> str:
    return f"UPDATE employees SET salary = {salary} WHERE employee_id = {EMPLOYEE_ID} AND {MARK}"


# -- fixtures -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def death_evidence(
    oracle_target: oracle_config.OracleTestConfig | None,
) -> Iterator[Evidence | None]:
    """The process-death report, on Oracle only.

    Written beside ``HARNESS_QUAL_REPORT`` rather than into it, so this suite and the
    backend suite cannot overwrite each other's JSON sidecar.
    """

    if oracle_target is None:
        yield None
        return
    report = oracle_target.report_path
    record = Evidence(
        config=oracle_target,
        title="Process-death run",
        path=report.with_name(f"{report.stem}-process-death{report.suffix}") if report else None,
    )
    try:
        yield record
    finally:
        record.write()


@pytest.fixture
def spawn(settings: Settings, seeded: dict, workspace: Path) -> Iterator[Callable[..., ApiProcess]]:
    """Start API processes, and make sure none outlives the test however it ends."""

    started: list[ApiProcess] = []

    def start(barrier: str | None = None, match: str = "") -> ApiProcess:
        child = start_api(
            settings,
            workspace / "processes" / f"api-{len(started)}",
            barrier=barrier,
            match=match,
        )
        started.append(child)
        return child

    yield start
    for child in started:
        child.kill()


@pytest.fixture
def observer(
    settings: Settings,
    seeded: dict,
    oracle_target: oracle_config.OracleTestConfig | None,
) -> Iterator[Observer]:
    """A session on the development target that is not the API's, and a restored row."""

    backend = create_backend(
        settings.oracle_backend,
        driver_mode=settings.oracle_driver_mode,
        lib_dir=settings.oracle_client_lib_dir or None,
        fake_data_dir=settings.oracle_fake_data_dir or None,
    )
    if oracle_target is not None:
        spec = oracle_target.connection_spec("process-death-observer")
    else:
        with _store(settings)() as db:
            profile = db.scalars(
                select(ConnectionProfile).where(ConnectionProfile.name == "development")
            ).one()
            spec = ConnectionSpec(
                profile_id="process-death-observer",
                host=profile.host,
                port=profile.port,
                service_name=profile.service_name,
                username=profile.username,
                default_schema=profile.default_schema,
            )
    watcher = Observer(backend, spec)
    original = watcher.salary()
    try:
        yield watcher
    finally:
        try:
            if watcher.salary() != original:
                watcher.set_salary(original)
        finally:
            watcher.close()
            backend.shutdown()


# -- helpers ------------------------------------------------------------------------------


def _store(settings: Settings) -> Any:
    return build_session_factory(build_engine(settings))


def _token(client: httpx.Client, subject: str, roles: list[str]) -> dict[str, str]:
    response = client.post("/api/v1/auth/dev-token", json={"subject": subject, "roles": roles})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


def _developer(client: httpx.Client) -> dict[str, str]:
    return _token(client, "dev@example.internal", ["developer"])


def _administrator(client: httpx.Client) -> dict[str, str]:
    return _token(client, "admin@example.internal", ["administrator"])


def _open_worksheet(client: httpx.Client, headers: dict[str, str]) -> tuple[str, dict[str, Any]]:
    targets = client.get("/api/v1/targets", headers=headers).json()
    target = next(item["id"] for item in targets if item["name"] == "development")
    response = client.post("/api/v1/worksheets", headers=headers, json={"profileId": target})
    assert response.status_code == 201, response.text
    session = response.json()["session"]
    return session["sessionId"], session["identity"]


def _abandon(child: ApiProcess, path: str, headers: dict[str, str], body: dict | None) -> None:
    """Send the request that will be in progress when the process dies."""

    def call() -> None:
        with child.client(timeout=300.0) as client:
            client.post(path, headers=headers, json=body)

    fire_and_abandon(call)


def _confirm_backend(
    reached: dict[str, Any],
    identity: dict[str, Any],
    oracle_target: oracle_config.OracleTestConfig | None,
) -> None:
    confirm_backend(reached, identity, on_oracle=oracle_target is not None)


def _records_for(settings: Settings, statement: str) -> list[Execution]:
    digest = fingerprint(prepare(statement)[0])
    with _store(settings)() as db:
        return list(
            db.scalars(select(Execution).where(Execution.statement_fingerprint == digest)).all()
        )


def _the_one_record(settings: Settings, statement: str) -> Execution:
    """The statement's execution record - and proof there is only one.

    A restart that replayed the statement would have written a second record for it.
    """

    records = _records_for(settings, statement)
    assert len(records) == 1, (
        f"{len(records)} execution records for the interrupted statement; the restart "
        "must not have dispatched it again"
    )
    return records[0]


def _assert_not_redispatched(settings: Settings, execution_id: str) -> None:
    with _store(settings)() as db:
        events = db.scalars(
            select(AuditEvent).where(
                AuditEvent.execution_id == execution_id,
                AuditEvent.operation_id == RECONCILE_EXECUTION,
            )
        ).all()
    assert [event.detail["redispatched"] for event in events] == [False]


def _reconciliation(child: ApiProcess) -> dict[str, Any]:
    with child.client() as client:
        response = client.get("/api/v1/admin/reconciliation", headers=_administrator(client))
    assert response.status_code == 200, response.text
    return response.json()


def _note(
    evidence: Evidence | None,
    question: str,
    child: ApiProcess,
    identity: dict[str, Any],
    cleanup: Cleanup,
    answer: str,
) -> None:
    if evidence is None:
        return
    evidence.record_database(
        {
            "databaseName": identity.get("databaseName"),
            "containerName": identity.get("containerName"),
            "isCdb": identity.get("isCdb"),
            "versionFull": identity.get("versionFull"),
        }
    )
    evidence.note(
        question,
        f"API process {child.pid} ended with {KILL_METHOD} at `{child.barrier}`. "
        f"Dead session {identity.get('sessionId')},{identity.get('serialNumber')} cleaned "
        f"up after {cleanup.seconds:.1f}s ({cleanup.detail}). {answer}",
    )


# -- the four moments ---------------------------------------------------------------------


def test_death_before_dispatch(
    settings: Settings,
    spawn: Callable[..., ApiProcess],
    observer: Observer,
    oracle_target: oracle_config.OracleTestConfig | None,
    death_evidence: Evidence | None,
) -> None:
    """The record says queued and the statement never left the process.

    The only situation in which ``cancelled`` is the truth, and the database proves it.
    """

    before = observer.salary()
    statement = _write(4242)
    doomed = spawn(barrier="before_dispatch", match=MARK)
    with doomed.client() as client:
        headers = _developer(client)
        session_id, identity = _open_worksheet(client, headers)
    _abandon(doomed, f"/api/v1/worksheets/{session_id}/execute", headers, {"statement": statement})
    reached = doomed.wait_for_barrier()
    _confirm_backend(reached, identity, oracle_target)

    doomed.kill()
    cleanup = observer.wait_for_cleanup(identity.get("sessionId"), identity.get("serialNumber"))

    restarted = spawn()
    report = _reconciliation(restarted)
    record = _the_one_record(settings, statement)
    assert record.id == reached["executionId"]
    assert record.state == ExecutionState.CANCELLED.value
    assert record.error_code == "interrupted_before_dispatch"
    assert record.dispatched_at is None
    assert record.id in report["cancelledBeforeDispatch"]
    assert observer.salary() == before, "a statement recorded as never dispatched changed the row"
    _assert_not_redispatched(settings, record.id)

    _note(
        death_evidence,
        "Process death before dispatch",
        doomed,
        identity,
        cleanup,
        f"Restart recorded `{record.state}`; an independent session read salary {before}, "
        "unchanged.",
    )


def test_death_during_a_read(
    settings: Settings,
    spawn: Callable[..., ApiProcess],
    observer: Observer,
    oracle_target: oracle_config.OracleTestConfig | None,
    death_evidence: Evidence | None,
) -> None:
    """The rows were fetched and never returned. A read changes nothing, so: failed."""

    doomed = spawn(barrier="statement_returned", match=MARK)
    with doomed.client() as client:
        headers = _developer(client)
        session_id, identity = _open_worksheet(client, headers)
    _abandon(doomed, f"/api/v1/worksheets/{session_id}/execute", headers, {"statement": READ})
    reached = doomed.wait_for_barrier()
    _confirm_backend(reached, identity, oracle_target)

    doomed.kill()
    cleanup = observer.wait_for_cleanup(identity.get("sessionId"), identity.get("serialNumber"))

    restarted = spawn()
    report = _reconciliation(restarted)
    record = _the_one_record(settings, READ)
    assert record.state == ExecutionState.FAILED.value
    assert record.error_code == "interrupted"
    assert record.dispatched_at is not None
    assert not (record.verification_json or {}).get("verificationRequired")
    assert record.id in report["failedReads"]
    assert record.id not in {item["id"] for item in report["outstanding"]}
    _assert_not_redispatched(settings, record.id)

    _note(
        death_evidence,
        "Process death during a read",
        doomed,
        identity,
        cleanup,
        f"Restart recorded `{record.state}` with no verification required.",
    )


def test_death_during_a_write_with_the_answer_not_yet_recorded(
    settings: Settings,
    spawn: Callable[..., ApiProcess],
    observer: Observer,
    oracle_target: oracle_config.OracleTestConfig | None,
    death_evidence: Evidence | None,
) -> None:
    """The UPDATE was applied inside an open transaction, and then the process died.

    The harness cannot know which, so the record is ``outcome_unknown``. The database
    answers the question: an abandoned session's transaction is rolled back, and the
    operator records exactly that finding.
    """

    before = observer.salary()
    statement = _write(4242)
    doomed = spawn(barrier="statement_returned", match=MARK)
    with doomed.client() as client:
        headers = _developer(client)
        session_id, identity = _open_worksheet(client, headers)
    _abandon(doomed, f"/api/v1/worksheets/{session_id}/execute", headers, {"statement": statement})
    reached = doomed.wait_for_barrier()
    _confirm_backend(reached, identity, oracle_target)

    doomed.kill()
    cleanup = observer.wait_for_cleanup(identity.get("sessionId"), identity.get("serialNumber"))
    after_cleanup = observer.salary()

    restarted = spawn()
    report = _reconciliation(restarted)
    record = _the_one_record(settings, statement)
    assert record.state == ExecutionState.OUTCOME_UNKNOWN.value
    assert record.verification_json["verificationRequired"] is True
    assert record.id in {item["id"] for item in report["outstanding"]}
    assert after_cleanup == before, (
        f"the dead session's uncommitted UPDATE survived it: salary {after_cleanup}, was {before}"
    )
    assert observer.salary() == before, "the restart applied the interrupted write"
    _assert_not_redispatched(settings, record.id)

    with restarted.client() as client:
        verified = client.post(
            f"/api/v1/admin/executions/{record.id}/verification",
            headers=_administrator(client),
            json={
                "finding": "not_applied",
                "note": f"Independent session read salary {after_cleanup} after cleanup.",
            },
        )
    assert verified.status_code == 200, verified.text
    assert verified.json()["state"] == ExecutionState.OUTCOME_UNKNOWN.value

    _note(
        death_evidence,
        "Process death during a write (applied, uncommitted, answer not recorded)",
        doomed,
        identity,
        cleanup,
        f"Restart recorded `{record.state}` with verification required. The independent "
        f"session read salary {after_cleanup} (was {before}): Oracle rolled the dead "
        "session's transaction back. Recorded finding `not_applied`.",
    )


@pytest.mark.oracle_only
def test_death_while_a_write_waits_inside_the_database(
    settings: Settings,
    spawn: Callable[..., ApiProcess],
    observer: Observer,
    oracle_target: oracle_config.OracleTestConfig | None,
    death_evidence: Evidence | None,
) -> None:
    """The statement is genuinely inside Oracle when the process dies.

    The observer holds the row lock, so the UPDATE waits in the database and the kill
    lands mid-statement. The server process does not notice its client has gone until the
    wait ends, which is why the lock is released before the cleanup is waited for, and
    why nothing is read until it has happened.
    """

    before = observer.salary()
    statement = _write(4343)
    observer.lock_row()
    try:
        doomed = spawn(barrier="statement_sent", match=MARK)
        with doomed.client() as client:
            headers = _developer(client)
            session_id, identity = _open_worksheet(client, headers)
        _abandon(
            doomed, f"/api/v1/worksheets/{session_id}/execute", headers, {"statement": statement}
        )
        sending = doomed.wait_for_file("sending.json")
        _confirm_backend(sending, identity, oracle_target)
        try:
            waiting = observer.wait_until_blocked(
                identity.get("sessionId"), identity.get("serialNumber")
            )
        except RuntimeError as unreadable:
            if death_evidence is not None:
                death_evidence.gap("Process death mid-statement", str(unreadable))
            pytest.skip(str(unreadable))

        doomed.kill()
        survived_kill = observer.session(identity.get("sessionId"), identity.get("serialNumber"))
    finally:
        observer.release()
    cleanup = observer.wait_for_cleanup(identity.get("sessionId"), identity.get("serialNumber"))
    after_cleanup = observer.salary()

    restarted = spawn()
    _reconciliation(restarted)
    record = _the_one_record(settings, statement)
    assert record.state == ExecutionState.OUTCOME_UNKNOWN.value
    assert record.verification_json["verificationRequired"] is True
    assert after_cleanup == before
    _assert_not_redispatched(settings, record.id)

    _note(
        death_evidence,
        "Process death while a write waits inside the database",
        doomed,
        identity,
        cleanup,
        f"The session was waiting on `{waiting}` when killed; immediately after the kill "
        f"V$SESSION still showed it as {survived_kill}. After the lock was released the "
        f"independent session read salary {after_cleanup} (was {before}). Restart recorded "
        f"`{record.state}`.",
    )


def _write_then_commit(
    settings: Settings,
    spawn: Callable[..., ApiProcess],
    observer: Observer,
    oracle_target: oracle_config.OracleTestConfig | None,
    barrier: str,
    salary: int,
) -> tuple[ApiProcess, str, dict[str, Any], Cleanup, int, dict[str, Any], ApiProcess]:
    """Run an UPDATE to completion, then kill the process at a point in its COMMIT."""

    statement = _write(salary)
    doomed = spawn(barrier=barrier)
    with doomed.client() as client:
        headers = _developer(client)
        session_id, identity = _open_worksheet(client, headers)
        written = client.post(
            f"/api/v1/worksheets/{session_id}/execute",
            headers=headers,
            json={"statement": statement},
        )
        assert written.status_code == 200, written.text
        assert written.json()["outcome"]["rowsAffected"] == 1
    _abandon(doomed, f"/api/v1/worksheets/{session_id}/commit", headers, None)
    reached = doomed.wait_for_barrier()
    _confirm_backend(reached, identity, oracle_target)

    doomed.kill()
    cleanup = observer.wait_for_cleanup(identity.get("sessionId"), identity.get("serialNumber"))
    after_cleanup = observer.salary()

    restarted = spawn()
    report = _reconciliation(restarted)
    assert session_id in report["commitsUnknown"]
    assert session_id in {item["sessionId"] for item in report["outstandingCommits"]}
    with _store(settings)() as db:
        row = db.get(WorksheetSessionRecord, session_id)
        assert row is not None
        assert row.commit_requested_at is not None
        assert row.close_reason == "process restart with a commit in flight"
        # No false success: nothing recorded the commit as having happened.
        committed = db.scalars(
            select(AuditEvent).where(
                AuditEvent.operation_id == "worksheet.commit",
                AuditEvent.outcome == "succeeded",
            )
        ).all()
    assert [event for event in committed if event.detail.get("sessionId") == session_id] == []
    return doomed, session_id, identity, cleanup, after_cleanup, report, restarted


def _record_commit_finding(restarted: ApiProcess, session_id: str, finding: str, note: str) -> None:
    with restarted.client() as client:
        administrator = _administrator(client)
        recorded = client.post(
            f"/api/v1/admin/worksheets/{session_id}/commit-verification",
            headers=administrator,
            json={"finding": finding, "note": note},
        )
        assert recorded.status_code == 200, recorded.text
        trail = client.get("/api/v1/audit", headers=administrator).json()
    # Each test has a store of its own, so this is the only finding in it.
    verified = [event for event in trail if event["operationId"] == VERIFIED_OPERATION_ID]
    assert [event["outcome"] for event in verified] == [finding]


def test_death_with_a_commit_intended_but_not_sent(
    settings: Settings,
    spawn: Callable[..., ApiProcess],
    observer: Observer,
    oracle_target: oracle_config.OracleTestConfig | None,
    death_evidence: Evidence | None,
) -> None:
    """The commit marker is written and COMMIT never reached the database.

    The record cannot tell this from the next scenario, so both are unknown. Here the
    database shows the transaction was rolled back with the session.
    """

    before = observer.salary()
    doomed, session_id, identity, cleanup, after_cleanup, _, restarted = _write_then_commit(
        settings, spawn, observer, oracle_target, "commit_before_send", 4444
    )
    assert after_cleanup == before, (
        f"a COMMIT that was never sent became durable: salary {after_cleanup}, was {before}"
    )
    _record_commit_finding(
        restarted, session_id, "not_applied", f"Independent session read salary {after_cleanup}."
    )

    _note(
        death_evidence,
        "Process death during COMMIT (intent recorded, COMMIT not sent)",
        doomed,
        identity,
        cleanup,
        f"Restart listed the session under `commitsUnknown`. The independent session read "
        f"salary {after_cleanup} (was {before}): not durable. Recorded finding `not_applied`.",
    )


def test_death_with_a_commit_applied_but_not_acknowledged(
    settings: Settings,
    spawn: Callable[..., ApiProcess],
    observer: Observer,
    oracle_target: oracle_config.OracleTestConfig | None,
    death_evidence: Evidence | None,
) -> None:
    """COMMIT returned from the database and the process died before saying so.

    The transaction is durable and the harness never saw it happen. Reporting failure
    would invite a retry that applies it twice; reporting success would be a guess. The
    record stays unknown, and the database shows why that is the only honest answer.
    """

    before = observer.salary()
    doomed, session_id, identity, cleanup, after_cleanup, _, restarted = _write_then_commit(
        settings, spawn, observer, oracle_target, "commit_returned", 4545
    )
    assert after_cleanup == 4545, (
        f"a COMMIT that returned from the database is not durable: salary {after_cleanup}"
    )
    _record_commit_finding(
        restarted, session_id, "applied", f"Independent session read salary {after_cleanup}."
    )

    _note(
        death_evidence,
        "Process death during COMMIT (COMMIT returned, answer not recorded)",
        doomed,
        identity,
        cleanup,
        f"Restart listed the session under `commitsUnknown`, with no successful commit "
        f"audited. The independent session read salary {after_cleanup} (was {before}): "
        "durable. Recorded finding `applied`.",
    )
