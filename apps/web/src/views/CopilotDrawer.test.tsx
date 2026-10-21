/**
 * Multi-part proposals in the Kiwi drawer: a package spec and body are proposed
 * together and applied together. The server decides whether the whole proposal may be
 * applied; these tests check the console sends every part with its current revision,
 * replaces every part on success, and changes nothing when any part is refused.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { makeTarget } from "./fixtures";
import { CopilotDrawer } from "./CopilotDrawer";
import { PlsqlView } from "./PlsqlView";

vi.mock("@monaco-editor/react", () => ({
  default: ({ value }: { value: string }) => <textarea readOnly value={value} />,
}));

const contextPreview = vi.fn();
const copilot = vi.fn();
const applyCheck = vi.fn();
const compile = vi.fn();

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      contextPreview: (...args: unknown[]) => contextPreview(...args),
      copilot: (...args: unknown[]) => copilot(...args),
      applyCheck: (...args: unknown[]) => applyCheck(...args),
      compile: (...args: unknown[]) => compile(...args),
    },
  };
});

const target = makeTarget();
const SPEC = "CREATE OR REPLACE PACKAGE payroll AS\n  PROCEDURE run;\nEND payroll;";
const NEW_SPEC = "CREATE OR REPLACE PACKAGE payroll AS\n  PROCEDURE run(p_month DATE);\nEND payroll;";
const BODY = "CREATE OR REPLACE PACKAGE BODY payroll AS\n  PROCEDURE run IS BEGIN NULL; END;\nEND payroll;";
const NEW_BODY =
  "CREATE OR REPLACE PACKAGE BODY payroll AS\n  PROCEDURE run(p_month DATE) IS BEGIN NULL; END;\nEND payroll;";

function proposalEvents() {
  return (async function* () {
    yield { event: "delta", data: { text: "Both parts change together." } };
    yield {
      event: "proposal",
      data: {
        proposalId: "prop_1",
        editorId: "",
        baseRevision: "",
        proposedText: "",
        rationale: "",
        note: "Applying changes the editor text only.",
        multiPart: true,
        parts: [
          { part: "spec", editorId: "console:spec", baseRevision: "1", proposedText: NEW_SPEC, changed: true },
          { part: "body", editorId: "console:body", baseRevision: "2", proposedText: NEW_BODY, changed: true },
        ],
      },
    };
  })();
}

async function openWithParts() {
  render(<CopilotDrawer target={target} seed={{ text: SPEC, action: "propose" }} onClose={() => undefined} />);
  fireEvent.click(screen.getByLabelText(/edit several parts together/i));
  expect((screen.getByLabelText("Part spec") as HTMLTextAreaElement).value).toBe(SPEC);
  fireEvent.change(screen.getByLabelText("Part body"), { target: { value: BODY } });
  await waitFor(() => expect((screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  await screen.findByRole("button", { name: /apply all parts/i });
}

beforeEach(() => {
  contextPreview.mockResolvedValue({
    totalBytes: 100,
    categories: ["selected_source"],
    excluded: ["credentials"],
    attachments: [],
  });
  copilot.mockImplementation(() => proposalEvents());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("multi-part proposals", () => {
  it("sends every part with its editor and revision", async () => {
    await openWithParts();
    const request = copilot.mock.calls[0][0];
    expect(request.editor).toBeUndefined();
    expect(request.parts).toEqual([
      { part: "spec", editorId: "console:spec", revision: "1", text: SPEC },
      { part: "body", editorId: "console:body", revision: "2", text: BODY },
    ]);
    expect(request.attachments.map((a: { name: string }) => a.name)).toEqual(["part:spec", "part:body"]);
  });

  it("applies every part together", async () => {
    applyCheck.mockResolvedValue({
      proposalId: "prop_1",
      canApply: true,
      reasons: [],
      parts: [
        { part: "spec", editorId: "console:spec", proposedText: NEW_SPEC },
        { part: "body", editorId: "console:body", proposedText: NEW_BODY },
      ],
      executesDatabaseOperations: false,
      note: "Applied to the editor text only.",
    });
    await openWithParts();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /apply all parts/i }));
    });

    expect(applyCheck).toHaveBeenCalledWith("prop_1", {
      parts: [
        { part: "spec", editorId: "console:spec", revision: "1", currentText: SPEC },
        { part: "body", editorId: "console:body", revision: "2", currentText: BODY },
      ],
      targetReference: `harness:${target.id}:${target.defaultSchema}`,
    });
    expect((screen.getByLabelText("Part spec") as HTMLTextAreaElement).value).toBe(NEW_SPEC);
    expect((screen.getByLabelText("Part body") as HTMLTextAreaElement).value).toBe(NEW_BODY);
    expect(screen.getByText("Applied to the editor text only.")).toBeTruthy();
  });

  it("changes nothing when one part went stale", async () => {
    applyCheck.mockResolvedValue({
      proposalId: "prop_1",
      canApply: false,
      reasons: ["body: The editor has changed since the proposal was generated."],
      partReasons: { spec: [], body: ["The editor has changed since the proposal was generated."] },
      executesDatabaseOperations: false,
      note: "Nothing was applied: a multi-part proposal applies all parts or none.",
    });
    await openWithParts();
    const edited = BODY + "\n-- edited after the proposal";
    fireEvent.change(screen.getByLabelText("Part body"), { target: { value: edited } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /apply all parts/i }));
    });

    // The edit moved the body to a new revision, and that is what the check sees.
    const sent = applyCheck.mock.calls[0][1].parts;
    expect(sent[1]).toEqual({ part: "body", editorId: "console:body", revision: "3", currentText: edited });
    expect((screen.getByLabelText("Part spec") as HTMLTextAreaElement).value).toBe(SPEC);
    expect((screen.getByLabelText("Part body") as HTMLTextAreaElement).value).toBe(edited);
    expect(screen.getByText(/refused, nothing was changed: body: the editor has changed/i)).toBeTruthy();
  });

  it("refuses a part name the server would reject", () => {
    render(<CopilotDrawer target={target} seed={{ text: SPEC }} onClose={() => undefined} />);
    fireEvent.click(screen.getByLabelText(/edit several parts together/i));
    fireEvent.change(screen.getByLabelText("New part name"), { target: { value: "bad name" } });
    expect((screen.getByRole("button", { name: "Add part" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("New part name"), { target: { value: "body" } });
    expect((screen.getByRole("button", { name: "Add part" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("New part name"), { target: { value: "types" } });
    fireEvent.click(screen.getByRole("button", { name: "Add part" }));
    expect(screen.getByLabelText("Part types")).toBeTruthy();
  });
});

describe("Ask Kiwi to fix", () => {
  it("opens Kiwi on the diagnose playbook with the compiler errors", async () => {
    compile.mockResolvedValue({
      compiled: false,
      errors: [{ line: 3, position: 5, text: "PLS-00103: Encountered the symbol END" }],
      note: "",
    });
    const onAsk = vi.fn();
    render(<PlsqlView target={target} onAsk={onAsk} />);
    expect(screen.queryByRole("button", { name: /ask kiwi to fix/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^compile$/i }));
    fireEvent.click(await screen.findByRole("button", { name: /ask kiwi to fix/i }));

    const seed = onAsk.mock.calls[0][0];
    expect(seed.action).toBe("kiwi.diagnose");
    expect(seed.errorText).toBe("3:5 PLS-00103: Encountered the symbol END");
    expect(seed.text).toContain("PACKAGE BODY employee_report");
  });

  it("starts the drawer on the seeded action and error", () => {
    render(
      <CopilotDrawer
        target={target}
        seed={{ text: "BEGIN NULL END;", action: "kiwi.diagnose", errorText: "1:11 PLS-00103" }}
        onClose={() => undefined}
      />,
    );
    expect((screen.getByLabelText(/action/i) as HTMLSelectElement).value).toBe("kiwi.diagnose");
    expect(screen.getByDisplayValue("1:11 PLS-00103")).toBeTruthy();
  });
});

describe("explain actions", () => {
  const LINEAGE = {
    nodes: [
      { id: "n0", kind: "package", label: "HARNESS_APP.ETL_ORDERS" },
      { id: "n1", kind: "table", label: "ETL_ORDERS_STAGE" },
    ],
    edges: [
      { source: "n0", target: "n1", relation: "writes", evidence: "source", detail: "via 2 helper subprogram(s)" },
      { source: "n1", target: "n0", relation: "fires", evidence: "catalog" },
    ],
    mermaid: 'flowchart LR\n  n0 -->|"writes · source"| n1',
    notes: ["2 subprograms not summarised"],
  };

  it("asks by name, shows every edge with its evidence, and copies the Mermaid source", async () => {
    copilot.mockImplementation(() =>
      (async function* () {
        yield { event: "delta", data: { text: "Explained." } };
        yield { event: "lineage", data: LINEAGE };
      })(),
    );
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    render(<CopilotDrawer target={target} seed={{ text: "", action: "kiwi.explain_package" }} onClose={() => undefined} />);
    const ask = screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement;
    expect(ask.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: " HARNESS_APP.ETL_ORDERS " } });
    expect(ask.disabled).toBe(false);
    fireEvent.click(ask);

    await screen.findByText("Lineage");
    expect(copilot.mock.calls[0][0]).toMatchObject({
      action: "kiwi.explain_package",
      subject: "HARNESS_APP.ETL_ORDERS",
    });
    expect(screen.getByText("source")).toBeTruthy();
    expect(screen.getByText("catalog")).toBeTruthy();
    expect(screen.getByText("(via 2 helper subprogram(s))")).toBeTruthy();
    expect(screen.getByText("2 subprograms not summarised")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Copy Mermaid" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(LINEAGE.mermaid));
  });
});

const INCOMPLETE = {
  kind: "plsql_source",
  complete: false,
  problems: ["The unit is not closed: a BEGIN, IS/AS, CASE, IF or LOOP has no matching END."],
  compiled: false,
  note: "Structural check of statement boundaries only.",
};

describe("incomplete proposals", () => {
  it("warns on a multi-part proposal and applies only after a second click", async () => {
    copilot.mockImplementation(() =>
      (async function* () {
        yield {
          event: "proposal",
          data: {
            proposalId: "prop_1",
            editorId: "",
            baseRevision: "",
            proposedText: "",
            rationale: "",
            note: "Applying changes the editor text only.",
            multiPart: true,
            parts: [
              { part: "spec", editorId: "console:spec", baseRevision: "1", proposedText: NEW_SPEC, changed: true },
              {
                part: "body",
                editorId: "console:body",
                baseRevision: "2",
                proposedText: "CREATE OR REPLACE PACKAGE BODY payroll AS",
                changed: true,
                validation: INCOMPLETE,
              },
            ],
          },
        };
      })(),
    );
    applyCheck.mockResolvedValue({
      proposalId: "prop_1",
      canApply: false,
      reasons: ["body: stale"],
      executesDatabaseOperations: false,
    });
    await openWithParts();

    expect(screen.getByRole("alert").textContent).toContain("body: The unit is not closed");
    expect(screen.getByRole("alert").textContent).toContain("Nothing was compiled or run");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /apply all parts$/i }));
    });
    expect(applyCheck).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /apply all parts anyway/i }));
    });
    // The server's all-or-none and stale-source checks still decide.
    expect(applyCheck).toHaveBeenCalledTimes(1);
  });

  it("warns on a single proposal and asks twice", async () => {
    copilot.mockImplementation(() =>
      (async function* () {
        yield {
          event: "proposal",
          data: {
            proposalId: "prop_2",
            editorId: "console:main",
            baseRevision: "1",
            proposedText: "BEGIN NULL;",
            rationale: "",
            note: "Applying changes the editor text only.",
            validation: INCOMPLETE,
          },
        };
      })(),
    );
    applyCheck.mockResolvedValue({
      proposalId: "prop_2",
      canApply: true,
      reasons: [],
      proposedText: "BEGIN NULL;",
      executesDatabaseOperations: false,
    });
    render(<CopilotDrawer target={target} seed={{ text: "BEGIN NULL; END;", action: "propose" }} onClose={() => undefined} />);
    await waitFor(() => expect((screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByRole("alert");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Apply to the editor" }));
    });
    expect(applyCheck).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Apply anyway" }));
    });
    expect(applyCheck).toHaveBeenCalledTimes(1);
  });

  it("shows the code check for an answer with no editor", async () => {
    copilot.mockImplementation(() =>
      (async function* () {
        yield { event: "delta", data: { text: "Use this." } };
        yield { event: "code_check", data: { fragments: [{ block: 1, ...INCOMPLETE }] } };
      })(),
    );
    render(<CopilotDrawer target={target} seed={{ text: "SELECT 1 FROM dual", action: "explain" }} onClose={() => undefined} />);
    await waitFor(() => expect((screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Block 1: The unit is not closed");
  });

  it("replaces a revised draft and shows what the claim check still finds", async () => {
    const finding = { kind: "session_role", claim: "read SID 42's ROW_WAIT_OBJ#", problem: "SID 42 is not waiting." };
    copilot.mockImplementation(() =>
      (async function* () {
        yield { event: "delta", data: { text: "Looking up the blocker." } };
        yield { event: "delta", data: { text: "Read SID 42's ROW_WAIT_OBJ#." } };
        yield { event: "claim_check", data: { stage: "answer", findings: [finding], action: "revise" } };
        yield { event: "revision", data: { draft: "Read SID 42's ROW_WAIT_OBJ#.", findings: [finding] } };
        yield { event: "delta", data: { text: "Read SID 77's ROW_WAIT_OBJ#." } };
        yield { event: "claim_check", data: { stage: "revised", findings: [], action: "none" } };
      })(),
    );
    render(<CopilotDrawer target={target} seed={{ text: "SELECT 1 FROM dual", action: "explain" }} onClose={() => undefined} />);
    await waitFor(() => expect((screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText(/Kiwi revised this answer/);
    const shown = document.querySelector(".answer")?.textContent ?? "";
    expect(shown).toBe("Looking up the blocker.Read SID 77's ROW_WAIT_OBJ#.");
    expect(screen.queryByLabelText("Claim check findings")).toBeNull();
  });

  it("replaces the next steps the harness rendered without saying Kiwi revised them", async () => {
    const draft = "Next steps:\n1. Run something.\n";
    copilot.mockImplementation(() =>
      (async function* () {
        yield { event: "delta", data: { text: "Finding: INVALID.\n\n" } };
        yield { event: "delta", data: { text: draft } };
        yield { event: "claim_check", data: { stage: "answer", findings: [], action: "none" } };
        yield { event: "revision", data: { draft, findings: [], rendered: "next_checks" } };
        yield { event: "delta", data: { text: "## Next steps\n1. Written by the harness." } };
      })(),
    );
    render(<CopilotDrawer target={target} seed={{ text: "SELECT 1 FROM dual", action: "explain" }} onClose={() => undefined} />);
    await waitFor(() => expect((screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(document.querySelector(".answer")?.textContent).toContain("Written by the harness"));
    const shown = document.querySelector(".answer")?.textContent ?? "";
    expect(shown).not.toContain("Run something");
    expect(screen.queryByText(/Kiwi revised this answer/)).toBeNull();
  });

  it("keeps the notes and adds the harness's answer when a bound left no diagnosis", async () => {
    copilot.mockImplementation(() =>
      (async function* () {
        yield { event: "delta", data: { text: "Reading the failures." } };
        yield { event: "revision", data: { draft: "", findings: [], rendered: "bounded_fallback" } };
        yield { event: "delta", data: { text: "\n\n**Finding:** The harness wrote this answer." } };
        yield { event: "claim_check", data: { stage: "answer", findings: [], action: "none" } };
      })(),
    );
    render(<CopilotDrawer target={target} seed={{ text: "SELECT 1 FROM dual", action: "explain" }} onClose={() => undefined} />);
    await waitFor(() => expect((screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(document.querySelector(".answer")?.textContent).toContain("The harness wrote this answer"));
    const shown = document.querySelector(".answer")?.textContent ?? "";
    expect(shown).toContain("Reading the failures.");
    expect(screen.queryByText(/Kiwi revised this answer/)).toBeNull();
  });

  it("shows claims the harness could not check instead of reading them as a pass", async () => {
    const claim = { kind: "mod_arithmetic", claim: "MOD(Infinity, 2) = 1", problem: "not checked: not finite" };
    copilot.mockImplementation(() =>
      (async function* () {
        yield { event: "delta", data: { text: "MOD(Infinity, 2) = 1." } };
        yield { event: "claim_check", data: { stage: "answer", findings: [], action: "none", unchecked: [claim] } };
      })(),
    );
    render(<CopilotDrawer target={target} seed={{ text: "SELECT 1 FROM dual", action: "explain" }} onClose={() => undefined} />);
    await waitFor(() => expect((screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    const notice = await screen.findByLabelText("Claims not checked");
    expect(notice.textContent).toContain("MOD(Infinity, 2) = 1");
    expect(screen.queryByLabelText("Claim check findings")).toBeNull();
  });
  it("says so when an edit answer gives no usable proposal", async () => {
    copilot.mockImplementation(() =>
      (async function* () {
        yield { event: "delta", data: { text: "CREATE OR REPLACE FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END f;" } };
        yield { event: "edit_contract", data: { contract: "edit/1", delivered: "missing", repair: "failed:missing" } };
      })(),
    );
    render(<CopilotDrawer target={target} seed={{ text: "SELECT 1 FROM dual", action: "explain" }} onClose={() => undefined} />);
    await waitFor(() => expect((screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    const notice = await screen.findByLabelText("No usable proposal");
    expect(notice.textContent).toContain("failed:missing");
  });
  it("shows the harness's lookup record apart from the model's answer", async () => {
    const entry = {
      lookup: 3,
      callId: "call_1",
      operationId: "dba.scheduler_run_history",
      parameters: { owner: "APP_OWNER", job_name: "LOAD_ORDERS", row_limit: 25 },
      coverage: "complete",
      rows: 2,
      rowLimit: 25,
      summary: "2 rows, complete (fewer than row_limit 25, not truncated)",
      facts: ["APP_OWNER.LOAD_ORDERS run 7: FAILED"],
      subjects: ["APP_OWNER.LOAD_ORDERS"],
      sourceRead: [],
    };
    copilot.mockImplementation(() =>
      (async function* () {
        yield { event: "delta", data: { text: "Run 7 failed." } };
        yield {
          event: "lookup_record",
          data: { lookups: [entry], notRun: [{ operationId: "schema.object_source", reason: "budget: tool_calls" }] },
        };
      })(),
    );
    render(<CopilotDrawer target={target} seed={{ text: "SELECT 1 FROM dual", action: "explain" }} onClose={() => undefined} />);
    await waitFor(() => expect((screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    const record = await screen.findByLabelText("Lookups made");
    expect(record.textContent).toContain("dba.scheduler_run_history");
    expect(record.textContent).toContain("row_limit=25");
    expect(record.textContent).toContain("complete (fewer than row_limit 25, not truncated)");
    expect(record.textContent).toContain("Not run: schema.object_source (budget: tool_calls)");
    expect(record.querySelector("ul li")?.textContent).toBe("APP_OWNER.LOAD_ORDERS run 7: FAILED");
    expect(document.querySelector(".answer")?.textContent).toBe("Run 7 failed.");
  });
  it("shows a lookup the harness refused before it ran", async () => {
    copilot.mockImplementation(() =>
      (async function* () {
        yield { event: "tool_call", data: { callId: "call_1", toolName: "schema__object_source", why: "Read the view's filter." } };
        yield {
          event: "tool_result",
          data: { callId: "call_1", operationId: "schema.object_source", status: "invalid", rowCount: null, errorCode: "invalid_request" },
        };
        yield { event: "delta", data: { text: "The view's query is not stored source." } };
      })(),
    );
    render(<CopilotDrawer target={target} seed={{ text: "SELECT 1 FROM dual", action: "explain" }} onClose={() => undefined} />);
    await waitFor(() => expect((screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    const lookups = (await screen.findByText("Lookups")).closest("section");
    await waitFor(() => expect(lookups?.textContent).toContain("schema__object_source - invalid (invalid_request)"));
    expect(lookups?.textContent).not.toContain("rows");
  });
  it("shows which source lines and members a lookup returned", async () => {
    const entry = {
      lookup: 1,
      callId: "call_1",
      operationId: "schema.object_source_range",
      parameters: { owner: "APP", object_name: "PKG", object_type: "PACKAGE BODY" },
      coverage: "complete",
      rows: 4,
      rowLimit: 50,
      summary: "4 rows, complete (fewer than row_limit 50, not truncated)",
      facts: [],
      subjects: ["APP.PKG PACKAGE BODY"],
      sourceRead: [
        { unit: "APP.PKG PACKAGE BODY", lines: "lines 10-13", members: ["RUN: implementation from line 10, read whole"] },
      ],
    };
    copilot.mockImplementation(() =>
      (async function* () {
        yield { event: "delta", data: { text: "The fix belongs in APP.PKG.RUN." } };
        yield { event: "lookup_record", data: { lookups: [entry], notRun: [] } };
      })(),
    );
    render(<CopilotDrawer target={target} seed={{ text: "SELECT 1 FROM dual", action: "explain" }} onClose={() => undefined} />);
    await waitFor(() => expect((screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    const record = await screen.findByLabelText("Lookups made");
    expect(record.querySelector("ul li")?.textContent).toBe(
      "Read lines 10-13 of APP.PKG PACKAGE BODY: RUN: implementation from line 10, read whole",
    );
    expect(document.querySelector(".answer")?.textContent).toBe("The fix belongs in APP.PKG.RUN.");
  });
});
