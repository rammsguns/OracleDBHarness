import { useEffect, useState } from "react";
import { HarnessError, api } from "../api";
import type { RunbookRun, RunbookSpec, Target } from "../api";
import { ResultGrid } from "../components/ResultGrid";

/**
 * A mutating runbook shows exactly what it will do and to which target, and needs an
 * explicit confirmation. Its verification evidence is shown with the result.
 */
export function RunbooksView({ target }: { target: Target }) {
  const [runbooks, setRunbooks] = useState<RunbookSpec[]>([]);
  const [selected, setSelected] = useState<RunbookSpec | null>(null);
  const [parameters, setParameters] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof api.previewRunbook>> | null>(
    null,
  );
  const [run, setRun] = useState<RunbookRun | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .runbooks()
      .then((response) => setRunbooks(response.runbooks))
      .catch((cause) => setError(String(cause)));
  }, []);

  const fail = (cause: unknown) =>
    setError(cause instanceof HarnessError ? cause.message : String(cause));

  const choose = (runbook: RunbookSpec) => {
    setSelected(runbook);
    setPreview(null);
    setRun(null);
    setError(null);
    setParameters(
      Object.fromEntries(runbook.parameters.map((parameter) => [parameter.name, parameter.example])),
    );
  };

  return (
    <>
      {error && <div className="notice error">{error}</div>}

      <div className="grid-2">
        {runbooks.map((runbook) => (
          <section className="card" key={runbook.id}>
            <h3>
              {runbook.title}{" "}
              <span className={runbook.mutating ? "badge danger" : "badge ok"}>
                {runbook.risk}
              </span>
            </h3>
            <p className="meta">{runbook.id}</p>
            <p>{runbook.description}</p>
            <button onClick={() => choose(runbook)}>Select</button>
          </section>
        ))}
      </div>

      {selected && (
        <section className="card">
          <h3>{selected.title}</h3>
          <p className="meta">
            target {target.name} ({target.environment})
          </p>

          {selected.parameters.map((parameter) => (
            <label key={parameter.name} style={{ display: "block", marginBottom: 8 }}>
              {parameter.label}{" "}
              <input
                value={parameters[parameter.name] ?? ""}
                onChange={(event) =>
                  setParameters({ ...parameters, [parameter.name]: event.target.value })
                }
              />
            </label>
          ))}

          <div className="toolbar">
            <button
              onClick={async () => {
                setError(null);
                try {
                  setPreview(await api.previewRunbook(selected.id, target.id, parameters));
                } catch (cause) {
                  fail(cause);
                }
              }}
            >
              Preview
            </button>
            <button
              className="primary"
              disabled={selected.mutating && !preview?.ready}
              onClick={async () => {
                setError(null);
                try {
                  setRun(
                    await api.runRunbook(selected.id, target.id, parameters, selected.mutating),
                  );
                } catch (cause) {
                  setRun(null);
                  fail(cause);
                }
              }}
            >
              {selected.mutating ? "Confirm and run" : "Run"}
            </button>
          </div>

          {preview && (
            <div className={preview.willChangeDatabase ? "notice warn" : "notice"}>
              {preview.willChangeDatabase
                ? "This will change the database."
                : "This is read only."}{" "}
              Target {preview.target.name} ({preview.target.environment}). Parameters:{" "}
              <code>{JSON.stringify(preview.parameters)}</code>
              {preview.missingParameters.length > 0 && (
                <div>Missing: {preview.missingParameters.join(", ")}</div>
              )}
            </div>
          )}
        </section>
      )}

      {run && <RunResult run={run} />}
    </>
  );
}

function RunResult({ run }: { run: RunbookRun }) {
  const verification = run.verification as {
    operationId?: string;
    collectedAt?: string;
    columns?: string[];
    rows?: unknown[][];
    observed?: boolean;
    note?: string;
    error?: { code: string; message: string };
  };

  return (
    <section className="card">
      <h3>
        {run.runbook.title}{" "}
        <span className={run.outcome === "succeeded" ? "badge ok" : "badge warn"}>
          {run.outcome}
        </span>
      </h3>
      <p className="meta">
        {new Date(run.startedAt).toLocaleTimeString()} - {new Date(run.finishedAt).toLocaleTimeString()}
      </p>

      {run.steps.map((step, index) => {
        const typed = step as {
          operationId: string;
          title: string;
          state?: string;
          available?: boolean;
          columns?: string[];
          rows?: unknown[][];
          error?: { code: string; message: string };
          collectedAt?: string;
        };
        return (
          <div key={index} style={{ marginBottom: 12 }}>
            <strong>{typed.title}</strong>{" "}
            <span className={typed.available === false ? "badge danger" : "badge ok"}>
              {typed.state ?? "collected"}
            </span>
            {typed.error && (
              <div className="notice error">
                <strong>{typed.error.code}</strong>: {typed.error.message}
              </div>
            )}
            {typed.columns && typed.rows && typed.available !== false && (
              <ResultGrid columns={typed.columns} rows={typed.rows} />
            )}
          </div>
        );
      })}

      {verification.operationId && (
        <>
          <h4>Verification</h4>
          <p className="meta">
            {verification.operationId} -{" "}
            {verification.collectedAt
              ? new Date(verification.collectedAt).toLocaleTimeString()
              : ""}
          </p>
          {verification.error ? (
            <div className="notice error">{verification.error.message}</div>
          ) : (
            <ResultGrid
              columns={verification.columns ?? []}
              rows={verification.rows ?? []}
              emptyMessage="The verification query returned nothing, so the change is unconfirmed."
            />
          )}
          {verification.note && <div className="notice warn">{verification.note}</div>}
        </>
      )}
    </section>
  );
}
