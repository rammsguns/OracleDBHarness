# Compatibility matrix

Nothing in this table is a certification. It records what has actually been run.

## Tested

| Component | Version | How it was tested |
| --- | --- | --- |
| Adapter | 0.1.0 | `node --test test/adapter.test.ts` against a stubbed harness |
| Protocol | 1.0 | Negotiation, and refusal of major version 2 |
| Harness API | 0.1.0 | End-to-end through the harness test suite |
| Node | 24.x | Native TypeScript execution, no build step |
| Adapter against a real harness process | 0.1.0 | `python -m tests.dataforge_live run` starts a disposable harness API (fake Oracle backend, fixture copilot provider) and runs `HarnessAdapter` and `registerHarnessRoutes` from `src/` against it over real HTTP, including a raw `http.Server` playing DataForge's part. Real capability negotiation, a real streamed assist response and a real `/api/ai/chat` round trip. See *2026-09-14* below. |
| The streaming mechanism through a proxy | `test/proxy-streaming.test.ts` (always run, no live harness needed) shows a passthrough proxy delivers SSE chunks as they arrive and a proxy that reads the full response first collapses them into one - proving the distinction is detectable at all, before trusting a real deployment's proxy not to do the latter. |

## Not tested

| Component | Status |
| --- | --- |
| OracleDataForge | **Not integrated.** No DataForge commit has been run against this adapter. The inspected commit in `ORACLEDATAFORGE_INTEGRATION.md` is the first test candidate, not a baseline. |
| Oracle 19c | Not exercised through a real DataForge integration. The harness itself has passing evidence on one 19.9 non-CDB/thin instance; see `docs/compatibility.md`. |
| A real model provider | The harness ships an Anthropic adapter. The live-harness run above and the tests use the fixture provider only; no provider call has been made. |
| A real network/compression proxy | `test/proxy-streaming.test.ts` shows the buffering-vs-passthrough distinction is detectable in principle, with a synthetic origin and synthetic proxies. It says nothing about a specific real proxy (nginx, an ALB, a CDN) in front of a real deployment; the routes' `X-Accel-Buffering: no` header is not sufficient on its own against one that recompresses or buffers regardless. |
| Ten-minute setup target | Not measured. It is a target for the pilot, not a claim; nothing above involves DataForge's own setup. |

## Release gates still open

From `ORACLEDATAFORGE_INTEGRATION.md`, these are unmet:

- Setup measured on an already-configured DataForge and a running harness.
- Explaining a selected statement without registering a second Oracle connection,
  demonstrated on a real installation.
- Verification that existing DataForge tests, typecheck and build still pass.
- Verification that worksheet commit/rollback, compilation, role restrictions and
  confirmation dialogs are unchanged by the integration.
- End-to-end context, stream and diff tests inside DataForge, including outage,
  cancellation and partial-response handling.

## Recording a run

When the integration is first exercised against a real DataForge, add a row here
with: harness build, DataForge commit, adapter version, protocol version, Oracle
version, and what was and was not exercised. Do not replace this file with a summary;
append.

### 2026-09-14, adapter-against-a-real-harness preparation (NP-05)

Run locally on Windows. No OracleDataForge checkout, no Oracle database and no model
provider were used, so nothing here is DataForge integration evidence - it closes the
"tests here run against a stubbed harness" and "streaming through a proxy: untested"
lines above, not NP-05 itself.

- New: `test/proxy-streaming.test.ts` (self-contained; runs in the `adapter` CI job
  with no other services) and `test/live-harness.test.ts` (skipped without
  `DATAFORGE_LIVE_HARNESS_URL`/`_TOKEN`, which only `python -m tests.dataforge_live
  run` sets). That package starts a disposable harness API in development auth mode
  with the fake Oracle backend and the fixture copilot provider, registers an
  administrator directly on its throwaway store (a development token still needs a
  registered account), issues a real `dataforge` integration credential over HTTP,
  and runs the two new test files against it.
- `node --test test/adapter.test.ts test/proxy-streaming.test.ts test/live-harness.test.ts`:
  22 passed, 4 skipped (the live-harness checks, without a running harness). Under
  `python -m tests.dataforge_live run`, with the harness up: 6 passed, 0 failed - real
  capability negotiation, a real streamed assist response with distinct `start`,
  `delta` and `done` events, a real `/api/ai/chat` round trip served by a raw
  `http.Server` standing in for DataForge's Express app, and its 401 path with no
  session. `tsc --noEmit` and the repository's `ruff check`, `ruff format --check` and
  `mypy` (adding `tests/dataforge_live`) are clean; full Python suite 564 passed, 72
  skipped.
- The proxy test's negative control matters more than the happy path: a proxy that
  reads the whole response before writing anything (what a compressing or buffering
  proxy does) is asserted to collapse the response to what looks like one chunk
  delivered at the end, proving the passthrough assertion is not passing by accident.
- Owed for this preparation to become NP-05 evidence: everything in "Not tested"
  above, principally a real DataForge checkout and its own release gates.
