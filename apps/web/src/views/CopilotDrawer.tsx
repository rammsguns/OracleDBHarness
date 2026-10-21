import { useEffect, useRef, useState } from "react";
import { HarnessError, api } from "../api";
import type {
  CodeCheckFragment,
  ContextAttachment,
  CopilotEvent,
  KiwiLineage,
  KiwiLookupRecord,
  LineageEvidence,
  ProposalValidation,
  Target,
} from "../api";

const ACTIONS = [
  { id: "explain", label: "Explain" },
  { id: "diagnose", label: "Diagnose an error" },
  { id: "propose", label: "Propose a change" },
  { id: "test_block", label: "Write a test block" },
  { id: "explain_plan", label: "Explain a plan" },
  { id: "validate", label: "Review" },
  { id: "kiwi.diagnose", label: "Fix a failure (playbook)" },
  { id: "kiwi.create", label: "Create a unit (playbook)" },
  { id: "kiwi.test_block", label: "Test block (playbook)" },
  { id: "kiwi.explain_package", label: "Explain a package" },
  { id: "kiwi.explain_process", label: "Explain a process (job or chain)" },
] as const;

export type CopilotActionId = (typeof ACTIONS)[number]["id"];

/** These read the database themselves, so they need a name, not pasted code. */
const isExplain = (id: CopilotActionId) => id === "kiwi.explain_package" || id === "kiwi.explain_process";

const EVIDENCE_HELP: Record<LineageEvidence, string> = {
  source: "read from the source text",
  inferred: "inferred, e.g. from dynamic SQL",
  catalog: "reported by the data dictionary",
  scheduler: "reported by the scheduler",
};

/** What the drawer opens with: the code, and optionally an action and an error. */
export interface CopilotSeed {
  text: string;
  action?: CopilotActionId;
  errorText?: string;
}

/**
 * One buffer of a multi-part edit, such as a package spec or body. The revision goes
 * up on every edit, so a proposal made against older text is refused on apply.
 */
interface PartBuffer {
  name: string;
  text: string;
  revision: number;
}

const PART_NAME = /^[A-Za-z0-9_-]+$/;

const partEditor = (name: string) => `console:${name}`;

interface TraceEntry {
  callId: string;
  toolName: string;
  why: string;
  status?: string;
  rowCount?: number | null;
  truncated?: boolean;
  executionId?: string | null;
  errorCode?: string;
}

interface Proposal {
  validation?: ProposalValidation;
  proposalId: string;
  editorId: string;
  baseRevision: string;
  proposedText: string;
  rationale: string;
  note: string;
  multiPart?: true;
  parts?: Array<{
    part: string;
    editorId: string;
    baseRevision: string;
    proposedText: string;
    changed: boolean;
    validation?: ProposalValidation;
  }>;
}

/** Why a proposal is not known to be a whole unit; empty when nothing was found wrong. */
function incompleteProblems(proposal: Proposal): string[] {
  const found: string[] = [];
  if (proposal.validation && !proposal.validation.complete) {
    found.push(...proposal.validation.problems);
  }
  for (const part of proposal.parts ?? []) {
    if (part.changed && part.validation && !part.validation.complete) {
      found.push(...part.validation.problems.map((problem) => `${part.part}: ${problem}`));
    }
  }
  return found;
}

function ValidationNotice({ problems }: { problems: string[] }) {
  if (problems.length === 0) return null;
  return (
    <div className="notice warning" role="alert" aria-label="Incomplete proposal">
      <strong>This may not be a whole unit.</strong>
      <ul>
        {problems.map((problem) => (
          <li key={problem}>{problem}</li>
        ))}
      </ul>
      <p className="muted">
        A structural check of statement boundaries only. Nothing was compiled or run.
      </p>
    </div>
  );
}

/**
 * The Kiwi panel (the copilot).
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
  seed: CopilotSeed;
  onClose: () => void;
}) {
  const [action, setAction] = useState<CopilotActionId>(seed.action ?? "explain");
  const [selection, setSelection] = useState(seed.text);
  const [errorText, setErrorText] = useState(seed.errorText ?? "");
  // Multi-part mode edits several buffers together, e.g. a package spec and body, and
  // applies Kiwi's proposal to all of them or to none.
  const [multiPart, setMultiPart] = useState(false);
  const [parts, setParts] = useState<PartBuffer[]>([]);
  const [newPartName, setNewPartName] = useState("");
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
  // Code in an answer with no editor to apply it to, and the boundary problems found in it.
  const [codeCheck, setCodeCheck] = useState<CodeCheckFragment[]>([]);
  // An incomplete proposal is applied only after a second, explicit click.
  const [confirmIncomplete, setConfirmIncomplete] = useState(false);
  const [usage, setUsage] = useState<string | null>(null);
  const [fixture, setFixture] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  // Kiwi's lookups: what it asked for and how each went. Never the rows themselves.
  const [trace, setTrace] = useState<TraceEntry[]>([]);
  const [budget, setBudget] = useState<string | null>(null);
  const [partial, setPartial] = useState<string | null>(null);
  const [subject, setSubject] = useState("");
  const [lineage, setLineage] = useState<KiwiLineage | null>(null);
  // The harness's claim checks: whether it had Kiwi revise the answer, and what it still
  // finds in the answer shown.
  const [revised, setRevised] = useState(false);
  const [claimFindings, setClaimFindings] = useState<string[]>([]);
  // Claims a check recognised but could not compute, and checks that failed: neither
  // confirmed nor corrected, so they are shown rather than read as a pass.
  const [claimUnchecked, setClaimUnchecked] = useState<string[]>([]);
  // An edit request whose answer gave neither a proposal nor a no-change line.
  const [editMissing, setEditMissing] = useState("");
  // The harness's record of the lookups, not the model's: shown beside the answer.
  const [lookupRecord, setLookupRecord] = useState<KiwiLookupRecord | null>(null);
  const [copied, setCopied] = useState(false);
  const abort = useRef<AbortController | null>(null);

  const targetReference = target ? `harness:${target.id}:${target.defaultSchema}` : "harness:none";

  // Closing the drawer, or switching target, ends the request rather than leaving it
  // streaming into a panel nobody can see.
  useEffect(() => () => abort.current?.abort(), []);

  const attachments = () => {
    const list: ContextAttachment[] = [];
    if (multiPart) {
      for (const part of parts) {
        if (!part.text.trim()) continue;
        list.push({
          category: "selected_source",
          name: `part:${part.name}`,
          content: part.text,
          provenance: "console part",
        });
      }
    } else if (selection.trim()) {
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
  }, [selection, errorText, targetReference, multiPart, parts]);

  const startMultiPart = () => {
    setMultiPart(true);
    setProposal(null);
    setApplied(null);
    setParts((current) =>
      current.length > 0
        ? current
        : [
            { name: "spec", text: selection, revision: 1 },
            { name: "body", text: "", revision: 1 },
          ],
    );
  };

  const editPart = (name: string, text: string) =>
    setParts((current) =>
      current.map((part) =>
        part.name === name
          ? { ...part, text, revision: part.revision + 1 }
          : part,
      ),
    );

  const partNameProblem = (() => {
    const name = newPartName.trim();
    if (!name) return null;
    if (!PART_NAME.test(name)) return "Use letters, digits, _ and - only.";
    if (parts.some((part) => part.name === name))
      return "That part already exists.";
    return null;
  })();

  const addPart = () => {
    const name = newPartName.trim();
    if (!name || partNameProblem || parts.length >= 8) return;
    setParts((current) => [...current, { name, text: "", revision: 1 }]);
    setNewPartName("");
  };

  const removePart = (name: string) =>
    setParts((current) => current.filter((part) => part.name !== name));

  const ask = async () => {
    setRunning(true);
    setAnswer("");
    setProposal(null);
    setApplied(null);
    setCodeCheck([]);
    setConfirmIncomplete(false);
    setError(null);
    setTrace([]);
    setBudget(null);
    setPartial(null);
    setLineage(null);
    setRevised(false);
    setClaimFindings([]);
    setClaimUnchecked([]);
    setEditMissing("");
    setLookupRecord(null);
    setCopied(false);
    abort.current = new AbortController();
    try {
      const stream = api.copilot(
        {
          action,
          targetReference,
          profileId: target?.id,
          userMessage: question,
          ...(isExplain(action) ? { subject: subject.trim() } : {}),
          databaseVersion: target?.identity?.version ?? "",
          schema: target?.defaultSchema ?? "",
          attachments: attachments(),
          ...(multiPart
            ? {
                parts: parts.map((part) => ({
                  part: part.name,
                  editorId: partEditor(part.name),
                  revision: String(part.revision),
                  text: part.text,
                })),
              }
            : {
                editor: { editorId: "console", revision: "1", text: selection },
              }),
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
    if (event.event === "code_check") setCodeCheck(event.data.fragments);
    if (event.event === "usage") {
      setUsage(
        `${event.data.provider}/${event.data.model} - ${event.data.promptTokens ?? "?"} in, ` +
          `${event.data.completionTokens ?? "?"} out`,
      );
    }
    if (event.event === "tool_call") {
      const { callId, toolName, why } = event.data;
      setTrace((current) => [...current, { callId, toolName, why }]);
    }
    if (event.event === "tool_result") {
      const result = event.data;
      setTrace((current) =>
        current.map((entry) => (entry.callId === result.callId ? { ...entry, ...result } : entry)),
      );
    }
    if (event.event === "budget") {
      const b = event.data;
      setBudget(`${b.toolCalls}/${b.maxToolCalls} lookups, step ${b.steps}/${b.maxSteps}`);
    }
    if (event.event === "lineage") setLineage(event.data);
    if (event.event === "revision") {
      // The text that follows replaces the draft, which was the last text streamed.
      const { draft, restored, rendered } = event.data;
      setAnswer((current) =>
        draft && current.endsWith(draft) ? current.slice(0, current.length - draft.length) : current,
      );
      // A rendered revision is the harness's own text, not a revision by Kiwi.
      if (!rendered) setRevised(!restored);
    }
    if (event.event === "claim_check" && event.data.action !== "revise") {
      setClaimFindings(event.data.findings.map((finding) => `${finding.claim} — ${finding.problem}`));
      const unchecked = (event.data.unchecked ?? []).map((claim) => `${claim.claim} — ${claim.problem}`);
      if (event.data.action.startsWith("check_failed:")) {
        unchecked.push(`The harness's claim check did not run to completion (${event.data.action}).`);
      }
      setClaimUnchecked(unchecked);
    }
    if (event.event === "edit_contract" && event.data.delivered === "missing") {
      setEditMissing(event.data.repair);
    }
    if (event.event === "lookup_record") setLookupRecord(event.data);
    if (event.event === "done" && event.data.partial) {
      setPartial(event.data.stopReason ?? "budget");
    }
    if (event.event === "error") setError(`${event.data.code}: ${event.data.message}`);
  };

  const labelOf = (graph: KiwiLineage, id: string) =>
    graph.nodes.find((node) => node.id === id)?.label ?? id;

  // Only the diagram source is copied; nothing is sent anywhere.
  const copyMermaid = async (source: string) => {
    try {
      await navigator.clipboard.writeText(source);
      setCopied(true);
    } catch {
      setCopied(false);
      setError("Could not copy; select the Mermaid source below and copy it by hand.");
    }
  };

  const applyParts = async () => {
    if (!proposal) return;
    try {
      const result = await api.applyCheck(proposal.proposalId, {
        parts: parts.map((part) => ({
          part: part.name,
          editorId: partEditor(part.name),
          revision: String(part.revision),
          currentText: part.text,
        })),
        targetReference,
      });
      if (result.canApply && result.parts) {
        const next = new Map(
          result.parts.map((part) => [part.part, part.proposedText]),
        );
        // Applying is an edit too: each part moves to a new revision.
        setParts((current) =>
          current.map((part) =>
            next.has(part.name)
              ? {
                  ...part,
                  text: next.get(part.name) ?? part.text,
                  revision: part.revision + 1,
                }
              : part,
          ),
        );
        setApplied(result.note ?? "Applied to every part's editor text only.");
      } else {
        // The reasons already name the part each one is about ("body: ...").
        setApplied(`Refused, nothing was changed: ${result.reasons.join(" ")}`);
      }
    } catch (cause) {
      setError(cause instanceof HarnessError ? cause.message : String(cause));
    }
  };

  const apply = async () => {
    if (!proposal) return;
    // Policy: an edit the check found incomplete is never applied by one click. The
    // server's staleness and all-or-none checks still run on the confirming click.
    if (incompleteProblems(proposal).length > 0 && !confirmIncomplete) {
      setConfirmIncomplete(true);
      return;
    }
    setConfirmIncomplete(false);
    if (proposal.multiPart) return applyParts();
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
        <h3 style={{ margin: 0 }}>Kiwi</h3>
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
          <input
            type="checkbox"
            checked={multiPart}
            onChange={(event) =>
              event.target.checked ? startMultiPart() : setMultiPart(false)
            }
          />{" "}
          Edit several parts together (e.g. spec and body)
        </label>

        {isExplain(action) ? (
          <label>
            {action === "kiwi.explain_package" ? "Package name" : "Job or chain name"} (OWNER.NAME or NAME)
            <input
              aria-label="Subject"
              placeholder={action === "kiwi.explain_package" ? "HARNESS_APP.ETL_ORDERS" : "HARNESS_APP.ETL_ORDERS_NIGHTLY"}
              value={subject}
              onChange={(event) => setSubject(event.target.value)}
            />
          </label>
        ) : multiPart ? (
          <div className="stack">
            {parts.map((part) => (
              <label key={part.name}>
                <span
                  className="row"
                  style={{ justifyContent: "space-between" }}
                >
                  <span>
                    Part <code>{part.name}</code>{" "}
                    <span className="muted">revision {part.revision}</span>
                  </span>
                  <button
                    onClick={() => removePart(part.name)}
                    disabled={parts.length <= 1}
                  >
                    Remove {part.name}
                  </button>
                </span>
                <textarea
                  aria-label={`Part ${part.name}`}
                  className="editor-fallback"
                  style={{ minHeight: 100 }}
                  value={part.text}
                  onChange={(event) => editPart(part.name, event.target.value)}
                />
              </label>
            ))}
            <div className="row">
              <input
                aria-label="New part name"
                placeholder="part name"
                value={newPartName}
                onChange={(event) => setNewPartName(event.target.value)}
              />
              <button
                onClick={addPart}
                disabled={
                  !newPartName.trim() ||
                  partNameProblem !== null ||
                  parts.length >= 8
                }
              >
                Add part
              </button>
            </div>
            {partNameProblem && <p className="muted">{partNameProblem}</p>}
          </div>
        ) : (
          <label>
            Selected code
            <textarea
              className="editor-fallback"
              style={{ minHeight: 140 }}
              value={selection}
              onChange={(event) => setSelection(event.target.value)}
            />
          </label>
        )}

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
        <button
          className="primary"
          onClick={ask}
          disabled={running || (isExplain(action) ? !subject.trim() : !preview)}
        >
          {running ? "Asking..." : "Ask"}
        </button>
        {running && <button onClick={() => abort.current?.abort()}>Stop</button>}
      </div>

      {error && <div className="notice error">{error}</div>}

      {trace.length > 0 && (
        <section className="card">
          <h3>Lookups</h3>
          {budget && <p className="meta">{budget}</p>}
          <ul style={{ paddingLeft: 18 }}>
            {trace.map((entry) => (
              <li key={entry.callId}>
                <code>{entry.toolName}</code> - {entry.status ?? "running"}
                {entry.rowCount != null && ` (${entry.rowCount} rows${entry.truncated ? ", truncated" : ""})`}
                {entry.errorCode && ` (${entry.errorCode})`}
                {entry.executionId && <span className="muted"> {entry.executionId}</span>}
                {entry.why && <div className="muted" style={{ fontSize: 12 }}>{entry.why}</div>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {partial && (
        <div className="notice warn">
          Partial answer: Kiwi stopped at its {partial} limit before finishing.
        </div>
      )}

      {revised && (
        <div className="notice">
          Kiwi revised this answer after the harness checked its claims against the lookups.
        </div>
      )}
      {claimFindings.length > 0 && (
        <div className="notice warn" aria-label="Claim check findings">
          The harness found claims the lookups or Oracle&apos;s behaviour contradict:
          <ul>
            {claimFindings.map((finding) => (
              <li key={finding}>{finding}</li>
            ))}
          </ul>
        </div>
      )}
      {editMissing && (
        <div className="notice warn" aria-label="No usable proposal">
          This answer gives no proposal the harness could use and does not say the code needs
          no change ({editMissing}). Nothing can be applied from it.
        </div>
      )}
      {claimUnchecked.length > 0 && (
        <div className="notice" aria-label="Claims not checked">
          The harness could not check these claims; they are neither confirmed nor corrected:
          <ul>
            {claimUnchecked.map((claim) => (
              <li key={claim}>{claim}</li>
            ))}
          </ul>
        </div>
      )}

      {answer && (
        <section className="card">
          <h3>Answer</h3>
          {usage && <p className="meta">{usage}</p>}
          <div className="answer">{answer}</div>
          {lookupRecord && (lookupRecord.lookups.length > 0 || lookupRecord.notRun.length > 0) && (
            <div aria-label="Lookups made">
              <p className="meta">Lookups made (the harness&apos;s record, not written by the model):</p>
              <ol>
                {lookupRecord.lookups.map((entry) => (
                  <li key={entry.lookup} value={entry.lookup}>
                    <code>{entry.operationId}</code>
                    {Object.keys(entry.parameters).length > 0 &&
                      ` (${Object.entries(entry.parameters)
                        .map(([name, value]) => `${name}=${value}`)
                        .join(", ")})`}
                    : {entry.summary}
                    {((entry.facts ?? []).length > 0 || (entry.sourceRead ?? []).length > 0) && (
                      <ul>
                        {(entry.facts ?? []).map((fact, index) => (
                          <li key={`fact-${index}`}>{fact}</li>
                        ))}
                        {(entry.sourceRead ?? []).map((read) => (
                          <li key={`read-${read.unit}`}>
                            Read {read.lines} of {read.unit}
                            {read.members.length > 0 && `: ${read.members.join("; ")}`}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ol>
              {lookupRecord.notRun.map((entry, index) => (
                <p className="meta" key={`${entry.operationId}-${index}`}>
                  Not run: <code>{entry.operationId}</code> ({entry.reason})
                </p>
              ))}
            </div>
          )}
        </section>
      )}

      {lineage && (
        <section className="card">
          <h3>Lineage</h3>
          {lineage.notes.map((note) => (
            <div className="notice warn" key={note}>
              {note}
            </div>
          ))}
          <table>
            <thead>
              <tr>
                <th>From</th>
                <th>Relation</th>
                <th>To</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {lineage.edges.map((edge, index) => (
                <tr key={`${edge.source}|${edge.relation}|${edge.target}|${index}`}>
                  <td>{labelOf(lineage, edge.source)}</td>
                  <td>{edge.relation}</td>
                  <td>{labelOf(lineage, edge.target)}</td>
                  <td title={EVIDENCE_HELP[edge.evidence]}>
                    {edge.evidence}
                    {edge.detail && <span className="muted"> ({edge.detail})</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="toolbar">
            <button onClick={() => void copyMermaid(lineage.mermaid)}>
              {copied ? "Copied" : "Copy Mermaid"}
            </button>
          </div>
          <pre aria-label="Mermaid source">{lineage.mermaid}</pre>
        </section>
      )}

      {!proposal && codeCheck.length > 0 && (
        <section className="card" aria-label="Code check">
          <h3>Code in the answer</h3>
          {codeCheck.map((fragment) => (
            <ValidationNotice
              key={fragment.block}
              problems={fragment.problems.map((problem) => `Block ${fragment.block}: ${problem}`)}
            />
          ))}
        </section>
      )}

      {proposal?.multiPart && (
        <section className="card">
          <h3>Proposed change to {proposal.parts?.length ?? 0} parts</h3>
          {(proposal.parts ?? []).map((part) => (
            <div key={part.part}>
              <p className="meta">
                <code>{part.part}</code> based on revision {part.baseRevision}
                {part.changed ? "" : " (unchanged)"}
              </p>
              {part.changed && (
                <div className="diff">
                  <div>
                    <p className="muted">current</p>
                    <pre>
                      {parts.find((buffer) => buffer.name === part.part)
                        ?.text ?? ""}
                    </pre>
                  </div>
                  <div>
                    <p className="muted">proposed</p>
                    <pre>{part.proposedText}</pre>
                  </div>
                </div>
              )}
            </div>
          ))}
          <div className="notice">{proposal.note}</div>
          <ValidationNotice problems={incompleteProblems(proposal)} />
          <button className="primary" onClick={apply}>
            {confirmIncomplete ? "Apply all parts anyway" : "Apply all parts"}
          </button>
          {applied && <div className="notice">{applied}</div>}
        </section>
      )}

      {proposal && !proposal.multiPart && (
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
          <ValidationNotice problems={incompleteProblems(proposal)} />
          <button className="primary" onClick={apply}>
            {confirmIncomplete ? "Apply anyway" : "Apply to the editor"}
          </button>
          {applied && <div className="notice">{applied}</div>}
        </section>
      )}
    </aside>
  );
}
