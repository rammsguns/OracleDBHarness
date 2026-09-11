import { Fragment, useCallback, useEffect, useState } from "react";
import {
  HarnessError,
  api,
  oidcSignInConfig,
  setToken,
  signInWithAccessToken,
  signInWithDevToken,
} from "./api";
import type { Me, SystemInfo, Target } from "./api";
import { SignInError, authorizationUrl, completeSignIn, isCallback } from "./oidc";
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
  // The copilot is opened for one target, usually seeded with that target's source.
  const [copilot, setCopilot] = useState<{ targetId: string | null; seed: string } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<number | null>(null);
  const [signInNotice, setSignInNotice] = useState<string | null>(null);

  useEffect(() => {
    api.systemInfo().then(setInfo).catch(() => setInfo(null));
  }, []);

  const signedIn = useCallback((next: Me, expiresIn: number | null) => {
    setSignInNotice(null);
    setExpiresAt(expiresIn === null ? null : Date.now() + expiresIn * 1000);
    setMe(next);
  }, []);

  const signOut = useCallback((notice: string | null) => {
    setToken(null);
    setMe(null);
    setExpiresAt(null);
    setTargets([]);
    setSelected(null);
    setSignInNotice(notice);
  }, []);

  useEffect(() => {
    // Every call would start failing with authentication_required at this point.
    // Returning to the sign-in screen says why instead.
    if (expiresAt === null) return;
    const timer = window.setTimeout(
      () => signOut("Your session expired. Sign in again to continue."),
      // setTimeout fires at once for anything past 2^31 - 1 ms.
      Math.min(Math.max(0, expiresAt - Date.now()), 2 ** 31 - 1),
    );
    return () => window.clearTimeout(timer);
  }, [expiresAt, signOut]);

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

  const selectedId = selected?.id ?? null;
  useEffect(() => {
    // A drawer opened for another target stays closed if the user switches back.
    setCopilot((current) => (current && current.targetId !== selectedId ? null : current));
  }, [selectedId]);

  if (!me) {
    return <SignIn info={info} notice={signInNotice} onSignedIn={signedIn} />;
  }

  const askCopilot = (seed: string) => setCopilot({ targetId: selectedId, seed });
  // Checked at render, not left to the effect above: the effect runs after a render,
  // and that render would show the drawer against the new target with the old
  // target's source still in it.
  const copilotHere = copilot?.targetId === selectedId ? copilot : null;

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
          onClick={() =>
            signOut(
              info?.authMode === "oidc"
                ? "Signed out of the console. You are still signed in to the identity " +
                    "provider, so signing in again may not ask for your password."
                : null,
            )
          }
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
        {selected && (
          // Keyed by target, so a switch mounts fresh views: nothing typed or loaded
          // for one database is left in place to be sent to another, and a response
          // still in flight lands in the discarded instance rather than this one.
          <Fragment key={selected.id}>
            {view === "schema" && <SchemaView target={selected} onAsk={askCopilot} />}
            {view === "worksheet" && <WorksheetView target={selected} onAsk={askCopilot} />}
            {view === "plsql" && <PlsqlView target={selected} onAsk={askCopilot} />}
            {view === "tuning" && <TuningView target={selected} onAsk={askCopilot} />}
            {view === "dba" && <DbaView target={selected} />}
            {view === "runbooks" && <RunbooksView target={selected} />}
          </Fragment>
        )}
        {view === "history" && <HistoryView target={selected} />}
        {!selected && view !== "targets" && view !== "history" && (
          <p className="muted">You have no target grants. An administrator has to grant access.</p>
        )}
      </main>

      {copilotHere && (
        <CopilotDrawer
          key={selectedId ?? "none"}
          target={selected}
          seed={copilotHere.seed}
          onClose={() => setCopilot(null)}
        />
      )}
    </div>
  );
}

function describe(cause: unknown): string {
  return cause instanceof HarnessError || cause instanceof SignInError
    ? cause.message
    : String(cause);
}

function SignIn({
  info,
  notice,
  onSignedIn,
}: {
  info: SystemInfo | null;
  notice: string | null;
  onSignedIn: (me: Me, expiresIn: number | null) => void;
}) {
  const [subject, setSubject] = useState("dev@example.internal");
  const [roles, setRoles] = useState("developer");
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [busy, setBusy] = useState(() => isCallback());

  const devMode = info?.authMode === "dev";
  const oidcMode = info?.authMode === "oidc";

  useEffect(() => {
    if (!isCallback()) return;
    const search = window.location.search;
    // Redeemed or not, the code has no business staying in the address bar or
    // history. Doing it before the first await is also what keeps the single-use
    // code from being redeemed twice when StrictMode runs this effect again.
    window.history.replaceState(null, "", "/");
    void (async () => {
      try {
        const config = await oidcSignInConfig();
        const tokens = await completeSignIn(config, search);
        onSignedIn(await signInWithAccessToken(tokens.accessToken), tokens.expiresIn);
      } catch (cause) {
        setError(describe(cause));
      } finally {
        setBusy(false);
      }
    })();
  }, [onSignedIn]);

  const startProviderSignIn = async () => {
    setError(null);
    setBusy(true);
    try {
      window.location.assign(await authorizationUrl(await oidcSignInConfig()));
    } catch (cause) {
      setError(describe(cause));
      setBusy(false);
    }
  };

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

      {notice && <div className="notice">{notice}</div>}

      {oidcMode ? (
        <div className="stack">
          <p>This deployment signs you in through its identity provider.</p>
          <button className="primary" disabled={busy} onClick={startProviderSignIn}>
            {busy ? "Signing in..." : "Sign in with your identity provider"}
          </button>
        </div>
      ) : devMode ? (
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
              onSignedIn(result.me, result.expiresIn);
            } catch (cause) {
              setError(describe(cause));
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
        info && <p>This deployment reports an identity mode the console does not know.</p>
      )}

      {warning && <div className="notice warn">{warning}</div>}
      {error && <div className="notice error">{error}</div>}
    </div>
  );
}
