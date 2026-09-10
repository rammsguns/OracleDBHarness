import { useEffect, useState } from "react";
import { HarnessError, api } from "../api";
import type { ExecutionOutcome, PolicyDecision, Target, WorksheetSession } from "../api";
import { ResultGrid } from "../components/ResultGrid";
import { SqlEditor } from "../components/SqlEditor";

interface Bind {
  name: string;
  value: string;
}

/**
 * Everything on screen that belongs to one target, kept together and stamped with the
 * target it came from.
 *
 * The target can be switched while a request is in flight, and the response for the
 * target the user left must never be applied to the one they are now looking at: a
 * session is a live Oracle connection with a transaction on it, and showing it under
 * the wrong heading invites a commit against the wrong database. Every update is
 * stamped, and the render reads only the stamp that matches the selected target, so a
 * late response is inert rather than merely unlikely to be seen.
 */
interface Panel {
  targetId: string;
  session: WorksheetSession | null;
  outcome: ExecutionOutcome | null;
  policy: PolicyDecision | null;
  error: string | null;
  unknownCommit: string | null;
  running: boolean;
}

function emptyPanel(targetId: string): Panel {
  return {
    targetId,
    session: null,
    outcome: null,
    policy: null,
    error: null,
    unknownCommit: null,
    running: false,
  };
}

function describe(cause: unknown): string {
  return cause instanceof HarnessError ? cause.message : String(cause);
}

const WRONG_TARGET =
  "That worksheet session belongs to a different target. Open a session on the " +
  "selected target before running anything on it.";

function retired(session: WorksheetSession): string {
  return (
    `The worksheet session was closed: ${session.closeReason ?? "unknown reason"}. ` +
    "Any uncommitted work was rolled back. Open a new session to carry on."
  );
}

/**
 * One selected statement or one complete PL/SQL block per run. The session owns its
 * Oracle transaction; the transaction state is always visible.
 */
export function WorksheetView({
  target,
  onAsk,
}: {
  target: Target;
  onAsk: (seed: string) => void;
}) {
  const [panel, setPanel] = useState<Panel>(() => emptyPanel(target.id));
  const [statement, setStatement] = useState(
    "SELECT last_name, salary\n  FROM employees\n WHERE department_id = :dept\n ORDER BY salary DESC",
  );
  const [binds, setBinds] = useState<Bind[]>([{ name: "dept", value: "20" }]);
  const [maxRows, setMaxRows] = useState(100);

  // Read only what belongs to the selected target. State written by a request that
  // started before a switch is still in `panel`, but it is never shown or acted on.
  const current = panel.targetId === target.id ? panel : emptyPanel(target.id);
  const { session, outcome, policy, error, unknownCommit, running } = current;

  /** Apply an update only if the target it was started for is still selected. */
  const applyFor = (targetId: string, update: (panel: Panel) => Panel) => {
    setPanel((previous) => (previous.targetId === targetId ? update(previous) : previous));
  };

  useEffect(() => {
    let live = true;
    setPanel(emptyPanel(target.id));
    // Navigating between targets does not close the session behind it. A worksheet
    // may hold an open transaction, and closing it would roll that work back without
    // being asked, so the session is left running on the server and re-discovered
    // here whenever its target is selected again.
    void (async () => {
      try {
        const { sessions } = await api.worksheets();
        if (!live) return;
        const existing = sessions.find((s) => s.targetId === target.id && !s.closed) ?? null;
        applyFor(target.id, (previous) => ({ ...previous, session: existing }));
      } catch (cause) {
        if (!live) return;
        applyFor(target.id, (previous) => ({ ...previous, error: describe(cause) }));
      }
    })();
    return () => {
      live = false;
    };
  }, [target.id]);

  const refreshSession = async (targetId: string, sessionId: string) => {
    try {
      const { sessions } = await api.worksheets();
      applyFor(targetId, (previous) => ({
        ...previous,
        session: sessions.find((s) => s.sessionId === sessionId) ?? null,
      }));
    } catch (cause) {
      applyFor(targetId, (previous) => ({ ...previous, error: describe(cause) }));
    }
  };

  const open = async () => {
    const startedFor = target.id;
    applyFor(startedFor, (previous) => ({ ...previous, error: null }));
    try {
      const opened = await api.openWorksheet(startedFor);
      applyFor(startedFor, (previous) => ({ ...previous, session: opened.session }));
    } catch (cause) {
      applyFor(startedFor, (previous) => ({ ...previous, error: describe(cause) }));
    }
  };

  const run = async () => {
    if (!session) return;
    const startedFor = target.id;
    if (session.targetId !== startedFor) {
      applyFor(startedFor, (previous) => ({ ...previous, session: null, error: WRONG_TARGET }));
      return;
    }
    applyFor(startedFor, (previous) => ({ ...previous, running: true, error: null }));
    try {
      const response = await api.execute(
        session.sessionId,
        statement,
        binds
          .filter((bind) => bind.name.trim())
          .map((bind) => ({ name: bind.name.trim(), value: coerce(bind.value) })),
        { maxRows },
      );
      applyFor(startedFor, (previous) => ({
        ...previous,
        outcome: response.outcome,
        policy: response.policy,
        // A session the server retired while the statement ran -- a cancellation it
        // did not obey, a lost connection, access withdrawn mid-flight -- comes back
        // closed. Keeping it would leave the toolbar offering Commit, Roll back,
        // Cancel and Close on a session that no longer exists, and every one of those
        // fails until the view is reloaded. Drop it and say why; the outcome of the
        // statement itself is still shown.
        session: response.session.closed ? null : response.session,
        error: response.session.closed ? retired(response.session) : previous.error,
      }));
    } catch (cause) {
      // The server answers `session_expired` when the session is no longer there to
      // run anything: reaped while idle, closed from another tab, or retired before
      // the statement was sent. Holding on to it leaves Run pointed at a session that
      // cannot work and hides Open Session, so every retry fails the same way until
      // the view is reloaded. Drop it and let the toolbar offer a new one.
      const gone = cause instanceof HarnessError && cause.code === "session_expired";
      applyFor(startedFor, (previous) => ({
        ...previous,
        outcome: null,
        session: gone ? null : previous.session,
        error: describe(cause),
      }));
    } finally {
      applyFor(startedFor, (previous) => ({ ...previous, running: false }));
    }
  };

  const transactional = async (action: "commit" | "rollback" | "cancel") => {
    if (!session) return;
    const startedFor = target.id;
    const sessionId = session.sessionId;
    if (session.targetId !== startedFor) {
      applyFor(startedFor, (previous) => ({ ...previous, session: null, error: WRONG_TARGET }));
      return;
    }
    try {
      applyFor(startedFor, (previous) => ({ ...previous, unknownCommit: null }));
      if (action === "commit") await api.commit(sessionId);
      if (action === "rollback") await api.rollback(sessionId);
      if (action === "cancel") {
        const result = await api.cancel(sessionId);
        applyFor(startedFor, (previous) => ({
          ...previous,
          error: result.delivered
            ? "Cancellation was delivered. Whether the statement stopped before doing its work is reported in the outcome."
            : result.reason,
        }));
      }
    } catch (cause) {
      if (cause instanceof HarnessError && cause.code === "outcome_unknown") {
        // The commit may have reached Oracle. Say so instead of offering a retry:
        // running the statement again could apply the work a second time.
        applyFor(startedFor, (previous) => ({
          ...previous,
          unknownCommit: cause.message,
          error: null,
        }));
      } else {
        applyFor(startedFor, (previous) => ({ ...previous, error: describe(cause) }));
      }
    } finally {
      // Refresh either way. A session that lost its commit has been retired, and the
      // toolbar must stop offering to commit it again.
      await refreshSession(startedFor, sessionId);
    }
  };

  if (!target.worksheetsEnabled) {
    return (
      <div className="notice warn">
        Free-form worksheets are not enabled on {target.name}. Reviewed diagnostics are
        still available from the schema explorer and the DBA overview.
      </div>
    );
  }

  return (
    <>
      <div className="toolbar">
        {session ? (
          <>
            <span className="badge accent">{session.sessionId}</span>
            <span className={session.transactionOpen ? "badge warn" : "badge ok"}>
              {session.transactionOpen ? "transaction open" : "no open transaction"}
            </span>
            <span className="muted">
              expires {new Date(session.expiresAt).toLocaleTimeString()}
            </span>
            <button onClick={() => transactional("commit")} disabled={!session.transactionOpen}>
              Commit
            </button>
            <button onClick={() => transactional("rollback")} disabled={!session.transactionOpen}>
              Roll back
            </button>
            <button onClick={() => transactional("cancel")}>Cancel statement</button>
            <button
              className="danger"
              onClick={async () => {
                const startedFor = target.id;
                await api.closeWorksheet(session.sessionId);
                applyFor(startedFor, (previous) => ({ ...previous, session: null }));
              }}
            >
              Close session
            </button>
          </>
        ) : (
          <button className="primary" onClick={open}>
            Open a worksheet session
          </button>
        )}
      </div>

      <SqlEditor value={statement} onChange={setStatement} height={220} />

      <div className="toolbar">
        <button className="primary" onClick={run} disabled={!session || running}>
          {running ? "Running..." : "Run statement"}
        </button>
        <label>
          Max rows{" "}
          <input
            type="number"
            min={1}
            max={1000}
            value={maxRows}
            style={{ width: 90 }}
            onChange={(event) => setMaxRows(Number(event.target.value))}
          />
        </label>
        <button onClick={() => setBinds([...binds, { name: "", value: "" }])}>Add bind</button>
        <button onClick={() => onAsk(statement)}>Explain this statement</button>
      </div>

      {binds.length > 0 && (
        <div className="toolbar">
          {binds.map((bind, index) => (
            <span className="row" key={index}>
              <input
                placeholder="name"
                value={bind.name}
                style={{ width: 110 }}
                onChange={(event) => {
                  const next = [...binds];
                  next[index] = { ...bind, name: event.target.value };
                  setBinds(next);
                }}
              />
              <input
                placeholder="value"
                value={bind.value}
                style={{ width: 150 }}
                onChange={(event) => {
                  const next = [...binds];
                  next[index] = { ...bind, value: event.target.value };
                  setBinds(next);
                }}
              />
              <button onClick={() => setBinds(binds.filter((_, i) => i !== index))}>x</button>
            </span>
          ))}
        </div>
      )}

      {unknownCommit && (
        <div className="notice error" role="alert">
          <strong>Verification required.</strong> {unknownCommit} The harness has not
          retried it and will not: check the affected rows in the database before you run
          the statement again. The worksheet session has been retired.
        </div>
      )}
      {error && <div className="notice error">{error}</div>}
      {policy?.notes.map((note) => (
        <div className="notice" key={note}>
          {note}
        </div>
      ))}

      {outcome && <OutcomeCard outcome={outcome} />}
    </>
  );
}

export function OutcomeCard({ outcome }: { outcome: ExecutionOutcome }) {
  return (
    <section className="card">
      <h3>
        Result <span className={stateClass(outcome.state)}>{outcome.state}</span>
      </h3>
      <p className="meta">
        {outcome.statementKind} - {outcome.elapsedMs} ms total
        {outcome.databaseElapsedMs !== null
          ? `, ${outcome.databaseElapsedMs} ms in the database`
          : ""}
        {outcome.rowsAffected !== null ? ` - ${outcome.rowsAffected} row(s) affected` : ""}
      </p>

      {outcome.warnings.map((warning) => (
        <div className="notice warn" key={warning}>
          {warning}
        </div>
      ))}
      {outcome.error && (
        <div className="notice error">
          <strong>{outcome.error.code}</strong>: {outcome.error.message}
        </div>
      )}
      {outcome.state === "outcome_unknown" && (
        <div className="notice error">
          The outcome of this write is unknown. Verify in the database before retrying;
          the harness will not retry it for you.
        </div>
      )}

      {outcome.resultSet && (
        <ResultGrid
          columns={outcome.resultSet.columns.map((column) => column.name)}
          rows={outcome.resultSet.rows}
          truncated={outcome.resultSet.truncated}
          truncationReason={outcome.resultSet.truncationReason}
        />
      )}

      {outcome.dbmsOutput.length > 0 && (
        <>
          <h4 style={{ marginBottom: 4 }}>DBMS_OUTPUT</h4>
          <pre className="mono">{outcome.dbmsOutput.join("\n")}</pre>
          {outcome.dbmsOutputTruncated && (
            <p className="muted">Output was truncated at the configured limit.</p>
          )}
        </>
      )}

      {outcome.compilerErrors.length > 0 && (
        <>
          <h4 style={{ marginBottom: 4 }}>Compiler errors</h4>
          <ul>
            {outcome.compilerErrors.map((compilerError, index) => (
              <li key={index} className="mono">
                line {compilerError.line}, col {compilerError.position}: {compilerError.text}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function stateClass(state: string): string {
  if (state === "succeeded") return "badge ok";
  if (state === "outcome_unknown") return "badge danger";
  if (state === "failed" || state === "cancelled") return "badge warn";
  return "badge";
}

function coerce(value: string): unknown {
  if (value === "") return null;
  const asNumber = Number(value);
  return Number.isNaN(asNumber) ? value : asNumber;
}
