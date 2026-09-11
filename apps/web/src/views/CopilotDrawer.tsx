import { useEffect, useRef, useState } from "react";
import { HarnessError, api } from "../api";
import type { ContextAttachment, CopilotEvent, Target } from "../api";

const ACTIONS = [
  { id: "explain", label: "Explain" },
  { id: "diagnose", label: "Diagnose an error" },
  { id: "propose", label: "Propose a change" },
  { id: "test_block", label: "Write a test block" },
  { id: "explain_plan", label: "Explain a plan" },
  { id: "validate", label: "Review" },
] as const;

interface Proposal {
  proposalId: string;
  editorId: string;
  baseRevision: string;
  proposedText: string;
  rationale: string;
  note: string;
}

/**
 * The copilot panel.
 *
 * Two things this panel is careful about: the user sees exactly what context will be
 * sent before it is sent, and accepting a proposal changes the editor text only -
 * running it is a separate action the user takes themselves.
 */
export function CopilotDrawer({
  target,
  seed,
  onClose,
}: {
  target: Target | null;
  seed: string;
  onClose: () => void;
}) {
  const [action, setAction] = useState<(typeof ACTIONS)[number]["id"]>("explain");
  const [selection, setSelection] = useState(seed);
  const [errorText, setErrorText] = useState("");
  const [question, setQuestion] = useState("");
  const [preview, setPreview] = useState<{
    totalBytes: number;
    categories: string[];
    excluded: string[];
    attachments: Array<{ category: string; name: string; byteLength: number }>;
  } | null>(null);
  const [answer, setAnswer] = useState("");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [applied, setApplied] = useState<string | null>(null);
  const [usage, setUsage] = useState<string | null>(null);
  const [fixture, setFixture] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const abort = useRef<AbortController | null>(null);

  const targetReference = target ? `harness:${target.id}:${target.defaultSchema}` : "harness:none";

  // Closing the drawer, or switching target, ends the request rather than leaving it
  // streaming into a panel nobody can see.
  useEffect(() => () => abort.current?.abort(), []);

  const attachments = () => {
    const list: ContextAttachment[] = [];
    if (selection.trim()) {
      list.push({
        category: "selected_source",
        name: "selection",
        content: selection,
        provenance: "console selection",
      });
    }
    if (errorText.trim()) {
      list.push({
        category: "error_text",
        name: "supplied error",
        content: errorText,
        provenance: "user supplied",
      });
    }
    return list;
  };

  useEffect(() => {
    const list = attachments();
    if (list.length === 0) {
      setPreview(null);
      return;
    }
    api
      .contextPreview({
        targetReference,
        attachments: list,
        databaseVersion: target?.identity?.version ?? "",
        schema: target?.defaultSchema ?? "",
      })
      .then(setPreview)
      .catch(() => setPreview(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection, errorText, targetReference]);

  const ask = async () => {
    setRunning(true);
    setAnswer("");
    setProposal(null);
    setApplied(null);
    setError(null);
    abort.current = new AbortController();
    try {
      const stream = api.copilot(
        {
          action,
          targetReference,
          userMessage: question,
          databaseVersion: target?.identity?.version ?? "",
          schema: target?.defaultSchema ?? "",
          attachments: attachments(),
          editor: { editorId: "console", revision: "1", text: selection },
        },
        abort.current.signal,
      );
      for await (const event of stream) {
        handle(event);
      }
    } catch (cause) {
      setError(cause instanceof HarnessError ? cause.message : String(cause));
    } finally {
      setRunning(false);
      abort.current = null;
    }
  };

  const handle = (event: CopilotEvent) => {
    if (event.event === "start") setFixture(event.data.isFixtureProvider);
    if (event.event === "delta") setAnswer((current) => current + event.data.text);
    if (event.event === "proposal") setProposal(event.data as Proposal);
    if (event.event === "usage") {
      setUsage(
        `${event.data.provider}/${event.data.model} - ${event.data.promptTokens ?? "?"} in, ` +
          `${event.data.completionTokens ?? "?"} out`,
      );
    }
    if (event.event === "error") setError(`${event.data.code}: ${event.data.message}`);
  };

  const apply = async () => {
    if (!proposal) return;
    try {
      const result = await api.applyCheck(proposal.proposalId, {
        editorId: "console",
        revision: "1",
        currentText: selection,
        targetReference,
      });
      if (result.canApply) {
        setSelection(result.proposedText ?? selection);
        setApplied(result.note ?? "Applied to the editor text only.");
      } else {
        setApplied(`Refused: ${result.reasons.join(" ")}`);
      }
    } catch (cause) {
      setError(cause instanceof HarnessError ? cause.message : String(cause));
    }
  };

  return (
    <aside className="drawer">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 style={{ margin: 0 }}>Copilot</h3>
        <button onClick={onClose}>Close</button>
      </div>

      {fixture && (
        <div className="notice warn">
          This harness is configured with the fixture provider. Answers are canned and no
          model is being called.
        </div>
      )}

      <div className="stack" style={{ marginTop: 12 }}>
        <label>
          Action{" "}
          <select value={action} onChange={(event) => setAction(event.target.value as never)}>
            {ACTIONS.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>

        <label>
          Selected code
          <textarea
            className="editor-fallback"
            style={{ minHeight: 140 }}
            value={selection}
            onChange={(event) => setSelection(event.target.value)}
          />
        </label>

        <label>
          Error or compiler output (optional)
          <textarea
            className="editor-fallback"
            style={{ minHeight: 60 }}
            value={errorText}
            onChange={(event) => setErrorText(event.target.value)}
          />
        </label>

        <label>
          Your question
          <input value={question} onChange={(event) => setQuestion(event.target.value)} />
        </label>
      </div>

      {preview && (
        <section className="card" style={{ marginTop: 12 }}>
          <h3>What will be sent</h3>
          <p className="meta">{preview.totalBytes} bytes in {preview.categories.join(", ")}</p>
          <ul style={{ paddingLeft: 18 }}>
            {preview.attachments.map((attachment) => (
              <li key={attachment.name}>
                {attachment.name} - {attachment.category} ({attachment.byteLength} bytes)
              </li>
            ))}
          </ul>
          <p className="muted" style={{ fontSize: 12 }}>
            Never sent: {preview.excluded.join(", ")}.
          </p>
        </section>
      )}

      <div className="toolbar">
        <button className="primary" onClick={ask} disabled={running || !preview}>
          {running ? "Asking..." : "Ask"}
        </button>
        {running && <button onClick={() => abort.current?.abort()}>Stop</button>}
      </div>

      {error && <div className="notice error">{error}</div>}

      {answer && (
        <section className="card">
          <h3>Answer</h3>
          {usage && <p className="meta">{usage}</p>}
          <div className="answer">{answer}</div>
        </section>
      )}

      {proposal && (
        <section className="card">
          <h3>Proposed change</h3>
          <p className="meta">
            based on revision {proposal.baseRevision} of {proposal.editorId}
          </p>
          <div className="diff">
            <div>
              <p className="muted">current</p>
              <pre>{selection}</pre>
            </div>
            <div>
              <p className="muted">proposed</p>
              <pre>{proposal.proposedText}</pre>
            </div>
          </div>
          <div className="notice">{proposal.note}</div>
          <button className="primary" onClick={apply}>
            Apply to the editor
          </button>
          {applied && <div className="notice">{applied}</div>}
        </section>
      )}
    </aside>
  );
}
