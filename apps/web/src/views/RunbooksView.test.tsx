/**
 * A runbook confirmation is only good for the preview the user read.
 *
 * "Confirm and run" on a mutating runbook is the user agreeing to one target and one
 * set of parameters. If the form or the target changes after the preview, the button
 * must stop meaning "yes" until the new values have been previewed; otherwise it runs
 * values nobody looked at under a notice that still shows the old ones.
 */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RunbookRun, RunbookSpec } from "../api";
import { deferred, makeTarget } from "./fixtures";
import { RunbooksView } from "./RunbooksView";

const runbooks = vi.fn();
const previewRunbook = vi.fn();
const runRunbook = vi.fn();

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      runbooks: (...args: unknown[]) => runbooks(...args),
      previewRunbook: (...args: unknown[]) => previewRunbook(...args),
      runRunbook: (...args: unknown[]) => runRunbook(...args),
    },
  };
});

const gatherStats: RunbookSpec = {
  id: "gather_table_stats",
  title: "Gather table statistics",
  description: "Refresh optimizer statistics for one table.",
  risk: "mutating",
  mutating: true,
  requiresConfirmation: true,
  parameters: [
    { name: "owner", label: "Owner", required: true, example: "HR" },
    { name: "table", label: "Table", required: true, example: "EMPLOYEES" },
  ],
  steps: ["gather"],
  verificationOperationId: "schema.table_statistics",
  verificationRequirement:
    "The named table has a recorded row count and collection time after the gather.",
};

const development = makeTarget({ id: "prf_dev", name: "development" });
const staging = makeTarget({ id: "prf_stage", name: "staging", environment: "staging" });

type Preview = Awaited<ReturnType<typeof import("../api").api.previewRunbook>>;

function previewOf(
  parameters: Record<string, string>,
  target: { name: string; environment: string } = development,
): Preview {
  return {
    runbook: gatherStats,
    parameters,
    missingParameters: [],
    willChangeDatabase: true,
    ready: true,
    target: { name: target.name, environment: target.environment },
  };
}

function runOf(): RunbookRun {
  return {
    runbook: gatherStats,
    startedAt: "2026-01-01T00:00:00+00:00",
    finishedAt: "2026-01-01T00:00:01+00:00",
    outcome: "succeeded",
    steps: [],
    verification: {},
  };
}

const confirmButton = () => screen.getByRole("button", { name: /confirm and run/i });

async function selectRunbook(target = development) {
  const view = render(<RunbooksView target={target} />);
  fireEvent.click(await screen.findByRole("button", { name: /select/i }));
  return view;
}

async function previewAsShown(parameters = { owner: "HR", table: "EMPLOYEES" }) {
  previewRunbook.mockResolvedValueOnce(previewOf(parameters));
  fireEvent.click(screen.getByRole("button", { name: /preview/i }));
  await screen.findByText(/this will change the database/i);
}

beforeEach(() => {
  runbooks.mockResolvedValue({ runbooks: [gatherStats], note: "" });
  runRunbook.mockResolvedValue(runOf());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("a confirmation bound to its preview", () => {
  it("cannot confirm before previewing", async () => {
    await selectRunbook();
    expect(confirmButton().hasAttribute("disabled")).toBe(true);
  });

  it("confirms exactly the values that were previewed", async () => {
    await selectRunbook();
    await previewAsShown();
    expect(confirmButton().hasAttribute("disabled")).toBe(false);

    fireEvent.click(confirmButton());
    await screen.findByText(/ran with/i);
    expect(runRunbook).toHaveBeenCalledWith(
      "gather_table_stats",
      "prf_dev",
      { owner: "HR", table: "EMPLOYEES" },
      true,
    );
  });

  it("withdraws the confirmation when a parameter changes after the preview", async () => {
    await selectRunbook();
    await previewAsShown();

    fireEvent.change(screen.getByLabelText(/table/i), { target: { value: "DEPARTMENTS" } });

    // The old preview is gone rather than left describing values that are not sent.
    expect(screen.queryByText(/this will change the database/i)).toBeNull();
    expect(screen.queryByText(/EMPLOYEES/)).toBeNull();
    expect(confirmButton().hasAttribute("disabled")).toBe(true);
    fireEvent.click(confirmButton());
    expect(runRunbook).not.toHaveBeenCalled();

    // Previewing the new values makes them confirmable, and those are what run.
    await previewAsShown({ owner: "HR", table: "DEPARTMENTS" });
    fireEvent.click(confirmButton());
    await screen.findByText(/ran with/i);
    expect(runRunbook).toHaveBeenCalledWith(
      "gather_table_stats",
      "prf_dev",
      { owner: "HR", table: "DEPARTMENTS" },
      true,
    );
  });

  it("does not come back when a parameter is changed back to the previewed value", async () => {
    // Editing and restoring a field is still a change the user made after reading the
    // preview; the preview was discarded, not hidden.
    await selectRunbook();
    await previewAsShown();
    const table = screen.getByLabelText(/table/i);
    fireEvent.change(table, { target: { value: "DEPARTMENTS" } });
    fireEvent.change(table, { target: { value: "EMPLOYEES" } });
    expect(confirmButton().hasAttribute("disabled")).toBe(true);
  });

  it("withdraws the confirmation when the target changes after the preview", async () => {
    const view = await selectRunbook(development);
    await previewAsShown();

    view.rerender(<RunbooksView target={staging} />);

    expect(screen.queryByText(/this will change the database/i)).toBeNull();
    expect(confirmButton().hasAttribute("disabled")).toBe(true);
    fireEvent.click(confirmButton());
    expect(runRunbook).not.toHaveBeenCalled();
  });
});

describe("a preview that answers late", () => {
  it("is dropped if a parameter changed while it was in flight", async () => {
    await selectRunbook();
    const pending = deferred<Preview>();
    previewRunbook.mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByRole("button", { name: /preview/i }));

    fireEvent.change(screen.getByLabelText(/table/i), { target: { value: "DEPARTMENTS" } });
    await act(async () => {
      pending.resolve(previewOf({ owner: "HR", table: "EMPLOYEES" }));
    });

    expect(screen.queryByText(/this will change the database/i)).toBeNull();
    expect(confirmButton().hasAttribute("disabled")).toBe(true);
  });

  it("is dropped if the target changed while it was in flight", async () => {
    const view = await selectRunbook(development);
    const pending = deferred<Preview>();
    previewRunbook.mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByRole("button", { name: /preview/i }));

    view.rerender(<RunbooksView target={staging} />);
    await act(async () => {
      pending.resolve(previewOf({ owner: "HR", table: "EMPLOYEES" }));
    });

    expect(screen.queryByText(/target development/i)).toBeNull();
    expect(confirmButton().hasAttribute("disabled")).toBe(true);
    fireEvent.click(confirmButton());
    expect(runRunbook).not.toHaveBeenCalled();
  });
});

describe("a run in flight", () => {
  it("locks the form, so the result always matches the values shown with it", async () => {
    await selectRunbook();
    await previewAsShown();
    const pending = deferred<RunbookRun>();
    runRunbook.mockReturnValueOnce(pending.promise);

    fireEvent.click(confirmButton());
    expect(screen.getByLabelText(/table/i).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: /preview/i }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: /running/i }).hasAttribute("disabled")).toBe(true);

    await act(async () => {
      pending.resolve(runOf());
    });
    expect(screen.getByText(/ran with/i).textContent).toContain("EMPLOYEES");
    expect(screen.getByLabelText(/table/i).hasAttribute("disabled")).toBe(false);
  });
});
