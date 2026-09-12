"""Request and response shapes.

These models are the published contract. ``packages/contracts`` generates the OpenAPI
document and the TypeScript client from them, so the console and every IDE adapter
speak the same types.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Schema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


# -- system -------------------------------------------------------------------------


class SystemInfo(Schema):
    version: str
    environment: str
    auth_mode: str = Field(alias="authMode")
    oracle_backend: str = Field(alias="oracleBackend")
    oracle_driver_mode: str = Field(alias="oracleDriverMode")
    metadata_schema_version: str = Field(alias="metadataSchemaVersion")
    catalog_operations: int = Field(alias="catalogOperations")
    copilot_enabled: bool = Field(alias="copilotEnabled")
    warnings: list[str] = Field(default_factory=list)
    limits: dict[str, Any] = Field(default_factory=dict)


class DevTokenRequest(Schema):
    subject: str
    roles: list[str] = Field(default_factory=list)
    display_name: str = Field(default="", alias="displayName")


class DevTokenResponse(Schema):
    access_token: str = Field(alias="accessToken")
    token_type: str = Field(default="bearer", alias="tokenType")
    expires_in_seconds: int = Field(default=28800, alias="expiresInSeconds")
    warning: str = ""


class OidcSignInConfig(Schema):
    """The console's side of an authorization code flow with PKCE. Nothing secret."""

    issuer: str
    client_id: str = Field(alias="clientId")
    authorization_endpoint: str = Field(alias="authorizationEndpoint")
    token_endpoint: str = Field(alias="tokenEndpoint")
    scopes: list[str]
    audience: str | None = None


class MeResponse(Schema):
    subject: str
    display_name: str = Field(alias="displayName")
    roles: list[str]
    user_id: str | None = Field(default=None, alias="userId")
    targets: list[dict[str, Any]] = Field(default_factory=list)


# -- administration ------------------------------------------------------------------


class SecretReferenceIn(Schema):
    name: str
    provider: str = "file"
    locator: str
    description: str = ""


class UserIn(Schema):
    subject: str
    display_name: str = Field(default="", alias="displayName")
    email: str = ""
    roles: list[str] = Field(default_factory=list)


class ProfileIn(Schema):
    name: str
    environment: str = "development"
    host: str
    port: int = 1521
    service_name: str = Field(alias="serviceName")
    username: str
    default_schema: str = Field(default="", alias="defaultSchema")
    protocol: str = "tcp"
    wallet_dir: str = Field(default="", alias="walletDir")
    secret_reference: str = Field(alias="secretReference")
    worksheets_enabled: bool = Field(default=False, alias="worksheetsEnabled")
    mutating_runbooks_enabled: bool = Field(default=False, alias="mutatingRunbooksEnabled")
    notes: str = ""


class GrantIn(Schema):
    subject: str
    profile_id: str = Field(alias="profileId")
    permissions: list[str]
    secret_reference: str | None = Field(default=None, alias="secretReference")


class IntegrationIn(Schema):
    name: str
    kind: str = "dataforge"
    scopes: list[str] = Field(default_factory=lambda: ["copilot:assist"])


class IntegrationCreated(Schema):
    id: str
    name: str
    kind: str
    scopes: list[str]
    token: str
    note: str


# -- targets --------------------------------------------------------------------------


class CapabilityView(Schema):
    capability: str
    available: bool
    detail: str = ""
    checked_at: str | None = Field(default=None, alias="checkedAt")


class TargetView(Schema):
    id: str
    name: str
    environment: str
    host: str
    port: int
    service_name: str = Field(alias="serviceName")
    username: str
    default_schema: str = Field(default="", alias="defaultSchema")
    worksheets_enabled: bool = Field(alias="worksheetsEnabled")
    mutating_runbooks_enabled: bool = Field(alias="mutatingRunbooksEnabled")
    permissions: list[str] = Field(default_factory=list)
    identity: dict[str, Any] | None = None
    identity_checked_at: str | None = Field(default=None, alias="identityCheckedAt")
    capabilities: list[CapabilityView] = Field(default_factory=list)


class ConnectionTestResult(Schema):
    profile_id: str = Field(alias="profileId")
    connected: bool
    identity: dict[str, Any] | None = None
    capabilities: list[CapabilityView] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    error: dict[str, Any] | None = None


# -- schema explorer -------------------------------------------------------------------


class ObjectPage(Schema):
    owner: str
    object_type: str | None = Field(default=None, alias="objectType")
    offset: int
    limit: int
    columns: list[str]
    rows: list[list[Any]]
    has_more: bool = Field(alias="hasMore")
    collected_at: str = Field(alias="collectedAt")


class PanelResult(Schema):
    """A diagnostic panel plus the state it was collected in.

    ``available`` is false when the data could not be collected. It is never
    presented as an empty, healthy panel.
    """

    operation_id: str = Field(alias="operationId")
    title: str
    available: bool
    collected_at: str = Field(alias="collectedAt")
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    truncated: bool = False
    error: dict[str, Any] | None = None


# -- worksheet --------------------------------------------------------------------------


class OpenSessionRequest(Schema):
    profile_id: str = Field(alias="profileId")


class BindIn(Schema):
    name: str
    value: Any = None
    type_hint: str | None = Field(default=None, alias="typeHint")


class ExecuteRequest(Schema):
    statement: str
    binds: list[BindIn] = Field(default_factory=list)
    max_rows: int | None = Field(default=None, alias="maxRows")
    deadline_seconds: float | None = Field(default=None, alias="deadlineSeconds")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")


class ExecuteResponse(Schema):
    outcome: dict[str, Any]
    policy: dict[str, Any]
    session: dict[str, Any]


class SavedScriptIn(Schema):
    name: str
    body: str
    profile_id: str | None = Field(default=None, alias="profileId")


# -- PL/SQL ------------------------------------------------------------------------------


class CompileRequest(Schema):
    source: str
    profile_id: str = Field(alias="profileId")


class SourceQuery(Schema):
    owner: str
    object_name: str = Field(alias="objectName")
    object_type: str = Field(alias="objectType")


# -- tuning ---------------------------------------------------------------------------


class ExplainRequest(Schema):
    profile_id: str = Field(alias="profileId")
    statement: str


class ObservationIn(Schema):
    profile_id: str = Field(alias="profileId")
    label: str
    sql_id: str = Field(default="", alias="sqlId")
    child_number: int | None = Field(default=None, alias="childNumber")
    source: str = "cursor"
    measured: dict[str, Any] = Field(default_factory=dict)
    estimated: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class ComparisonRequest(Schema):
    before_id: str = Field(alias="beforeId")
    after_id: str = Field(alias="afterId")


# -- runbooks -----------------------------------------------------------------------------


class RunbookRunRequest(Schema):
    profile_id: str = Field(alias="profileId")
    parameters: dict[str, Any] = Field(default_factory=dict)
    confirm: bool = False


# -- copilot -------------------------------------------------------------------------------


class ContextAttachmentIn(Schema):
    category: str
    name: str = ""
    content: str
    provenance: str = "caller"


class EditorIn(Schema):
    editor_id: str = Field(default="", alias="editorId")
    revision: str = ""
    text: str = ""


class CopilotRequestIn(Schema):
    protocol_version: str = Field(default="1.0", alias="protocolVersion")
    action: str
    target_reference: str = Field(alias="targetReference")
    conversation_id: str = Field(default="", alias="conversationId")
    user_message: str = Field(default="", alias="userMessage")
    database_version: str = Field(default="", alias="databaseVersion")
    schema_name: str = Field(default="", alias="schema")
    attachments: list[ContextAttachmentIn] = Field(default_factory=list)
    editor: EditorIn = Field(default_factory=EditorIn)
    # Set by an adapter to say which of *its* users is acting. The harness namespaces
    # this by integration instance and never treats it as a role.
    actor_reference: str | None = Field(default=None, alias="actorReference")
    actor_is_durable: bool = Field(default=True, alias="actorIsDurable")


class ApplyCheckIn(Schema):
    editor_id: str = Field(alias="editorId")
    revision: str = ""
    current_text: str = Field(alias="currentText")
    target_reference: str = Field(alias="targetReference")
    # An adapter has to assert the same actor it used for the request; a proposal
    # belongs to the actor it was generated for, not to the integration as a whole.
    actor_reference: str | None = Field(default=None, alias="actorReference")


class ContextPreviewIn(Schema):
    target_reference: str = Field(alias="targetReference")
    attachments: list[ContextAttachmentIn] = Field(default_factory=list)
    database_version: str = Field(default="", alias="databaseVersion")
    schema_name: str = Field(default="", alias="schema")


# -- history --------------------------------------------------------------------------------


class ExecutionView(Schema):
    id: str
    operation_id: str = Field(alias="operationId")
    profile_id: str = Field(alias="profileId")
    statement_kind: str = Field(alias="statementKind")
    risk_class: str = Field(alias="riskClass")
    state: str
    policy_decision: str = Field(alias="policyDecision")
    policy_reason: str = Field(default="", alias="policyReason")
    statement_fingerprint: str = Field(alias="statementFingerprint")
    rows_returned: int | None = Field(default=None, alias="rowsReturned")
    rows_affected: int | None = Field(default=None, alias="rowsAffected")
    truncated: bool = False
    elapsed_ms: int | None = Field(default=None, alias="elapsedMs")
    database_elapsed_ms: int | None = Field(default=None, alias="databaseElapsedMs")
    error_code: str = Field(default="", alias="errorCode")
    error_message: str = Field(default="", alias="errorMessage")
    started_at: str = Field(alias="startedAt")
    finished_at: str | None = Field(default=None, alias="finishedAt")
    verification: dict[str, Any] = Field(default_factory=dict)


class AuditView(Schema):
    id: str
    created_at: str = Field(alias="createdAt")
    actor_subject: str = Field(alias="actorSubject")
    integration_id: str | None = Field(default=None, alias="integrationId")
    profile_id: str | None = Field(default=None, alias="profileId")
    operation_id: str = Field(alias="operationId")
    execution_id: str | None = Field(default=None, alias="executionId")
    risk_class: str = Field(alias="riskClass")
    policy_decision: str = Field(alias="policyDecision")
    outcome: str
    statement_fingerprint: str = Field(default="", alias="statementFingerprint")
    affected_counts: dict[str, Any] = Field(default_factory=dict, alias="affectedCounts")


# -- restart reconciliation ----------------------------------------------------------


class InterruptedCommitView(Schema):
    """A worksheet session whose COMMIT was in flight when its process died."""

    session_id: str = Field(alias="sessionId")
    profile_id: str = Field(alias="profileId")
    user_id: str = Field(default="", alias="userId")
    opened_at: str = Field(alias="openedAt")
    commit_requested_at: str = Field(alias="commitRequestedAt")
    closed_at: str | None = Field(default=None, alias="closedAt")
    close_reason: str = Field(default="", alias="closeReason")
    oracle_session_id: int | None = Field(default=None, alias="oracleSessionId")


class ReconciliationView(Schema):
    """What this process found in the store when it started, and what remains open."""

    runtime_id: str = Field(alias="runtimeId")
    started_at: str = Field(alias="startedAt")
    superseded_runtimes: list[str] = Field(default_factory=list, alias="supersededRuntimes")
    executions_resolved: int = Field(alias="executionsResolved")
    cancelled_before_dispatch: list[str] = Field(
        default_factory=list, alias="cancelledBeforeDispatch"
    )
    failed_reads: list[str] = Field(default_factory=list, alias="failedReads")
    outcome_unknown: list[str] = Field(default_factory=list, alias="outcomeUnknown")
    sessions_closed: list[str] = Field(default_factory=list, alias="sessionsClosed")
    commits_unknown: list[str] = Field(default_factory=list, alias="commitsUnknown")
    needs_verification: int = Field(alias="needsVerification")
    summary: str
    # Executions still waiting for someone to look in the database. These outlive the
    # restart that produced them: an unverified write from two restarts ago is still
    # unverified.
    outstanding: list[ExecutionView] = Field(default_factory=list)
    # Commits whose durability nobody observed. Listed separately because a commit has no
    # execution record of its own, and counted in `needsVerification` alongside the
    # executions, so the number and the lists always agree.
    outstanding_commits: list[InterruptedCommitView] = Field(
        default_factory=list, alias="outstandingCommits"
    )
    procedure: list[str] = Field(default_factory=list)


class VerificationFindingIn(Schema):
    """An operator's answer to "did this write actually happen?".

    The execution keeps its ``outcome_unknown`` state: it was uncertain at the time and
    rewriting history would lose that. The finding is recorded alongside it, which is
    what takes it off the outstanding list.
    """

    finding: str = Field(
        description=(
            "applied: the change is in the database. not_applied: it is not. "
            "unresolved: it could not be established."
        )
    )
    note: str = Field(default="", max_length=2000)
