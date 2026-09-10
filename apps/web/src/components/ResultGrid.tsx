/** A bounded result grid. Truncation and null are shown, never quietly hidden. */

interface Props {
  columns: string[];
  rows: unknown[][];
  truncated?: boolean;
  truncationReason?: string | null;
  emptyMessage?: string;
}

export function ResultGrid({
  columns,
  rows,
  truncated,
  truncationReason,
  emptyMessage = "No rows.",
}: Props) {
  if (columns.length === 0) return <p className="muted">{emptyMessage}</p>;
  return (
    <>
      <div className="scroll">
        <table className="result">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((value, cellIndex) => (
                  <td key={cellIndex} className={value === null ? "null" : undefined}>
                    {renderCell(value)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ marginTop: 6 }}>
        {rows.length} row{rows.length === 1 ? "" : "s"}
        {truncated ? ` - truncated. ${truncationReason ?? ""}` : ""}
      </p>
    </>
  );
}

function renderCell(value: unknown): string {
  if (value === null || value === undefined) return "(null)";
  if (typeof value === "object") {
    const lob = value as { kind?: string; preview?: string; byteLength?: number };
    if (lob.kind === "lob" || lob.kind === "raw") {
      return `${lob.preview ?? ""}... (${lob.byteLength ?? 0} bytes, preview only)`;
    }
    return JSON.stringify(value);
  }
  return String(value);
}
