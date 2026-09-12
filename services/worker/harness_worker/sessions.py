"""Worksheet session ownership.

A worksheet session owns one Oracle connection outright, from the moment it opens
until commit, rollback, idle expiry or an explicit close. Connections are never
pooled and never handed to a second actor, which is the simplest way to satisfy the
rule in MVP_PLAN.md that a connection with an open transaction or residual session
state must not reach another user.

Operations inside one session are serialized. A second concurrent request against a
busy session is refused rather than queued, so a user never waits behind their own
runaway statement without being told why.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from harness_worker.backend import ConnectionSpec, OracleBackend, OracleConnection
from harness_worker.errors import (
    AuthorizationError,
    OutcomeUnknownError,
    SessionBusyError,
    SessionExpiredError,
)
from harness_worker.types import TargetIdentity, utcnow

log = logging.getLogger("harness.sessions")


@dataclass
class WorksheetSession:
    """One leased Oracle connection and the bookkeeping that goes with it."""

    session_id: str
    actor_id: str
    target_id: str
    connection: OracleConnection
    identity: TargetIdentity
    created_at: datetime
    last_used_at: datetime
    idle_timeout_seconds: float
    closed: bool = False
    close_reason: str | None = None
    current_execution_id: str | None = None
    cancel_requested_for: str | None = None
    # Set while a break request for that execution is on its way to the database, and
    # cleared once the driver call returns. A break is aimed at a connection, not at a
    # statement, so anything that would put a second statement on this connection has
    # to wait for it: see :meth:`SessionRegistry.request_cancel`.
    cancel_delivery_for: str | None = None
    # Set by the engine, on the worker thread, immediately before the driver call for
    # that execution. Until it matches ``current_execution_id`` nothing has been sent
    # to Oracle, which is what lets a cancellation be exact rather than best effort.
    statement_started_for: str | None = None
    # Set when a worker thread is still inside a driver call on this connection.
    # Whoever set it owns disposing of the connection; nobody else may close it.
    connection_abandoned: bool = False
    # Set once the connection has been closed, so a session that is retired and then
    # disposed of is not closed a second time by whoever next holds its lock.
    connection_disposed: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # Guards the small state machine that decides whether a statement may still be
    # sent: ``closed``, ``current_execution_id``, ``cancel_requested_for`` and
    # ``statement_started_for``. Deliberately not the session lock, which is held for
    # the whole of an execution by the very thread this state has to be changed
    # underneath. Held only across these field transitions, never across a driver call.
    _state_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def transaction_open(self) -> bool:
        return not self.closed and self.connection.transaction_open

    def expires_at(self) -> datetime:
        return self.last_used_at + timedelta(seconds=self.idle_timeout_seconds)

    def is_expired(self, now: datetime | None = None) -> bool:
        # A statement in flight is not an idle session. ``last_used_at`` is only
        # stamped when the statement returns, so a long execution would otherwise
        # start to look abandoned and be closed underneath the thread running it --
        # including by the expiry check that a cancellation request passes through.
        if self.current_execution_id is not None:
            return False
        return (now or utcnow()) >= self.expires_at()

    def claim_dispatch(self, execution_id: str) -> str | None:
        """Take the right to send this execution to the database.

        Returns ``None`` when the caller owns the dispatch, or ``"closed"`` /
        ``"cancelled"`` when the statement must not be sent at all.

        Reading the flags and setting the marker has to be one step. Done separately
        the two sides cross: the worker sees no cancellation, a cancellation then
        lands, reads a started marker that is not set yet, and reports the statement
        as never sent -- and the worker sends it anyway. For a write that is the whole
        bug, because the user has already been told nothing was applied.
        """

        with self._state_lock:
            if self.closed:
                return "closed"
            if self.cancel_requested_for == execution_id:
                return "cancelled"
            self.statement_started_for = execution_id
            return None

    def describe(self) -> dict:
        return {
            "sessionId": self.session_id,
            "targetId": self.target_id,
            "actorId": self.actor_id,
            "createdAt": self.created_at.isoformat(),
            "lastUsedAt": self.last_used_at.isoformat(),
            "expiresAt": self.expires_at().isoformat(),
            "transactionOpen": self.transaction_open,
            "busy": self.current_execution_id is not None,
            "currentExecutionId": self.current_execution_id,
            "closed": self.closed,
            "closeReason": self.close_reason,
            "identity": self.identity.model_dump(by_alias=True, mode="json"),
        }


class SessionRegistry:
    """Holds the worksheet sessions owned by one execution service."""

    def __init__(self, backend: OracleBackend, idle_timeout_seconds: float = 300.0) -> None:
        self._backend = backend
        self._idle_timeout = idle_timeout_seconds
        self._sessions: dict[str, WorksheetSession] = {}
        self._registry_lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------------

    def open(
        self,
        *,
        actor_id: str,
        target_id: str,
        spec: ConnectionSpec,
        idle_timeout_seconds: float | None = None,
    ) -> WorksheetSession:
        connection = self._backend.connect(spec)
        try:
            identity = connection.identity()
        except Exception:
            connection.close()
            raise
        now = utcnow()
        session = WorksheetSession(
            session_id=f"ws_{uuid.uuid4().hex[:16]}",
            actor_id=actor_id,
            target_id=target_id,
            connection=connection,
            identity=identity,
            created_at=now,
            last_used_at=now,
            idle_timeout_seconds=idle_timeout_seconds or self._idle_timeout,
        )
        with self._registry_lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str, actor_id: str) -> WorksheetSession:
        with self._registry_lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise SessionExpiredError(
                "The worksheet session no longer exists. Open a new one; any uncommitted "
                "work was rolled back.",
                detail={"sessionId": session_id},
            )
        if session.actor_id != actor_id:
            # Deliberately the same shape as a missing session so one user cannot probe
            # for another user's session identifiers.
            raise AuthorizationError(
                "This worksheet session belongs to another user.",
                detail={"sessionId": session_id},
            )
        if session.closed:
            raise SessionExpiredError(
                f"The worksheet session was closed: {session.close_reason or 'unknown reason'}.",
                detail={"sessionId": session_id},
            )
        if session.is_expired():
            self._close_or_retire(session, reason="idle timeout")
            raise SessionExpiredError(
                "The worksheet session expired while idle. Uncommitted work was rolled "
                "back and the connection was closed.",
                detail={"sessionId": session_id},
            )
        return session

    @contextmanager
    def acquire(
        self, session_id: str, actor_id: str, execution_id: str
    ) -> Iterator[WorksheetSession]:
        """Take exclusive use of a session for one execution."""

        session = self.get(session_id, actor_id)
        if not session._lock.acquire(blocking=False):
            raise SessionBusyError(
                "Another statement is still running in this worksheet session. Wait for "
                "it to finish or cancel it.",
                detail={
                    "sessionId": session_id,
                    "currentExecutionId": session.current_execution_id,
                },
            )
        try:
            # ``get`` checked the session before this lock was taken, and revocation,
            # expiry and an explicit close all close a session through that same lock.
            # Rechecking here is what stops a statement from running on a connection
            # that was closed in the gap.
            self._reject_if_closed(session)
            # One step, so a cancellation cannot land between this session being
            # claimed and the previous execution's markers being cleared, and be
            # thrown away along with them.
            with session._state_lock:
                self._reject_if_cancel_in_flight(session)
                session.cancel_requested_for = None
                session.statement_started_for = None
                session.current_execution_id = execution_id
            yield session
        finally:
            with session._state_lock:
                session.current_execution_id = None
            session.last_used_at = utcnow()
            # Both decisions are taken while the lock is still held: releasing first
            # would let another actor lease the session and start a statement on a
            # connection this block is about to close.
            try:
                if session.closed:
                    # The session was retired while this statement was in flight --
                    # revoked access, or a cancellation it did not obey. Its
                    # connection was deliberately left open at that point because
                    # this thread was inside a driver call on it. Unless it still is
                    # (the quarantine case, where the engine disposes of the
                    # connection once the runaway call returns), this is the first
                    # safe moment to close it.
                    if not (session.connection_abandoned or session.connection_disposed):
                        self._dispose(session)
                elif session.connection.is_broken:
                    self._close(session, reason="connection lost")
            finally:
                session._lock.release()

    def request_cancel(self, session_id: str, actor_id: str) -> dict:
        """Ask the database to break the running call.

        Runs outside the session lock on purpose: the lock is held by the statement
        being cancelled. Once a statement is in flight, cancellation is best effort
        and the result says so.

        A statement that is still waiting for a worker slot is the better case: nothing
        has been sent, so withdrawing it is exact. Setting the flag and reading the
        started marker happen under the state lock -- the same lock
        :meth:`WorksheetSession.claim_dispatch` takes to set that marker -- so the two
        cannot interleave. Either the statement is already in flight, or the dispatch
        guard is guaranteed to see the cancellation and refuse to send it, which
        matters most for a write: one dispatched after the user was told it had been
        cancelled would be applied behind their back. Ordering the accesses without a
        lock is not enough. A check-then-set on one side and a set-then-check on the
        other can both miss, and then the cancellation reports a write as never sent
        while a worker thread is on its way to send it.

        A break is aimed at a connection, not at a statement, and the statement being
        cancelled can finish on its own while the break is still being composed. The
        session would then be free, the next statement would start on the same
        connection, and the break would land on that one instead -- stopping work the
        user never asked to stop, while the reply named the execution that had already
        finished. ``cancel_delivery_for`` closes that window: it is set under the same
        state lock that reads the started marker, and it is what
        :meth:`_reject_if_cancel_in_flight` refuses a new statement, commit, rollback
        or close on until the break request has returned.
        """

        session = self.get(session_id, actor_id)
        with session._state_lock:
            execution_id = session.current_execution_id
            if execution_id is None:
                return {"delivered": False, "reason": "No statement is running in this session."}
            session.cancel_requested_for = execution_id
            started = session.statement_started_for == execution_id
            if started:
                session.cancel_delivery_for = execution_id
        # The break itself is a driver round trip, so it is made outside the state
        # lock; by this point the statement is known to be in flight.
        if not started:
            return {
                "delivered": True,
                "executionId": execution_id,
                "statementStarted": False,
                "reason": None,
            }
        try:
            with session._state_lock:
                # Nothing else can have claimed the session since the marker went up,
                # so a statement that is no longer current is one that finished by
                # itself. Sending the break anyway would leave it queued against a
                # connection that is about to be used for something else.
                finished = session.current_execution_id != execution_id
            if finished:
                return {
                    "delivered": False,
                    "executionId": execution_id,
                    "statementStarted": True,
                    "reason": "The statement finished before the cancellation reached the "
                    "database.",
                }
            delivered = session.connection.cancel()
        finally:
            with session._state_lock:
                session.cancel_delivery_for = None
        return {
            "delivered": delivered,
            "executionId": execution_id,
            "statementStarted": True,
            "reason": None
            if delivered
            else "The database did not accept the break request; the statement may still run.",
        }

    def commit(self, session_id: str, actor_id: str) -> dict:
        """Make the session's transaction durable, or say honestly that we do not know.

        Three outcomes are possible and they are kept distinct. The commit succeeds;
        it fails definitely, in which case the session is still usable and the work is
        still pending; or the connection dies with the commit in flight, in which case
        Oracle may already have committed. The last case is never reported as a
        failure, because a caller that treats it as one will retry and apply the work
        twice. The session is retired instead, so nothing can commit it again.
        """

        session = self.get(session_id, actor_id)
        with self._exclusive(session, "commit"):
            had_transaction = session.connection.transaction_open
            try:
                session.connection.commit()
            except OutcomeUnknownError as exc:
                exc.detail.setdefault("sessionId", session_id)
                exc.detail["hadOpenTransaction"] = had_transaction
                exc.detail["verificationRequired"] = True
                self._close(session, reason="connection lost during commit")
                raise
            except Exception:
                # A definite failure leaves the transaction pending and the session
                # usable, unless the driver also told us the session itself is gone.
                if session.connection.is_broken:
                    self._close(session, reason="connection lost")
                raise
            session.last_used_at = utcnow()
        return {"committed": True, "hadOpenTransaction": had_transaction}

    def rollback(self, session_id: str, actor_id: str) -> dict:
        session = self.get(session_id, actor_id)
        with self._exclusive(session, "rollback"):
            had_transaction = session.connection.transaction_open
            session.connection.rollback()
            session.last_used_at = utcnow()
        return {"rolledBack": True, "hadOpenTransaction": had_transaction}

    def close(
        self,
        session_id: str,
        actor_id: str,
        *,
        reason: str = "closed by user",
        force: bool = False,
    ) -> dict:
        """Close a session, refusing while a statement is still running in it.

        Closing takes the same exclusive hold as commit and rollback. A close that
        skipped the lock would hand the connection to the driver's close while a
        worker thread is still inside a call on it, which does not stop the statement
        and leaves nothing trustworthy to report; the user cancels first, then closes.

        ``force`` is for the callers that cannot take no for an answer -- withdrawn
        access above all. It retires a busy session immediately, so nothing can be
        leased or committed on it again, and leaves the connection to be disposed of
        when the statement finally returns.
        """

        session = self.get(session_id, actor_id)
        if force:
            had_transaction = session.transaction_open
            self._close_or_retire(session, reason=reason)
            return {"closed": True, "rolledBackUncommittedWork": had_transaction}
        with self._exclusive(session, "close"):
            had_transaction = session.transaction_open
            self._close(session, reason=reason)
        return {"closed": True, "rolledBackUncommittedWork": had_transaction}

    def quarantine(self, session: WorksheetSession, *, reason: str) -> None:
        """Retire a session whose connection is no longer under our control.

        Used when a statement did not stop after cancellation. The connection is
        deliberately *not* closed here: a worker thread is still inside a driver call
        on it, and closing it underneath that thread is not safe. Retiring the session
        drops it from the registry and marks it closed, so it can never be leased
        again and its transaction can never be committed; disposing of the connection
        is the caller's job, once the runaway statement finally returns.
        """

        session.connection_abandoned = True
        self._retire(session, reason=reason)

    def close_all_for_actor(
        self, actor_id: str, *, reason: str, target_id: str | None = None
    ) -> list[str]:
        """Close this actor's sessions, optionally only those on one target."""

        with self._registry_lock:
            sessions = [
                s
                for s in self._sessions.values()
                if s.actor_id == actor_id and (target_id is None or s.target_id == target_id)
            ]
        for session in sessions:
            # Revocation is never refused, so a busy session is retired rather than
            # closed: it can no longer be leased or committed either way.
            self._close_or_retire(session, reason=reason)
        return [s.session_id for s in sessions]

    def reap_expired(self, now: datetime | None = None) -> list[str]:
        """Close idle sessions. Called on a timer by the execution service."""

        now = now or utcnow()
        with self._registry_lock:
            # ``is_expired`` already excludes sessions with a statement in flight.
            expired = [s for s in self._sessions.values() if not s.closed and s.is_expired(now)]
        for session in expired:
            self._close_or_retire(session, reason="idle timeout")
        return [s.session_id for s in expired]

    def list_for_actor(self, actor_id: str) -> list[WorksheetSession]:
        with self._registry_lock:
            return [s for s in self._sessions.values() if s.actor_id == actor_id and not s.closed]

    def shutdown(self, *, reason: str = "service shutdown") -> list[str]:
        """Retire every session, closing the connections nobody is inside.

        Shutting down is not a reason to skip the session lock: a statement may still
        be in flight, and closing its connection from here would put the driver's close
        and that statement on the same handle at the same time. A busy session is
        retired instead, exactly as revocation and expiry retire one, and its
        connection is disposed of by the thread that holds it when the statement
        returns.

        ``reason`` is what the session records say happened. It is not always a
        shutdown: a runtime that has lost ownership of the metadata store gives up its
        sessions the same way, and an operator reading the records needs to see which.
        """

        with self._registry_lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            self._close_or_retire(session, reason=reason)
        return [session.session_id for session in sessions]

    # -- internals -----------------------------------------------------------------

    @contextmanager
    def _exclusive(self, session: WorksheetSession, what: str) -> Iterator[None]:
        if not session._lock.acquire(blocking=False):
            raise SessionBusyError(
                f"Cannot {what} while a statement is running in this worksheet session. "
                "Wait for it to finish or cancel it first.",
                detail={
                    "sessionId": session.session_id,
                    "currentExecutionId": session.current_execution_id,
                },
            )
        try:
            # The same gap as in :meth:`acquire`: the session can be closed between
            # ``get`` and this lock, and committing on a closed connection is worse
            # than refusing.
            self._reject_if_closed(session)
            # A break already on its way to this connection would land on the commit or
            # rollback instead of on the statement it was aimed at.
            with session._state_lock:
                self._reject_if_cancel_in_flight(session)
            yield
        finally:
            try:
                # A revocation that could not take this lock retired the session and
                # left its connection open for whoever holds it. That is this thread.
                if session.closed and not (
                    session.connection_abandoned or session.connection_disposed
                ):
                    self._dispose(session)
            finally:
                session._lock.release()

    @staticmethod
    def _reject_if_cancel_in_flight(session: WorksheetSession) -> None:
        """Refuse a session with a break request still on its way to the database.

        Called with the state lock held. Refusing rather than queueing is the same
        answer this module gives to any other second use of a busy session, and the
        wait is one driver round trip.
        """

        if session.cancel_delivery_for is None:
            return
        raise SessionBusyError(
            "A cancellation is still being delivered to this worksheet session. Try "
            "again in a moment.",
            detail={
                "sessionId": session.session_id,
                "cancellingExecutionId": session.cancel_delivery_for,
            },
        )

    @staticmethod
    def _reject_if_closed(session: WorksheetSession) -> None:
        """Refuse a session that was closed while its lock was being taken."""

        if not session.closed:
            return
        raise SessionExpiredError(
            f"The worksheet session was closed: {session.close_reason or 'unknown reason'}.",
            detail={"sessionId": session.session_id},
        )

    def _close_or_retire(self, session: WorksheetSession, *, reason: str) -> None:
        """Retire a session, closing its connection only if nobody is inside it.

        Holding the session lock is what makes a close safe: while a statement is in
        flight that lock belongs to the thread inside the driver call. When it cannot
        be taken, the session is still retired -- dropped from the registry and marked
        closed, so it can never be leased or committed again -- and disposing of the
        connection is left to :meth:`acquire`, which closes it as soon as the
        statement returns.
        """

        if session._lock.acquire(blocking=False):
            try:
                self._close(session, reason=reason)
            finally:
                session._lock.release()
            return
        self._retire(session, reason=reason)

    def _retire(self, session: WorksheetSession, *, reason: str) -> None:
        """Drop a session from the registry without touching its connection."""

        if not self._mark_closed(session, reason):
            return
        with self._registry_lock:
            self._sessions.pop(session.session_id, None)

    @staticmethod
    def _dispose(session: WorksheetSession) -> None:
        """Close the connection of a session that was already retired."""

        session.connection_disposed = True
        try:
            session.connection.close()
        except Exception:  # noqa: BLE001 - the connection is being thrown away anyway
            log.warning("Could not close the connection of a retired session", exc_info=True)

    @staticmethod
    def _mark_closed(session: WorksheetSession, reason: str) -> bool:
        """Retire the session; ``False`` if somebody else had already retired it.

        Taken under the state lock, which is the lock the dispatch guard holds while
        it decides whether it may send a statement. A statement is therefore either
        refused because the session is closed, or claimed before it was closed --
        never both, and never neither.
        """

        with session._state_lock:
            if session.closed:
                return False
            session.closed = True
            session.close_reason = reason
            return True

    def _close(self, session: WorksheetSession, *, reason: str) -> None:
        if not self._mark_closed(session, reason):
            return
        session.connection_disposed = True
        try:
            # Expiry rolls back rather than commits: an abandoned transaction is never
            # assumed to be work the user wanted kept.
            session.connection.close()
        finally:
            with self._registry_lock:
                self._sessions.pop(session.session_id, None)
