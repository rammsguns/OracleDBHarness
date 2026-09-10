"""A session whose statement outlived cancellation must never be reused.

The engine's deadline path is exercised directly here rather than through the API:
it needs a connection that ignores a break request, which the local stand-in has no
reason to simulate.
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
from harness_worker.errors import SessionExpiredError
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


class DeafConnection(OracleConnection):
    """A connection whose statement keeps running after the break is delivered."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Event()
        self.closed = False
        self.commits = 0
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
        self.started.set()
        self.release.wait(30)
        return StatementResult(statement_kind=kind, rows_affected=1)

    def commit(self) -> None:
        self.commits += 1

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
        return True

    @property
    def is_broken(self) -> bool:
        return False


class StubBackend(OracleBackend):
    name = "stub"

    def __init__(self) -> None:
        self.connections: list[DeafConnection] = []

    def connect(self, spec: ConnectionSpec) -> OracleConnection:
        connection = DeafConnection()
        self.connections.append(connection)
        return connection


@pytest.fixture
def quick_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_module, "CANCEL_GRACE_SECONDS", 0.2)


def test_a_session_that_ignores_cancellation_is_retired_not_reused(
    quick_grace: None,
) -> None:
    backend = StubBackend()
    registry = SessionRegistry(backend, idle_timeout_seconds=300.0)
    engine = ExecutionEngine(backend, registry, max_workers=2)
    session = registry.open(
        actor_id="usr_1",
        target_id="prf_1",
        spec=ConnectionSpec(
            profile_id="prf_1", host="h", port=1521, service_name="s", username="u"
        ),
    )
    connection = session.connection
    assert isinstance(connection, DeafConnection)

    outcome = engine.execute_in_session(
        session,
        ExecutionRequest(
            executionId="exe_1",
            operationId="worksheet.execute",
            statement="UPDATE employees SET salary = 1",
            limits=ExecutionLimits(deadlineSeconds=0.05),
        ),
    )

    # The write's fate is genuinely unknown, and the report says so.
    assert outcome.state == ExecutionState.OUTCOME_UNKNOWN
    assert connection.cancels == 1

    # The session is gone: it cannot be fetched, run in, or committed, so nothing can
    # reach the connection the runaway statement still owns.
    assert session.closed is True
    with pytest.raises(SessionExpiredError):
        registry.get(session.session_id, "usr_1")
    with pytest.raises(SessionExpiredError):
        registry.commit(session.session_id, "usr_1")
    assert registry.list_for_actor("usr_1") == []
    assert connection.commits == 0

    # Once the statement finally returns, the abandoned connection is reclaimed.
    connection.release.set()
    for _ in range(200):
        if connection.closed:
            break
        threading.Event().wait(0.01)
    assert connection.closed is True

    engine.shutdown()


def test_a_statement_that_stops_in_time_keeps_its_session(quick_grace: None) -> None:
    """The session survives when cancellation actually works."""

    backend = StubBackend()
    registry = SessionRegistry(backend, idle_timeout_seconds=300.0)
    engine = ExecutionEngine(backend, registry, max_workers=2)
    session = registry.open(
        actor_id="usr_1",
        target_id="prf_1",
        spec=ConnectionSpec(
            profile_id="prf_1", host="h", port=1521, service_name="s", username="u"
        ),
    )
    connection = session.connection
    assert isinstance(connection, DeafConnection)

    def release_once_started() -> None:
        connection.started.wait(5)
        connection.release.set()

    releaser = threading.Thread(target=release_once_started)
    releaser.start()
    try:
        outcome = engine.execute_in_session(
            session,
            ExecutionRequest(
                executionId="exe_2",
                operationId="worksheet.execute",
                statement="SELECT 1 FROM dual",
                limits=ExecutionLimits(deadlineSeconds=30.0),
            ),
        )
    finally:
        releaser.join()

    assert outcome.state == ExecutionState.SUCCEEDED
    assert session.closed is False
    assert registry.get(session.session_id, "usr_1") is session

    engine.shutdown()
    registry.shutdown()
