import { useState } from "react";
import { HarnessError, api } from "../api";
import type { Target } from "../api";

/**
 * Registered connections. Identity is what the database said about itself when it
 * was last probed, not what the profile claims.
 */
export function TargetsView({
  targets,
  onRefresh,
  onSelect,
}: {
  targets: Target[];
  onRefresh: () => void;
  onSelect: (target: Target) => void;
}) {
  const [testing, setTesting] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, string[]>>({});

  const test = async (target: Target) => {
    setTesting(target.id);
    try {
      const outcome = await api.testConnection(target.id);
      setResult((current) => ({
        ...current,
        [target.id]: outcome.connected
          ? outcome.diagnostics.length > 0
            ? outcome.diagnostics
            : ["Connected. Every probed capability is available."]
          : [outcome.error?.message ?? "Could not connect."],
      }));
      onRefresh();
    } catch (cause) {
      setResult((current) => ({
        ...current,
        [target.id]: [cause instanceof HarnessError ? cause.message : String(cause)],
      }));
    } finally {
      setTesting(null);
    }
  };

  if (targets.length === 0) {
    return (
      <p className="muted">
        You have no target grants. An administrator registers a connection and grants you
        access to it; administrators do not inherit access themselves.
      </p>
    );
  }

  return (
    <div className="grid-2">
      {targets.map((target) => (
        <section className="card" key={target.id}>
          <h3>
            {target.name}{" "}
            <span
              className={
                target.environment === "production" ? "badge danger" : "badge accent"
              }
            >
              {target.environment}
            </span>
          </h3>
          <p className="meta">
            {target.username}@{target.host}:{target.port}/{target.serviceName}
          </p>

          <div className="row" style={{ marginBottom: 8 }}>
            {target.permissions.map((permission) => (
              <span className="badge" key={permission}>
                {permission}
              </span>
            ))}
            {!target.worksheetsEnabled && (
              <span className="badge warn">worksheets off</span>
            )}
            {target.environment === "production" && (
              <span className="badge warn">observation only</span>
            )}
          </div>

          {target.identity ? (
            <p className="mono" style={{ margin: "6px 0" }}>
              {target.identity.databaseName} - Oracle {target.identity.version}
              {target.identity.containerName ? ` - ${target.identity.containerName}` : ""}
              <br />
              current schema {target.identity.currentSchema || "unknown"} as{" "}
              {target.identity.currentUser || "unknown"}
            </p>
          ) : (
            <p className="muted">Identity has not been proved yet. Run a connection test.</p>
          )}

          <details>
            <summary>
              Capabilities ({target.capabilities.filter((c) => c.available).length}/
              {target.capabilities.length} available)
            </summary>
            <ul style={{ paddingLeft: 18 }}>
              {target.capabilities.map((capability) => (
                <li key={capability.capability}>
                  <span className={capability.available ? "badge ok" : "badge danger"}>
                    {capability.available ? "yes" : "no"}
                  </span>{" "}
                  {capability.capability}
                  {!capability.available && capability.detail && (
                    <div className="muted" style={{ fontSize: 12 }}>
                      {capability.detail}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </details>

          <div className="row" style={{ marginTop: 10 }}>
            <button onClick={() => test(target)} disabled={testing === target.id}>
              {testing === target.id ? "Testing..." : "Test connection"}
            </button>
            <button onClick={() => onSelect(target)}>Use this target</button>
          </div>

          {result[target.id]?.map((line) => (
            <div className="notice" key={line}>
              {line}
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
