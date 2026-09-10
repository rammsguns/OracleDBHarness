import { useEffect, useState } from "react";
import { HarnessError, api } from "../api";
import type { ExecutionRecord, Target } from "../api";

/**
 * Your own execution history. Statements are identified by fingerprint; raw SQL is
 * not retained unless the deployment enabled statement retention.
 */
export function HistoryView({ target }: { target: Target | null }) {
  const [records, setRecords] = useState<ExecutionRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [onlyThisTarget, setOnlyThisTarget] = useState(true);

  useEffect(() => {
    api
      .executions(onlyThisTarget && target ? target.id : undefined)
      .then(setRecords)
      .catch((cause) =>
        setError(cause instanceof HarnessError ? cause.message : String(cause)),
      );
  }, [target, onlyThisTarget]);

  return (
    <>
      <div className="toolbar">
        <label>
          <input
            type="checkbox"
            checked={onlyThisTarget}
            onChange={(event) => setOnlyThisTarget(event.target.checked)}
          />{" "}
          only the selected target
        </label>
      </div>

      {error && <div className="notice error">{error}</div>}

      <div className="scroll">
        <table className="result">
          <thead>
            <tr>
              <th>started</th>
              <th>operation</th>
              <th>kind</th>
              <th>risk</th>
              <th>state</th>
              <th>rows</th>
              <th>elapsed</th>
              <th>fingerprint</th>
            </tr>
          </thead>
          <tbody>
            {records.map((record) => (
              <tr key={record.id}>
                <td>{new Date(record.startedAt).toLocaleTimeString()}</td>
                <td>{record.operationId}</td>
                <td>{record.statementKind}</td>
                <td>{record.riskClass}</td>
                <td>
                  <span
                    className={
                      record.state === "succeeded"
                        ? "badge ok"
                        : record.state === "outcome_unknown"
                          ? "badge danger"
                          : "badge warn"
                    }
                  >
                    {record.state}
                  </span>
                  {record.errorCode ? ` ${record.errorCode}` : ""}
                </td>
                <td>
                  {record.rowsReturned ?? record.rowsAffected ?? ""}
                  {record.truncated ? " (truncated)" : ""}
                </td>
                <td>
                  {record.elapsedMs ?? ""} ms
                  {record.databaseElapsedMs !== null
                    ? ` (${record.databaseElapsedMs} in db)`
                    : ""}
                </td>
                <td title={record.statementFingerprint}>
                  {record.statementFingerprint.slice(0, 12)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {records.length === 0 && <p className="muted">Nothing recorded yet.</p>}
    </>
  );
}
