import { useEffect, useRef, useState } from "react";
import { HarnessError, api } from "../api";
import type { RunbookRun, RunbookSpec, Target } from "../api";
import { ResultGrid } from "../components/ResultGrid";

type PreviewResponse = Awaited<ReturnType<typeof api.previewRunbook>>;

/** What a preview was asked about. A confirmation is only good for exactly this. */
interface RunbookRequest {
  runbookId: string;
  targetId: string;
  parameters: Record<string, string>;
}

function requestKey(request: RunbookRequest): string {
  const parameters = Object.entries(request.parameters).sort(([a], [b]) => a.localeCompare(b));
  return JSON.stringify([request.runbookId, request.targetId, parameters]);
}

/**
 * A mutating runbook shows exactly what it will do and to which target, and needs an
 * explicit confirmation. Its verification evidence is shown with the result.
 *
 * The confirmation is bound to the preview the user read. Changing a parameter, the
 * runbook or the target discards the preview, so "Confirm and run" can never send
 * values other than the ones on screen, and a preview that answers after such a change
 * is dropped. While a run is in flight the form is locked instead: its result reports
 * something that happened, and it is shown with the values it ran with. Only a switch
 * of target drops it, as the worksheet does; the execution record is in History.
 */
export function RunbooksView({ target }: { target: Target }) {
  const [runbooks, setRunbooks] = useState<RunbookSpec[]>([]);
  const [selected, setSelected] = useState<RunbookSpec | null>(null);
  const [parameters, setParameters] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<{
    request: RunbookRequest;
    response: PreviewResponse;
  } | null>(null);
  const [run, setRun] = useState<{ request: RunbookRequest; result: RunbookRun } | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bumped whenever what a preview was asked about changes. A preview request
  // remembers the generation it started in and is dropped if it has moved on.
  const previewGeneration = useRef(0);
  // Bumped on a target switch, the one change a run in flight cannot lock out.
  const targetGeneration = useRef(0);

  useEffect(() => {
    api
      .runbooks()
      .then((response) => setRunbooks(response.runbooks))
      .catch((cause) => setError(String(cause)));
  }, []);

  const discardPreview = () => {
    previewGeneration.current += 1;
    setPreview(null);
  };

  useEffect(() => {
    // A preview or a result for the previous target says nothing about this one.
    targetGeneration.current += 1;
    discardPreview();
    setRun(null);
    setRunning(false);
    setError(null);
  }, [target.id]);

  const fail = (cause: unknown) =>
    setError(cause instanceof HarnessError ? cause.message : String(cause));

  const choose = (runbook: RunbookSpec) => {
    discardPreview();
    setSelected(runbook);
    setRun(null);
    setError(null);
    setParameters(
      Object.fromEntries(runbook.parameters.map((parameter) => [parameter.name, parameter.example])),
    );
  };

  const current: RunbookRequest | null = selected
    ? { runbookId: selected.id, targetId: target.id, parameters }
    : null;
  // Belt and braces with discardPreview(): even if some path forgot to discard it, a
  // preview of different values never enables the confirmation or stays on screen.
  const shownPreview =
    preview !== null && current !== null && requestKey(preview.request) === requestKey(current)
      ? preview
      : null;
  const confirmable = shownPreview !== null && shownPreview.response.ready;

  const previewNow = async () => {
    if (!current) return;
    const request = current;
    const started = previewGeneration.current;
    setError(null);
    try {
      const response = await api.previewRunbook(
        request.runbookId,
        request.targetId,
        request.parameters,
      );
      if (previewGeneration.current !== started) return;
      setPreview({ request, response });
    } catch (cause) {
      if (previewGeneration.current !== started) return;
      fail(cause);
    }
  };

  const runNow = async (runbook: RunbookSpec) => {
    // A mutating run sends what the preview showed, never the live form.
    const request = runbook.mutating ? (confirmable ? shownPreview.request : null) : current;
    if (!request) return;
    const started = targetGeneration.current;
    setError(null);
    setRunning(true);
    try {
      const result = await api.runRunbook(
        request.runbookId,
        request.targetId,
        request.parameters,
        runbook.mutating,
      );
      if (targetGeneration.current !== started) return;
      setRun({ request, result });
    } catch (cause) {
      if (targetGeneration.current !== started) return;
      setRun(null);
      fail(cause);
    } finally {
      if (targetGeneration.current === started) setRunning(false);
    }
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
            <button disabled={running} onClick={() => choose(runbook)}>
              Select
            </button>
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
                disabled={running}
                onChange={(event) => {
                  discardPreview();
                  setParameters({ ...parameters, [parameter.name]: event.target.value });
                }}
              />
            </label>
          ))}

          <div className="toolbar">
            <button disabled={running} onClick={previewNow}>
              Preview
            </button>
            <button
              className="primary"
              disabled={running || (selected.mutating && !confirmable)}
              onClick={() => runNow(selected)}
            >
              {running ? "Running..." : selected.mutating ? "Confirm and run" : "Run"}
            </button>
          </div>

          {shownPreview && (
            <div className={shownPreview.response.willChangeDatabase ? "notice warn" : "notice"}>
              {shownPreview.response.willChangeDatabase
                ? "This will change the database."
                : "This is read only."}{" "}
              Target {shownPreview.response.target.name} (
              {shownPreview.response.target.environment}). Parameters:{" "}
              <code>{JSON.stringify(shownPreview.response.parameters)}</code>
              {shownPreview.response.missingParameters.length > 0 && (
                <div>Missing: {shownPreview.response.missingParameters.join(", ")}</div>
              )}
            </div>
          )}
          {selected.mutating && !shownPreview && (
            <p className="muted">Preview these exact values before you can confirm.</p>
          )}
        </section>
      )}

      {run && <RunResult run={run.result} parameters={run.request.parameters} />}
    </>
  );
}

function RunResult({
  run,
  parameters,
}: {
  run: RunbookRun;
  parameters: Record<string, string>;
}) {
  const verification = run.verification as {
    operationId?: string;
    collectedAt?: string;
    columns?: string[];
    rows?: unknown[][];
    observed?: boolean;
    verified?: boolean;
    requirement?: string;
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
      <p className="meta">
        Ran with <code>{JSON.stringify(parameters)}</code>
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
          {verification.requirement && (
            <p className="meta">Requires: {verification.requirement}</p>
          )}
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
