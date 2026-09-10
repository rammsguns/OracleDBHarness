"""Metadata records.

These are the core records named in MVP_PLAN.md. Two rules shape them:

* No database password is stored here. A profile points at a secret reference; the
  value is resolved server-side at connection time and never written back.
* No result rows or bind values are stored here. An execution record keeps the
  statement fingerprint, the policy decision and the outcome, which is what an audit
  needs, without turning the metadata store into a copy of the database.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class Base(DeclarativeBase):
    pass


class AppRole(str, enum.Enum):
    """Application roles. They never bypass Oracle privileges."""

    VIEWER = "viewer"
    DEVELOPER = "developer"
    DBA = "dba"
    ADMINISTRATOR = "administrator"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("usr"))
    subject: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    roles: Mapped[list] = mapped_column(JSON, default=list)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    grants: Mapped[list[UserTargetGrant]] = relationship(back_populates="user")

    def role_set(self) -> set[AppRole]:
        out: set[AppRole] = set()
        for value in self.roles or []:
            try:
                out.add(AppRole(value))
            except ValueError:
                continue
        return out


class SecretReference(Base):
    """A pointer to a credential held outside the application database."""

    __tablename__ = "secret_references"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("sec"))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    provider: Mapped[str] = mapped_column(String(40), default="file")
    locator: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConnectionProfile(Base):
    __tablename__ = "connection_profiles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("tgt"))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    environment: Mapped[str] = mapped_column(String(20), default="development")
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=1521)
    service_name: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(128))
    default_schema: Mapped[str] = mapped_column(String(128), default="")
    protocol: Mapped[str] = mapped_column(String(10), default="tcp")
    wallet_dir: Mapped[str] = mapped_column(String(500), default="")
    secret_reference_id: Mapped[str] = mapped_column(ForeignKey("secret_references.id"))
    # Free-form worksheets are opt-in per profile. Production profiles never get them.
    worksheets_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mutating_runbooks_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    identity_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    identity_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    secret: Mapped[SecretReference] = relationship()
    capabilities: Mapped[list[TargetCapability]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class TargetCapability(Base):
    """One discovered capability on one target. Never assumed, always probed."""

    __tablename__ = "target_capabilities"
    __table_args__ = (UniqueConstraint("profile_id", "capability"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cap"))
    profile_id: Mapped[str] = mapped_column(ForeignKey("connection_profiles.id"))
    capability: Mapped[str] = mapped_column(String(60))
    available: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    profile: Mapped[ConnectionProfile] = relationship(back_populates="capabilities")


class UserTargetGrant(Base):
    """Per-user, per-target access. The UI and the API read the same table."""

    __tablename__ = "user_target_grants"
    __table_args__ = (UniqueConstraint("user_id", "profile_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("grt"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    profile_id: Mapped[str] = mapped_column(ForeignKey("connection_profiles.id"))
    # Capabilities the grant permits, expressed as harness operations rather than
    # Oracle privileges: "read", "worksheet", "compile", "runbook".
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    secret_reference_id: Mapped[str | None] = mapped_column(
        ForeignKey("secret_references.id"), nullable=True
    )
    granted_by: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="grants")
    profile: Mapped[ConnectionProfile] = relationship()


class WorksheetSessionRecord(Base):
    """The durable trace of a leased session. The live connection is in memory."""

    __tablename__ = "worksheet_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("connection_profiles.id"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str] = mapped_column(String(200), default="")
    oracle_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SavedScript(Base):
    __tablename__ = "saved_scripts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("scr"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("connection_profiles.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Execution(Base):
    """Persisted before dispatch so interrupted work can be reconciled on restart."""

    __tablename__ = "executions"
    __table_args__ = (
        Index("ix_executions_user_started", "user_id", "started_at"),
        # Scoped to the actor: an idempotency key is one user's name for one
        # request, not a global identifier another user can collide with.
        UniqueConstraint("user_id", "dedup_key", name="uq_executions_actor_dedup_key"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("connection_profiles.id"), index=True)
    session_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    operation_id: Mapped[str] = mapped_column(String(120))
    statement_kind: Mapped[str] = mapped_column(String(30), default="unknown")
    risk_class: Mapped[str] = mapped_column(String(30), default="read")
    statement_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    # Raw SQL is retained only where the deployment has enabled statement retention.
    statement_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    bind_names: Mapped[list] = mapped_column(JSON, default=list)
    limits_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    policy_decision: Mapped[str] = mapped_column(String(30), default="allowed")
    policy_reason: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(30), default="queued")
    rows_returned: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_affected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    database_elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str] = mapped_column(String(60), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    verification_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedup_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Separate from the redacted audit fingerprint: exact request identity, without
    # retaining SQL literals or bind values. NULL for pre-digest executions.
    request_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    """Append-only. Ordinary application code writes; nothing updates or deletes."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_actor_time", "actor_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("aud"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_id: Mapped[str] = mapped_column(String(40), default="")
    actor_subject: Mapped[str] = mapped_column(String(255), default="")
    integration_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    profile_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    operation_id: Mapped[str] = mapped_column(String(120), default="")
    execution_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    risk_class: Mapped[str] = mapped_column(String(30), default="read")
    policy_decision: Mapped[str] = mapped_column(String(30), default="allowed")
    outcome: Mapped[str] = mapped_column(String(30), default="")
    statement_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    affected_counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RunbookDefinition(Base):
    """Registered maintenance operations, loaded from the reviewed catalog."""

    __tablename__ = "runbook_definitions"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    risk_class: Mapped[str] = mapped_column(String(30), default="read")
    mutating: Mapped[bool] = mapped_column(Boolean, default=False)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    parameters: Mapped[list] = mapped_column(JSON, default=list)
    verification_operation_id: Mapped[str] = mapped_column(String(120), default="")
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DiagnosticObservation(Base):
    """A saved before/after observation for the tuning workbench."""

    __tablename__ = "diagnostic_observations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("obs"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("connection_profiles.id"))
    label: Mapped[str] = mapped_column(String(200))
    sql_id: Mapped[str] = mapped_column(String(40), default="")
    child_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_hash_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    statement_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(30), default="cursor")
    measured_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    estimated_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IntegrationInstance(Base):
    """A registered IDE adapter, such as one OracleDataForge installation."""

    __tablename__ = "integration_instances"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("int"))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(40), default="dataforge")
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    token_hash: Mapped[str] = mapped_column(String(128))
    token_prefix: Mapped[str] = mapped_column(String(16), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    adapter_version: Mapped[str] = mapped_column(String(40), default="")
    protocol_version: Mapped[str] = mapped_column(String(20), default="")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CopilotRequest(Base):
    """One copilot call. Prompt text is not stored unless logging is enabled."""

    __tablename__ = "copilot_requests"
    __table_args__ = (Index("ix_copilot_actor_time", "actor_key", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cop"))
    actor_key: Mapped[str] = mapped_column(String(255), index=True)
    user_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    integration_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_reference: Mapped[str] = mapped_column(String(255), default="")
    profile_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    conversation_id: Mapped[str] = mapped_column(String(64), default="")
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(80), default="")
    context_categories: Mapped[list] = mapped_column(JSON, default=list)
    context_bytes: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(String(30), default="")
    error_code: Mapped[str] = mapped_column(String(60), default="")
    prompt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProposedEdit(Base):
    """An editor diff the copilot produced, pinned to the revision it was based on."""

    __tablename__ = "proposed_edits"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("edt"))
    copilot_request_id: Mapped[str] = mapped_column(ForeignKey("copilot_requests.id"))
    editor_id: Mapped[str] = mapped_column(String(120), default="")
    base_revision: Mapped[str] = mapped_column(String(120), default="")
    base_hash: Mapped[str] = mapped_column(String(64), default="")
    target_reference: Mapped[str] = mapped_column(String(255), default="")
    original_text: Mapped[str] = mapped_column(Text, default="")
    proposed_text: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    rejected_reason: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CopilotBudget(Base):
    """Per-actor request counters backing the configured daily limit."""

    __tablename__ = "copilot_budgets"
    __table_args__ = (UniqueConstraint("actor_key", "day"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("bud"))
    actor_key: Mapped[str] = mapped_column(String(255), index=True)
    day: Mapped[str] = mapped_column(String(10))
    requests: Mapped[int] = mapped_column(Integer, default=0)
    context_bytes: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SchemaVersion(Base):
    """Records which metadata schema this store was created with."""

    __tablename__ = "schema_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[str] = mapped_column(String(20), default="1")
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    note: Mapped[str] = mapped_column(String(200), default="")
