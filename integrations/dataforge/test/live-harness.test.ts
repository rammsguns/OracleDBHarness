/**
 * The real adapter and route-registration code against a real, running harness API.
 *
 * adapter.test.ts checks the adapter's own logic against a stubbed `fetch`; nothing in
 * this package has ever made a real HTTP call. This file removes the stub: it expects
 * `DATAFORGE_LIVE_HARNESS_URL` and `DATAFORGE_LIVE_HARNESS_TOKEN` to name a harness
 * process that is already running with the fixture copilot provider enabled, and it
 * drives `HarnessAdapter` and `registerHarnessRoutes` against it exactly as DataForge's
 * own backend would.
 *
 * It is skipped, not failed, when those variables are absent - which is every ordinary
 * `npm test`. `python -m tests.dataforge_live run` is what sets them: it starts the
 * harness, provisions the credential, and runs this file. There is still no real
 * OracleDataForge checkout and no real model provider anywhere in this run; see
 * `tests/dataforge_live/__init__.py`.
 *
 * Run with: node --test test/live-harness.test.ts
 */

import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import test from "node:test";

import { HarnessAdapter, type AssistRequest, type ContextResolver, type DataForgeActor } from "../src/adapter.ts";
import { readConfig } from "../src/config.ts";
import {
  registerHarnessRoutes,
  type MinimalRequest,
  type MinimalResponse,
  type MinimalRouter,
  type RouteHandler,
} from "../src/routes.ts";

const HARNESS_URL = process.env.DATAFORGE_LIVE_HARNESS_URL;
const HARNESS_TOKEN = process.env.DATAFORGE_LIVE_HARNESS_TOKEN;
const skip = HARNESS_URL && HARNESS_TOKEN
  ? false
  : "DATAFORGE_LIVE_HARNESS_URL and DATAFORGE_LIVE_HARNESS_TOKEN are not set; this " +
    "file only runs under `python -m tests.dataforge_live run`, which starts a real " +
    "harness process. See tests/dataforge_live/__init__.py.";

const ACTOR: DataForgeActor = { id: "df-live-user", role: "Developer", durable: true };

// A minimal stand-in for the real thing: resolveContextForActor is DataForge's own job
// (see integrations/dataforge/README.md, "Wiring it up"). This just has to return
// something so the adapter has context to forward to a live harness.
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

const REQUEST: AssistRequest = {
  action: "explain",
  connectionId: "conn-live-1",
  schema: "HARNESS_APP",
  selection: "SELECT 1 FROM dual",
  editorId: "buffer-live-1",
  editorRevision: "1",
};

function adapter(): HarnessAdapter {
  return new HarnessAdapter({
    config: readConfig({
      DATAFORGE_HARNESS_ENABLED: "true",
      DATAFORGE_HARNESS_URL: HARNESS_URL ?? "",
      DATAFORGE_HARNESS_TOKEN: HARNESS_TOKEN ?? "",
    }),
    resolver: RESOLVER,
    instanceId: "live-1",
  });
}

test("the real adapter negotiates capabilities with a real running harness", { skip }, async () => {
  const capabilities = await adapter().capabilities(ACTOR);
  assert.equal(capabilities.reachable, true, capabilities.detail);
  assert.equal(capabilities.compatible, true, capabilities.detail);
  assert.equal(
    capabilities.isFixtureProvider,
    true,
    "this run must be against the fixture provider - it is not evidence about a real model",
  );
  assert.ok(capabilities.actions.includes("explain"));
});

test("a real assist request streams several real SSE events from the harness", { skip }, async () => {
  const events = [];
  for await (const event of adapter().assist(ACTOR, REQUEST)) events.push(event);
  assert.ok(
    events.length >= 3,
    `expected start, at least one delta and done; got ${JSON.stringify(events.map((e) => e.event))}`,
  );
  assert.equal(events[0]?.event, "start");
  assert.equal(events.at(-1)?.event, "done");
  assert.ok(events.some((event) => event.event === "delta"));
});

// -- registerHarnessRoutes over a real Node HTTP server, exercised by a real client ----

interface CompiledRoute {
  method: string;
  pattern: RegExp;
  paramNames: string[];
  handler: RouteHandler;
}

function compile(path: string): { pattern: RegExp; paramNames: string[] } {
  const paramNames: string[] = [];
  const source = path.replace(/:([A-Za-z]+)/g, (_match, name: string) => {
    paramNames.push(name);
    return "([^/]+)";
  });
  return { pattern: new RegExp(`^${source}$`), paramNames };
}

function readBody(request: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let body = "";
    request.on("data", (chunk: Buffer) => (body += chunk.toString("utf8")));
    request.on("end", () => resolve(body));
    request.on("error", reject);
  });
}

/**
 * A raw `http.Server` playing the part of DataForge's Express app: it has no framework,
 * just `registerHarnessRoutes` wired to real request/response objects, the same way
 * integrations/dataforge/README.md's "Wiring it up" section shows.
 */
function createDataForgeStandIn(
  harnessAdapter: HarnessAdapter,
  currentActor: () => Promise<DataForgeActor | null> = async () => ACTOR,
): Server {
  const routes: CompiledRoute[] = [];
  const router: MinimalRouter = {
    get(path, handler) {
      routes.push({ method: "GET", handler, ...compile(path) });
    },
    post(path, handler) {
      routes.push({ method: "POST", handler, ...compile(path) });
    },
  };
  registerHarnessRoutes({
    router,
    adapter: harnessAdapter,
    currentActor,
  });

  return createServer((request: IncomingMessage, response: ServerResponse) => {
    void (async () => {
      const url = new URL(request.url ?? "/", "http://127.0.0.1");
      const route = routes.find(
        (candidate) => candidate.method === request.method && candidate.pattern.test(url.pathname),
      );
      if (!route) {
        response.statusCode = 404;
        response.end();
        return;
      }
      const match = route.pattern.exec(url.pathname);
      const params: Record<string, string> = {};
      route.paramNames.forEach((name, index) => {
        params[name] = match?.[index + 1] ?? "";
      });
      const bodyText = await readBody(request);
      const minimalRequest: MinimalRequest = {
        body: bodyText ? (JSON.parse(bodyText) as Record<string, unknown>) : {},
        params,
        on: (event, listener) => {
          if (event === "close") request.on("close", listener);
        },
      };
      const minimalResponse: MinimalResponse = {
        status(code) {
          response.statusCode = code;
          return minimalResponse;
        },
        json(body) {
          response.setHeader("Content-Type", "application/json");
          response.end(JSON.stringify(body));
        },
        setHeader: (name, value) => response.setHeader(name, value),
        write: (chunk) => response.write(chunk),
        end: () => response.end(),
        flushHeaders: () => response.flushHeaders(),
      };
      await route.handler(minimalRequest, minimalResponse);
    })();
  });
}

async function listen(server: Server): Promise<number> {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (address === null || typeof address === "string") throw new Error("expected a network address");
  return address.port;
}

test(
  "the DataForge-side routes serve a real client end to end, through the real harness",
  { skip },
  async () => {
    const server = createDataForgeStandIn(adapter());
    const port = await listen(server);
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/ai/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: REQUEST.action,
          connectionId: REQUEST.connectionId,
          schema: REQUEST.schema,
          selection: REQUEST.selection,
          editorId: REQUEST.editorId,
          editorRevision: REQUEST.editorRevision,
        }),
      });
      assert.equal(response.status, 200);
      assert.equal(response.headers.get("content-type"), "text/event-stream");
      const text = await response.text();
      assert.match(text, /event: start/);
      assert.match(text, /event: done/);
    } finally {
      server.close();
      await once(server, "close");
    }
  },
);

test("the stand-in's own 401 path works when DataForge has no session", { skip }, async () => {
  const noActorServer = createDataForgeStandIn(adapter(), async () => null);
  const port = await listen(noActorServer);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/api/ai/capabilities`);
    assert.equal(response.status, 401);
  } finally {
    noActorServer.close();
    await once(noActorServer, "close");
  }
});
