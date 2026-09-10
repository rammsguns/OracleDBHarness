"""Typed vocabulary for the operation contract described in MVP_PLAN.md.

The contract is::

    operation ID + actor + target + parameters + capability requirements
        + risk class + limits -> execution record + structured result
        + verification evidence
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class Environment(str, enum.Enum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class RiskClass(str, enum.Enum):
    """How much damage an operation can do if it is wrong.

    READ never changes data. SESSION_WRITE changes data inside the caller's own
    uncommitted transaction. PERSISTENT_WRITE commits, or performs DDL that
    implicitly commits. ADMINISTRATIVE changes shared database state.
    """

    READ = "read"
    SESSION_WRITE = "session_write"
    PERSISTENT_WRITE = "persistent_write"
    ADMINISTRATIVE = "administrative"


class ExecutionState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLED = "cancelled"
    OUTCOME_UNKNOWN = "outcome_unknown"


TERMINAL_STATES = {
    ExecutionState.SUCCEEDED,
    ExecutionState.FAILED,
    ExecutionState.CANCELLED,
    ExecutionState.OUTCOME_UNKNOWN,
}


class StatementKind(str, enum.Enum):
    QUERY = "query"
    DML = "dml"
    DDL = "ddl"
    PLSQL_BLOCK = "plsql_block"
    PLSQL_SOURCE = "plsql_source"
    TRANSACTION_CONTROL = "transaction_control"
    SESSION_CONTROL = "session_control"
    UNKNOWN = "unknown"


class Capability(str, enum.Enum):
    """Discovered per target, never assumed.

    Each value maps to a concrete probe in harness_worker.capabilities. A missing
    capability disables the feature that needs it and explains why; it never
    silently degrades into an empty or healthy-looking panel.
    """

    CONNECT = "connect"
    SESSION_SCHEMA = "session_schema"
    ALL_OBJECTS = "all_objects"
    EXPLAIN_PLAN = "explain_plan"
    DISPLAY_CURSOR = "display_cursor"
    V_SESSION = "v_session"
    V_SQL = "v_sql"
    DBA_TABLESPACES = "dba_tablespaces"
    DBA_SCHEDULER_JOBS = "dba_scheduler_jobs"
    DBMS_STATS = "dbms_stats"
    COMPILE_OBJECTS = "compile_objects"


class TargetIdentity(Model):
    """Identity proved by querying the target, not copied from its profile."""

    database_name: str = Field(default="", alias="databaseName")
    instance_name: str | None = Field(default=None, alias="instanceName")
    host_name: str | None = Field(default=None, alias="hostName")
    version: str = ""
    version_full: str = Field(default="", alias="versionFull")
    is_cdb: bool = Field(default=False, alias="isCdb")
    container_name: str | None = Field(default=None, alias="containerName")
    current_schema: str = Field(default="", alias="currentSchema")
    current_user: str = Field(default="", alias="currentUser")
    session_id: int | None = Field(default=None, alias="sessionId")
    serial_number: int | None = Field(default=None, alias="serialNumber")

    @property
    def major_version(self) -> int:
        head = self.version.split(".", 1)[0]
        return int(head) if head.isdigit() else 0


class CapabilityReport(Model):
    """Result of probing one capability on one target."""

    capability: Capability
    available: bool
    detail: str = ""
    checked_at: datetime = Field(default_factory=utcnow, alias="checkedAt")


class ExecutionLimits(Model):
    """Bounds applied to a single execution.

    deadline_seconds is the total operation deadline. It is enforced in addition
    to the driver round-trip timeout, so a statement that keeps returning data
    still cannot outlive its budget.
    """

    max_rows: int = Field(default=1000, alias="maxRows", ge=1, le=100_000)
    max_response_bytes: int = Field(default=10 * 1024 * 1024, alias="maxResponseBytes", ge=1024)
    deadline_seconds: float = Field(default=30.0, alias="deadlineSeconds", gt=0, le=3600)
    max_dbms_output_bytes: int = Field(default=64 * 1024, alias="maxDbmsOutputBytes", ge=0)
    lob_preview_bytes: int = Field(default=8 * 1024, alias="lobPreviewBytes", ge=0)

    def narrowed_to(self, other: ExecutionLimits) -> ExecutionLimits:
        """Return the stricter of two limit sets, field by field.

        A caller may ask for less than the configured maximum, never for more.
        """
        return ExecutionLimits(
            maxRows=min(self.max_rows, other.max_rows),
            maxResponseBytes=min(self.max_response_bytes, other.max_response_bytes),
            deadlineSeconds=min(self.deadline_seconds, other.deadline_seconds),
            maxDbmsOutputBytes=min(self.max_dbms_output_bytes, other.max_dbms_output_bytes),
            lobPreviewBytes=min(self.lob_preview_bytes, other.lob_preview_bytes),
        )


class ColumnMetadata(Model):
    name: str
    type_name: str = Field(alias="typeName")
    nullable: bool = True
    precision: int | None = None
    scale: int | None = None
    display_size: int | None = Field(default=None, alias="displaySize")


class ResultSet(Model):
    """A bounded result. truncated states plainly that rows were withheld."""

    columns: list[ColumnMetadata] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = Field(default=0, alias="rowCount")
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, alias="truncationReason")


class CompilerError(Model):
    """One row of USER_ERRORS / ALL_ERRORS for a compiled object."""

    line: int
    position: int
    text: str
    attribute: str = "ERROR"
    message_number: int | None = Field(default=None, alias="messageNumber")


class ExecutionOutcome(Model):
    """The structured result half of the operation contract."""

    execution_id: str = Field(alias="executionId")
    state: ExecutionState
    statement_kind: StatementKind = Field(default=StatementKind.UNKNOWN, alias="statementKind")
    result_set: ResultSet | None = Field(default=None, alias="resultSet")
    rows_affected: int | None = Field(default=None, alias="rowsAffected")
    dbms_output: list[str] = Field(default_factory=list, alias="dbmsOutput")
    dbms_output_truncated: bool = Field(default=False, alias="dbmsOutputTruncated")
    bind_outputs: dict[str, Any] = Field(default_factory=dict, alias="bindOutputs")
    compiler_errors: list[CompilerError] = Field(default_factory=list, alias="compilerErrors")
    error: dict | None = None
    elapsed_ms: int = Field(default=0, alias="elapsedMs")
    database_elapsed_ms: int | None = Field(default=None, alias="databaseElapsedMs")
    transaction_open: bool = Field(default=False, alias="transactionOpen")
    warnings: list[str] = Field(default_factory=list)
    verification: dict = Field(default_factory=dict)
    started_at: datetime | None = Field(default=None, alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")


class BindParameter(Model):
    name: str
    value: Any = None
    type_hint: str | None = Field(default=None, alias="typeHint")
    direction: str = "in"


class ExecutionRequest(Model):
    """Everything the engine needs; it never reads policy or identity itself."""

    execution_id: str = Field(alias="executionId")
    operation_id: str = Field(alias="operationId")
    statement: str
    binds: list[BindParameter] = Field(default_factory=list)
    statement_kind: StatementKind = Field(default=StatementKind.UNKNOWN, alias="statementKind")
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    collect_dbms_output: bool = Field(default=False, alias="collectDbmsOutput")
    autocommit: bool = False
