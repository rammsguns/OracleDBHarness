import { useEffect, useState } from "react";
import { HarnessError, api } from "../api";
import type { DbaOverview, Target } from "../api";
import { PanelCard } from "../components/PanelCard";

const PANEL_TITLES: Record<string, string> = {
  sessions: "Sessions",
  blocking: "Blocked sessions and blockers",
  tablespaces: "Tablespace usage",
  invalidObjects: "Invalid objects",
  schedulerJobs: "Scheduler jobs",
  schedulerFailures: "Failed scheduler runs",
};

export function DbaView({ target }: { target: Target }) {
  const [overview, setOverview] = useState<DbaOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setOverview(await api.dbaOverview(target.id));
    } catch (cause) {
      setOverview(null);
      setError(cause instanceof HarnessError ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.id]);

  return (
    <>
      <div className="toolbar">
        <button onClick={load} disabled={loading}>
          {loading ? "Collecting..." : "Refresh"}
        </button>
        {overview && (
          <span className="muted">
            collected {new Date(overview.collectedAt).toLocaleTimeString()}
          </span>
        )}
      </div>

      {error && <div className="notice error">{error}</div>}

      {overview && (
        <>
          {overview.unavailablePanels.length > 0 && (
            <div className="notice warn">
              {overview.unavailablePanels.length} panel(s) could not be collected:{" "}
              {overview.unavailablePanels.join(", ")}. Each one says why below; none of
              them is showing an empty result as if it were healthy.
            </div>
          )}
          <div className="notice">{overview.note}</div>

          <section className="card">
            <h3>Connection health</h3>
            <p className="meta">
              identity last proved{" "}
              {overview.connectionHealth.identityCheckedAt
                ? new Date(overview.connectionHealth.identityCheckedAt).toLocaleString()
                : "never"}
            </p>
            <div className="row">
              {overview.connectionHealth.capabilities.map((capability) => (
                <span
                  key={capability.capability}
                  className={capability.available ? "badge ok" : "badge danger"}
                  title={capability.detail}
                >
                  {capability.capability}
                </span>
              ))}
            </div>
          </section>

          <div className="grid-2">
            {Object.entries(overview.panels).map(([name, panel]) => (
              <PanelCard key={name} panel={panel} title={PANEL_TITLES[name] ?? name} />
            ))}
          </div>
        </>
      )}
    </>
  );
}
