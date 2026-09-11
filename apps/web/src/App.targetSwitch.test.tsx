/**
 * Everything a view holds belongs to the target it was loaded for.
 *
 * The PL/SQL workspace and the tuning workbench keep what the user typed and what
 * came back in component state. Before these tests, switching from one target to
 * another left that state in place: Compile then sent the first database's source to
 * the second, and a response that arrived after the switch was shown under the new
 * target's heading. These tests switch targets through the console the way a user
 * would and check that nothing crosses over.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me, SystemInfo } from "./api";
import { App } from "./App";
import { deferred, makeTarget } from "./views/fixtures";

vi.mock("@monaco-editor/react", () => ({
  // The real editor loads its bundle over the network, which jsdom cannot do.
  default: ({ value, onChange }: { value: string; onChange: (next: string) => void }) => (
    <textarea
      aria-label="editor"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));

const systemInfo = vi.fn();
const signInWithDevToken = vi.fn();
const targets = vi.fn();
const compile = vi.fn();
const objectDetail = vi.fn();
const explain = vi.fn();
const contextPreview = vi.fn();

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    signInWithDevToken: (...args: unknown[]) => signInWithDevToken(...args),
    api: {
      ...actual.api,
      systemInfo: (...args: unknown[]) => systemInfo(...args),
      targets: (...args: unknown[]) => targets(...args),
      compile: (...args: unknown[]) => compile(...args),
      objectDetail: (...args: unknown[]) => objectDetail(...args),
      explain: (...args: unknown[]) => explain(...args),
      contextPreview: (...args: unknown[]) => contextPreview(...args),
    },
  };
});

const info: SystemInfo = {
  version: "0.1.0",
  environment: "development",
  authMode: "dev",
  oracleBackend: "fake",
  oracleDriverMode: "thin",
  metadataSchemaVersion: "3",
  catalogOperations: 20,
  copilotEnabled: true,
  warnings: [],
  limits: {},
};

const alice: Me = {
  subject: "alice@example.internal",
  displayName: "Alice Example",
  roles: ["developer"],
  userId: "usr_alice",
  targets: [],
};

const development = makeTarget({ id: "prf_dev", name: "development" });
const staging = makeTarget({ id: "prf_stage", name: "staging", environment: "staging" });

const DEV_SOURCE = "CREATE OR REPLACE PROCEDURE only_on_development IS BEGIN NULL; END;";

beforeEach(() => {
  systemInfo.mockResolvedValue(info);
  signInWithDevToken.mockResolvedValue({ me: alice, warning: "", expiresIn: 3600 });
  targets.mockResolvedValue([development, staging]);
  contextPreview.mockResolvedValue({
    totalBytes: 0,
    categories: [],
    excluded: [],
    attachments: [],
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/** Sign in, wait for both targets, and open a view on development. */
async function openOnDevelopment(view: RegExp) {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /^sign in$/i }));
  const picker = await screen.findByLabelText("Target");
  await waitFor(() => expect((picker as HTMLSelectElement).value).toBe("prf_dev"));
  fireEvent.click(screen.getByRole("button", { name: view }));
}

function switchTo(targetId: string) {
  fireEvent.change(screen.getByLabelText("Target"), { target: { value: targetId } });
}

function editor(): HTMLTextAreaElement {
  return screen.getByLabelText("editor") as HTMLTextAreaElement;
}

describe("the PL/SQL workspace across a target switch", () => {
  it("does not compile the previous target's source on the new one", async () => {
    compile.mockResolvedValue({ compiled: false, errors: [], note: "" });
    await openOnDevelopment(/pl\/sql workspace/i);
    fireEvent.change(editor(), { target: { value: DEV_SOURCE } });

    switchTo("prf_stage");
    expect(editor().value).not.toContain("only_on_development");
    fireEvent.click(screen.getByRole("button", { name: /^compile$/i }));

    await waitFor(() => expect(compile).toHaveBeenCalledTimes(1));
    const [profileId, source] = compile.mock.calls[0] as [string, string];
    expect(profileId).toBe("prf_stage");
    expect(source).not.toContain("only_on_development");
  });

  it("ignores a compilation that finished after the user moved on", async () => {
    const compiling = deferred<unknown>();
    compile.mockReturnValue(compiling.promise);
    await openOnDevelopment(/pl\/sql workspace/i);
    fireEvent.change(editor(), { target: { value: DEV_SOURCE } });
    fireEvent.click(screen.getByRole("button", { name: /^compile$/i }));
    expect(compile).toHaveBeenCalledWith("prf_dev", DEV_SOURCE);

    switchTo("prf_stage");
    await act(async () => {
      compiling.resolve({
        compiled: false,
        errors: [{ line: 1, position: 1, text: "PLS-00103 reported by development" }],
        note: "compiled on development",
      });
    });

    expect(screen.queryByText(/reported by development/)).toBeNull();
    expect(screen.queryByText(/compiled on development/)).toBeNull();
    expect(screen.getByRole("button", { name: /^compile$/i }).hasAttribute("disabled")).toBe(
      false,
    );
    expect(objectDetail).not.toHaveBeenCalled();
  });
});

describe("the tuning workbench across a target switch", () => {
  const plan = (note: string) => ({
    statementId: "stm_1",
    kind: "query",
    columns: ["OPERATION"],
    rows: [["TABLE ACCESS FULL"]],
    note,
    error: null,
  });

  it("drops the previous target's plan", async () => {
    explain.mockResolvedValue(plan("plan from development"));
    await openOnDevelopment(/tuning workbench/i);
    fireEvent.click(screen.getByRole("button", { name: /^explain$/i }));
    expect(await screen.findByText(/plan from development/)).toBeTruthy();

    switchTo("prf_stage");
    expect(screen.queryByText(/plan from development/)).toBeNull();
  });

  it("ignores a plan that arrived after the user moved on", async () => {
    const explaining = deferred<unknown>();
    explain.mockReturnValue(explaining.promise);
    await openOnDevelopment(/tuning workbench/i);
    fireEvent.click(screen.getByRole("button", { name: /^explain$/i }));

    switchTo("prf_stage");
    await act(async () => {
      explaining.resolve(plan("plan from development"));
    });
    expect(screen.queryByText(/plan from development/)).toBeNull();
  });
});

describe("the copilot across a target switch", () => {
  it("closes a drawer seeded with the previous target's source, for good", async () => {
    await openOnDevelopment(/pl\/sql workspace/i);
    fireEvent.change(editor(), { target: { value: DEV_SOURCE } });
    fireEvent.click(screen.getByRole("button", { name: /ask the copilot about this unit/i }));
    await screen.findByRole("heading", { name: "Copilot" });
    // The editor, and the drawer's copy of it.
    expect(screen.getAllByDisplayValue(DEV_SOURCE)).toHaveLength(2);

    switchTo("prf_stage");
    expect(screen.queryAllByDisplayValue(DEV_SOURCE)).toHaveLength(0);
    expect(screen.queryByRole("heading", { name: "Copilot" })).toBeNull();
    // Nothing about development's source was previewed against staging.
    for (const [request] of contextPreview.mock.calls as Array<[{ targetReference: string }]>) {
      expect(request.targetReference).toContain("prf_dev");
    }

    switchTo("prf_dev");
    expect(screen.queryByRole("heading", { name: "Copilot" })).toBeNull();
  });
});
