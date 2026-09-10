"""What may take a worksheet session away while a statement is still running in it.

A session's connection belongs to the thread inside the driver call for as long as
that call lasts. Closing it from a request thread does not stop the statement: it
puts two threads on one handle and throws away the only honest report of what
Oracle did. So the three ways a live session can be taken away -- the user closing
it, the idle sweep, and withdrawn access -- are all held to the same rule here.

The stand-in backend has no reason to simulate a statement that stays in flight, so
the registry is driven directly.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import pytest

from harness_worker.backend.base import (
    ConnectionSpec,
    OracleBackend,
    OracleConnection,
    StatementResult,
)
from harness_worker.errors import SessionBusyError, SessionExpiredError
from harness_worker.sessions import SessionRegistry, WorksheetSession
from harness_worker.types import (
    Capability,
    CapabilityReport,
    ExecutionLimits,
    StatementKind,
    TargetIdentity,
)

SPEC = ConnectionSpec(profile_id="prf_1", host="h", port=1521, service_name="s", username="u")


class StubConnection(OracleConnection):
    """A connection that reports anything done to it during a driver call."""

    def __init__(self) -> None:
        self._in_call = threading.Event()
        self.closes = 0
        self.rollbacks = 0
        self.commits = 0
        self.cancels = 0
        # A break is a driver round trip like any other. These hold one open so a test
        # can see what the rest of the registry does while it is still in flight.
        self.cancel_entered = threading.Event()
        self.cancel_release = threading.Event()
        self.cancel_release.set()
        # Every entry here is a second thread reaching the connection while the
        # worker was still inside execute(). Each one is a bug.
        self.unsafe: list[str] = []

    def _use(self, what: str) -> None:
        if self._in_call.is_set():
            self.unsafe.append(what)

    def enter_call(self) -> None:
        self._in_call.set()

    def leave_call(self) -> None:
        self._in_call.clear()

    def identity(self) -> TargetIdentity:
        return TargetIdentity(
            databaseName="STUB",
            instanceName="STUB",
            version="19.3.0.0.0",
            versionFull="Oracle Database 19c",
            sessionId=1,
        )

    def probe_capability(self, capability: Capability) -> CapabilityReport:
        return CapabilityReport(capability=capability, available=True, detail="stub")

    def execute(
        self,
        statement: str,
        binds: dict[str, Any],
        kind: StatementKind,
        limits: ExecutionLimits,
        *,
        collect_dbms_output: bool = False,
    ) -> StatementResult:  # pragma: no cover - the registry is driven directly
        return StatementResult(statement_kind=kind, rows_affected=1)

    def commit(self) -> None:
        self._use("commit")
        self.commits += 1

    def rollback(self) -> None:
        self._use("rollback")
        self.rollbacks += 1

    def cancel(self) -> bool:
        self.cancels += 1
        self.cancel_entered.set()
        self.cancel_release.wait(10)
        return True

    def close(self) -> None:
        self._use("close")
        self.closes += 1

    def is_healthy(self) -> bool:
        return True

    @property
    def transaction_open(self) -> bool:
        return True

    @property
    def is_broken(self) -> bool:
        return False


class StubBackend(OracleBackend):
    name = "stub"

    def connect(self, spec: ConnectionSpec) -> OracleConnection:
        return StubConnection()


@pytest.fixture
def registry() -> SessionRegistry:
    return SessionRegistry(StubBackend(), idle_timeout_seconds=300.0)


@pytest.fixture
def session(registry: SessionRegistry) -> WorksheetSession:
    return registry.open(actor_id="usr_1", target_id="prf_1", spec=SPEC)


@contextmanager
def running_statement(
    registry: SessionRegistry, session: WorksheetSession, execution_id: str = "exe_1"
) -> Iterator[None]:
    """Hold the session the way an executing statement holds it."""

    connection = session.connection
    assert isinstance(connection, StubConnection)
    started = threading.Event()
    release = threading.Event()

    def run() -> None:
        with registry.acquire(session.session_id, session.actor_id, execution_id):
            # What the engine's dispatch guard claims on the worker thread once the
            # statement is really on its way to the database.
            assert session.claim_dispatch(execution_id) is None
            connection.enter_call()
            started.set()
            release.wait(10)
            connection.leave_call()

    worker = threading.Thread(target=run, name="stub-statement")
    worker.start()
    assert started.wait(5), "the statement never started"
    try:
        yield
    finally:
        release.set()
        worker.join(10)
        assert not worker.is_alive()


def test_closing_a_busy_session_is_refused_rather_than_done_underneath_it(
    registry: SessionRegistry, session: WorksheetSession
) -> None:
    connection = session.connection
    assert isinstance(connection, StubConnection)

    with running_statement(registry, session):
        with pytest.raises(SessionBusyError) as raised:
            registry.close(session.session_id, "usr_1")
        assert raised.value.detail["currentExecutionId"] == "exe_1"
        # The statement keeps its connection and the session is still the user's.
        assert connection.closes == 0
        assert session.closed is False
        assert registry.get(session.session_id, "usr_1") is session

    # Once the statement has finished, the same close goes through.
    assert registry.close(session.session_id, "usr_1") == {
        "closed": True,
        "rolledBackUncommittedWork": True,
    }
    assert connection.closes == 1
    assert connection.unsafe == []


def test_cancelling_a_long_statement_does_not_expire_the_session_underneath_it(
    registry: SessionRegistry, session: WorksheetSession
) -> None:
    """A statement in flight is not an idle session, however long it has run.

    ``last_used_at`` is only stamped when the statement returns, so a statement that
    outlives the idle window would otherwise be killed by the expiry check that every
    request -- including the cancellation request meant to stop it -- passes through.
    """

    connection = session.connection
    assert isinstance(connection, StubConnection)

    with running_statement(registry, session):
        session.last_used_at -= timedelta(seconds=600)

        result = registry.request_cancel(session.session_id, "usr_1")
        assert result == {
            "delivered": True,
            "executionId": "exe_1",
            "statementStarted": True,
            "reason": None,
        }
        assert connection.cancels == 1
        assert session.closed is False
        assert connection.closes == 0
        assert registry.reap_expired() == []

    # The idle rule still applies once the session really is idle.
    session.last_used_at -= timedelta(seconds=600)
    assert registry.reap_expired() == [session.session_id]
    assert connection.closes == 1
    assert connection.unsafe == []


class _FinishInTheGap:
    """Let a statement end in the window ``request_cancel`` re-checks for.

    The window is between the state-lock block that puts the break marker up and the
    one that re-reads which execution is running. Driving the statement's end from the
    session's own state lock is that interleaving, made repeatable.
    """

    def __init__(self, session: WorksheetSession, finish: Callable[[], None]) -> None:
        self._session = session
        self._lock = session._state_lock
        self._finish = finish
        self._fired = False

    def acquire(self, *args: Any, **kwargs: Any) -> bool:
        return self._lock.acquire(*args, **kwargs)

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> Any:
        # Fired before the lock is taken, so the statement's own last state-lock hold
        # is not waiting on this thread.
        if not self._fired and self._session.cancel_delivery_for is not None:
            self._fired = True
            self._finish()
        return self._lock.__enter__()

    def __exit__(self, *exc: Any) -> Any:
        return self._lock.__exit__(*exc)


def test_a_break_still_in_flight_keeps_the_session_from_being_used_again(
    registry: SessionRegistry, session: WorksheetSession
) -> None:
    """A break is aimed at a connection, not at a statement.

    The statement being cancelled can finish on its own while the break request is
    still being made. If the session were free in that window it would be leased
    again, and the break would stop the next statement instead -- work the user never
    asked to stop -- while the reply named the execution that had already finished.
    """

    connection = session.connection
    assert isinstance(connection, StubConnection)
    connection.cancel_release.clear()
    cancelled: dict[str, Any] = {}

    def break_it() -> None:
        cancelled.update(registry.request_cancel(session.session_id, "usr_1"))

    breaker = threading.Thread(target=break_it, name="stub-cancel")
    with running_statement(registry, session):
        breaker.start()
        assert connection.cancel_entered.wait(5), "the break was never sent"

    # The statement has finished and let go of the session, and the break has not
    # landed yet. This is the whole window, and nothing may enter it.
    for what in ("commit", "rollback", "close"):
        with pytest.raises(SessionBusyError) as raised:
            getattr(registry, what)(session.session_id, "usr_1")
        assert raised.value.detail["cancellingExecutionId"] == "exe_1"
    with pytest.raises(SessionBusyError):
        with registry.acquire(session.session_id, "usr_1", "exe_2"):  # pragma: no cover
            pytest.fail("a second statement started while a break was in flight")

    connection.cancel_release.set()
    breaker.join(10)
    assert not breaker.is_alive()
    assert cancelled == {
        "delivered": True,
        "executionId": "exe_1",
        "statementStarted": True,
        "reason": None,
    }
    # One break, delivered to the statement it named, and the session is usable again
    # as soon as it has landed.
    assert connection.cancels == 1
    with registry.acquire(session.session_id, "usr_1", "exe_2"):
        pass
    assert connection.unsafe == []


def test_a_statement_that_finished_first_is_not_broken_behind_the_next_one(
    registry: SessionRegistry, session: WorksheetSession
) -> None:
    """Nothing is sent when the statement ends before the break can leave.

    Holding the session is what makes this knowable: no other execution can have
    claimed it since the marker went up, so an execution that is no longer current
    finished by itself. A break sent anyway would sit against a connection that is
    about to run something else.
    """

    connection = session.connection
    assert isinstance(connection, StubConnection)
    started, release = threading.Event(), threading.Event()

    def run() -> None:
        with registry.acquire(session.session_id, "usr_1", "exe_1"):
            assert session.claim_dispatch("exe_1") is None
            started.set()
            release.wait(10)

    worker = threading.Thread(target=run, name="stub-statement")
    worker.start()
    assert started.wait(5), "the statement never started"

    def finish() -> None:
        release.set()
        worker.join(10)

    session._state_lock = _FinishInTheGap(session, finish)  # type: ignore[assignment]
    result = registry.request_cancel(session.session_id, "usr_1")

    assert not worker.is_alive()
    assert result == {
        "delivered": False,
        "executionId": "exe_1",
        "statementStarted": True,
        "reason": "The statement finished before the cancellation reached the database.",
    }
    assert connection.cancels == 0
    # The marker is always taken down, so the session is not left unusable.
    assert session.cancel_delivery_for is None
    with registry.acquire(session.session_id, "usr_1", "exe_2"):
        pass
    assert connection.unsafe == []


def test_withdrawn_access_retires_a_busy_session_and_disposes_of_it_afterwards(
    registry: SessionRegistry, session: WorksheetSession
) -> None:
    """Revocation is never refused, but it still waits for the driver call to end."""

    connection = session.connection
    assert isinstance(connection, StubConnection)

    with running_statement(registry, session):
        assert registry.close_all_for_actor("usr_1", reason="access revoked") == [
            session.session_id
        ]
        # Nothing can be leased, run or committed on the session again...
        assert session.closed is True
        assert registry.list_for_actor("usr_1") == []
        with pytest.raises(SessionExpiredError):
            registry.get(session.session_id, "usr_1")
        with pytest.raises(SessionExpiredError):
            registry.commit(session.session_id, "usr_1")
        # ...but the connection is left alone while the statement still owns it.
        assert connection.closes == 0

    # It is reclaimed as soon as the statement returns, so nothing is leaked.
    assert connection.closes == 1
    assert connection.unsafe == []


@pytest.fixture
def revoked_between_lookup_and_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Land a revocation in the gap between ``get`` and the session lock.

    Every entry point looks the session up first and takes its lock second, and a
    revocation running in that gap finds the lock free and closes the connection
    outright. Driving it from inside ``get`` is the same interleaving, made
    repeatable.
    """

    original = SessionRegistry.get

    def get_then_revoke(self: SessionRegistry, session_id: str, actor_id: str) -> WorksheetSession:
        session = original(self, session_id, actor_id)
        self.close_all_for_actor(actor_id, reason="access revoked")
        return session

    monkeypatch.setattr(SessionRegistry, "get", get_then_revoke)


def test_a_session_revoked_while_its_lock_was_taken_is_not_leased(
    registry: SessionRegistry,
    session: WorksheetSession,
    revoked_between_lookup_and_lock: None,
) -> None:
    """A closed session must not be handed out, however late the close landed."""

    connection = session.connection
    assert isinstance(connection, StubConnection)

    with pytest.raises(SessionExpiredError) as raised:
        with registry.acquire(session.session_id, "usr_1", "exe_1"):  # pragma: no cover
            pytest.fail("a closed session was leased to an execution")
    assert raised.value.detail["sessionId"] == session.session_id
    assert "access revoked" in raised.value.message

    # The connection was closed by the revocation, once, and nothing since has run
    # on it or closed it a second time.
    assert connection.closes == 1
    assert connection.unsafe == []
    assert session.current_execution_id is None


def test_a_session_revoked_while_its_lock_was_taken_is_not_committed(
    registry: SessionRegistry,
    session: WorksheetSession,
    revoked_between_lookup_and_lock: None,
) -> None:
    """The same gap, on the transaction operations: a revoked session never commits."""

    connection = session.connection
    assert isinstance(connection, StubConnection)

    with pytest.raises(SessionExpiredError):
        registry.commit(session.session_id, "usr_1")
    with pytest.raises(SessionExpiredError):
        registry.rollback(session.session_id, "usr_1")

    assert connection.commits == 0
    # The revocation closed the connection; neither refusal touched it again.
    assert connection.closes == 1
    assert connection.unsafe == []


def test_shutting_down_does_not_close_a_connection_a_statement_is_still_inside(
    registry: SessionRegistry, session: WorksheetSession
) -> None:
    """Stopping the service is not a reason to put two threads on one handle.

    Shutdown is held to the same rule as revocation and the idle sweep: a session
    nobody is inside is closed there and then, and a busy one is retired, with the
    close left to the thread that owns the driver call.
    """

    connection = session.connection
    assert isinstance(connection, StubConnection)
    idle = registry.open(actor_id="usr_1", target_id="prf_1", spec=SPEC)
    idle_connection = idle.connection
    assert isinstance(idle_connection, StubConnection)

    with running_statement(registry, session):
        registry.shutdown()

        # Neither session can be reached again...
        assert (session.closed, idle.closed) == (True, True)
        assert session.close_reason == "service shutdown"
        assert registry.list_for_actor("usr_1") == []
        # ...but only the idle one had its connection closed here.
        assert idle_connection.closes == 1
        assert connection.closes == 0

    # The statement returned and the thread that owned it disposed of the connection.
    assert connection.closes == 1
    assert connection.unsafe == []
    assert idle_connection.unsafe == []
