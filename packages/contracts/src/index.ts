/**
 * The generated-contract-facing TypeScript client.
 *
 * Both the web console and the OracleDataForge adapter import this, so they cannot
 * drift apart. The shapes here mirror `openapi.json` and
 * `copilot-protocol.schema.json` in this package; `tests/integration/test_contracts.py`
 * fails if the API stops matching them.
 */

export const PROTOCOL_VERSION = "1.3";
export const SUPPORTED_PROTOCOL_MAJOR = 1;

// -- shared shapes ----------------------------------------------------------------

export interface HarnessErrorBody {
  code: string;
  message: string;
  detail?: Record<string, unknown>;
  retryable?: boolean;
  oracleCode?: string;
}

export class HarnessError extends Error {
  readonly code: string;
  readonly detail: Record<string, unknown>;
  readonly status: number;

  constructor(status: number, body: HarnessErrorBody) {
    super(body.message);
    this.name = "HarnessError";
    this.code = body.code;
    this.detail = body.detail ?? {};
    this.status = status;
  }
}

export type ExecutionState =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancellation_requested"
  | "cancelled"
  | "outcome_unknown";

export type RiskClass = "read" | "session_write" | "persistent_write" | "administrative";

export interface ColumnMetadata {
  name: string;
  typeName: string;
  nullable: boolean;
  precision: number | null;
  scale: number | null;
  displaySize: number | null;
}

export interface ResultSet {
  columns: ColumnMetadata[];
  rows: unknown[][];
  rowCount: number;
  truncated: boolean;
  truncationReason: string | null;
}

export interface CompilerError {
  line: number;
  position: number;
  text: string;
  attribute: string;
  messageNumber: number | null;
}

export interface ExecutionOutcome {
  executionId: string;
  state: ExecutionState;
  statementKind: string;
  resultSet: ResultSet | null;
  rowsAffected: number | null;
  dbmsOutput: string[];
  dbmsOutputTruncated: boolean;
  compilerErrors: CompilerError[];
  error: HarnessErrorBody | null;
  elapsedMs: number;
  databaseElapsedMs: number | null;
  transactionOpen: boolean;
  warnings: string[];
  verification: Record<string, unknown>;
}

export interface PolicyDecision {
  allowed: boolean;
  reason: string;
  permission: string;
  risk: RiskClass;
  missingCapabilities: string[];
  notes: string[];
}

export interface WorksheetSession {
  sessionId: string;
  targetId: string;
  actorId: string;
  createdAt: string;
  lastUsedAt: string;
  expiresAt: string;
  transactionOpen: boolean;
  busy: boolean;
  currentExecutionId: string | null;
  closed: boolean;
  closeReason: string | null;
  identity: TargetIdentity;
}

export interface TargetIdentity {
  databaseName: string;
  instanceName: string | null;
  hostName: string | null;
  version: string;
  versionFull: string;
  isCdb: boolean;
  containerName: string | null;
  currentSchema: string;
  currentUser: string;
  sessionId: number | null;
  serialNumber: number | null;
}

export interface Capability {
  capability: string;
  available: boolean;
  detail: string;
  checkedAt: string | null;
}

export interface Target {
  id: string;
  name: string;
  environment: "development" | "test" | "staging" | "production";
  host: string;
  port: number;
  serviceName: string;
  username: string;
  defaultSchema: string;
  worksheetsEnabled: boolean;
  mutatingRunbooksEnabled: boolean;
  permissions: string[];
  identity: TargetIdentity | null;
  identityCheckedAt: string | null;
  capabilities: Capability[];
}

/**
 * A diagnostic panel. `available: false` means the data could not be collected and
 * `error` says why. An unavailable panel is never rendered as healthy.
 */
export interface PanelResult {
  operationId: string;
  title: string;
  available: boolean;
  collectedAt: string;
  columns: string[];
  rows: unknown[][];
  truncated: boolean;
  error: HarnessErrorBody | null;
}

export interface SystemInfo {
  version: string;
  environment: string;
  authMode: string;
  oracleBackend: string;
  oracleDriverMode: string;
  metadataSchemaVersion: string;
  catalogOperations: number;
  copilotEnabled: boolean;
  warnings: string[];
  limits: Record<string, number>;
}

// -- copilot protocol ---------------------------------------------------------------

export type CopilotAction =
  | "explain"
  | "diagnose"
  | "propose"
  | "test_block"
  | "explain_plan"
  | "validate"
  /** Kiwi playbooks (protocol 1.2). */
  | "kiwi.diagnose"
  | "kiwi.create"
  | "kiwi.test_block"
  | "kiwi.explain_package"
  | "kiwi.explain_process";

export type ContextCategory =
  | "selected_source"
  | "object_definition"
  | "schema_metadata"
  | "error_text"
  | "plan_text"
  | "database_version"
  | "user_message";

/** Categories the harness refuses outright, whatever a caller sends. */
export const FORBIDDEN_CONTEXT_CATEGORIES = [
  "result_rows",
  "bind_values",
  "credentials",
  "wallet",
] as const;

export interface ContextAttachment {
  category: ContextCategory;
  name: string;
  content: string;
  provenance?: string;
}

export interface EditorReference {
  editorId: string;
  revision: string;
  text: string;
}

/** One buffer of a multi-part request, such as a package spec or body (protocol 1.2). */
export interface EditorPart extends EditorReference {
  /** Letters, digits, "_" and "-"; unique within the request. */
  part: string;
}

export interface CopilotRequest {
  protocolVersion?: string;
  action: CopilotAction;
  targetReference: string;
  conversationId?: string;
  userMessage?: string;
  databaseVersion?: string;
  schema?: string;
  attachments: ContextAttachment[];
  editor?: EditorReference;
  /** Up to eight buffers edited together. Each needs its own name and editor. */
  parts?: EditorPart[];
  /** Which of the adapter's own users is acting. Never a role. */
  actorReference?: string;
  actorIsDurable?: boolean;
  /** A target the user can open. With Kiwi enabled, allows read-only lookups on it. */
  profileId?: string;
  /** What a kiwi.explain_* action explains: OWNER.NAME or NAME (protocol 1.3). */
  subject?: string;
}

export interface ContextPreview {
  targetReference: string;
  databaseVersion: string;
  schema: string;
  totalBytes: number;
  categories: string[];
  attachments: Array<{
    category: string;
    name: string;
    provenance: string;
    byteLength: number;
    truncated: boolean;
    sha256: string;
  }>;
  excluded: string[];
  notes: string[];
}

export interface ProposalEvent {
  proposalId: string;
  editorId: string;
  baseRevision: string;
  baseHash: string;
  targetReference: string;
  proposedText: string;
  rationale: string;
  appliesToEditorOnly: true;
  note: string;
  /** Present on a multi-part proposal. Then proposedText is "" and parts carry the text. */
  multiPart?: true;
  parts?: ProposalPart[];
}

export interface ProposalPart {
  part: string;
  editorId: string;
  baseRevision: string;
  baseHash: string;
  proposedText: string;
  /** False when the model left this part alone; it is still pinned and checked. */
  changed: boolean;
}

/** Why a Kiwi request stopped early. */
export type KiwiStopReasonKind =
  | "steps"
  | "tool_calls"
  | "tokens"
  | "wall_time"
  | "tool_bytes"
  | "max_output_tokens";

/** done.stopReason joins every reason that applied with commas, e.g. "tool_calls,steps". */
export type KiwiStopReason = string;

/** Kiwi asked for a reviewed read-only catalog lookup (protocol 1.1). */
export interface KiwiToolCall {
  callId: string;
  toolName: string;
  operationId: string;
  parameters: Record<string, unknown>;
  why: string;
}

/** How a lookup ended. Counts only: the rows never reach the stream. */
export interface KiwiToolResult {
  callId: string;
  operationId: string;
  status: "succeeded" | "failed" | "refused" | "invalid";
  rowCount: number | null;
  bytes: number;
  truncated: boolean;
  executionId: string | null;
  errorCode: string;
}

export interface KiwiBudget {
  steps: number;
  maxSteps: number;
  toolCalls: number;
  maxToolCalls: number;
  tokens: number;
  maxTokens: number;
  elapsedSeconds: number;
  maxWallSeconds: number;
  toolBytes: number;
  maxToolBytes: number;
  exhausted: KiwiStopReasonKind[];
}

export type LineageEvidence = "source" | "inferred" | "catalog" | "scheduler";

export interface LineageNode {
  id: string;
  kind: string;
  label: string;
}

export interface LineageEdge {
  source: string;
  target: string;
  relation: string;
  evidence: LineageEvidence;
  detail?: string;
}

/** The lineage an explain action read; every edge carries its evidence (protocol 1.3). */
export interface KiwiLineage {
  nodes: LineageNode[];
  edges: LineageEdge[];
  mermaid: string;
  notes: string[];
}

export type CopilotEvent =
  | { event: "start"; data: { requestId: string; protocolVersion: string; action: string; provider: string; model: string; isFixtureProvider: boolean; contextPreview: ContextPreview } }
  | { event: "delta"; data: { text: string } }
  | { event: "proposal"; data: ProposalEvent }
  | { event: "usage"; data: { provider: string; model: string; promptTokens: number | null; completionTokens: number | null; stopReason: string } }
  | { event: "plan_step"; data: { step: number; maxSteps: number } }
  | { event: "tool_call"; data: KiwiToolCall }
  | { event: "tool_result"; data: KiwiToolResult }
  | { event: "budget"; data: KiwiBudget }
  | { event: "lineage"; data: KiwiLineage }
  | { event: "done"; data: { requestId: string; outcome: string; latencyMs: number; partial?: boolean; stopReason?: KiwiStopReason } }
  | { event: "error"; data: HarnessErrorBody };

export interface ApplyCheckResult {
  proposalId: string;
  canApply: boolean;
  reasons: string[];
  proposedText?: string;
  /** Multi-part: every part's new text, only when all of them may be applied. */
  parts?: Array<{ part: string; editorId: string; proposedText: string }>;
  /** Multi-part: why each part was refused. Any entry refuses the whole proposal. */
  partReasons?: Record<string, string[]>;
  executesDatabaseOperations: false;
  note?: string;
}

export interface ApplyCheckPart {
  part: string;
  editorId: string;
  revision: string;
  currentText: string;
}

export interface IntegrationCapabilities {
  /** The name to show users. Absent from harnesses older than Kiwi. */
  assistant?: string;
  protocolVersion: string;
  supportedProtocolMajors: number[];
  enabled: boolean;
  actions: CopilotAction[];
  providerReady: boolean;
  providerDetail: string;
  provider: string;
  model: string;
  isFixtureProvider: boolean;
  limits: { maxContextBytes: number; userDailyRequests: number };
  contextCategories: string[];
  executesDatabaseOperations: false;
  requiredAdapterVersion: string;
  /** Read-only lookups. Absent from harnesses before protocol 1.1. */
  kiwi?: {
    enabled: boolean;
    readOnlyLookups: true;
    needsProfileId: boolean;
    tools: string[];
    limits: {
      maxSteps: number;
      maxToolCalls: number;
      maxTokens: number;
      maxWallSeconds: number;
      maxRowsPerTool: number;
      maxResultBytes: number;
      maxToolBytes: number;
    };
    /** The team standards file (protocol 1.2). error is set when it could not be used. */
    standards?: { configured: boolean; keys: string[]; error?: string };
    multiPartProposals?: boolean;
  };
}

// -- client ---------------------------------------------------------------------------

export interface ClientOptions {
  baseUrl: string;
  /** Returns the bearer token. Tokens are never persisted by this client. */
  token: () => string | null;
  fetchImpl?: typeof fetch;
}

export class HarnessClient {
  private readonly baseUrl: string;
  private readonly token: () => string | null;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.token = options.token;
    this.fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    const token = this.token();
    return {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...extra,
    };
  }

  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method,
      headers: this.headers(),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    const payload = text ? JSON.parse(text) : null;
    if (!response.ok) {
      const error = (payload?.error ?? {
        code: "http_error",
        message: `${response.status} ${response.statusText}`,
      }) as HarnessErrorBody;
      throw new HarnessError(response.status, error);
    }
    return payload as T;
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>("GET", path);
  }

  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>("POST", path, body);
  }

  del<T>(path: string): Promise<T> {
    return this.request<T>("DELETE", path);
  }

  systemInfo(): Promise<SystemInfo> {
    return this.get<SystemInfo>("/api/v1/system/info");
  }

  targets(): Promise<Target[]> {
    return this.get<Target[]>("/api/v1/targets");
  }

  testConnection(profileId: string) {
    return this.post<{ profileId: string; connected: boolean; identity: TargetIdentity | null; capabilities: Capability[]; diagnostics: string[]; error: HarnessErrorBody | null }>(
      `/api/v1/targets/${profileId}/test`,
    );
  }

  openWorksheet(profileId: string) {
    return this.post<{ session: WorksheetSession; note: string }>("/api/v1/worksheets", {
      profileId,
    });
  }

  execute(
    sessionId: string,
    statement: string,
    binds: Array<{ name: string; value: unknown }> = [],
    options: { maxRows?: number; deadlineSeconds?: number; idempotencyKey?: string } = {},
  ) {
    return this.post<{ outcome: ExecutionOutcome; policy: PolicyDecision; session: WorksheetSession }>(
      `/api/v1/worksheets/${sessionId}/execute`,
      { statement, binds, ...options },
    );
  }

  commit(sessionId: string) {
    return this.post<{ committed: boolean; hadOpenTransaction: boolean }>(
      `/api/v1/worksheets/${sessionId}/commit`,
    );
  }

  rollback(sessionId: string) {
    return this.post<{ rolledBack: boolean; hadOpenTransaction: boolean }>(
      `/api/v1/worksheets/${sessionId}/rollback`,
    );
  }

  cancel(sessionId: string) {
    return this.post<{ delivered: boolean; reason: string | null }>(
      `/api/v1/worksheets/${sessionId}/cancel`,
    );
  }

  /**
   * Stream a copilot request. Aborting the returned controller propagates the
   * cancellation; a partially delivered request is never replayed automatically.
   */
  async *copilot(
    request: CopilotRequest,
    signal?: AbortSignal,
  ): AsyncGenerator<CopilotEvent, void, unknown> {
    const response = await this.fetchImpl(`${this.baseUrl}/api/v1/copilot/requests`, {
      method: "POST",
      headers: this.headers({ Accept: "text/event-stream" }),
      body: JSON.stringify({ protocolVersion: PROTOCOL_VERSION, ...request }),
      signal,
    });
    if (!response.ok || !response.body) {
      const text = await response.text();
      const payload = text ? JSON.parse(text) : null;
      throw new HarnessError(response.status, payload?.error ?? {
        code: "http_error",
        message: `${response.status} ${response.statusText}`,
      });
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const chunk = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const parsed = parseSseChunk(chunk);
        if (parsed) yield parsed;
        boundary = buffer.indexOf("\n\n");
      }
    }
  }

  applyCheck(
    proposalId: string,
    body: {
      /** Single-part proposals. */
      editorId?: string;
      revision?: string;
      currentText?: string;
      /** Multi-part proposals: every part's current buffer. */
      parts?: ApplyCheckPart[];
      targetReference: string;
      actorReference?: string;
    },
  ): Promise<ApplyCheckResult> {
    return this.post<ApplyCheckResult>(
      `/api/v1/copilot/proposals/${proposalId}/apply-check`,
      body,
    );
  }
}

export function parseSseChunk(chunk: string): CopilotEvent | null {
  let event = "";
  let data = "";
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7).trim();
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!event || !data) return null;
  return { event, data: JSON.parse(data) } as CopilotEvent;
}

/** True when an adapter can speak to this harness at all. */
export function protocolCompatible(theirVersion: string): boolean {
  const major = Number.parseInt(theirVersion.split(".")[0] ?? "", 10);
  return Number.isInteger(major) && major === SUPPORTED_PROTOCOL_MAJOR;
}
