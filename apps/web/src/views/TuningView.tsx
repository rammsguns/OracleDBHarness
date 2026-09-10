import { useState } from "react";
import { HarnessError, api } from "../api";
import type { MeasuredRows, Target } from "../api";
import { ResultGrid } from "../components/ResultGrid";
import { SqlEditor } from "../components/SqlEditor";

interface Estimated {
  statementId: string;
  kind: string;
  columns: string[];
  rows: unknown[][];
  note: string;
  explainState?: string;
  error?: { code: string; message: string } | null;
}

interface CursorDetail {
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
}

/**
 * Estimates and measurements are shown in separate cards and labelled. They are
 * never combined into one number.
 */
export function TuningView({
  target,
  onAsk,
}: {
  target: Target;
  onAsk: (seed: string) => void;
}) {
  const [statement, setStatement] = useState(
    "SELECT order_id, SUM(quantity * unit_price)\n  FROM order_lines\n WHERE product_id = 3\n GROUP BY order_id",
  );
  const [estimated, setEstimated] = useState<Estimated | null>(null);
  const [filter, setFilter] = useState("order_lines");
  const [cursors, setCursors] = useState<MeasuredRows | null>(null);
  const [detail, setDetail] = useState<CursorDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fail = (cause: unknown) =>
    setError(cause instanceof HarnessError ? cause.message : String(cause));

  return (
    <>
      {error && <div className="notice error">{error}</div>}

      <section className="card">
        <h3>Estimated plan</h3>
        <p className="meta">
          EXPLAIN PLAN asks the optimizer what it would do. It does not execute the
          statement and produces no measurements.
        </p>
        <SqlEditor value={statement} onChange={setStatement} height={160} />
        <div className="toolbar">
          <button
            className="primary"
            onClick={async () => {
              setError(null);
              try {
                const result = await api.explain(target.id, statement);
                // An explain that failed produced no plan. Showing an empty grid
                // would read as "no rows", which is a different thing entirely.
                setEstimated(result.error ? null : result);
                if (result.error) setError(result.error.message);
              } catch (cause) {
                setEstimated(null);
                fail(cause);
              }
            }}
          >
            Explain
          </button>
          {estimated && (
            <button
              onClick={() =>
                onAsk(
                  `Explain this plan.\n\nStatement:\n${statement}\n\nPlan rows:\n` +
                    estimated.rows.map((row) => row.join(" | ")).join("\n"),
                )
              }
            >
              Ask the copilot about this plan
            </button>
          )}
        </div>
        {estimated && (
          <>
            <p>
              <span className="badge warn">estimated</span> {estimated.note}
            </p>
            <ResultGrid columns={estimated.columns} rows={estimated.rows} />
          </>
        )}
      </section>

      <section className="card">
        <h3>Cached cursors</h3>
        <p className="meta">
          Counters Oracle recorded for executions that already happened. Reading them
          does not re-run anything.
        </p>
        <div className="toolbar">
          <input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="text in the statement"
          />
          <button
            onClick={async () => {
              setError(null);
              try {
                setCursors(await api.cursors(target.id, filter));
              } catch (cause) {
                setCursors(null);
                fail(cause);
              }
            }}
          >
            Search
          </button>
        </div>
        {cursors && !cursors.available && (
          <div className="notice error">
            <strong>{cursors.error?.code}</strong>: {cursors.error?.message}
          </div>
        )}
        {cursors?.available && (
          <div className="scroll">
            <table className="result">
              <thead>
                <tr>
                  {(cursors.columns ?? []).map((column) => (
                    <th key={column}>{column}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(cursors.rows ?? []).map((row, index) => (
                  <tr
                    key={index}
                    style={{ cursor: "pointer" }}
                    onClick={async () => {
                      setError(null);
                      try {
                        setDetail(
                          await api.cursorDetail(target.id, String(row[0]), Number(row[1] ?? 0)),
                        );
                      } catch (cause) {
                        fail(cause);
                      }
                    }}
                  >
                    {row.map((value, cellIndex) => (
                      <td key={cellIndex}>{value === null ? "(null)" : String(value)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {detail && (
        <>
          <section className="card">
            <h3>
              {detail.sqlId} child {detail.childNumber}{" "}
              {detail.statistics.available ? (
                <span className="badge ok">measured</span>
              ) : (
                <span className="badge danger">unavailable</span>
              )}
            </h3>
            {detail.statistics.available ? (
              <ResultGrid
                columns={detail.statistics.columns ?? []}
                rows={detail.statistics.rows ?? []}
              />
            ) : (
              <div className="notice error">
                <strong>{detail.statistics.error?.code}</strong>:{" "}
                {detail.statistics.error?.message}
              </div>
            )}
          </section>
          <section className="card">
            <h3>
              Plan actually used{" "}
              {detail.plan.available ? (
                <span className="badge ok">available</span>
              ) : (
                <span className="badge danger">unavailable</span>
              )}
            </h3>
            {detail.plan.available ? (
              <>
                <p className="meta">{detail.plan.note}</p>
                <ResultGrid
                  columns={detail.plan.columns ?? []}
                  rows={detail.plan.rows ?? []}
                />
              </>
            ) : (
              <div className="notice error">
                <strong>{detail.plan.error?.code}</strong>: {detail.plan.error?.message}
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}
