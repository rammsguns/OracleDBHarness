"""Cleanup of a connection the engine opened for one call.

Diagnostics, compilation and runbooks all run through
:meth:`ExecutionEngine.execute_once`, which opens a connection, runs one statement
and disposes of it. When the statement outlives its budget *and* ignores the break,
the worker thread is still inside a driver call on that connection. Committing,
rolling it back or closing it from the request thread at that point is two threads on
one handle, so disposal has to wait for the statement to return -- and then happen
exactly once.

The stand-in has no reason to simulate a connection that ignores cancellation, so the
engine is driven directly here.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from harness_worker import engine as engine_module
from harness_worker.backend.base import (
    ConnectionSpec,
    OracleBackend,
    OracleConnection,
    StatementResult,
)
from harness_worker.engine import ExecutionEngine
from harness_worker.errors import CancelledError_
from harness_worker.sessions import SessionRegistry
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
    """A connection that reports any use made of it while a statement is still running."""

    def __init__(self, *, stops_when_cancelled: bool) -> None:
        self._stops_when_cancelled = stops_when_cancelled
        self.release = threading.Event()
        self.started = threading.Event()
        self.in_call = threading.Event()
        self.cancels = 0
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.open_transaction = False
        # Anything recorded here happened on a second thread while the worker was
        # inside execute(). Every entry is a bug.
        self.unsafe: list[str] = []

    def _use(self, what: str) -> None:
        if self.in_call.is_set():
            self.unsafe.append(what)

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
        self.in_call.set()
        self.started.set()
        # The write reaches the database before the deadline elapses, so a transaction
        # is pending for the whole time the statement runs.
        self.open_transaction = True
        try:
            self.release.wait(30)
            if self._stops_when_cancelled and self.cancels:
                raise CancelledError_("The statement stopped when it was broken.")
            return StatementResult(statement_kind=kind, rows_affected=1)
        finally:
            self.in_call.clear()

    def commit(self) -> None:
        self._use("commit")
        self.commits += 1
        self.open_transaction = False

    def rollback(self) -> None:
        self._use("rollback")
        self.rollbacks += 1
        self.open_transaction = False

    def cancel(self) -> bool:
        self.cancels += 1
        if self._stops_when_cancelled:
            self.release.set()
        return True

    def close(self) -> None:
        self._use("close")
        self.closes += 1

    def is_healthy(self) -> bool:
        return True

    @property
    def transaction_open(self) -> bool:
        return self.open_transaction

    @property
    def is_broken(self) -> bool:
        return False


class StubBackend(OracleBackend):
    name = "stub"

    def __init__(self, *, stops_when_cancelled: bool) -> None:
        self._stops_when_cancelled = stops_when_cancelled
        self.connections: list[StubConnection] = []

    def connect(self, spec: ConnectionSpec) -> OracleConnection:
        connection = StubConnection(stops_when_cancelled=self._stops_when_cancelled)
        self.connections.append(connection)
        return connection


@pytest.fixture
def quick_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_module, "CANCEL_GRACE_SECONDS", 0.2)


def _engine(backend: StubBackend) -> ExecutionEngine:
    return ExecutionEngine(backend, SessionRegistry(backend, idle_timeout_seconds=300.0))


def _request(execution_id: str, *, autocommit: bool = False) -> ExecutionRequest:
    return ExecutionRequest(
        executionId=execution_id,
        operationId="runbook.gather_table_stats",
        statement="UPDATE employees SET salary = 1",
        limits=ExecutionLimits(deadlineSeconds=0.05),
        autocommit=autocommit,
    )


def test_a_one_shot_statement_that_stops_when_cancelled_is_cleaned_up_normally(
    quick_grace: None,
) -> None:
    backend = StubBackend(stops_when_cancelled=True)
    engine = _engine(backend)

    outcome = engine.execute_once(SPEC, _request("exe_stops", autocommit=True))

    connection = backend.connections[0]
    assert outcome.state == ExecutionState.CANCELLED
    assert connection.cancels == 1
    # Cancellation worked, so the connection came back under our control: the pending
    # write was rolled back rather than committed, and the connection was closed.
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closes == 1
    assert connection.unsafe == []

    engine.shutdown()


def test_a_one_shot_statement_that_ignores_cancellation_is_not_touched_until_it_returns(
    quick_grace: None,
) -> None:
    backend = StubBackend(stops_when_cancelled=False)
    engine = _engine(backend)

    outcome = engine.execute_once(SPEC, _request("exe_deaf", autocommit=True))

    connection = backend.connections[0]
    # A write we no longer control is reported as unknown, never as a clean failure.
    assert outcome.state == ExecutionState.OUTCOME_UNKNOWN
    assert outcome.verification["statementStopped"] is False
    assert connection.cancels == 1

    # The statement is still running. Nothing has been done to its connection.
    assert connection.in_call.is_set()
    assert (connection.commits, connection.rollbacks, connection.closes) == (0, 0, 0)

    # Once the statement finally returns, the connection is reclaimed -- once, and
    # only after the driver call is over.
    connection.release.set()
    for _ in range(500):
        if connection.closes:
            break
        threading.Event().wait(0.01)
    assert connection.closes == 1
    assert connection.commits == 0
    assert connection.unsafe == []

    engine.shutdown()
