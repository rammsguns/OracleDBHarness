"""Two windows between a decision and the round trip it authorizes.

Both bugs here have the same shape: something is checked, and by the time the
statement is actually sent the answer has changed. Both are about writes, so getting
them wrong means work the user was told would not happen.

* A cancellation that lands while the dispatch guard is deciding. The guard reads the
  cancellation flag and then sets the started marker; the cancellation sets the flag
  and then reads that marker. Ordered but unsynchronized, both sides can miss, and
  then the user is told the write was never sent while a worker thread is on its way
  to send it.
* DDL admitted while the session had no open transaction, and leased after another
  statement in that session left one. Oracle commits the pending DML as a side effect
  of the DDL, so the check has to be made again under the lock that decides what
  actually runs.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest

from harness_worker.backend.base import (
    ConnectionSpec,
    OracleBackend,
    OracleConnection,
    StatementResult,
)
from harness_worker.engine import ExecutionEngine
from harness_worker.errors import PolicyError
from harness_worker.sessions import SessionRegistry, WorksheetSession
from harness_worker.types import (
    Capability,
    CapabilityReport,
    ExecutionLimits,
    ExecutionRequest,
    ExecutionState,
    StatementKind,
    TargetIdentity,
)

SPEC = ConnectionSpec(profile_id="prf_1", host="h", port=1521, service_name="s", username="u")


class StubConnection(OracleConnection):
    """Records what it is asked to run, and opens a transaction when that is a write."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.executed: list[str] = []
        self.closed = False
        self.cancels = 0
        self.commits = 0
        self._transaction_open = False

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
    ) -> StatementResult:
        self.executed.append(statement)
        self.started.set()
        self.release.wait(30)
        if kind == StatementKind.DML:
            self._transaction_open = True
        if kind == StatementKind.DDL:
            # What this test file exists to prevent: Oracle commits whatever was
            # pending, whether or not the user asked for it.
            self._transaction_open = False
            self.commits += 1
        return StatementResult(statement_kind=kind, rows_affected=1)

    def commit(self) -> None:
        self.commits += 1
        self._transaction_open = False

    def rollback(self) -> None:
        self._transaction_open = False

    def cancel(self) -> bool:
        self.cancels += 1
        return True

    def close(self) -> None:
        self.closed = True

    def is_healthy(self) -> bool:
        return True

    @property
    def transaction_open(self) -> bool:
        return self._transaction_open

    @property
    def is_broken(self) -> bool:
        return False


class StubBackend(OracleBackend):
    name = "stub"

    def __init__(self) -> None:
        self.connections: list[StubConnection] = []

    def connect(self, spec: ConnectionSpec) -> OracleConnection:
        connection = StubConnection()
        self.connections.append(connection)
        return connection


def _request(execution_id: str, statement: str) -> ExecutionRequest:
    return ExecutionRequest(
        executionId=execution_id,
        operationId="worksheet.execute",
        statement=statement,
        binds=[],
        statementKind=StatementKind.UNKNOWN,
        limits=ExecutionLimits(maxRows=10, deadlineSeconds=30.0),
    )


@pytest.fixture
def backend() -> StubBackend:
    return StubBackend()


@pytest.fixture
def registry(backend: StubBackend) -> Iterator[SessionRegistry]:
    registry = SessionRegistry(backend, idle_timeout_seconds=300.0)
    yield registry
    registry.shutdown()


@pytest.fixture
def engine(backend: StubBackend, registry: SessionRegistry) -> Iterator[ExecutionEngine]:
    engine = ExecutionEngine(backend, registry, max_workers=2)
    yield engine
    engine.shutdown()


@dataclass
class _Race:
    """The cancellation that was forced into the guard's window, and its answer."""

    settled: threading.Event
    result: dict[str, Any]


@contextmanager
def cancel_while_the_guard_decides(
    session: WorksheetSession, cancel: Callable[[], dict]
) -> Iterator[_Race]:
    """Force a cancellation into the window the dispatch guard decides in.

    The window is a couple of bytecodes wide, so it is held open rather than waited
    for. The guard's read of the cancellation flag is intercepted: the value it would
    have seen is captured first, a thread that cancels is released, and only then does
    the read return that captured value. The guard therefore behaves exactly as it
    would if the cancellation had landed immediately after it looked.

    What happens next is the whole test. If the guard's decision is atomic the
    cancellation cannot get in -- it blocks on the lock the guard holds, and settles
    afterwards, on a statement that really is on its way. If it is not, it settles
    here, in the middle, reporting a write that has not been sent yet as one that
    never will be.
    """

    original = WorksheetSession.cancel_requested_for
    values: dict[int, str | None] = {}
    fired = threading.Event()
    at_the_window = threading.Event()
    race = _Race(settled=threading.Event(), result={})

    def read(self: WorksheetSession) -> str | None:
        seen = values.get(id(self))
        if self is session and not fired.is_set():
            fired.set()
            at_the_window.set()
            race.settled.wait(0.5)
        return seen

    def write(self: WorksheetSession, value: str | None) -> None:
        values[id(self)] = value

    def canceller() -> None:
        assert at_the_window.wait(10), "the dispatch guard never read its flags"
        race.result.update(cancel())
        race.settled.set()

    WorksheetSession.cancel_requested_for = property(read, write)  # type: ignore[assignment]
    thread = threading.Thread(target=canceller, name="stub-cancels")
    thread.start()
    try:
        yield race
    finally:
        at_the_window.set()
        thread.join(10)
        assert not thread.is_alive()
        WorksheetSession.cancel_requested_for = original  # type: ignore[assignment]


def test_a_cancellation_cannot_report_a_write_as_never_sent_and_then_let_it_run(
    registry: SessionRegistry, engine: ExecutionEngine
) -> None:
    """``statementStarted: False`` is a promise that nothing reached the database.

    It is the answer the console shows as "the statement was cancelled, nothing was
    applied". A write dispatched after that answer is applied behind the user's back,
    and no later outcome can take it back.
    """

    session = registry.open(actor_id="usr_1", target_id="prf_1", spec=SPEC)
    connection = session.connection
    assert isinstance(connection, StubConnection)
    outcomes: dict[str, Any] = {}

    def cancel() -> dict:
        return registry.request_cancel(session.session_id, "usr_1")

    with cancel_while_the_guard_decides(session, cancel) as race:
        runner = threading.Thread(
            target=lambda: outcomes.update(
                outcome=engine.execute_in_session(
                    session, _request("exe_1", "UPDATE employees SET salary = 1")
                )
            ),
            name="stub-writes",
        )
        runner.start()
        # The cancellation has to have an answer before the statement is let go, or
        # the two would be racing again and the test would be reading a coin toss.
        assert race.settled.wait(10), "the cancellation never settled"
        connection.release.set()
        runner.join(15)
        assert not runner.is_alive()

    reported = race.result
    assert reported["delivered"] is True
    if reported["statementStarted"] is False:
        # The user was told nothing was sent. Nothing may have been.
        assert connection.executed == []
        assert outcomes["outcome"].state == ExecutionState.CANCELLED
        assert outcomes["outcome"].verification["statementStarted"] is False
    else:
        # The statement won the race and is genuinely in flight, so cancellation is
        # the best-effort break, and the outcome speaks for what the write did.
        assert connection.executed == ["UPDATE employees SET salary = 1"]
        assert connection.cancels == 1


def test_ddl_is_refused_when_the_transaction_opened_after_the_request_was_admitted(
    registry: SessionRegistry, engine: ExecutionEngine
) -> None:
    """The admission check is not the last word, because it does not hold the lock.

    A DML statement running in the same session can commit nothing and still leave a
    transaction open between the moment DDL is admitted and the moment it is leased.
    Oracle would then commit that DML as a side effect of the DDL -- a commit nobody
    asked for, on work the user may have been about to roll back.
    """

    session = registry.open(actor_id="usr_1", target_id="prf_1", spec=SPEC)
    connection = session.connection
    assert isinstance(connection, StubConnection)

    # Admission time: no transaction, so the DDL passes the check in the API.
    assert session.transaction_open is False

    connection.release.set()
    engine.execute_in_session(session, _request("exe_1", "UPDATE employees SET salary = 1"))
    assert session.transaction_open is True

    # The DDL now arrives at the lease, carrying an admission decision that is stale.
    with pytest.raises(PolicyError) as raised:
        engine.execute_in_session(session, _request("exe_2", "CREATE TABLE t (id NUMBER)"))

    assert raised.value.detail["statementStarted"] is False
    assert raised.value.detail["statementKind"] == StatementKind.DDL.value
    # Nothing was sent, so the pending write is still pending and still the user's to
    # commit or roll back.
    assert connection.executed == ["UPDATE employees SET salary = 1"]
    assert connection.commits == 0
    assert session.transaction_open is True
    # The session survives the refusal: it is the DDL that is wrong, not the session.
    assert session.closed is False
    assert registry.get(session.session_id, "usr_1") is session
