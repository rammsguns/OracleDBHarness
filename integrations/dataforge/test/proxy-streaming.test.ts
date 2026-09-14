/**
 * Streaming through a proxy - the item COMPATIBILITY.md lists as untested.
 *
 * This does not involve DataForge, the harness, or the adapter: it is a self-contained
 * check of the mechanism the `/api/ai/chat` route relies on (`response.write` per SSE
 * event, `X-Accel-Buffering: no`) against the two proxy shapes that matter in practice -
 * one that relays each chunk as it arrives, and one that reads the whole response
 * before writing anything, which is what a compressing or buffering proxy does. It
 * proves the difference is observable at all, which is what makes the second, real
 * check in live-harness.test.ts (an actual harness, no synthetic delay) meaningful
 * even though its answer streams almost instantly.
 *
 * It does not reproduce a real reverse proxy such as nginx or a load balancer with
 * compression enabled - only the buffering behaviour that such a proxy can introduce.
 *
 * Run with: node --test test/proxy-streaming.test.ts
 */

import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer, request as httpRequest, type Server } from "node:http";
import test from "node:test";

const CHUNK_DELAY_MS = 40;
const CHUNK_COUNT = 4;

async function listen(server: Server): Promise<number> {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (address === null || typeof address === "string") {
    throw new Error("expected a network address");
  }
  return address.port;
}

/** An SSE origin that writes `CHUNK_COUNT` events, `CHUNK_DELAY_MS` apart. */
function startOrigin(): Promise<{ server: Server; port: number }> {
  const server = createServer((_request, response) => {
    response.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "X-Accel-Buffering": "no",
    });
    let sent = 0;
    const timer = setInterval(() => {
      sent += 1;
      response.write(`event: delta\ndata: {"index":${sent}}\n\n`);
      if (sent >= CHUNK_COUNT) {
        clearInterval(timer);
        response.end();
      }
    }, CHUNK_DELAY_MS);
    _request.on("close", () => clearInterval(timer));
  });
  return listen(server).then((port) => ({ server, port }));
}

/** Relays each upstream chunk to the client as soon as it arrives. */
function startPassthroughProxy(targetPort: number): Promise<{ server: Server; port: number }> {
  const server = createServer((request, response) => {
    const upstream = httpRequest(
      { host: "127.0.0.1", port: targetPort, path: request.url, method: request.method },
      (upstreamResponse) => {
        response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
        upstreamResponse.on("data", (chunk: Buffer) => response.write(chunk));
        upstreamResponse.on("end", () => response.end());
      },
    );
    upstream.end();
  });
  return listen(server).then((port) => ({ server, port }));
}

/**
 * The negative control: reads the whole upstream response before writing anything,
 * the way a proxy that buffers or recompresses a response body does. If this test
 * suite cannot tell this apart from the passthrough proxy, it cannot tell a real
 * buffering regression apart from a working deployment either.
 */
function startBufferingProxy(targetPort: number): Promise<{ server: Server; port: number }> {
  const server = createServer((request, response) => {
    const upstream = httpRequest(
      { host: "127.0.0.1", port: targetPort, path: request.url, method: request.method },
      (upstreamResponse) => {
        const chunks: Buffer[] = [];
        upstreamResponse.on("data", (chunk: Buffer) => chunks.push(chunk));
        upstreamResponse.on("end", () => {
          response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
          response.end(Buffer.concat(chunks));
        });
      },
    );
    upstream.end();
  });
  return listen(server).then((port) => ({ server, port }));
}

async function timeChunks(url: string): Promise<{ chunkTimesMs: number[]; totalMs: number }> {
  const start = performance.now();
  const response = await fetch(url);
  const reader = response.body?.getReader();
  assert.ok(reader, "expected a readable response body");
  const chunkTimesMs: number[] = [];
  for (;;) {
    const { done } = await reader.read();
    if (done) break;
    chunkTimesMs.push(performance.now() - start);
  }
  return { chunkTimesMs, totalMs: performance.now() - start };
}

test("a passthrough proxy delivers events as they arrive, not after the origin finishes", async () => {
  const origin = await startOrigin();
  const proxy = await startPassthroughProxy(origin.port);
  try {
    const { chunkTimesMs, totalMs } = await timeChunks(`http://127.0.0.1:${proxy.port}/`);
    assert.ok(
      chunkTimesMs.length >= 2,
      `expected several separately-delivered reads, got ${chunkTimesMs.length}`,
    );
    // The first byte should show up well before the origin is done, not at the end.
    assert.ok(
      chunkTimesMs[0]! < totalMs * 0.7,
      `first chunk arrived at ${chunkTimesMs[0]}ms of ${totalMs}ms total - looks buffered`,
    );
  } finally {
    proxy.server.close();
    origin.server.close();
  }
});

test("a proxy that buffers the full response erases the streaming benefit (negative control)", async () => {
  const origin = await startOrigin();
  const proxy = await startBufferingProxy(origin.port);
  try {
    const { chunkTimesMs, totalMs } = await timeChunks(`http://127.0.0.1:${proxy.port}/`);
    // Everything arrives in one shot, at the end - the failure mode this suite exists
    // to catch. This assertion is the proof that the passthrough test above is not
    // passing by accident: a genuinely buffering proxy fails it.
    assert.ok(
      chunkTimesMs[0]! > totalMs * 0.7,
      `expected the buffering proxy to withhold everything until the end; first chunk ` +
        `arrived at ${chunkTimesMs[0]}ms of ${totalMs}ms total`,
    );
  } finally {
    proxy.server.close();
    origin.server.close();
  }
});
