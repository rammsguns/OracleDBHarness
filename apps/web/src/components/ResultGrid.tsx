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
    const lob = value as {
      kind?: string;
      preview?: string;
      byteLength?: number;
      charLength?: number;
    };
    if (lob.kind === "lob" || lob.kind === "raw") {
      // A CLOB is measured in characters, a BLOB or RAW in bytes.
      const size =
        lob.charLength !== undefined
          ? `${lob.charLength} characters`
          : `${lob.byteLength ?? 0} bytes`;
      return `${lob.preview ?? ""}... (${size}, preview only)`;
    }
    if (lob.kind === "vector") {
      // A sparse vector lists only its stored elements, each with its index.
      const vector = value as {
        format?: string | null;
        dimensions?: number;
        length?: number;
        sparse?: boolean;
        indices?: number[];
        values?: number[];
        truncated?: boolean;
      };
      const values = vector.values ?? [];
      const shown =
        lob.preview ??
        (vector.sparse
          ? `{${values.map((v, i) => `${vector.indices?.[i]}: ${v}`).join(", ")}}`
          : `[${values.join(", ")}]`);
      const more = vector.truncated ? ", preview only" : "";
      if (!vector.format) {
        // An older Oracle Client: only the element count is known.
        return `${shown} (vector, ${vector.length ?? 0} elements${more})`;
      }
      const kind = `${vector.sparse ? "sparse " : ""}${vector.format}`;
      return `${shown} (${kind} ${vector.dimensions ?? 0} dimensions${more})`;
    }
    return JSON.stringify(value);
  }
  return String(value);
}
