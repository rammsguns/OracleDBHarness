/**
 * Console-side API access.
 *
 * The access token is kept in memory for the life of the tab and is never written to
 * localStorage: a token in storage outlives the tab and is readable by anything else
 * running on the origin.
 */

import { HarnessClient, HarnessError } from "@contracts";
import type { OidcSignInConfig } from "./oidc";
import type {
  ApplyCheckResult,
  ContextAttachment,
  CopilotEvent,
  CopilotRequest,
  ExecutionOutcome,
  PanelResult,
  PolicyDecision,
  SystemInfo,
  Target,
  WorksheetSession,
} from "@contracts";

export { HarnessError };
export type {
  ApplyCheckResult,
  ContextAttachment,
  CopilotEvent,
  CopilotRequest,
  ExecutionOutcome,
  PanelResult,
  PolicyDecision,
  SystemInfo,
  Target,
  WorksheetSession,
};

let accessToken: string | null = null;

export function setToken(token: string | null): void {
  accessToken = token;
}

export function hasToken(): boolean {
  return accessToken !== null;
}

export const client = new HarnessClient({
  baseUrl: "",
  token: () => accessToken,
});

export interface Me {
  subject: string;
  displayName: string;
  roles: string[];
  userId: string | null;
  targets: Array<{
    profileId: string;
    name: string;
    environment: string;
    permissions: string[];
  }>;
}

/**
 * Adopt an access token, keeping it only if the harness accepts it.
 *
 * An identity provider can authenticate someone the harness has not registered; the
 * token is dropped again rather than left in place for every later call to fail on.
 */
export async function signInWithAccessToken(token: string): Promise<Me> {
  setToken(token);
  try {
    return await client.get<Me>("/api/v1/auth/me");
  } catch (error) {
    setToken(null);
    throw error;
  }
}

export async function signInWithDevToken(
  subject: string,
  roles: string[],
): Promise<{ me: Me; warning: string; expiresIn: number }> {
  const issued = await client.post<{
    accessToken: string;
    expiresInSeconds: number;
    warning: string;
  }>("/api/v1/auth/dev-token", { subject, roles });
  return {
    me: await signInWithAccessToken(issued.accessToken),
    warning: issued.warning,
    expiresIn: issued.expiresInSeconds,
  };
}

export function oidcSignInConfig(): Promise<OidcSignInConfig> {
  return client.get<OidcSignInConfig>("/api/v1/auth/oidc");
}

export interface ObjectPage {
  owner: string;
  objectType: string | null;
  offset: number;
  limit: number;
  columns: string[];
  rows: unknown[][];
  hasMore: boolean;
  collectedAt: string;
}

export interface ObjectDetail {
  owner: string;
  objectName: string;
  objectType: string;
  panels: Record<string, PanelResult>;
}

export interface DbaOverview {
  profileId: string;
  collectedAt: string;
  connectionHealth: {
    identityCheckedAt: string | null;
    identity: Record<string, unknown> | null;
    capabilities: Array<{ capability: string; available: boolean; detail: string }>;
  };
  panels: Record<string, PanelResult>;
  unavailablePanels: string[];
  note: string;
}

export interface RunbookSpec {
  id: string;
  title: string;
  description: string;
  risk: string;
  mutating: boolean;
  requiresConfirmation: boolean;
  parameters: Array<{ name: string; label: string; required: boolean; example: string }>;
  steps: string[];
  verificationOperationId: string;
}

export interface RunbookRun {
  runbook: RunbookSpec;
  startedAt: string;
  finishedAt: string;
  outcome: string;
  steps: Array<Record<string, unknown>>;
  verification: Record<string, unknown>;
}

/**
 * Rows Oracle recorded, or the reason there are none. A query that failed carries no
 * columns or rows, so a caller has to check `available` before rendering a grid: an
 * empty grid would read as "nothing recorded" rather than "we could not read it".
 */
export interface MeasuredRows {
  kind: string;
  available: boolean;
  columns?: string[];
  rows?: unknown[][];
  error?: { code: string; message: string };
}

export interface ExecutionRecord {
  id: string;
  operationId: string;
  profileId: string;
  statementKind: string;
  riskClass: string;
  state: string;
  policyDecision: string;
  policyReason: string;
  statementFingerprint: string;
  rowsReturned: number | null;
  rowsAffected: number | null;
  truncated: boolean;
  elapsedMs: number | null;
  databaseElapsedMs: number | null;
  errorCode: string;
  errorMessage: string;
  startedAt: string;
  finishedAt: string | null;
  verification: Record<string, unknown>;
}

export const api = {
  systemInfo: () => client.systemInfo(),
  targets: () => client.targets(),
  testConnection: (profileId: string) => client.testConnection(profileId),
  schemas: (profileId: string) => client.get<PanelResult>(`/api/v1/targets/${profileId}/schemas`),
  objects: (profileId: string, params: Record<string, string | number | undefined>) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") query.set(key, String(value));
    }
    return client.get<ObjectPage>(`/api/v1/targets/${profileId}/objects?${query}`);
  },
  objectDetail: (profileId: string, owner: string, name: string, type: string) =>
    client.get<ObjectDetail>(
      `/api/v1/targets/${profileId}/objects/detail?owner=${encodeURIComponent(owner)}` +
        `&objectName=${encodeURIComponent(name)}&objectType=${encodeURIComponent(type)}`,
    ),
  dbaOverview: (profileId: string) =>
    client.get<DbaOverview>(`/api/v1/targets/${profileId}/dba/overview`),
  openWorksheet: (profileId: string) => client.openWorksheet(profileId),
  worksheets: () => client.get<{ sessions: WorksheetSession[] }>("/api/v1/worksheets"),
  closeWorksheet: (sessionId: string) => client.del(`/api/v1/worksheets/${sessionId}`),
  execute: client.execute.bind(client),
  commit: client.commit.bind(client),
  rollback: client.rollback.bind(client),
  cancel: client.cancel.bind(client),
  compile: (profileId: string, source: string) =>
    client.post<{
      outcome: ExecutionOutcome;
      policy: PolicyDecision;
      compiled: boolean;
      errors: Array<{ line: number; position: number; text: string }>;
      note: string;
    }>("/api/v1/plsql/compile", { profileId, source }),
  explain: (profileId: string, statement: string) =>
    client.post<{
      statementId: string;
      kind: string;
      columns: string[];
      rows: unknown[][];
      note: string;
      explainState?: string;
      error?: { code: string; message: string } | null;
    }>("/api/v1/tuning/explain", { profileId, statement }),
  cursors: (profileId: string, textFilter: string) =>
    client.get<MeasuredRows & { note: string }>(
      `/api/v1/tuning/${profileId}/cursors?textFilter=${encodeURIComponent(textFilter)}`,
    ),
  cursorDetail: (profileId: string, sqlId: string, childNumber = 0) =>
    client.get<{
      sqlId: string;
      childNumber: number;
      statistics: MeasuredRows;
      plan: {
        available: boolean;
        columns?: string[];
        rows?: unknown[][];
        note?: string;
        error?: { code: string; message: string };
      };
    }>(`/api/v1/tuning/${profileId}/cursors/${sqlId}?childNumber=${childNumber}`),
  runbooks: () => client.get<{ runbooks: RunbookSpec[]; note: string }>("/api/v1/runbooks"),
  previewRunbook: (id: string, profileId: string, parameters: Record<string, string>) =>
    client.post<{
      runbook: RunbookSpec;
      parameters: Record<string, string>;
      missingParameters: string[];
      willChangeDatabase: boolean;
      ready: boolean;
      target: { name: string; environment: string };
    }>(`/api/v1/runbooks/${id}/preview`, { profileId, parameters }),
  runRunbook: (
    id: string,
    profileId: string,
    parameters: Record<string, string>,
    confirm: boolean,
  ) => client.post<RunbookRun>(`/api/v1/runbooks/${id}/run`, { profileId, parameters, confirm }),
  executions: (profileId?: string) =>
    client.get<ExecutionRecord[]>(
      `/api/v1/executions${profileId ? `?profileId=${profileId}` : ""}`,
    ),
  contextPreview: (body: Record<string, unknown>) =>
    client.post<{
      totalBytes: number;
      categories: string[];
      excluded: string[];
      attachments: Array<{ category: string; name: string; byteLength: number }>;
    }>("/api/v1/copilot/context/preview", body),
  copilot: (request: CopilotRequest, signal?: AbortSignal) => client.copilot(request, signal),
  applyCheck: client.applyCheck.bind(client),
};
