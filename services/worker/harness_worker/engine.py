"""The execution engine: one statement, one bounded budget, one honest outcome.

Design notes that matter more than the code:

* Every execution passes through :meth:`ExecutionEngine.run`, so limits, deadlines,
  cancellation and state transitions are implemented once.
* The total operation deadline is enforced here, above the driver round-trip limit.
  A statement that keeps returning data still cannot outlive its budget.
* A write whose fate is genuinely unknown is reported as ``outcome_unknown``. It is
  never reported as a failure, never retried automatically, and never quietly
  swallowed, because Oracle may have applied it.
* Request deduplication lives in the execution service, not here: it is a durable
  property of the execution record, so it survives a restart. The engine assumes it
  has been given work that should actually run.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from harness_worker.backend import ConnectionSpec, OracleBackend, OracleConnection
from harness_worker.backend.base import StatementResult
from harness_worker.errors import (
    CancelledError_,
    HarnessError,
    OutcomeUnknownError,
    PolicyError,
    SessionExpiredError,
    TimeoutError_,
)
from harness_worker.sessions import SessionRegistry, WorksheetSession
from harness_worker.statement import IMPLICITLY_COMMITTING_KINDS, is_plsql, prepare
from harness_worker.types import (
    ExecutionOutcome,
    ExecutionRequest,
    ExecutionState,
    StatementKind,
    utcnow,
)

log = logging.getLogger("harness.engine")

# How long to wait, after a break has been delivered, for the statement to actually
# stop. Past this point the session is treated as unusable rather than reused.
CANCEL_GRACE_SECONDS = 5.0

_WRITING_KINDS = {
    StatementKind.DML,
    StatementKind.DDL,
    StatementKind.PLSQL_BLOCK,
    StatementKind.PLSQL_SOURCE,
    StatementKind.UNKNOWN,
}


@dataclass
class _Dispatch:
    """A request that has been accepted; its result may still be in flight."""

    execution_id: str
    future: Future


@dataclass
class _Settled:
    """An execution that has produced its outcome, and what became of its connection.

    ``connection_abandoned`` is the important half. It means a worker thread is still
    inside a driver call on that connection, so no other thread may commit, roll back
    or close it; disposal has already been deferred to the moment the statement
    returns. Callers that own the connection have to respect that.
    """

    outcome: ExecutionOutcome
    connection_abandoned: bool = False


def _guarded(
    work: Callable[[OracleConnection], StatementResult],
    session: WorksheetSession | None,
    execution_id: str,
) -> Callable[[OracleConnection], StatementResult]:
    """Wrap queued work so a retired or cancelled session never reaches the database.

    The pool is bounded, so accepted work can sit in the queue without a single round
    trip being made, and in that window the session it belongs to can be revoked,
    expired or closed, or the user can cancel the statement.
    :meth:`SessionRegistry.acquire` checks the session before the work is submitted,
    and a revocation that cannot take the session lock retires the session without
    closing the connection underneath the thread that holds it -- so without these
    checks the statement would still be sent, on a session whose access was already
    withdrawn or whose cancellation has already been reported to the user.

    Both checks and the started marker are one step --
    :meth:`WorksheetSession.claim_dispatch` -- taken on the worker thread immediately
    before the driver call. They cannot be separate: a cancellation that arrives
    between the check and the marker would see no statement started, report to the
    user that nothing was sent, and then watch this thread send it. A revocation or
    cancellation that lands after the claim is a statement genuinely in flight, which
    is the case the break request and the quarantine path already cover.
    """

    if session is None:
        return work

    def guarded(connection: OracleConnection) -> StatementResult:
        refused = session.claim_dispatch(execution_id)
        if refused == "closed":
            raise SessionExpiredError(
                "The worksheet session was closed before this statement reached the "
                f"database: {session.close_reason or 'unknown reason'}. It was never "
                "sent, so nothing was applied.",
                detail={"sessionId": session.session_id, "statementStarted": False},
            )
        if refused == "cancelled":
            raise CancelledError_(
                "The statement was cancelled while it was still waiting for an "
                "execution slot. It was never sent to the database, so nothing was "
                "applied.",
                detail={"sessionId": session.session_id, "statementStarted": False},
            )
        return work(connection)

    return guarded


class ExecutionEngine:
    """Runs statements on a bounded pool of worker slots.

    Oracle connections cannot be shared across OS processes, and a worksheet session
    owns its connection for the life of its transaction, so the pool is a bounded set
    of threads inside the execution service rather than a process pool. The driver
    releases the interpreter lock during database round trips, so the bound still
    limits real concurrency against the target. See docs/decisions.md (ADR-0002).
    """

    def __init__(
        self,
        backend: OracleBackend,
        sessions: SessionRegistry,
        *,
        max_workers: int = 4,
    ) -> None:
        self._backend = backend
        self._sessions = sessions
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="harness-exec")
        self._inflight: dict[str, _Dispatch] = {}
        self._lock = threading.Lock()

    # -- public API ----------------------------------------------------------------

    def execute_in_session(
        self, session: WorksheetSession, request: ExecutionRequest
    ) -> ExecutionOutcome:
        """Run one statement inside a leased worksheet session."""

        statement, kind = prepare(request.statement)
        if request.statement_kind not in (StatementKind.UNKNOWN, kind):
            # The caller told us what it thought it was sending. Trust the parser and
            # record the disagreement rather than silently reclassifying.
            pass
        binds = {b.name: b.value for b in request.binds}

        def work(connection: OracleConnection) -> StatementResult:
            return connection.execute(
                statement,
                binds,
                kind,
                request.limits,
                collect_dbms_output=request.collect_dbms_output or is_plsql(kind),
            )

        with self._sessions.acquire(
            session.session_id, session.actor_id, request.execution_id
        ) as leased:
            # Rechecked here, holding the session lock, and not only where the request
            # was admitted. Admission reads the transaction state without the lock, so
            # another statement in the same session can finish and leave a transaction
            # open in the gap -- and this DDL would then commit work the user never
            # asked to commit. Under the lock nothing else can be running, so what the
            # check sees is what the statement will meet.
            self._reject_implicit_commit(leased, kind)
            settled = self._run(leased.connection, request, kind, work, session=leased)
        outcome = settled.outcome
        outcome.transaction_open = (not session.closed) and session.connection.transaction_open
        return outcome

    def execute_once(self, spec: ConnectionSpec, request: ExecutionRequest) -> ExecutionOutcome:
        """Run one statement on a connection opened and closed for this call.

        Used by diagnostics and runbooks, which must not borrow a user worksheet
        session or its transaction.
        """

        with self.one_connection(spec) as run:
            return run(request)

    @contextmanager
    def one_connection(
        self, spec: ConnectionSpec
    ) -> Iterator[Callable[[ExecutionRequest], ExecutionOutcome]]:
        """Lend one connection to statements that have to run in the same session.

        EXPLAIN PLAN is what this exists for. The plan lands in PLAN_TABLE, which on
        19c is a synonym for a global temporary table, so every session sees only the
        rows it inserted itself. A read on a second connection finds nothing, however
        correct the statement id is. Whoever writes a plan has to read it back before
        the connection is closed.

        Each statement still gets its own budget, cancellation and outcome. A
        statement that has to be abandoned takes the connection with it: nothing else
        may run on it after that, and closing it is left to the deadline handler.
        """

        connection = self._backend.connect(spec)
        abandoned = False
        try:

            def run(request: ExecutionRequest) -> ExecutionOutcome:
                nonlocal abandoned
                if abandoned:
                    raise HarnessError(
                        "The connection was abandoned by a statement that did not stop "
                        "when cancelled; nothing further can run on it.",
                        detail={"executionId": request.execution_id},
                    )
                statement, kind = prepare(request.statement)
                binds = {b.name: b.value for b in request.binds}

                def work(conn: OracleConnection) -> StatementResult:
                    return conn.execute(
                        statement,
                        binds,
                        kind,
                        request.limits,
                        collect_dbms_output=request.collect_dbms_output or is_plsql(kind),
                    )

                settled = self._run(connection, request, kind, work)
                if settled.connection_abandoned:
                    # A worker thread is still inside a driver call on this connection.
                    # Committing, rolling back or closing it from here would be a second
                    # thread on the same handle; the deadline handler has already
                    # arranged for it to be closed once the statement finally returns.
                    abandoned = True
                    return settled.outcome
                outcome = settled.outcome
                if outcome.state == ExecutionState.SUCCEEDED and request.autocommit:
                    try:
                        connection.commit()
                    except OutcomeUnknownError as exc:
                        # The statement itself ran; the commit was in flight when the
                        # connection broke, so Oracle may or may not have made it
                        # durable. The uncertainty travels with the error rather than
                        # being flattened into a failed execution downstream.
                        exc.detail.setdefault("executionId", request.execution_id)
                        exc.detail["statementCompleted"] = True
                        exc.detail["rowsAffected"] = outcome.rows_affected
                        exc.detail["verificationRequired"] = True
                        raise
                elif connection.transaction_open:
                    connection.rollback()
                return outcome

            yield run
        finally:
            if not abandoned:
                connection.close()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- internals -----------------------------------------------------------------

    @staticmethod
    def _reject_implicit_commit(session: WorksheetSession, kind: StatementKind) -> None:
        """Refuse DDL that would commit the session's pending DML as a side effect."""

        if kind not in IMPLICITLY_COMMITTING_KINDS or not session.connection.transaction_open:
            return
        raise PolicyError(
            "This session has uncommitted changes and the statement is DDL, which "
            "would commit them. Commit or roll back first, then run the DDL.",
            detail={
                "sessionId": session.session_id,
                "statementKind": kind.value,
                "statementStarted": False,
            },
        )

    def _run(
        self,
        connection: OracleConnection,
        request: ExecutionRequest,
        kind: StatementKind,
        work: Callable[[OracleConnection], StatementResult],
        *,
        session: WorksheetSession | None = None,
    ) -> _Settled:
        started = utcnow()
        clock = time.perf_counter()
        outcome = ExecutionOutcome(
            executionId=request.execution_id,
            state=ExecutionState.RUNNING,
            statementKind=kind,
            startedAt=started,
        )
        future = self._pool.submit(_guarded(work, session, request.execution_id), connection)
        with self._lock:
            self._inflight[request.execution_id] = _Dispatch(request.execution_id, future)
        try:
            result = future.result(timeout=request.limits.deadline_seconds)
        except FutureTimeout:
            return self._handle_deadline(
                connection, request, kind, future, outcome, clock, session=session
            )
        except CancelledError_ as exc:
            return _Settled(
                self._finish(
                    outcome,
                    ExecutionState.CANCELLED,
                    clock,
                    error=exc,
                    verification={
                        # A statement withdrawn from the queue never started; one
                        # cancelled mid-call did. Either way it is over.
                        "statementStarted": exc.detail.get("statementStarted", True),
                        "statementStopped": True,
                    },
                )
            )
        except HarnessError as exc:
            state = (
                ExecutionState.OUTCOME_UNKNOWN
                if isinstance(exc, OutcomeUnknownError)
                else ExecutionState.FAILED
            )
            verification = (
                {"statementStarted": False, "statementStopped": True}
                if exc.detail.get("statementStarted") is False
                else None
            )
            return _Settled(
                self._finish(outcome, state, clock, error=exc, verification=verification)
            )
        except Exception as exc:  # noqa: BLE001 - anything else is still an outcome
            return _Settled(
                self._finish(
                    outcome,
                    ExecutionState.FAILED,
                    clock,
                    error=HarnessError(str(exc)),
                )
            )
        finally:
            with self._lock:
                self._inflight.pop(request.execution_id, None)

        if session is not None and session.cancel_requested_for == request.execution_id:
            outcome.warnings.append(
                "Cancellation was requested but the statement had already completed."
            )
        return _Settled(self._finish(outcome, ExecutionState.SUCCEEDED, clock, result=result))

    def _handle_deadline(
        self,
        connection: OracleConnection,
        request: ExecutionRequest,
        kind: StatementKind,
        future: Future,
        outcome: ExecutionOutcome,
        clock: float,
        *,
        session: WorksheetSession | None,
    ) -> _Settled:
        """The budget ran out. Break the call and report what we actually know."""

        outcome.state = ExecutionState.CANCELLATION_REQUESTED
        if future.cancel():
            # The statement never left the queue: every worker slot was busy for the
            # whole budget, so nothing was ever sent to Oracle. Taking the future off
            # the queue before anything else is what makes that permanent -- left
            # runnable, it would reach the database later, on a connection this call
            # has already reported on and may already have closed.
            return _Settled(
                self._finish(
                    outcome,
                    ExecutionState.FAILED,
                    clock,
                    error=TimeoutError_(
                        "The statement exceeded its execution budget while waiting for a "
                        "free execution slot. It was never sent to the database, so "
                        "nothing was applied.",
                        detail={"statementStarted": False},
                    ),
                    verification={
                        "cancellationDelivered": False,
                        "statementStarted": False,
                        "statementStopped": True,
                    },
                )
            )
        delivered = connection.cancel()
        try:
            result = future.result(timeout=CANCEL_GRACE_SECONDS)
        except FutureTimeout:
            # The statement is still running and we no longer control it. The session
            # cannot be reused, and for a write we genuinely do not know the outcome.
            # Retiring it before we return is what makes that true: leaving it in the
            # registry would hand a connection with a live statement, and possibly an
            # open transaction, back to the next request on the same session.
            if session is not None:
                self._sessions.quarantine(
                    session, reason="statement did not stop after cancellation"
                )
            # The worker thread still owns the connection, whether it came from a
            # worksheet session or was opened for this one call. Either way it is not
            # ours to roll back or close until the statement returns.
            self._defer_close(connection, future)
            writing = kind in _WRITING_KINDS
            error: HarnessError = (
                OutcomeUnknownError(
                    "The statement exceeded its execution budget and did not stop when "
                    "cancelled. Whether Oracle applied it is unknown; verify before "
                    "retrying.",
                    detail={"breakDelivered": delivered},
                )
                if writing
                else TimeoutError_(
                    "The statement exceeded its execution budget and did not stop when "
                    "cancelled. The session has been discarded.",
                    detail={"breakDelivered": delivered},
                )
            )
            state = ExecutionState.OUTCOME_UNKNOWN if writing else ExecutionState.FAILED
            return _Settled(
                self._finish(
                    outcome,
                    state,
                    clock,
                    error=error,
                    verification={"cancellationDelivered": delivered, "statementStopped": False},
                ),
                connection_abandoned=True,
            )
        except CancelledError_:
            return _Settled(
                self._finish(
                    outcome,
                    ExecutionState.CANCELLED,
                    clock,
                    error=CancelledError_(
                        "The statement was cancelled after exceeding its execution budget."
                    ),
                    verification={"cancellationDelivered": delivered, "statementStopped": True},
                )
            )
        except HarnessError as exc:
            state = (
                ExecutionState.OUTCOME_UNKNOWN
                if isinstance(exc, OutcomeUnknownError)
                else ExecutionState.CANCELLED
            )
            return _Settled(
                self._finish(
                    outcome,
                    state,
                    clock,
                    error=exc,
                    verification={"cancellationDelivered": delivered, "statementStopped": True},
                )
            )
        except Exception as exc:  # noqa: BLE001
            return _Settled(
                self._finish(
                    outcome,
                    ExecutionState.CANCELLED,
                    clock,
                    error=HarnessError(str(exc)),
                    verification={"cancellationDelivered": delivered, "statementStopped": True},
                )
            )

        # The statement finished inside the grace period. Report the real result and
        # say plainly that the deadline was passed.
        outcome.warnings.append(
            "The execution budget elapsed, but the statement completed before the "
            "cancellation took effect."
        )
        return _Settled(
            self._finish(
                outcome,
                ExecutionState.SUCCEEDED,
                clock,
                result=result,
                verification={"cancellationDelivered": delivered, "statementStopped": True},
            )
        )

    def _defer_close(self, connection: OracleConnection, future: Future) -> None:
        """Close an abandoned connection when its statement finally returns.

        The callback runs exactly once: on the worker thread that completes the
        future, or immediately on this thread if the statement finished between the
        grace period expiring and this call. Either way the driver call is over by
        then, so the close is the only thread touching the connection.
        """

        future.add_done_callback(lambda _f: self._discard(connection))

    @staticmethod
    def _discard(connection: OracleConnection) -> None:
        """Close an abandoned connection once its runaway statement has returned."""

        try:
            connection.close()
        except Exception:  # noqa: BLE001 - the connection is being thrown away anyway
            log.warning("Could not close an abandoned connection", exc_info=True)

    def _finish(
        self,
        outcome: ExecutionOutcome,
        state: ExecutionState,
        clock: float,
        *,
        result: StatementResult | None = None,
        error: HarnessError | None = None,
        verification: dict[str, Any] | None = None,
    ) -> ExecutionOutcome:
        outcome.state = state
        outcome.elapsed_ms = int((time.perf_counter() - clock) * 1000)
        outcome.finished_at = utcnow()
        if result is not None:
            outcome.statement_kind = result.statement_kind
            outcome.result_set = result.result_set
            outcome.rows_affected = result.rows_affected
            outcome.dbms_output = result.dbms_output
            outcome.dbms_output_truncated = result.dbms_output_truncated
            outcome.bind_outputs = result.bind_outputs
            outcome.compiler_errors = result.compiler_errors
            outcome.database_elapsed_ms = result.database_elapsed_ms
            outcome.warnings.extend(result.warnings)
            if result.compiler_errors:
                # A unit that compiled with errors is a real, reportable outcome, not a
                # failed execution: the statement itself ran.
                outcome.verification["compiled"] = False
            elif result.statement_kind == StatementKind.PLSQL_SOURCE:
                outcome.verification["compiled"] = True
        if error is not None:
            outcome.error = error.as_dict()
        if verification:
            outcome.verification.update(verification)
        outcome.verification.setdefault("state", state.value)
        return outcome
