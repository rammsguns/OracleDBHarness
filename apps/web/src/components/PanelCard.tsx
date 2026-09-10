import type { PanelResult } from "../api";
import { ResultGrid } from "./ResultGrid";

/**
 * One diagnostic panel. An unavailable panel shows why it could not be collected;
 * it never renders as an empty, healthy panel.
 */
export function PanelCard({ panel, title }: { panel: PanelResult; title?: string }) {
  return (
    <section className="card">
      <h3>
        {title ?? panel.title}{" "}
        {panel.available ? (
          <span className="badge ok">collected</span>
        ) : (
          <span className="badge danger">unavailable</span>
        )}
      </h3>
      <p className="meta">
        {panel.operationId} - {new Date(panel.collectedAt).toLocaleString()}
      </p>
      {panel.available ? (
        <ResultGrid
          columns={panel.columns}
          rows={panel.rows}
          truncated={panel.truncated}
          emptyMessage="Collected successfully; there is nothing to report."
        />
      ) : (
        <div className="notice error">
          <strong>{panel.error?.code}</strong>: {panel.error?.message}
        </div>
      )}
    </section>
  );
}
