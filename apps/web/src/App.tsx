import { useCallback, useEffect, useState } from "react";
import { HarnessError, api, setToken, signInWithDevToken } from "./api";
import type { Me, SystemInfo, Target } from "./api";
import { CopilotDrawer } from "./views/CopilotDrawer";
import { DbaView } from "./views/DbaView";
import { HistoryView } from "./views/HistoryView";
import { PlsqlView } from "./views/PlsqlView";
import { RunbooksView } from "./views/RunbooksView";
import { SchemaView } from "./views/SchemaView";
import { TargetsView } from "./views/TargetsView";
import { TuningView } from "./views/TuningView";
import { WorksheetView } from "./views/WorksheetView";

type ViewId =
  | "targets"
  | "schema"
  | "worksheet"
  | "plsql"
  | "tuning"
  | "dba"
  | "runbooks"
  | "history";

const VIEWS: Array<{ id: ViewId; label: string }> = [
  { id: "targets", label: "Connections" },
  { id: "schema", label: "Schema explorer" },
  { id: "worksheet", label: "SQL worksheet" },
  { id: "plsql", label: "PL/SQL workspace" },
  { id: "tuning", label: "Tuning workbench" },
  { id: "dba", label: "DBA overview" },
  { id: "runbooks", label: "Runbooks" },
  { id: "history", label: "History" },
];

export function App() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [targets, setTargets] = useState<Target[]>([]);
  const [selected, setSelected] = useState<Target | null>(null);
  const [view, setView] = useState<ViewId>("targets");
  const [copilotOpen, setCopilotOpen] = useState(false);
  const [copilotSeed, setCopilotSeed] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.systemInfo().then(setInfo).catch(() => setInfo(null));
  }, []);

  const refreshTargets = useCallback(async () => {
    try {
      const next = await api.targets();
      setTargets(next);
      setSelected((current) =>
        current ? next.find((t) => t.id === current.id) ?? next[0] ?? null : next[0] ?? null,
      );
    } catch (cause) {
      setError(cause instanceof HarnessError ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    if (me) void refreshTargets();
  }, [me, refreshTargets]);

  if (!me) {
    return <SignIn info={info} onSignedIn={setMe} />;
  }

  const askCopilot = (seed: string) => {
    setCopilotSeed(seed);
    setCopilotOpen(true);
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>OracleDBHarness</h1>
        <p className="who">
          {me.displayName || me.subject}
          <br />
          {me.roles.join(", ") || "no roles"}
        </p>
        <nav>
          {VIEWS.map((entry) => (
            <button
              key={entry.id}
              className={view === entry.id ? "active" : ""}
              onClick={() => setView(entry.id)}
            >
              {entry.label}
            </button>
          ))}
        </nav>
        <div className="spacer" />
        {info?.copilotEnabled ? (
          <button onClick={() => askCopilot("")}>Ask the copilot</button>
        ) : (
          <p className="muted" style={{ fontSize: 12 }}>
            The copilot is disabled on this harness.
          </p>
        )}
        <button
          onClick={() => {
            setToken(null);
            setMe(null);
            setTargets([]);
            setSelected(null);
          }}
        >
          Sign out
        </button>
      </aside>

      <main className="main">
        <header>
          <h2>{VIEWS.find((entry) => entry.id === view)?.label}</h2>
          <span className="sub">
            {selected ? (
              <>
                {selected.name} <span className="badge">{selected.environment}</span>{" "}
                {selected.identity?.version ? `Oracle ${selected.identity.version}` : "not probed"}
              </>
            ) : (
              "no target selected"
            )}
          </span>
        </header>

        {targets.length > 1 && (
          <div className="toolbar">
            <label htmlFor="target">Target</label>
            <select
              id="target"
              value={selected?.id ?? ""}
              onChange={(event) =>
                setSelected(targets.find((t) => t.id === event.target.value) ?? null)
              }
            >
              {targets.map((target) => (
                <option key={target.id} value={target.id}>
                  {target.name} ({target.environment})
                </option>
              ))}
            </select>
          </div>
        )}

        {error && <div className="notice error">{error}</div>}
        {info?.warnings.map((warning) => (
          <div className="notice warn" key={warning}>
            {warning}
          </div>
        ))}

        {view === "targets" && (
          <TargetsView targets={targets} onRefresh={refreshTargets} onSelect={setSelected} />
        )}
        {view === "schema" && selected && <SchemaView target={selected} onAsk={askCopilot} />}
        {view === "worksheet" && selected && (
          <WorksheetView target={selected} onAsk={askCopilot} />
        )}
        {view === "plsql" && selected && <PlsqlView target={selected} onAsk={askCopilot} />}
        {view === "tuning" && selected && <TuningView target={selected} onAsk={askCopilot} />}
        {view === "dba" && selected && <DbaView target={selected} />}
        {view === "runbooks" && selected && <RunbooksView target={selected} />}
        {view === "history" && <HistoryView target={selected} />}
        {!selected && view !== "targets" && view !== "history" && (
          <p className="muted">You have no target grants. An administrator has to grant access.</p>
        )}
      </main>

      {copilotOpen && (
        <CopilotDrawer
          target={selected}
          seed={copilotSeed}
          onClose={() => setCopilotOpen(false)}
        />
      )}
    </div>
  );
}

function SignIn({
  info,
  onSignedIn,
}: {
  info: SystemInfo | null;
  onSignedIn: (me: Me) => void;
}) {
  const [subject, setSubject] = useState("dev@example.internal");
  const [roles, setRoles] = useState("developer");
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);

  const devMode = info?.authMode === "dev";

  return (
    <div className="signin card">
      <h3>Sign in</h3>
      {info ? (
        <p className="meta">
          {info.environment} - identity mode {info.authMode} - Oracle backend{" "}
          {info.oracleBackend}
        </p>
      ) : (
        <p className="meta">Contacting the harness...</p>
      )}

      {devMode ? (
        <form
          className="stack"
          onSubmit={async (event) => {
            event.preventDefault();
            setError(null);
            try {
              const result = await signInWithDevToken(
                subject,
                roles.split(",").map((role) => role.trim()).filter(Boolean),
              );
              setWarning(result.warning);
              onSignedIn(result.me);
            } catch (cause) {
              setError(cause instanceof HarnessError ? cause.message : String(cause));
            }
          }}
        >
          <label>
            Subject
            <input value={subject} onChange={(event) => setSubject(event.target.value)} />
          </label>
          <label>
            Requested roles
            <input value={roles} onChange={(event) => setRoles(event.target.value)} />
          </label>
          <p className="muted" style={{ fontSize: 12 }}>
            Roles come from the harness account record, not from this field. Asking for a
            role you do not hold changes nothing.
          </p>
          <button className="primary" type="submit">
            Sign in
          </button>
        </form>
      ) : (
        <p>
          This deployment authenticates through its identity provider. Sign in there and
          return with an access token.
        </p>
      )}

      {warning && <div className="notice warn">{warning}</div>}
      {error && <div className="notice error">{error}</div>}
    </div>
  );
}
