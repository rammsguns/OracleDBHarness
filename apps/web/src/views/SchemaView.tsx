import { useEffect, useState } from "react";
import { HarnessError, api } from "../api";
import type { ObjectDetail, ObjectPage, PanelResult, Target } from "../api";
import { PanelCard } from "../components/PanelCard";
import { ResultGrid } from "../components/ResultGrid";

const PAGE_SIZE = 50;

const OBJECT_TYPES = [
  "",
  "TABLE",
  "VIEW",
  "INDEX",
  "SEQUENCE",
  "SYNONYM",
  "PACKAGE",
  "PACKAGE BODY",
  "PROCEDURE",
  "FUNCTION",
  "TRIGGER",
];

/** Paged object lists; a restricted account simply sees fewer rows. */
export function SchemaView({
  target,
  onAsk,
}: {
  target: Target;
  onAsk: (seed: string) => void;
}) {
  const [schemas, setSchemas] = useState<PanelResult | null>(null);
  const [owner, setOwner] = useState(target.defaultSchema || "");
  const [objectType, setObjectType] = useState("");
  const [nameFilter, setNameFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<ObjectPage | null>(null);
  const [detail, setDetail] = useState<ObjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.schemas(target.id).then(setSchemas).catch((cause) => setError(String(cause)));
  }, [target.id]);

  useEffect(() => {
    if (!owner) return;
    api
      .objects(target.id, {
        owner,
        objectType: objectType || undefined,
        nameFilter: nameFilter || undefined,
        offset,
        limit: PAGE_SIZE,
      })
      .then((next) => {
        setPage(next);
        setError(null);
      })
      .catch((cause) =>
        setError(cause instanceof HarnessError ? cause.message : String(cause)),
      );
  }, [target.id, owner, objectType, nameFilter, offset]);

  const open = async (row: unknown[]) => {
    const [rowOwner, name, type] = [String(row[0]), String(row[1]), String(row[2])];
    try {
      setDetail(await api.objectDetail(target.id, rowOwner, name, type));
    } catch (cause) {
      setError(cause instanceof HarnessError ? cause.message : String(cause));
    }
  };

  return (
    <>
      {schemas && !schemas.available && (
        <div className="notice error">
          Schemas could not be listed: {schemas.error?.message}
        </div>
      )}

      <div className="toolbar">
        <label>
          Schema{" "}
          <input
            list="schema-list"
            value={owner}
            onChange={(event) => {
              setOwner(event.target.value.toUpperCase());
              setOffset(0);
            }}
          />
        </label>
        <datalist id="schema-list">
          {schemas?.rows.map((row) => (
            <option key={String(row[0])} value={String(row[0])} />
          ))}
        </datalist>
        <label>
          Type{" "}
          <select
            value={objectType}
            onChange={(event) => {
              setObjectType(event.target.value);
              setOffset(0);
            }}
          >
            {OBJECT_TYPES.map((type) => (
              <option key={type} value={type}>
                {type || "all types"}
              </option>
            ))}
          </select>
        </label>
        <label>
          Name contains{" "}
          <input
            value={nameFilter}
            onChange={(event) => {
              setNameFilter(event.target.value);
              setOffset(0);
            }}
          />
        </label>
      </div>

      {error && <div className="notice error">{error}</div>}

      {page && (
        <section className="card">
          <h3>Objects in {page.owner}</h3>
          <p className="meta">
            rows {page.offset + 1}-{page.offset + page.rows.length}
            {page.hasMore ? " (more available)" : ""} - collected{" "}
            {new Date(page.collectedAt).toLocaleTimeString()}
          </p>
          <div className="scroll">
            <table className="result">
              <thead>
                <tr>
                  {page.columns.map((column) => (
                    <th key={column}>{column}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {page.rows.map((row, index) => (
                  <tr
                    key={index}
                    style={{ cursor: "pointer" }}
                    onClick={() => open(row)}
                    title="Open object detail"
                  >
                    {row.map((value, cellIndex) => (
                      <td key={cellIndex}>{value === null ? "(null)" : String(value)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Previous
            </button>
            <button disabled={!page.hasMore} onClick={() => setOffset(offset + PAGE_SIZE)}>
              Next
            </button>
          </div>
        </section>
      )}

      {detail && (
        <>
          <div className="toolbar">
            <strong>
              {detail.owner}.{detail.objectName}
            </strong>
            <span className="badge">{detail.objectType}</span>
            <button onClick={() => setDetail(null)}>Close</button>
            {detail.panels.source?.available && (
              <button
                onClick={() =>
                  onAsk(
                    detail.panels.source.rows.map((row) => String(row[1])).join("\n"),
                  )
                }
              >
                Explain this source
              </button>
            )}
          </div>
          <div className="grid-2">
            {Object.entries(detail.panels).map(([name, panel]) => (
              <PanelCard key={name} panel={panel} title={name} />
            ))}
          </div>
        </>
      )}

      {schemas?.available && !detail && (
        <section className="card">
          <h3>Accessible schemas</h3>
          <p className="meta">
            {schemas.operationId} - {new Date(schemas.collectedAt).toLocaleTimeString()}
          </p>
          <ResultGrid columns={schemas.columns} rows={schemas.rows} />
        </section>
      )}
    </>
  );
}
