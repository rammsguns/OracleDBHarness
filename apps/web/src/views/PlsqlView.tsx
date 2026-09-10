import { useState } from "react";
import { HarnessError, api } from "../api";
import type { ObjectDetail, Target } from "../api";
import { SqlEditor } from "../components/SqlEditor";

const STARTER = `CREATE OR REPLACE PACKAGE BODY employee_report AS
  FUNCTION headcount(p_department_id IN NUMBER) RETURN NUMBER IS
    l_count NUMBER;
  BEGIN
    SELECT COUNT(*) INTO l_count FROM employees WHERE department_id = p_department_id;
    RETURN l_count;
  END headcount;
END employee_report;`;

/**
 * Load stored source, edit it, compile it, and see line-level errors. Compilation
 * runs in its own session, so it never commits work pending in your worksheet.
 */
export function PlsqlView({
  target,
  onAsk,
}: {
  target: Target;
  onAsk: (seed: string) => void;
}) {
  const [owner, setOwner] = useState(target.defaultSchema || "");
  const [name, setName] = useState("EMPLOYEE_REPORT");
  const [type, setType] = useState("PACKAGE BODY");
  const [source, setSource] = useState(STARTER);
  const [loaded, setLoaded] = useState<string | null>(null);
  const [detail, setDetail] = useState<ObjectDetail | null>(null);
  const [result, setResult] = useState<
    | {
        compiled: boolean;
        errors: Array<{ line: number; position: number; text: string }>;
        note: string;
      }
    | null
  >(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setError(null);
    try {
      const next = await api.objectDetail(target.id, owner, name, type);
      setDetail(next);
      const rows = next.panels.source?.rows ?? [];
      const text = rows.map((row) => String(row[1])).join("\n");
      const header = `CREATE OR REPLACE ${text}`;
      setSource(text.startsWith("CREATE") ? text : header);
      setLoaded(text);
    } catch (cause) {
      setError(cause instanceof HarnessError ? cause.message : String(cause));
    }
  };

  const compile = async () => {
    setBusy(true);
    setError(null);
    try {
      const response = await api.compile(target.id, source);
      setResult(response);
      if (response.compiled) await load();
    } catch (cause) {
      setResult(null);
      setError(cause instanceof HarnessError ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const storedErrors = detail?.panels.errors;

  return (
    <>
      <div className="toolbar">
        <label>
          Schema <input value={owner} onChange={(event) => setOwner(event.target.value.toUpperCase())} />
        </label>
        <label>
          Object <input value={name} onChange={(event) => setName(event.target.value.toUpperCase())} />
        </label>
        <label>
          Type{" "}
          <select value={type} onChange={(event) => setType(event.target.value)}>
            {["PACKAGE", "PACKAGE BODY", "PROCEDURE", "FUNCTION", "TRIGGER", "TYPE"].map((kind) => (
              <option key={kind}>{kind}</option>
            ))}
          </select>
        </label>
        <button onClick={load}>Load source</button>
        <button className="primary" onClick={compile} disabled={busy}>
          {busy ? "Compiling..." : "Compile"}
        </button>
        <button onClick={() => onAsk(source)}>Ask the copilot about this unit</button>
      </div>

      {storedErrors && !storedErrors.available && (
        <div className="notice error">
          Stored compiler errors could not be read: {storedErrors.error?.message}
        </div>
      )}

      <SqlEditor value={source} onChange={setSource} height={340} />

      {loaded !== null && loaded !== source && (
        <div className="notice">
          The editor differs from the stored source. Compiling replaces the stored unit.
        </div>
      )}

      {error && <div className="notice error">{error}</div>}

      {result && (
        <section className="card">
          <h3>
            Compilation{" "}
            {result.compiled ? (
              <span className="badge ok">valid</span>
            ) : (
              <span className="badge danger">invalid</span>
            )}
          </h3>
          <p className="meta">{result.note}</p>
          {result.errors.length > 0 ? (
            <ul>
              {result.errors.map((compilerError, index) => (
                <li key={index} className="mono">
                  line {compilerError.line}, col {compilerError.position}: {compilerError.text}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No compiler errors were reported.</p>
          )}
        </section>
      )}

      {storedErrors?.available && storedErrors.rows.length > 0 && (
        <section className="card">
          <h3>Errors currently stored for {name}</h3>
          <p className="meta">
            {storedErrors.operationId} - {new Date(storedErrors.collectedAt).toLocaleTimeString()}
          </p>
          <ul>
            {storedErrors.rows.map((row, index) => (
              <li key={index} className="mono">
                line {String(row[0])}, col {String(row[1])}: {String(row[2])}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
