"""The database boundary the rest of the harness is written against.

Two implementations exist: python-oracledb against a real database, and a local
stand-in used by tests and demonstrations. Everything above this module -- policy,
sessions, diagnostics, runbooks -- is identical for both, so a test that passes on
the stand-in still exercises the real execution, limit and transaction logic.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from harness_worker.types import (
    Capability,
    CapabilityReport,
    CompilerError,
    ExecutionLimits,
    ResultSet,
    StatementKind,
    TargetIdentity,
)


@dataclass(frozen=True)
class ConnectionSpec:
    """Everything needed to open one Oracle session.

    The password is passed in from a resolved secret reference and is never stored
    on a profile, logged, or returned to a client.
    """

    profile_id: str
    host: str
    port: int
    service_name: str
    username: str
    password: str = field(repr=False, default="")
    driver_mode: str = "thin"
    wallet_dir: str | None = None
    wallet_password: str | None = field(repr=False, default=None)
    protocol: str = "tcp"
    connect_timeout_seconds: int = 10
    default_schema: str | None = None

    def dsn(self) -> str:
        return f"{self.host}:{self.port}/{self.service_name}"

    def redacted(self) -> dict[str, Any]:
        return {
            "profileId": self.profile_id,
            "dsn": self.dsn(),
            "username": self.username,
            "driverMode": self.driver_mode,
            "protocol": self.protocol,
        }


@dataclass
class StatementResult:
    """Raw execution result, before the engine wraps it in an ExecutionOutcome."""

    statement_kind: StatementKind
    result_set: ResultSet | None = None
    rows_affected: int | None = None
    dbms_output: list[str] = field(default_factory=list)
    dbms_output_truncated: bool = False
    bind_outputs: dict[str, Any] = field(default_factory=dict)
    compiler_errors: list[CompilerError] = field(default_factory=list)
    database_elapsed_ms: int | None = None
    warnings: list[str] = field(default_factory=list)


class OracleConnection(abc.ABC):
    """A single Oracle session owned by exactly one worksheet session or operation.

    Implementations are synchronous and are driven from a bounded worker pool. A
    connection is never shared between users and never returned to a pool while a
    transaction is open.
    """

    @abc.abstractmethod
    def identity(self) -> TargetIdentity:
        """Query the database for its own identity. Never derived from the profile."""

    @abc.abstractmethod
    def probe_capability(self, capability: Capability) -> CapabilityReport:
        """Test one capability with a real, minimal query against this session."""

    @abc.abstractmethod
    def execute(
        self,
        statement: str,
        binds: dict[str, Any],
        kind: StatementKind,
        limits: ExecutionLimits,
        *,
        collect_dbms_output: bool = False,
    ) -> StatementResult:
        """Run one prepared statement inside this session."""

    @abc.abstractmethod
    def commit(self) -> None: ...

    @abc.abstractmethod
    def rollback(self) -> None: ...

    @abc.abstractmethod
    def cancel(self) -> bool:
        """Ask the database to break the in-flight call.

        Cancellation is best effort. The return value says whether the break was
        delivered, not whether the statement stopped before doing its work.
        """

    @abc.abstractmethod
    def close(self) -> None: ...

    @abc.abstractmethod
    def is_healthy(self) -> bool:
        """Cheap round trip used before a leased connection is reused."""

    @property
    @abc.abstractmethod
    def transaction_open(self) -> bool: ...

    @property
    @abc.abstractmethod
    def is_broken(self) -> bool:
        """True once the session has failed in a way that makes reuse unsafe."""

    def enable_dbms_output(self, size_bytes: int) -> None:  # pragma: no cover - optional
        return None


class OracleBackend(abc.ABC):
    """Opens sessions for one process."""

    name: str = "base"

    @abc.abstractmethod
    def connect(self, spec: ConnectionSpec) -> OracleConnection: ...

    def shutdown(self) -> None:  # pragma: no cover - optional
        return None
