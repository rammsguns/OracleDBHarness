import type {
  ExecutionOutcome,
  PolicyDecision,
  Target,
  TargetIdentity,
  WorksheetSession,
} from "@contracts";

/** Fixtures for the console tests. Shapes match the published contract exactly. */

const identity: TargetIdentity = {
  databaseName: "ORCL",
  instanceName: "ORCL",
  hostName: "db.internal",
  version: "19.3.0.0.0",
  versionFull: "Oracle Database 19c",
  isCdb: false,
  containerName: null,
  currentSchema: "HR",
  currentUser: "HR",
  sessionId: 42,
  serialNumber: 1,
};

export function makeTarget(overrides: Partial<Target> = {}): Target {
  return {
    id: "prf_dev",
    name: "development",
    environment: "development",
    host: "db.internal",
    port: 1521,
    serviceName: "ORCLPDB1",
    username: "HR",
    defaultSchema: "HR",
    worksheetsEnabled: true,
    mutatingRunbooksEnabled: false,
    permissions: ["read", "worksheet"],
    identity,
    identityCheckedAt: "2026-01-01T00:00:00+00:00",
    capabilities: [],
    ...overrides,
  };
}

export function makeSession(overrides: Partial<WorksheetSession> = {}): WorksheetSession {
  return {
    sessionId: "ws_dev",
    targetId: "prf_dev",
    actorId: "usr_1",
    createdAt: "2026-01-01T00:00:00+00:00",
    lastUsedAt: "2026-01-01T00:00:00+00:00",
    expiresAt: "2026-01-01T00:05:00+00:00",
    transactionOpen: false,
    busy: false,
    currentExecutionId: null,
    closed: false,
    closeReason: null,
    identity,
    ...overrides,
  };
}

export function makeOutcome(overrides: Partial<ExecutionOutcome> = {}): ExecutionOutcome {
  return {
    executionId: "exe_1",
    state: "succeeded",
    statementKind: "query",
    resultSet: null,
    rowsAffected: null,
    dbmsOutput: [],
    dbmsOutputTruncated: false,
    compilerErrors: [],
    error: null,
    elapsedMs: 4,
    databaseElapsedMs: 2,
    transactionOpen: false,
    warnings: [],
    verification: {},
    ...overrides,
  };
}

export function makePolicy(overrides: Partial<PolicyDecision> = {}): PolicyDecision {
  return {
    allowed: true,
    reason: "allowed",
    permission: "worksheet",
    risk: "read",
    missingCapabilities: [],
    notes: [],
    ...overrides,
  };
}

/** A promise plus the handles to settle it, for holding a request open mid-test. */
export function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
