import assert from "node:assert/strict";
import test from "node:test";
import { HarnessAdapter } from "../src/adapter.ts";
import { parseAssistRequest, registerHarnessRoutes, type MinimalRequest, type MinimalResponse } from "../src/routes.ts";

test("assist route parsing preserves complete editor text", () => {
  const parsed = parseAssistRequest({action: "explain", connectionId: "conn", selection: "SELECT 1", editorId: "editor", editorRevision: "1", editorText: "SELECT 1; SELECT 2"});
  assert.ok(!("error" in parsed));
  assert.equal(parsed.request.editorText, "SELECT 1; SELECT 2");
});

for (const finished of [false, true]) {
  test(`response close aborts only unfinished streams (finished=${finished})`, async () => {
    let chat: ((request: MinimalRequest, response: MinimalResponse) => void | Promise<void>) | undefined;
    let close = () => {};
    let signal: AbortSignal | undefined;
    const adapter = new HarnessAdapter({ config: {enabled: true, baseUrl: "http://localhost", token: "test", timeoutMs: 1000, maxContextBytes: 1024, adapterVersion: "0.1.0"}, instanceId: "test",
      resolver: {targetReference: () => "target", databaseVersion: async () => "", resolve: async () => []},
      fetchImpl: async (_url, init) => { signal = init?.signal ?? undefined; return new Response('event: delta\ndata: {"text":"first"}\n\n'); },
    });
    registerHarnessRoutes({ adapter, currentActor: async () => ({id: "user", role: "Developer", durable: true}), router: {
      get: () => {}, post: (path, handler) => { if (path === "/api/ai/chat") chat = handler; },
    }});
    const response: MinimalResponse = {writableFinished: finished, on: (_event, listener) => {close = listener;}, status: () => response, json: () => {}, setHeader: () => {}, write: () => {close(); return true;}, end: () => {}};
    assert.ok(chat);
    await chat({body: {action: "explain", question: "Explain", connectionId: "conn", editorId: "editor", editorRevision: "1"}, params: {}, on: () => assert.fail("request close must not control cancellation")}, response);
    assert.equal(signal?.aborted, !finished);
  });
}
