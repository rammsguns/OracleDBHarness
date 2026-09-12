"""Restart reconciliation: what the durable record means after a process dies.

The execution service persists intent before dispatch, so a store that outlives a
process holds rows describing work nobody watched finish. This module turns those rows
into honest terminal states, once, at startup, before the new process dispatches
anything of its own.

Three rules shape every decision here:

* **Never redispatch.** Nothing in this module runs a statement. A write whose fate is
  unknown is recorded as unknown and left for a person to verify, because retrying a
  write that may already have been applied applies it twice.
* **Never invent an outcome.** A record is resolved to ``cancelled`` only when the
  durable state proves no connection was ever asked to run it. Anything else that
  could have written is ``outcome_unknown``, which is a state the API, the console and
  ``docs/operations.md`` already treat as requiring verification.
* **Never reconcile a live runtime's work.** A starting process claims the store and
  supersedes the previous claim; it resolves only the superseded runtime's records. A
  superseded process stops dispatching rather than racing the reconciliation of its
  own rows.

The dispatch marker is what makes the first rule usable. ``Execution.dispatched_at`` is
committed before the statement reaches the engine, so a non-terminal record without it
was never handed to a connection. A store written by a build older than schema version
4 has no marker at all, and its records are resolved conservatively instead.
"""

from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from harness_api import __version__
from harness_api.models import (
    AuditEvent,
    Execution,
    ExecutionRuntime,
    StoreOwner,
    WorksheetSessionRecord,
    new_id,
    utcnow,
)
from harness_worker.types import TERMINAL_STATES, ExecutionState, RiskClass

log = logging.getLogger("harness.recovery")

# The operation ids reconciliation writes to the audit trail. Operators and the admin
# endpoint find restart activity by these, so they are part of the interface.
RECONCILE_EXECUTION = "system.restart.execution"
RECONCILE_SESSION = "system.restart.worksheet"
RECONCILE_SUMMARY = "system.restart.reconciled"

# The store has exactly one owner row, and claiming locks it. See models.StoreOwner.
OWNER_ROW_ID = 1

# The reason reconciliation writes on a session whose commit was in flight. Also how the
# outstanding-commit query finds those sessions again after the next restart.
COMMIT_IN_FLIGHT_REASON = "process restart with a commit in flight"

# Where an operator's finding is recorded, for an execution or for a commit.
VERIFIED_OPERATION_ID = "system.restart.verified"

# Three heartbeat intervals. Used only to decide whether a double-start warning is
# worth logging; the fencing itself never depends on a liveness guess.
LIVE_HEARTBEAT_SECONDS = 45.0

NON_TERMINAL_STATES = tuple(state.value for state in ExecutionState if state not in TERMINAL_STATES)

_NEVER_DISPATCHED = (
    "The process that accepted this statement stopped before handing it to a "
    "connection, so the database never saw it. Nothing was applied and nothing was "
    "retried; submit it again if you still want it to run."
)
_READ_INTERRUPTED = (
    "The process running this read stopped before it returned. A read changes nothing, "
    "so there is nothing to verify or undo. Run it again if you still need the rows."
)
_WRITE_INTERRUPTED = (
    "The process running this statement stopped while it was in flight, so whether "
    "Oracle applied it is unknown. Verify the affected rows in the database before "
    "running anything again. It was not retried automatically."
)
_UNKNOWN_DISPATCH = (
    "This record was written by a build that did not mark the point of dispatch, so "
    "whether the statement reached Oracle cannot be established from the store. Treat "
    "it as unverified and check the affected rows in the database."
)
_COMMIT_INTERRUPTED = (
    "The process stopped with a COMMIT in flight on this session, so whether the "
    "transaction was made durable is unknown. Verify the affected rows in the database."
)


@dataclass(frozen=True)
class Resolution:
    """The terminal state one interrupted execution record is resolved to."""

    state: ExecutionState
    error_code: str
    message: str
    verification_required: bool


def resolve_execution(
    *, risk_class: str, dispatched_at: datetime | None, owner_id: str
) -> Resolution:
    """Decide the terminal state for one interrupted execution record.

    Pure, and separate from the database work, because this is the judgement the whole
    module exists to make and it is the part worth reading on its own.

    The non-terminal state the record was left in is deliberately not an input. Neither
    ``queued`` nor ``running`` nor ``cancellation_requested`` says anything the dispatch
    marker does not say better: a cancellation *request* is not a stopped statement, so a
    write left in that state is as uncertain as any other.

    ``dispatched_at`` is NULL for two different situations, which is why ``owner_id``
    is needed to tell them apart. A record stamped with a runtime id comes from a build
    that commits the dispatch marker, so a NULL there means the statement genuinely
    never left the API. An unstamped record predates the marker, so NULL says nothing,
    and a write is treated as uncertain.
    """

    if dispatched_at is None and owner_id:
        return Resolution(
            state=ExecutionState.CANCELLED,
            error_code="interrupted_before_dispatch",
            message=_NEVER_DISPATCHED,
            verification_required=False,
        )
    if risk_class == RiskClass.READ.value:
        return Resolution(
            state=ExecutionState.FAILED,
            error_code="interrupted",
            message=_READ_INTERRUPTED,
            verification_required=False,
        )
    return Resolution(
        state=ExecutionState.OUTCOME_UNKNOWN,
        error_code="interrupted",
        message=_WRITE_INTERRUPTED if dispatched_at is not None else _UNKNOWN_DISPATCH,
        verification_required=True,
    )


@dataclass
class ReconciliationReport:
    """What a startup reconciliation did, for the log and the admin endpoint."""

    runtime_id: str
    started_at: datetime
    superseded_runtimes: list[str] = field(default_factory=list)
    live_runtimes: list[str] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    outcome_unknown: list[str] = field(default_factory=list)
    sessions_closed: list[str] = field(default_factory=list)
    commits_unknown: list[str] = field(default_factory=list)

    @property
    def executions_resolved(self) -> int:
        return len(self.cancelled) + len(self.failed) + len(self.outcome_unknown)

    @property
    def needs_verification(self) -> int:
        return len(self.outcome_unknown) + len(self.commits_unknown)

    def as_dict(self) -> dict:
        return {
            "runtimeId": self.runtime_id,
            "startedAt": self.started_at.isoformat(),
            "supersededRuntimes": self.superseded_runtimes,
            "executionsResolved": self.executions_resolved,
            "cancelledBeforeDispatch": self.cancelled,
            "failedReads": self.failed,
            "outcomeUnknown": self.outcome_unknown,
            "sessionsClosed": self.sessions_closed,
            "commitsUnknown": self.commits_unknown,
            "needsVerification": self.needs_verification,
        }

    def summary_line(self) -> str:
        if not self.executions_resolved and not self.sessions_closed:
            return "nothing interrupted was found in the metadata store"
        return (
            f"{self.executions_resolved} execution record(s) resolved "
            f"({len(self.cancelled)} never dispatched, {len(self.failed)} interrupted "
            f"read(s), {len(self.outcome_unknown)} with an unknown outcome), "
            f"{len(self.sessions_closed)} worksheet session(s) closed, "
            f"{self.needs_verification} needing verification in the database"
        )


def new_runtime_id() -> str:
    return new_id("rt")


def claim_store(session_factory: sessionmaker[Session], runtime_id: str) -> ReconciliationReport:
    """Take ownership of the store and supersede any earlier owner.

    Returns the report the caller passes to :func:`reconcile_interrupted_work`. Split from
    the reconciliation itself so the claim is committed first: if reconciliation then
    fails, the store still records who owns it and the superseded process has already
    been told to stop.

    The claim is serialized on the single ``store_owner`` row, and that ordering is the
    whole point. Reading the live runtimes and then superseding them is not enough by
    itself: under PostgreSQL's default READ COMMITTED isolation, two simultaneous startups
    cannot see each other's uncommitted runtime row, so each would find no previous owner,
    each would leave its own claim unsuperseded, and both would go on serving one store.
    Updating the owner row before reading anything takes a row lock, so the second claimer
    waits for the first to commit and then sees it -- and the loser of a genuine race is
    superseded rather than left running.
    """

    now = utcnow()
    report = ReconciliationReport(runtime_id=runtime_id, started_at=now)
    with session_factory() as db:
        _ensure_owner_row(db)
        # First statement in the transaction, and a blind UPDATE rather than a read: it
        # takes the row lock that serializes concurrent claims. Everything below runs
        # holding it, so the reads that follow see whatever the previous claimer committed.
        db.execute(
            update(StoreOwner)
            .where(StoreOwner.id == OWNER_ROW_ID)
            .values(runtime_id=runtime_id, claimed_at=now)
        )
        db.add(
            ExecutionRuntime(
                id=runtime_id,
                host=socket.gethostname()[:255],
                pid=os.getpid(),
                version=__version__,
                started_at=now,
                heartbeat_at=now,
            )
        )
        db.flush()
        previous = db.scalars(
            select(ExecutionRuntime).where(
                ExecutionRuntime.id != runtime_id,
                ExecutionRuntime.stopped_at.is_(None),
                ExecutionRuntime.superseded_by.is_(None),
            )
        ).all()
        for row in previous:
            row.superseded_by = runtime_id
            report.superseded_runtimes.append(row.id)
            # A heartbeat inside the interval this process is about to use means the other
            # one was probably still running: the deployment is misconfigured with two
            # execution services on one store. It is fenced either way -- it can no longer
            # dispatch a write or commit -- but say so loudly, because its in-flight work
            # is about to be recorded as uncertain.
            if (now - _as_utc(row.heartbeat_at)).total_seconds() < LIVE_HEARTBEAT_SECONDS:
                report.live_runtimes.append(row.id)
        db.commit()

    for stale in report.live_runtimes:
        log.error(
            "recovery: runtime %s was still heartbeating when this process claimed the "
            "metadata store. Two execution services on one store is not a supported "
            "deployment; %s is now fenced and will refuse to dispatch or commit.",
            stale,
            stale,
        )
    return report


def _ensure_owner_row(db: Session) -> None:
    """Create the singleton owner row if a store somehow lacks it.

    ``initialize_schema`` writes it, both when creating a store and when upgrading one, so
    this is for a store that was restored or edited without it. Committed on its own and
    tolerant of a concurrent creator, because the row's whole job is to exist before two
    processes contend for it.
    """

    if db.get(StoreOwner, OWNER_ROW_ID) is not None:
        return
    try:
        db.add(StoreOwner(id=OWNER_ROW_ID, runtime_id=""))
        db.commit()
    except IntegrityError:
        db.rollback()  # another starting process created it first, which is the point


def owns_store(session_factory: sessionmaker[Session], runtime_id: str) -> bool:
    """Whether this runtime is still the store's owner, according to the store.

    Read synchronously before an operation that could change the database durably. The
    latched flag a heartbeat sets is not enough on its own: between another process
    claiming the store and this one's next heartbeat there is a window in which this
    process still believes it owns everything, and a commit issued in that window would
    make a transaction durable after the new process had already recorded the session as
    closed and its outcome as unknown.
    """

    with session_factory() as db:
        owner = db.get(StoreOwner, OWNER_ROW_ID)
        if owner is not None and owner.runtime_id:
            return owner.runtime_id == runtime_id
        # An unclaimed or missing owner row: fall back to this runtime's own record, so a
        # service running against a store written before this row existed still works, and
        # so releasing the claim on a clean stop does not read as ownership regained.
        row = db.get(ExecutionRuntime, runtime_id)
        return row is not None and row.superseded_by is None and row.stopped_at is None


def reconcile_interrupted_work(
    session_factory: sessionmaker[Session], report: ReconciliationReport
) -> ReconciliationReport:
    """Resolve the work runtimes other than this one left behind.

    Runs before the new process dispatches anything, and touches only records whose
    ``owner_id`` is not this runtime's. It is idempotent: every record it resolves
    becomes terminal, so a second call finds nothing left to do.
    """

    with session_factory() as db:
        _reconcile_executions(db, report)
        _reconcile_sessions(db, report)
        _record_summary(db, report)
        runtime = db.get(ExecutionRuntime, report.runtime_id)
        if runtime is not None:
            runtime.reconciled_at = utcnow()
        db.commit()

    if report.executions_resolved or report.sessions_closed:
        log.warning("recovery: %s", report.summary_line())
    else:
        log.info("recovery: %s", report.summary_line())
    if report.needs_verification:
        log.warning(
            "recovery: %d interrupted write(s) have an unknown outcome. They were not "
            "retried. GET /api/v1/admin/reconciliation lists them; verify each in the "
            "database and record the finding before anyone reruns the statement.",
            report.needs_verification,
        )
    return report


def _reconcile_executions(db: Session, report: ReconciliationReport) -> None:
    rows = db.scalars(
        select(Execution)
        .where(
            Execution.state.in_(NON_TERMINAL_STATES),
            Execution.owner_id != report.runtime_id,
        )
        .order_by(Execution.started_at)
    ).all()
    for row in rows:
        resolution = resolve_execution(
            risk_class=row.risk_class,
            dispatched_at=row.dispatched_at,
            owner_id=row.owner_id,
        )
        previous_state = row.state
        row.state = resolution.state.value
        row.error_code = resolution.error_code
        row.error_message = resolution.message
        row.finished_at = row.finished_at or utcnow()
        row.verification_json = {
            **(row.verification_json or {}),
            "reconciledAtRestart": True,
            "reconciledBy": report.runtime_id,
            "stateBeforeReconciliation": previous_state,
            "dispatched": row.dispatched_at is not None,
            **(
                {"verificationRequired": True, "state": resolution.state.value}
                if resolution.verification_required
                else {}
            ),
        }
        db.add(
            AuditEvent(
                actor_id="",
                actor_subject="system",
                profile_id=row.profile_id,
                operation_id=RECONCILE_EXECUTION,
                execution_id=row.id,
                risk_class=row.risk_class,
                outcome=resolution.state.value,
                statement_fingerprint=row.statement_fingerprint,
                detail={
                    "reconciledBy": report.runtime_id,
                    "previousOwner": row.owner_id,
                    "stateBeforeReconciliation": previous_state,
                    "dispatched": row.dispatched_at is not None,
                    "redispatched": False,
                    "reason": resolution.message,
                    **({"verificationRequired": True} if resolution.verification_required else {}),
                },
            )
        )
        if resolution.state == ExecutionState.CANCELLED:
            report.cancelled.append(row.id)
        elif resolution.state == ExecutionState.FAILED:
            report.failed.append(row.id)
        else:
            report.outcome_unknown.append(row.id)


def _reconcile_sessions(db: Session, report: ReconciliationReport) -> None:
    """Close worksheet records no live process holds a connection for.

    A worksheet session is a leased Oracle connection held in one process's memory.
    Nothing recreates it, so a record left open by a gone runtime is unreachable: the
    database closed the connection when the process went away, which rolled back the
    uncommitted work. The one case that is not a clean rollback is a COMMIT that was in
    flight, which the record marks.
    """

    rows = db.scalars(
        select(WorksheetSessionRecord).where(
            WorksheetSessionRecord.closed_at.is_(None),
            WorksheetSessionRecord.owner_id != report.runtime_id,
        )
    ).all()
    for row in rows:
        uncertain_commit = row.commit_requested_at is not None
        row.closed_at = utcnow()
        row.close_reason = COMMIT_IN_FLIGHT_REASON if uncertain_commit else "process restart"
        db.add(
            AuditEvent(
                actor_id=row.user_id,
                actor_subject="system",
                profile_id=row.profile_id,
                operation_id=RECONCILE_SESSION,
                risk_class=(
                    RiskClass.PERSISTENT_WRITE.value if uncertain_commit else RiskClass.READ.value
                ),
                outcome="outcome_unknown" if uncertain_commit else "closed",
                detail={
                    "sessionId": row.id,
                    "reconciledBy": report.runtime_id,
                    "previousOwner": row.owner_id,
                    "closeReason": row.close_reason,
                    **(
                        {
                            "verificationRequired": True,
                            "commitRequestedAt": row.commit_requested_at.isoformat(),
                            "reason": _COMMIT_INTERRUPTED,
                        }
                        if uncertain_commit and row.commit_requested_at is not None
                        else {}
                    ),
                },
            )
        )
        report.sessions_closed.append(row.id)
        if uncertain_commit:
            report.commits_unknown.append(row.id)


def _record_summary(db: Session, report: ReconciliationReport) -> None:
    """Write the reconciliation itself to the append-only trail.

    The report is held in memory for the admin endpoint, but a restart has to be
    auditable after the *next* restart too, so the durable copy goes here rather than
    into a table this code would then have to prune.
    """

    db.add(
        AuditEvent(
            actor_id="",
            actor_subject="system",
            operation_id=RECONCILE_SUMMARY,
            risk_class=RiskClass.ADMINISTRATIVE.value,
            outcome="reconciled" if report.executions_resolved else "nothing_interrupted",
            detail={**report.as_dict(), "summary": report.summary_line()},
        )
    )


def heartbeat(session_factory: sessionmaker[Session], runtime_id: str) -> bool:
    """Record that this runtime is alive. False means it has been superseded.

    A superseded runtime has had its interrupted work reconciled by another process,
    so dispatching anything further would run a statement already written down as never
    dispatched. The caller stops dispatching on False.
    """

    with session_factory() as db:
        row = db.get(ExecutionRuntime, runtime_id)
        if row is None:
            # The row was removed under us -- a restored backup, or a hand-edited
            # store. Treat it as superseded: this process cannot prove it owns the
            # store any more, and the safe reading of that is to stop.
            return False
        if row.superseded_by is not None:
            return False
        row.heartbeat_at = utcnow()
        db.commit()
        return True


def record_clean_stop(session_factory: sessionmaker[Session], runtime_id: str) -> None:
    """Mark this runtime as stopped on purpose, and release its claim on the store.

    Stopping separates an orderly shutdown from a process that died, which is the first
    thing an operator wants to know, and it changes nothing about how the next process
    reconciles: a clean stop leaves no non-terminal records to reconcile anyway.

    Releasing the claim is what makes the store read as unowned between an orderly stop and
    the next start, rather than still naming a process that is gone. The release is
    conditional on this runtime still being the named owner: a newer process may have
    claimed the store already, and a stopping one must not clear the new owner's name.
    """

    with session_factory() as db:
        row = db.get(ExecutionRuntime, runtime_id)
        if row is not None and row.stopped_at is None:
            row.stopped_at = utcnow()
        db.execute(
            update(StoreOwner)
            .where(StoreOwner.id == OWNER_ROW_ID, StoreOwner.runtime_id == runtime_id)
            .values(runtime_id="", claimed_at=None)
        )
        db.commit()


def outstanding_verifications(db: Session, limit: int = 200) -> list[Execution]:
    """Execution records reconciliation left for a person to verify in the database."""

    rows = db.scalars(
        select(Execution)
        .where(Execution.state == ExecutionState.OUTCOME_UNKNOWN.value)
        .order_by(Execution.started_at.desc())
        .limit(limit)
    ).all()
    return [row for row in rows if not _is_verified(row)]


def outstanding_commit_verifications(db: Session, limit: int = 200) -> list[WorksheetSessionRecord]:
    """Sessions whose commit was in flight when their process died, still unverified.

    These have no execution record of their own -- a commit never had one -- so they are
    counted and listed separately from ``outstanding_verifications``. Both are things a
    person still has to go and look at in the database; neither is something the harness
    can settle.

    Verification is recorded as an audit event rather than on the session record, so the
    query excludes any session that already has one. The trail is append-only and outlives
    the report, which is what this needs.
    """

    rows = db.scalars(
        select(WorksheetSessionRecord)
        .where(
            WorksheetSessionRecord.commit_requested_at.is_not(None),
            WorksheetSessionRecord.close_reason == COMMIT_IN_FLIGHT_REASON,
        )
        .order_by(WorksheetSessionRecord.opened_at.desc())
        .limit(limit)
    ).all()
    if not rows:
        return []
    verified = set(
        db.scalars(
            select(AuditEvent.detail["sessionId"].as_string()).where(
                AuditEvent.operation_id == VERIFIED_OPERATION_ID
            )
        ).all()
    )
    return [row for row in rows if row.id not in verified]


def _is_verified(row: Execution) -> bool:
    verification = row.verification_json or {}
    return bool(verification.get("operatorVerification"))


def _as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; PostgreSQL does not."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
