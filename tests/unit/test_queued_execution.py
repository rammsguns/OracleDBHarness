"""Work that is still queued must never reach the database.

The worker pool is bounded, so a request can spend its whole budget waiting for a
free slot without a single round trip being made. The deadline path has to take that
work off the queue, not just break a call that was never started: a future left
runnable would execute later, against a connection whose outcome has already been
reported and which may already have been closed.

The same window matters for authorization. A session can be revoked while its
statement is queued, and revocation cannot take the session lock from the request
that holds it, so it retires the session and leaves the connection open. Queued work
therefore has to recheck the session on the worker thread, immediately before the
round trip.

Cancellation lands in the same window, and there the user has been told the statement
was stopped. Work still on the queue has to be withdrawn rather than broken: a break
request cannot stop a statement the database has never seen, so a future left runnable
would apply the write later, on a session its owner believes is idle.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

import pytest

from harness_worker.backend.base import (
    ConnectionSpec,
    OracleBackend,
    OracleConnection,
    StatementResult,
)
from harness_worker.engine import ExecutionEngine
from harness_worker.errors import HarnessError
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
    """A connection that records every statement it is asked to run, and blocks."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.executed: list[str] = []
        self.closed = False
        self.cancels = 0

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
        return StatementResult(statement_kind=kind, rows_affected=1)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def cancel(self) -> bool:
        self.cancels += 1
        return True

    def close(self) -> None:
        self.closed = True

    def is_healthy(self) -> bool:
        return True

    @property
    def transaction_open(self) -> bool:
        return False

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


@pytest.fixture
def backend() -> StubBackend:
    return StubBackend()


@pytest.fixture
def registry(backend: StubBackend) -> SessionRegistry:
    return SessionRegistry(backend, idle_timeout_seconds=300.0)


@pytest.fixture
def engine(backend: StubBackend, registry: SessionRegistry) -> Iterator[ExecutionEngine]:
    """One worker slot, so the second request is queued rather than run."""

    engine = ExecutionEngine(backend, registry, max_workers=1)
    yield engine
    engine.shutdown()


def _request(execution_id: str, statement: str, deadline: float) -> ExecutionRequest:
    return ExecutionRequest(
        executionId=execution_id,
        operationId="worksheet.execute",
        statement=statement,
        limits=ExecutionLimits(deadlineSeconds=deadline),
    )


def test_a_statement_that_timed_out_in_the_queue_is_never_sent_to_the_database(
    backend: StubBackend, engine: ExecutionEngine
) -> None:
    occupied = threading.Thread(
        target=engine.execute_once,
        args=(SPEC, _request("exe_1", "SELECT 1 FROM dual", 30.0)),
        name="stub-occupies-the-pool",
    )
    occupied.start()
    try:
        blocker = backend.connections[0]
        assert blocker.started.wait(5), "the first statement never started"

        outcome = engine.execute_once(
            SPEC, _request("exe_2", "UPDATE employees SET salary = 1", 0.05)
        )

        # The budget elapsed without a round trip, so this is a plain failure: there
        # is nothing to verify inside Oracle.
        assert outcome.state == ExecutionState.FAILED
        assert outcome.error is not None
        assert outcome.error["code"] == "execution_timeout"
        assert outcome.verification["statementStarted"] is False
        queued = backend.connections[1]
        assert queued.executed == []
        assert queued.closed is True
        assert queued.cancels == 0
    finally:
        backend.connections[0].release.set()
        occupied.join(10)
        assert not occupied.is_alive()

    # The worker slot is free again, and the abandoned request stays abandoned.
    threading.Event().wait(0.25)
    assert backend.connections[1].executed == []


def test_a_session_whose_statement_never_ran_is_still_usable(
    backend: StubBackend, registry: SessionRegistry, engine: ExecutionEngine
) -> None:
    """Nothing reached the database, so there is nothing to quarantine the session for."""

    busy: WorksheetSession = registry.open(actor_id="usr_1", target_id="prf_1", spec=SPEC)
    waiting: WorksheetSession = registry.open(actor_id="usr_1", target_id="prf_1", spec=SPEC)

    def run_blocker() -> None:
        engine.execute_in_session(busy, _request("exe_1", "SELECT 1 FROM dual", 30.0))

    occupied = threading.Thread(target=run_blocker, name="stub-occupies-the-pool")
    occupied.start()
    try:
        blocker = busy.connection
        assert isinstance(blocker, StubConnection)
        assert blocker.started.wait(5), "the first statement never started"

        outcome = engine.execute_in_session(
            waiting, _request("exe_2", "UPDATE employees SET salary = 1", 0.05)
        )

        assert outcome.state == ExecutionState.FAILED
        queued = waiting.connection
        assert isinstance(queued, StubConnection)
        assert queued.executed == []
        # The session was never used, so it is not retired and its connection is
        # neither closed nor left with a statement nobody is watching.
        assert waiting.closed is False
        assert queued.closed is False
        assert registry.get(waiting.session_id, "usr_1") is waiting
    finally:
        blocker.release.set()
        occupied.join(10)
        assert not occupied.is_alive()

    threading.Event().wait(0.25)
    assert queued.executed == []
    registry.shutdown()


def test_a_revoked_session_does_not_send_its_queued_statement(
    backend: StubBackend, registry: SessionRegistry, engine: ExecutionEngine
) -> None:
    """Access was withdrawn while the statement was still waiting for a worker slot.

    Nothing had been sent to Oracle at that point, so the write must not be applied
    afterwards -- and the caller has to be told plainly that it never ran.
    """

    busy: WorksheetSession = registry.open(actor_id="usr_1", target_id="prf_1", spec=SPEC)
    waiting: WorksheetSession = registry.open(actor_id="usr_1", target_id="prf_1", spec=SPEC)

    occupied = threading.Thread(
        target=engine.execute_in_session,
        args=(busy, _request("exe_1", "SELECT 1 FROM dual", 30.0)),
        name="stub-occupies-the-pool",
    )
    occupied.start()
    blocker = busy.connection
    assert isinstance(blocker, StubConnection)
    assert blocker.started.wait(5), "the first statement never started"

    outcomes: dict[str, Any] = {}

    def run_queued() -> None:
        try:
            outcomes["outcome"] = engine.execute_in_session(
                waiting, _request("exe_2", "CREATE TABLE t (id NUMBER)", 30.0)
            )
        except HarnessError as exc:  # pragma: no cover - the outcome is returned, not raised
            outcomes["error"] = exc

    queued_request = threading.Thread(target=run_queued, name="stub-waits-in-the-queue")
    queued_request.start()
    try:
        queued = waiting.connection
        assert isinstance(queued, StubConnection)
        # It is accepted and queued, but no worker slot is free, so it has not run.
        assert not queued.started.wait(0.5)

        revoked = registry.close_all_for_actor("usr_1", reason="access revoked")
        assert waiting.session_id in revoked
        # Busy, so it is retired rather than closed: the connection stays open for the
        # request that holds the session lock to dispose of.
        assert waiting.closed is True
    finally:
        blocker.release.set()
        occupied.join(10)
        queued.release.set()
        queued_request.join(10)
        assert not queued_request.is_alive()

    # A free worker slot picked the statement up and it stopped at the session check.
    assert queued.executed == []
    outcome = outcomes["outcome"]
    assert outcome.state == ExecutionState.FAILED
    assert outcome.error is not None
    assert outcome.error["code"] == "session_expired"
    assert outcome.verification["statementStarted"] is False
    # The request that held the lock closed the retired session's connection on its
    # way out, so nothing is left open on a session nobody can reach.
    assert queued.closed is True

    registry.shutdown()


def test_cancelling_a_queued_write_stops_it_from_ever_being_sent(
    registry: SessionRegistry, engine: ExecutionEngine
) -> None:
    """The user cancelled a statement that had not left the queue.

    Breaking the call cannot help here -- Oracle has never seen the statement -- so
    the cancellation has to take the work off the queue instead. Left runnable, the
    UPDATE would reach the database whenever a slot freed, long after the user was
    told it had been cancelled, and the outcome would then claim the statement had
    already completed when the cancellation arrived.
    """

    busy: WorksheetSession = registry.open(actor_id="usr_1", target_id="prf_1", spec=SPEC)
    waiting: WorksheetSession = registry.open(actor_id="usr_1", target_id="prf_1", spec=SPEC)

    occupied = threading.Thread(
        target=engine.execute_in_session,
        args=(busy, _request("exe_1", "SELECT 1 FROM dual", 30.0)),
        name="stub-occupies-the-pool",
    )
    occupied.start()
    blocker = busy.connection
    assert isinstance(blocker, StubConnection)
    assert blocker.started.wait(5), "the first statement never started"

    outcomes: dict[str, Any] = {}

    def run_queued() -> None:
        outcomes["outcome"] = engine.execute_in_session(
            waiting, _request("exe_2", "UPDATE employees SET salary = 1", 30.0)
        )

    queued_request = threading.Thread(target=run_queued, name="stub-waits-in-the-queue")
    queued_request.start()
    try:
        queued = waiting.connection
        assert isinstance(queued, StubConnection)
        # Accepted, but no worker slot is free, so nothing has been sent.
        assert not queued.started.wait(0.5)

        result = registry.request_cancel(waiting.session_id, "usr_1")
        assert result["executionId"] == "exe_2"
        # Not the best-effort case: nothing is in flight to break, and the statement
        # will not run at all.
        assert result["statementStarted"] is False
        assert result["delivered"] is True
        assert queued.cancels == 0
    finally:
        blocker.release.set()
        occupied.join(10)
        queued.release.set()
        queued_request.join(10)
        assert not queued_request.is_alive()

    # A free worker slot picked the statement up and it stopped at the dispatch guard.
    assert queued.executed == []
    outcome = outcomes["outcome"]
    assert outcome.state == ExecutionState.CANCELLED
    assert outcome.error is not None
    assert outcome.error["code"] == "execution_cancelled"
    assert outcome.verification["statementStarted"] is False
    assert outcome.warnings == []
    # Nothing ran on it, so the session is still the user's to carry on with.
    assert waiting.closed is False
    assert queued.closed is False
    assert registry.get(waiting.session_id, "usr_1") is waiting

    registry.shutdown()
