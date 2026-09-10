/**
 * Adapter contract tests.
 *
 * They run against a stubbed harness, so they check what the adapter does, not what
 * the harness answers: disabled and outage behaviour, protocol negotiation, role
 * filtering, context sanitising, and the rule that a browser cannot supply its own
 * context, role, or target.
 *
 * Run with: node --test integrations/dataforge/test
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  HarnessAdapter,
  filterActionsForRole,
  parseSse,
  protocolCompatible,
  sanitize,
  type AssistRequest,
  type ContextAttachment,
  type ContextResolver,
  type DataForgeActor,
} from "../src/adapter.ts";
import { ConfigurationProblem, describeConfig, readConfig } from "../src/config.ts";
import { parseAssistRequest } from "../src/routes.ts";

const ACTOR: DataForgeActor = { id: "df-user-7", role: "Developer", durable: true };

const RESOLVER: ContextResolver = {
  targetReference: (instanceId, connectionId, schema) =>
    `dataforge:${instanceId}:${connectionId}:${schema ?? ""}`,
  databaseVersion: async () => "19.3.0.0.0",
  resolve: async () => [
    {
      category: "selected_source",
      name: "selection",
      content: "SELECT 1 FROM dual",
      provenance: "editor selection",
    },
  ],
};

function config(overrides: Record<string, string> = {}) {
  return readConfig({
    DATAFORGE_HARNESS_ENABLED: "true",
    DATAFORGE_HARNESS_URL: "http://localhost:8000",
    DATAFORGE_HARNESS_TOKEN: "odbh_test",
    ...overrides,
  });
}

function sseResponse(events: Array<[string, unknown]>): Response {
  const body = events
    .map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`)
    .join("");
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

const REQUEST: AssistRequest = {
  action: "explain",
  connectionId: "conn-9",
  schema: "HARNESS_APP",
  selection: "SELECT 1 FROM dual",
  editorId: "buffer-1",
  editorRevision: "3",
};

// -- configuration --------------------------------------------------------------------

test("configuration refuses to enable without a URL or credential", () => {
  assert.throws(
    () => readConfig({ DATAFORGE_HARNESS_ENABLED: "true" }),
    (error: unknown) => error instanceof ConfigurationProblem,
  );
  assert.throws(
    () =>
      readConfig({
        DATAFORGE_HARNESS_ENABLED: "true",
        DATAFORGE_HARNESS_URL: "https://harness.internal",
      }),
    (error: unknown) =>
      error instanceof ConfigurationProblem && error.field === "DATAFORGE_HARNESS_TOKEN",
  );
});

test("configuration requires https off loopback", () => {
  assert.throws(
    () =>
      readConfig({
        DATAFORGE_HARNESS_ENABLED: "true",
        DATAFORGE_HARNESS_URL: "http://harness.internal",
        DATAFORGE_HARNESS_TOKEN: "odbh_x",
      }),
    (error: unknown) => error instanceof ConfigurationProblem,
  );
  assert.ok(
    readConfig({
      DATAFORGE_HARNESS_ENABLED: "true",
      DATAFORGE_HARNESS_URL: "http://localhost:8000",
      DATAFORGE_HARNESS_TOKEN: "odbh_x",
    }).enabled,
  );
});

test("the described configuration never contains the credential", () => {
  const described = describeConfig(config());
  assert.equal(described.tokenConfigured, true);
  assert.ok(!JSON.stringify(described).includes("odbh_test"));
});

test("a disabled integration still reads its configuration", () => {
  const disabled = readConfig({ DATAFORGE_HARNESS_ENABLED: "false" });
  assert.equal(disabled.enabled, false);
});

// -- capabilities ----------------------------------------------------------------------

test("a disabled integration reports itself without calling anything", async () => {
  let called = false;
  const adapter = new HarnessAdapter({
    config: readConfig({ DATAFORGE_HARNESS_ENABLED: "false" }),
    resolver: RESOLVER,
    instanceId: "inst-1",
    fetchImpl: async () => {
      called = true;
      return new Response("{}");
    },
  });
  const capabilities = await adapter.capabilities(ACTOR);
  assert.equal(capabilities.enabled, false);
  assert.equal(called, false);
  assert.match(capabilities.detail, /disabled/);
});

test("an unreachable harness leaves the IDE usable and says so", async () => {
  const adapter = new HarnessAdapter({
    config: config(),
    resolver: RESOLVER,
    instanceId: "inst-1",
    fetchImpl: async () => {
      throw new Error("ECONNREFUSED");
    },
  });
  const capabilities = await adapter.capabilities(ACTOR);
  assert.equal(capabilities.reachable, false);
  assert.deepEqual(capabilities.actions, []);
  assert.match(capabilities.detail, /unaffected/);
  assert.ok(capabilities.requestId.length > 0);
});

test("an incompatible protocol major offers no actions", async () => {
  const adapter = new HarnessAdapter({
    config: config(),
    resolver: RESOLVER,
    instanceId: "inst-1",
    fetchImpl: async () =>
      new Response(
        JSON.stringify({
          enabled: true,
          protocolVersion: "2.0",
          actions: ["explain", "diagnose"],
          providerReady: true,
          isFixtureProvider: false,
          limits: {},
        }),
        { status: 200 },
      ),
  });
  const capabilities = await adapter.capabilities(ACTOR);
  assert.equal(capabilities.compatible, false);
  assert.deepEqual(capabilities.actions, []);
  assert.match(capabilities.detail, /protocol/);
});

test("a revoked credential is reported, not retried", async () => {
  let calls = 0;
  const adapter = new HarnessAdapter({
    config: config(),
    resolver: RESOLVER,
    instanceId: "inst-1",
    fetchImpl: async () => {
      calls += 1;
      return new Response("{}", { status: 401 });
    },
  });
  const capabilities = await adapter.capabilities(ACTOR);
  assert.equal(capabilities.compatible, false);
  assert.equal(calls, 1);
  assert.match(capabilities.detail, /refused the integration credential/);
});

test("DataForge roles are not a privilege ladder", () => {
  const actions = ["explain", "diagnose", "propose"];
  assert.deepEqual(filterActionsForRole(actions, "Analyst"), ["explain"]);
  assert.deepEqual(filterActionsForRole(actions, "Viewer"), actions);
  assert.deepEqual(filterActionsForRole(actions, "Developer"), actions);
});

// -- assist ----------------------------------------------------------------------------

test("assist forwards resolved context and streams events back", async () => {
  let sent: Record<string, unknown> = {};
  const adapter = new HarnessAdapter({
    config: config(),
    resolver: RESOLVER,
    instanceId: "inst-1",
    fetchImpl: async (_url, init) => {
      sent = JSON.parse(String(init?.body));
      return sseResponse([
        ["start", { requestId: "cop_1" }],
        ["delta", { text: "hello" }],
        ["done", { outcome: "succeeded" }],
      ]);
    },
  });

  const events = [];
  for await (const event of adapter.assist(ACTOR, REQUEST)) events.push(event);

  assert.deepEqual(
    events.map((event) => event.event),
    ["start", "delta", "done"],
  );
  assert.equal(sent.targetReference, "dataforge:inst-1:conn-9:HARNESS_APP");
  assert.equal(sent.actorReference, "df-user-7");
  assert.equal(sent.protocolVersion, "1.0");
  assert.equal((sent.editor as Record<string, unknown>).revision, "3");
  assert.equal((sent.attachments as unknown[]).length, 1);
  // The DataForge role is never sent as authority.
  assert.ok(!("role" in sent));
});

test("an installation without named accounts is reported as a local session", async () => {
  let sent: Record<string, unknown> = {};
  const adapter = new HarnessAdapter({
    config: config(),
    resolver: RESOLVER,
    instanceId: "inst-1",
    fetchImpl: async (_url, init) => {
      sent = JSON.parse(String(init?.body));
      return sseResponse([["done", { outcome: "succeeded" }]]);
    },
  });
  const anonymous: DataForgeActor = { id: null, role: "Developer", durable: false };
  for await (const _ of adapter.assist(anonymous, REQUEST)) void _;
  assert.equal(sent.actorReference, "local-session");
  assert.equal(sent.actorIsDurable, false);
});

test("a harness outage during assist becomes a typed error event", async () => {
  const adapter = new HarnessAdapter({
    config: config(),
    resolver: RESOLVER,
    instanceId: "inst-1",
    fetchImpl: async () => {
      throw new Error("socket hang up");
    },
  });
  const events = [];
  for await (const event of adapter.assist(ACTOR, REQUEST)) events.push(event);
  assert.equal(events.length, 1);
  assert.equal(events[0]?.event, "error");
  assert.equal(events[0]?.data.code, "harness_unreachable");
});

test("assist refuses when the resolver returned nothing and no question was asked", async () => {
  const adapter = new HarnessAdapter({
    config: config(),
    resolver: { ...RESOLVER, resolve: async () => [] },
    instanceId: "inst-1",
    fetchImpl: async () => {
      throw new Error("must not be called");
    },
  });
  const events = [];
  for await (const event of adapter.assist(ACTOR, REQUEST)) events.push(event);
  assert.equal(events[0]?.data.code, "no_context");
});

test("a partially delivered stream is not replayed", async () => {
  let calls = 0;
  const adapter = new HarnessAdapter({
    config: config(),
    resolver: RESOLVER,
    instanceId: "inst-1",
    fetchImpl: async () => {
      calls += 1;
      return sseResponse([
        ["start", { requestId: "cop_1" }],
        ["delta", { text: "partial" }],
      ]);
    },
  });
  const events = [];
  for await (const event of adapter.assist(ACTOR, REQUEST)) events.push(event);
  assert.equal(calls, 1);
  assert.equal(events.at(-1)?.event, "delta");
});

// -- sanitising and request parsing -------------------------------------------------------

test("only allowed context categories are forwarded", () => {
  const attachments = [
    { category: "selected_source", name: "a", content: "x", provenance: "editor" },
    { category: "result_rows", name: "b", content: "y", provenance: "grid" },
    { category: "credentials", name: "c", content: "z", provenance: "config" },
  ] as unknown as ContextAttachment[];
  const kept = sanitize(attachments, 1024);
  assert.deepEqual(
    kept.map((attachment) => attachment.category),
    ["selected_source"],
  );
});

test("context is bounded before it leaves DataForge", () => {
  const attachments = [
    { category: "selected_source", name: "a", content: "x".repeat(200), provenance: "e" },
    { category: "object_definition", name: "b", content: "y".repeat(200), provenance: "e" },
  ] as ContextAttachment[];
  assert.equal(sanitize(attachments, 250).length, 1);
});

test("the browser cannot supply context, a role, or a target", () => {
  for (const field of ["attachments", "role", "actorReference", "targetReference"]) {
    const parsed = parseAssistRequest({
      action: "explain",
      connectionId: "conn-9",
      editorId: "b1",
      editorRevision: "1",
      [field]: "anything",
    });
    assert.ok("error" in parsed, field);
  }
});

test("an assist request needs an editor id and revision", () => {
  const parsed = parseAssistRequest({
    action: "explain",
    connectionId: "conn-9",
    selection: "SELECT 1",
  });
  assert.ok("error" in parsed);
  assert.match(parsed.error, /editorId and editorRevision/);
});

test("protocol compatibility is decided on the major version", () => {
  assert.equal(protocolCompatible("1.0"), true);
  assert.equal(protocolCompatible("1.7"), true);
  assert.equal(protocolCompatible("2.0"), false);
  assert.equal(protocolCompatible("nonsense"), false);
});

test("stream chunks that are not valid events are ignored", () => {
  assert.equal(parseSse("event: delta"), null);
  assert.equal(parseSse("data: {}"), null);
  assert.equal(parseSse("event: delta\ndata: not json"), null);
  assert.deepEqual(parseSse('event: delta\ndata: {"text":"hi"}'), {
    event: "delta",
    data: { text: "hi" },
  });
});
