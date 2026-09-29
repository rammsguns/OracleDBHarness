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
