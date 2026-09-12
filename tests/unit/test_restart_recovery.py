"""What a restart is allowed to conclude about work it did not watch finish.

The execution service persists intent before dispatch. That is only worth doing if the
next process reads those records correctly, and "correctly" here means conservatively:
the one thing worse than losing a statement is telling someone a write did not happen
when it did, or running it a second time to find out.

Four moments matter, and they are the four the plan names:

* before dispatch -- nothing reached a connection, so the record can be closed cleanly;
* during a read -- nothing was changed, so a plain failure is the honest answer;
* during DML or PL/SQL -- Oracle may have applied it, so the outcome stays unknown;
* during a commit -- the transaction may be durable, so the session says so.

These run against the metadata store, which is where reconciliation lives. The Oracle
side of a restart -- that an abandoned connection really does roll back -- is
``tests/qualification/test_connection_loss.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from harness_api.db import build_session_factory, initialize_schema
from harness_api.models import (
    AuditEvent,
    Execution,
    ExecutionRuntime,
    WorksheetSessionRecord,
    new_id,
    utcnow,
)
from harness_api.recovery import (
    RECONCILE_EXECUTION,
    RECONCILE_SESSION,
    RECONCILE_SUMMARY,
    ReconciliationReport,
    claim_store,
    heartbeat,
    new_runtime_id,
    outstanding_verifications,
    reconcile_interrupted_work,
    record_clean_stop,
    resolve_execution,
)
from harness_worker.types import ExecutionState, RiskClass, StatementKind

DEAD_RUNTIME = "rt_the_process_that_died"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine: Engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'store.sqlite3').as_posix()}")
    initialize_schema(engine)
    factory = build_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


def _interrupted(
    db: Session,
    *,
    state: str,
    kind: StatementKind,
    risk: RiskClass,
    dispatched: bool,
    owner: str = DEAD_RUNTIME,
) -> str:
    """One execution record as a process that died would have left it."""

    record = Execution(
        id=new_id("exe"),
        user_id="usr_1",
        profile_id="tgt_1",
        operation_id="worksheet.execute",
        statement_kind=kind.value,
        risk_class=risk.value,
        statement_fingerprint="f" * 64,
        state=state,
        owner_id=owner,
        dispatched_at=utcnow() if dispatched else None,
    )
    db.add(record)
    db.commit()
    return record.id


def _reconcile(store: sessionmaker[Session], runtime_id: str = "") -> ReconciliationReport:
    runtime_id = runtime_id or new_runtime_id()
    return reconcile_interrupted_work(store, claim_store(store, runtime_id))


def _state(store: sessionmaker[Session], execution_id: str) -> Execution:
    with store() as db:
        row = db.get(Execution, execution_id)
        assert row is not None
        return row


def _audit_for(store: sessionmaker[Session], execution_id: str) -> list[AuditEvent]:
    with store() as db:
        return list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.execution_id == execution_id,
                    AuditEvent.operation_id == RECONCILE_EXECUTION,
                )
            ).all()
        )


# -- the decision itself --------------------------------------------------------------


def test_work_that_never_reached_a_connection_is_closed_cleanly() -> None:
    """A dispatch marker that was never written is proof, not a guess.

    ``dispatched_at`` is committed before the engine is handed the request, so its
    absence on a record that carries an owner means the process died holding a statement
    it had not sent. Cancelled is the truthful state: nothing ran.
    """

    resolution = resolve_execution(
        risk_class=RiskClass.PERSISTENT_WRITE.value,
        dispatched_at=None,
        owner_id=DEAD_RUNTIME,
    )
    assert resolution.state == ExecutionState.CANCELLED
    assert resolution.verification_required is False
    assert "never saw it" in resolution.message


def test_an_interrupted_read_is_a_plain_failure() -> None:
    resolution = resolve_execution(
        risk_class=RiskClass.READ.value,
        dispatched_at=utcnow(),
        owner_id=DEAD_RUNTIME,
    )
    assert resolution.state == ExecutionState.FAILED
    assert resolution.verification_required is False


@pytest.mark.parametrize(
    "risk",
    [RiskClass.SESSION_WRITE, RiskClass.PERSISTENT_WRITE, RiskClass.ADMINISTRATIVE],
)
def test_an_interrupted_write_keeps_its_outcome_unknown(risk: RiskClass) -> None:
    resolution = resolve_execution(
        risk_class=risk.value,
        dispatched_at=utcnow(),
        owner_id=DEAD_RUNTIME,
    )
    assert resolution.state == ExecutionState.OUTCOME_UNKNOWN
    assert resolution.verification_required is True


def test_a_record_with_no_dispatch_marker_at_all_is_treated_as_uncertain() -> None:
    """The upgrade case: a store an older build left behind.

    Schema version 3 and earlier had no dispatch marker, so a queued write from such a
    store could be either situation. Reading its NULL as "never dispatched" would be the
    one mistake that matters, so an unstamped write is uncertain instead.
    """

    resolution = resolve_execution(
        risk_class=RiskClass.PERSISTENT_WRITE.value,
        dispatched_at=None,
        owner_id="",
    )
    assert resolution.state == ExecutionState.OUTCOME_UNKNOWN
    assert resolution.verification_required is True
    assert "did not mark the point of dispatch" in resolution.message


# -- reconciliation against the store -------------------------------------------------


def test_reconciliation_resolves_every_interrupted_record(store: sessionmaker[Session]) -> None:
    with store() as db:
        never_sent = _interrupted(
            db,
            state=ExecutionState.QUEUED.value,
            kind=StatementKind.DML,
            risk=RiskClass.SESSION_WRITE,
            dispatched=False,
        )
        read = _interrupted(
            db,
            state=ExecutionState.RUNNING.value,
            kind=StatementKind.QUERY,
            risk=RiskClass.READ,
            dispatched=True,
        )
        write = _interrupted(
            db,
            state=ExecutionState.RUNNING.value,
            kind=StatementKind.DML,
            risk=RiskClass.SESSION_WRITE,
            dispatched=True,
        )
        cancelling = _interrupted(
            db,
            state=ExecutionState.CANCELLATION_REQUESTED.value,
            kind=StatementKind.PLSQL_BLOCK,
            risk=RiskClass.PERSISTENT_WRITE,
            dispatched=True,
        )

    report = _reconcile(store)

    assert report.cancelled == [never_sent]
    assert report.failed == [read]
    assert sorted(report.outcome_unknown) == sorted([write, cancelling])
    assert report.executions_resolved == 4
    assert report.needs_verification == 2

    assert _state(store, never_sent).state == ExecutionState.CANCELLED.value
    assert _state(store, read).state == ExecutionState.FAILED.value
    assert _state(store, write).state == ExecutionState.OUTCOME_UNKNOWN.value
    assert _state(store, cancelling).state == ExecutionState.OUTCOME_UNKNOWN.value


def test_an_uncertain_write_is_flagged_for_verification_not_reported_as_failed(
    store: sessionmaker[Session],
) -> None:
    """The distinction the whole module exists for.

    ``failed`` invites a retry, and retrying a write that may already have been applied
    applies it twice. So the record has to say "unknown", say that verification is
    required, and say that nothing was retried.
    """

    with store() as db:
        write = _interrupted(
            db,
            state=ExecutionState.RUNNING.value,
            kind=StatementKind.DML,
            risk=RiskClass.PERSISTENT_WRITE,
            dispatched=True,
        )

    _reconcile(store)

    row = _state(store, write)
    assert row.state == ExecutionState.OUTCOME_UNKNOWN.value
    assert row.state != ExecutionState.FAILED.value
    assert row.verification_json["verificationRequired"] is True
    assert row.verification_json["reconciledAtRestart"] is True
    assert row.verification_json["stateBeforeReconciliation"] == ExecutionState.RUNNING.value
    assert row.finished_at is not None

    (event,) = _audit_for(store, write)
    assert event.outcome == ExecutionState.OUTCOME_UNKNOWN.value
    assert event.detail["redispatched"] is False
    assert event.detail["verificationRequired"] is True


def test_reconciliation_cannot_reach_the_database_at_all() -> None:
    """Reconciliation reads the store and writes records. It has no Oracle path.

    Checked structurally, over the module's own imports, because "it did not redispatch
    this time" is a weaker claim than "it has nothing to redispatch with". Nothing here
    can reach the execution engine, the session registry or a backend connection, so no
    future edit can accidentally make a restart rerun a write without first adding an
    import this test refuses.
    """

    import ast

    import harness_api.recovery as recovery

    tree = ast.parse(Path(recovery.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    forbidden = {
        "harness_api.execution",
        "harness_worker.engine",
        "harness_worker.sessions",
        "harness_worker.backend",
        "harness_worker.statement",
    }
    assert not (imported & forbidden), f"recovery.py imports {sorted(imported & forbidden)}"


def test_reconciliation_is_idempotent(store: sessionmaker[Session]) -> None:
    """A second restart has nothing left to find, and does not re-resolve anything.

    Every record reconciliation touches becomes terminal, so the query that finds work
    to do comes back empty. A restart loop must not multiply audit events either.
    """

    with store() as db:
        write = _interrupted(
            db,
            state=ExecutionState.RUNNING.value,
            kind=StatementKind.DML,
            risk=RiskClass.SESSION_WRITE,
            dispatched=True,
        )

    first = _reconcile(store)
    finished_at = _state(store, write).finished_at
    second = _reconcile(store)

    assert first.executions_resolved == 1
    assert second.executions_resolved == 0
    assert _state(store, write).finished_at == finished_at
    assert len(_audit_for(store, write)) == 1


def test_this_runtimes_own_records_are_left_alone(store: sessionmaker[Session]) -> None:
    """Reconciliation must never resolve work the current process is still running.

    The ownership stamp is what makes that safe without a liveness guess: a record
    carrying this runtime's id is this runtime's business.
    """

    mine = new_runtime_id()
    with store() as db:
        own = _interrupted(
            db,
            state=ExecutionState.RUNNING.value,
            kind=StatementKind.DML,
            risk=RiskClass.SESSION_WRITE,
            dispatched=True,
            owner=mine,
        )
        theirs = _interrupted(
            db,
            state=ExecutionState.RUNNING.value,
            kind=StatementKind.DML,
            risk=RiskClass.SESSION_WRITE,
            dispatched=True,
        )

    report = _reconcile(store, mine)

    assert report.outcome_unknown == [theirs]
    assert _state(store, own).state == ExecutionState.RUNNING.value


def test_terminal_records_are_not_touched(store: sessionmaker[Session]) -> None:
    with store() as db:
        done = _interrupted(
            db,
            state=ExecutionState.SUCCEEDED.value,
            kind=StatementKind.DML,
            risk=RiskClass.PERSISTENT_WRITE,
            dispatched=True,
        )

    report = _reconcile(store)

    assert report.executions_resolved == 0
    row = _state(store, done)
    assert row.state == ExecutionState.SUCCEEDED.value
    assert row.verification_json == {}


def test_the_restart_is_written_to_the_audit_trail(store: sessionmaker[Session]) -> None:
    """A restart has to stay auditable after the next restart, so it goes in the trail."""

    with store() as db:
        _interrupted(
            db,
            state=ExecutionState.RUNNING.value,
            kind=StatementKind.DML,
            risk=RiskClass.SESSION_WRITE,
            dispatched=True,
        )

    report = _reconcile(store)

    with store() as db:
        summaries = db.scalars(
            select(AuditEvent).where(AuditEvent.operation_id == RECONCILE_SUMMARY)
        ).all()
    assert len(summaries) == 1
    assert summaries[0].outcome == "reconciled"
    assert summaries[0].detail["outcomeUnknown"] == report.outcome_unknown
    assert summaries[0].detail["needsVerification"] == 1


# -- worksheet sessions ---------------------------------------------------------------


def test_stale_worksheet_records_are_closed(store: sessionmaker[Session]) -> None:
    """Nothing recreates a leased connection, so an abandoned record is closed.

    The database closed the connection when the process went away, which rolled back
    whatever was uncommitted. Leaving the record open would show a user a session they
    cannot reach and an idle reaper would never collect.
    """

    with store() as db:
        stale = WorksheetSessionRecord(
            id=new_id("wks"), user_id="usr_1", profile_id="tgt_1", owner_id=DEAD_RUNTIME
        )
        db.add(stale)
        db.commit()
        stale_id = stale.id

    report = _reconcile(store)

    assert report.sessions_closed == [stale_id]
    assert report.commits_unknown == []
    with store() as db:
        row = db.get(WorksheetSessionRecord, stale_id)
        assert row is not None
        assert row.closed_at is not None
        assert row.close_reason == "process restart"


def test_a_commit_in_flight_when_the_process_died_is_recorded_as_uncertain(
    store: sessionmaker[Session],
) -> None:
    """A commit has no execution record, so the session record carries the intent.

    Without the marker this session would be indistinguishable from one that was merely
    abandoned, and an abandoned session is a clean rollback. With it, the operator is
    told the transaction may be durable.
    """

    with store() as db:
        committing = WorksheetSessionRecord(
            id=new_id("wks"),
            user_id="usr_1",
            profile_id="tgt_1",
            owner_id=DEAD_RUNTIME,
            commit_requested_at=utcnow(),
        )
        db.add(committing)
        db.commit()
        session_id = committing.id

    report = _reconcile(store)

    assert report.commits_unknown == [session_id]
    assert report.needs_verification == 1
    with store() as db:
        row = db.get(WorksheetSessionRecord, session_id)
        assert row is not None
        assert row.close_reason == "process restart with a commit in flight"
        event = db.scalars(
            select(AuditEvent).where(AuditEvent.operation_id == RECONCILE_SESSION)
        ).one()
    assert event.outcome == "outcome_unknown"
    assert event.detail["verificationRequired"] is True
    assert event.risk_class == RiskClass.PERSISTENT_WRITE.value


def test_a_session_this_runtime_owns_survives_reconciliation(
    store: sessionmaker[Session],
) -> None:
    mine = new_runtime_id()
    with store() as db:
        live = WorksheetSessionRecord(
            id=new_id("wks"), user_id="usr_1", profile_id="tgt_1", owner_id=mine
        )
        db.add(live)
        db.commit()
        live_id = live.id

    report = _reconcile(store, mine)

    assert report.sessions_closed == []
    with store() as db:
        row = db.get(WorksheetSessionRecord, live_id)
        assert row is not None and row.closed_at is None


# -- store ownership ------------------------------------------------------------------


def test_a_starting_process_supersedes_the_previous_one(store: sessionmaker[Session]) -> None:
    first = new_runtime_id()
    claim_store(store, first)
    second = new_runtime_id()

    report = claim_store(store, second)

    assert report.superseded_runtimes == [first]
    with store() as db:
        assert db.get(ExecutionRuntime, first).superseded_by == second  # type: ignore[union-attr]
        assert db.get(ExecutionRuntime, second).superseded_by is None  # type: ignore[union-attr]


def test_a_superseded_runtime_learns_it_at_its_next_heartbeat(
    store: sessionmaker[Session],
) -> None:
    """The fence. A process whose work has been reconciled must stop dispatching.

    Otherwise it would run a statement another process has already written down as
    never dispatched, and no record anywhere would say the write happened.
    """

    first = new_runtime_id()
    claim_store(store, first)
    assert heartbeat(store, first) is True

    claim_store(store, new_runtime_id())

    assert heartbeat(store, first) is False


def test_a_runtime_whose_row_has_vanished_stops_rather_than_assuming_ownership(
    store: sessionmaker[Session],
) -> None:
    """A restored backup or a hand-edited store. Unprovable ownership reads as lost."""

    runtime = new_runtime_id()
    claim_store(store, runtime)
    with store() as db:
        db.delete(db.get(ExecutionRuntime, runtime))
        db.commit()

    assert heartbeat(store, runtime) is False


def test_a_double_start_is_reported_as_a_live_runtime(store: sessionmaker[Session]) -> None:
    """Two execution services on one store is a misconfiguration worth naming.

    The second one fences the first either way; the point of noticing is that the first
    one's in-flight work is about to be recorded as uncertain, and the operator needs to
    know the cause was a second process rather than a crash.
    """

    first = new_runtime_id()
    claim_store(store, first)

    report = claim_store(store, new_runtime_id())

    assert report.live_runtimes == [first]


def test_a_runtime_that_stopped_cleanly_is_not_reported_as_live(
    store: sessionmaker[Session],
) -> None:
    first = new_runtime_id()
    claim_store(store, first)
    record_clean_stop(store, first)

    report = claim_store(store, new_runtime_id())

    assert report.live_runtimes == []
    assert report.superseded_runtimes == []
    with store() as db:
        assert db.get(ExecutionRuntime, first).stopped_at is not None  # type: ignore[union-attr]


def test_an_old_heartbeat_is_a_crash_not_a_double_start(store: sessionmaker[Session]) -> None:
    first = new_runtime_id()
    claim_store(store, first)
    with store() as db:
        row = db.get(ExecutionRuntime, first)
        assert row is not None
        row.heartbeat_at = utcnow() - timedelta(minutes=5)
        db.commit()

    report = claim_store(store, new_runtime_id())

    assert report.superseded_runtimes == [first]
    assert report.live_runtimes == []


# -- the operator's queue -------------------------------------------------------------


def test_outstanding_verifications_lists_unverified_writes_only(
    store: sessionmaker[Session],
) -> None:
    with store() as db:
        unverified = _interrupted(
            db,
            state=ExecutionState.RUNNING.value,
            kind=StatementKind.DML,
            risk=RiskClass.PERSISTENT_WRITE,
            dispatched=True,
        )
        checked = _interrupted(
            db,
            state=ExecutionState.RUNNING.value,
            kind=StatementKind.DML,
            risk=RiskClass.PERSISTENT_WRITE,
            dispatched=True,
        )
        read = _interrupted(
            db,
            state=ExecutionState.RUNNING.value,
            kind=StatementKind.QUERY,
            risk=RiskClass.READ,
            dispatched=True,
        )

    _reconcile(store)
    with store() as db:
        row = db.get(Execution, checked)
        assert row is not None
        row.verification_json = {
            **row.verification_json,
            "operatorVerification": {"finding": "not_applied"},
        }
        db.commit()

        outstanding = [item.id for item in outstanding_verifications(db)]

    assert outstanding == [unverified]
    assert checked not in outstanding
    assert read not in outstanding
