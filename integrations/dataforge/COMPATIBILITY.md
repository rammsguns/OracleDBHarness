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
| OracleDataForge completion | Pinned `3404f19` with a local adapter patch passed 7 real-route checks through deployed nginx on 2026-10-24 (A6): agent-qualified on test stand-ins (AI agent, not a human). Editor/UI, upstream integration, Oracle and a real provider remain open (human-owned). |
| Oracle 19c | Not exercised through a real DataForge integration. The harness itself has D1–D4 evidence and a 2026-10-24 D1/D2/D4 thin/thick rerun; see [harness evidence](../../docs/compatibility.md). |
| A real model provider | The harness ships an Anthropic adapter. The live-harness run above and the tests use the fixture provider only; no provider call has been made. |
| Proxy coverage beyond shipped nginx | A6 exercised real DataForge routes and chunk delivery through the shipped nginx config. Other deployment proxies, compression settings, ALBs and CDNs remain untested. |
| Ten-minute setup target | Not measured. It is a target for the pilot, not a claim; nothing above involves DataForge's own setup. |

## Release gates still open

NP-05 completion is open (human-owned). A6 is agent-qualified on test stand-ins
(AI agent, not a human), with no pilot or production qualification. From
`ORACLEDATAFORGE_INTEGRATION.md`, these remain:

- Setup measured on an already-configured DataForge and a running harness.
- Explaining a selected statement without registering a second Oracle connection,
  demonstrated on a real installation.
- Editor/UI and upstream integration beyond the local adapter patch; DataForge's own
  279 tests, typecheck and build passed at the pinned commit in A6.
- Verification that worksheet commit/rollback, compilation, role restrictions and
  confirmation dialogs are unchanged by the integration.
- Editor/UI end-to-end coverage, cancellation and partial-response handling; A6
  already passed real-route context, stream, stale/reviewed diff and outage checks.

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

### 2026-10-24, first run against a real OracleDataForge (A6, agent stand-in)

Run by Claude (AI agent, acting application owner, not a human), with tooling by agy, on
the z4g5 test host. This is agent-qualified on test stand-ins, not a pilot qualification.

| Item | Value |
| --- | --- |
| DataForge commit | `3404f1905da183e1903a35193a7d5b0df18d0870` (public HEAD on 2026-10-24) with the adapter patch `spike/dataforge-3404f19.patch` applied on a local branch. The patch was not pushed to DataForge |
| Adapter / protocol | 0.1.0 / 1.0. The adapter route now also flushes each SSE event and sends `Cache-Control: no-cache, no-transform` |
| Harness | Disposable harness (fake Oracle backend, fixture copilot provider) behind the shipped `deploy/nginx.conf`, with only its upstream and listen address rewritten, in `nginx:1.27-alpine` |
| DataForge's own checks | `npm test` 279/279 passed, `typecheck` clean, `build` clean. These match the baseline at the pinned commit |
| Through DataForge's real Express routes, via the proxy | 7/7 passed (`python -m tests.dataforge_live dataforge`) |

The 7 checks:
- capability negotiation;
- streamed `/api/ai/chat`: 9 SSE events arrived as chunks through nginx;
- a stale-revision proposal rejected, and a matching one accepted as reviewable, with no database or git mutation;
- harness outage: a typed `harness_unreachable`, and DataForge's non-AI health route still answered;
- disabled mode;
- a revoked or invalid token refused;
- an incompatible protocol refused.

Still not done, so NP-05 stays open:
- The DataForge editor and UI work: independent worksheet buffers, the apply UI and the
  context preview.
- The integration upstream in the DataForge repository.
- Oracle 19c through DataForge.
- A real model provider.
- Worksheet commit/rollback, compile and confirmation-dialog regression inside DataForge
  beyond its own test suite.
- The ten-minute setup measurement.
