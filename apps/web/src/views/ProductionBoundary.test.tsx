import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { makeTarget } from "./fixtures";
import { SchemaView } from "./SchemaView";
import { TuningView } from "./TuningView";

vi.mock("@monaco-editor/react", () => ({ default: () => <textarea aria-label="editor" /> }));
vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  const panel = {
    available: true, columns: ["LINE", "TEXT"], rows: [[1, "BEGIN NULL; END;"]],
    operationId: "schema.source", collectedAt: "2026-10-22T00:00:00Z",
  };
  return {
    ...actual,
    api: {
      ...actual.api,
      schemas: async () => ({ ...panel, rows: [["HARNESS_APP"]] }),
      objects: async () => ({
        owner: "HARNESS_APP", offset: 0, hasMore: false,
        collectedAt: panel.collectedAt, columns: ["OWNER", "NAME", "TYPE"],
        rows: [["HARNESS_APP", "BOUNDARY_PROBE", "PROCEDURE"]],
      }),
      objectDetail: async () => ({
        owner: "HARNESS_APP", objectName: "BOUNDARY_PROBE", objectType: "PROCEDURE",
        panels: { source: panel },
      }),
      explain: async () => ({ ...panel, note: "Stand-in plan", error: null }),
    },
  };
});

afterEach(cleanup);
const production = makeTarget({ environment: "production" });

describe("production source and plan copilot controls", () => {
  it("allows source observation and disables sending it to Kiwi", async () => {
    const onAsk = vi.fn();
    render(<SchemaView target={production} onAsk={onAsk} />);
    fireEvent.click(await screen.findByText("BOUNDARY_PROBE"));
    const ask = await screen.findByRole("button", { name: /explain this source/i });
    expect(ask.hasAttribute("disabled")).toBe(true);
    fireEvent.click(ask);
    expect(onAsk).not.toHaveBeenCalled();
  });

  it("disables asking Kiwi about an observed plan", async () => {
    const onAsk = vi.fn();
    render(<TuningView target={production} onAsk={onAsk} />);
    fireEvent.click(screen.getByRole("button", { name: /^explain$/i }));
    const ask = await screen.findByRole("button", { name: /ask kiwi about this plan/i });
    expect(ask.hasAttribute("disabled")).toBe(true);
    fireEvent.click(ask);
    expect(onAsk).not.toHaveBeenCalled();
  });
});
