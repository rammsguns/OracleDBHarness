/**
 * The worksheet has to stay tied to the target the user is looking at.
 *
 * A worksheet session is a live Oracle connection that may hold an open transaction.
 * If a response that was started before a target switch is applied afterwards, the
 * console shows a session from one database under the heading of another, and the
 * commit button then acts on the wrong one. These tests switch targets while a
 * request is in flight and assert that the late answer is inert.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HarnessError } from "@contracts";

import { deferred, makeOutcome, makePolicy, makeSession, makeTarget } from "./fixtures";
import { WorksheetView } from "./WorksheetView";

vi.mock("@monaco-editor/react", () => ({
  // The real editor loads its bundle over the network, which jsdom cannot do. The
  // component under test only passes text through it.
  default: ({ value }: { value: string }) => <textarea readOnly value={value} />,
}));

const openWorksheet = vi.fn();
const worksheets = vi.fn();
const execute = vi.fn();
const commit = vi.fn();
const rollback = vi.fn();
const cancel = vi.fn();
const closeWorksheet = vi.fn();

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      openWorksheet: (...args: unknown[]) => openWorksheet(...args),
      worksheets: (...args: unknown[]) => worksheets(...args),
      execute: (...args: unknown[]) => execute(...args),
      commit: (...args: unknown[]) => commit(...args),
      rollback: (...args: unknown[]) => rollback(...args),
      cancel: (...args: unknown[]) => cancel(...args),
      closeWorksheet: (...args: unknown[]) => closeWorksheet(...args),
    },
  };
});

const development = makeTarget({ id: "prf_dev", name: "development" });
const staging = makeTarget({ id: "prf_stage", name: "staging", environment: "staging" });

const noop = () => undefined;

function show(target = development) {
  return render(<WorksheetView target={target} onAsk={noop} />);
}

beforeEach(() => {
  worksheets.mockResolvedValue({ sessions: [] });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("switching targets while a request is in flight", () => {
  it("ignores a session that finished opening after the user moved on", async () => {
    const opening = deferred<{ session: ReturnType<typeof makeSession>; note: string }>();
    openWorksheet.mockReturnValue(opening.promise);

    const view = show(development);
    await screen.findByRole("button", { name: /open a worksheet session/i });
    fireEvent.click(screen.getByRole("button", { name: /open a worksheet session/i }));

    view.rerender(<WorksheetView target={staging} onAsk={noop} />);
    await act(async () => {
      opening.resolve({ session: makeSession({ sessionId: "ws_dev" }), note: "" });
    });

    // The session opened on development must not appear while staging is selected.
    expect(screen.queryByText("ws_dev")).toBeNull();
    expect(
      screen.getByRole("button", { name: /open a worksheet session/i }),
    ).toBeTruthy();
  });

  it("ignores a statement that finished running after the user moved on", async () => {
    worksheets.mockResolvedValue({ sessions: [makeSession({ sessionId: "ws_dev" })] });
    const running = deferred<unknown>();
    execute.mockReturnValue(running.promise);

    const view = show(development);
    await screen.findByText("ws_dev");
    fireEvent.click(screen.getByRole("button", { name: /run statement/i }));
    expect(execute).toHaveBeenCalledTimes(1);

    worksheets.mockResolvedValue({
      sessions: [makeSession({ sessionId: "ws_stage", targetId: "prf_stage" })],
    });
    view.rerender(<WorksheetView target={staging} onAsk={noop} />);
    await screen.findByText("ws_stage");
    await act(async () => {
      running.resolve({
        outcome: makeOutcome({ executionId: "exe_dev" }),
        policy: makePolicy(),
        session: makeSession({ sessionId: "ws_dev", transactionOpen: true }),
      });
    });

    // No result from the other target, and no session smuggled in with it.
    expect(screen.queryByText(/^Result/)).toBeNull();
    expect(screen.queryByText("ws_dev")).toBeNull();
    expect(screen.getByText("ws_stage")).toBeTruthy();
    // Staging is usable: the run that belonged to development left no "Running..."
    // state behind to block it.
    const runButton = screen.getByRole("button", { name: /run statement/i });
    expect(runButton.hasAttribute("disabled")).toBe(false);
  });

  it("does not close a session when its target is navigated away from", async () => {
    const devSession = makeSession({ sessionId: "ws_dev", transactionOpen: true });
    const stageSession = makeSession({ sessionId: "ws_stage", targetId: "prf_stage" });
    worksheets.mockResolvedValue({ sessions: [devSession, stageSession] });

    const view = show(development);
    expect(await screen.findByText("ws_dev")).toBeTruthy();
    expect(screen.getByText("transaction open")).toBeTruthy();

    view.rerender(<WorksheetView target={staging} onAsk={noop} />);
    expect(await screen.findByText("ws_stage")).toBeTruthy();
    expect(screen.queryByText("ws_dev")).toBeNull();

    // Coming back finds the pending transaction still there and still reachable.
    view.rerender(<WorksheetView target={development} onAsk={noop} />);
    expect(await screen.findByText("ws_dev")).toBeTruthy();
    expect(screen.getByText("transaction open")).toBeTruthy();
    expect(closeWorksheet).not.toHaveBeenCalled();
  });
});

describe("acting on a session", () => {
  it("refuses to execute on a session that belongs to another target", async () => {
    worksheets.mockResolvedValue({ sessions: [] });
    openWorksheet.mockResolvedValue({
      // A session whose target does not match the one on screen: whatever produced it,
      // running on it would touch a database the user is not looking at.
      session: makeSession({ sessionId: "ws_other", targetId: "prf_stage" }),
      note: "",
    });

    show(development);
    fireEvent.click(await screen.findByRole("button", { name: /open a worksheet session/i }));
    await screen.findByText("ws_other");

    fireEvent.click(screen.getByRole("button", { name: /run statement/i }));
    await screen.findByText(/belongs to a different target/i);
    expect(execute).not.toHaveBeenCalled();
  });

  it("refuses to commit a session that belongs to another target", async () => {
    worksheets.mockResolvedValue({ sessions: [] });
    openWorksheet.mockResolvedValue({
      session: makeSession({
        sessionId: "ws_other",
        targetId: "prf_stage",
        transactionOpen: true,
      }),
      note: "",
    });

    show(development);
    fireEvent.click(await screen.findByRole("button", { name: /open a worksheet session/i }));
    await screen.findByText("ws_other");

    fireEvent.click(screen.getByRole("button", { name: /^commit$/i }));
    await screen.findByText(/belongs to a different target/i);
    expect(commit).not.toHaveBeenCalled();
  });

  it("tells the user to verify when a commit's outcome is unknown", async () => {
    const session = makeSession({ sessionId: "ws_dev", transactionOpen: true });
    worksheets.mockResolvedValueOnce({ sessions: [session] });
    commit.mockRejectedValue(
      new HarnessError(502, {
        code: "outcome_unknown",
        message: "The connection was lost while the commit was in flight.",
        detail: { verificationRequired: true },
        retryable: false,
      }),
    );
    // The session was retired by the failure, so the refresh finds nothing.
    worksheets.mockResolvedValue({ sessions: [] });

    show(development);
    await screen.findByText("ws_dev");
    fireEvent.click(screen.getByRole("button", { name: /^commit$/i }));

    const notice = await screen.findByRole("alert");
    expect(notice.textContent).toContain("Verification required");
    expect(notice.textContent).toContain("lost while the commit was in flight");
    expect(notice.textContent).toContain("has not retried it and will not");

    // The retired session is gone from the toolbar, so it cannot be committed again.
    await waitFor(() => expect(screen.queryByText("ws_dev")).toBeNull());
    expect(rollback).not.toHaveBeenCalled();
    expect(cancel).not.toHaveBeenCalled();
  });

  it("drops a session the server retired while the statement was running", async () => {
    // A cancellation the statement did not obey, a lost connection, access withdrawn
    // mid-flight: the execution answers with the outcome and a session marked closed.
    // Keeping that session on screen leaves every button in the toolbar pointing at
    // something the server has already dropped.
    worksheets.mockResolvedValue({ sessions: [makeSession({ sessionId: "ws_dev" })] });
    execute.mockResolvedValue({
      outcome: makeOutcome({ executionId: "exe_1", state: "cancelled" }),
      policy: makePolicy(),
      session: makeSession({
        sessionId: "ws_dev",
        closed: true,
        closeReason: "the statement did not stop after cancellation",
      }),
    });

    show(development);
    await screen.findByText("ws_dev");
    fireEvent.click(screen.getByRole("button", { name: /run statement/i }));

    // The outcome is still reported -- it is the session that is gone.
    expect(await screen.findByText(/^Result/)).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("ws_dev")).toBeNull());
    expect(screen.getByText(/did not stop after cancellation/i)).toBeTruthy();

    // Nothing is left offering to act on it, and opening a new one is.
    expect(screen.queryByRole("button", { name: /^commit$/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /cancel statement/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /close session/i })).toBeNull();
    expect(screen.getByRole("button", { name: /open a worksheet session/i })).toBeTruthy();
  });

  it("drops a session the server says is gone when the statement is refused", async () => {
    // The other half of the same problem: the session is not reported back closed, the
    // request is refused outright because it no longer exists -- reaped while idle,
    // closed from another tab, retired before the statement was sent. Held on to, Run
    // stays pointed at a dead session and Open Session never reappears, so every retry
    // fails exactly the same way until the page is reloaded.
    worksheets.mockResolvedValue({ sessions: [makeSession({ sessionId: "ws_dev" })] });
    execute.mockRejectedValue(
      new HarnessError(409, {
        code: "session_expired",
        message: "The worksheet session expired while idle.",
        detail: { sessionId: "ws_dev" },
        retryable: false,
      }),
    );

    show(development);
    await screen.findByText("ws_dev");
    fireEvent.click(screen.getByRole("button", { name: /run statement/i }));

    expect(await screen.findByText(/expired while idle/i)).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("ws_dev")).toBeNull());
    expect(screen.queryByRole("button", { name: /^commit$/i })).toBeNull();
    expect(screen.getByRole("button", { name: /open a worksheet session/i })).toBeTruthy();
  });

  it("keeps the session when the statement itself failed", async () => {
    // A statement Oracle refused says nothing about the session. Dropping it here
    // would throw away a live connection, and with it any open transaction.
    worksheets.mockResolvedValue({
      sessions: [makeSession({ sessionId: "ws_dev", transactionOpen: true })],
    });
    execute.mockRejectedValue(
      new HarnessError(400, {
        code: "oracle_error",
        message: "ORA-00942: table or view does not exist",
        detail: { oracleCode: "ORA-00942" },
        retryable: false,
      }),
    );

    show(development);
    await screen.findByText("ws_dev");
    fireEvent.click(screen.getByRole("button", { name: /run statement/i }));

    expect(await screen.findByText(/ORA-00942/)).toBeTruthy();
    expect(screen.getByText("ws_dev")).toBeTruthy();
    expect(screen.getByText("transaction open")).toBeTruthy();
    expect(screen.getByRole("button", { name: /^commit$/i })).toBeTruthy();
  });
});
