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
| OracleDataForge completion | Pinned `3404f19` plus local patch commit `2883f60`: B1 passed 13/16 real-route/backend checks with Oracle and the local model through shipped nginx on 2026-10-24, agent-qualified on test stand-ins (AI agent, not a human). Registered-profile explain, viewer assist refusal and cancellation failed; editor/UI, upstream and human qualification remain open. |
| Oracle 19c | D1 19.9.1.0.0 bootstrapped/probed in B1 alongside real DataForge, but selected-statement assist did not execute against its registered profile. That requirement remains unqualified. D2 23.26.3.0.0 worksheet/compile regression passed through harness HTTP; see B1 below and [harness evidence](../../docs/compatibility.md). |
| A real model provider | B1 exercised the real local `qwen3.8-flash-next-iq3_s` provider through DataForge and nginx. Anthropic and other hosted providers remain untested through this integration. |
| Proxy coverage beyond shipped nginx | A6 exercised real DataForge routes and chunk delivery through the shipped nginx config. Other deployment proxies, compression settings, ALBs and CDNs remain untested. |
| Ten-minute setup target | B1 measured 19.277 seconds from configured/healthy DataForge and harness to the first complete successful assist, using scripted steps. Initial installation, provisioning and human setup remain unmeasured. |

## Release gates still open

NP-05 completion is open (human-owned). A6 is agent-qualified on test stand-ins
(AI agent, not a human), with no pilot or production qualification. From
`ORACLEDATAFORGE_INTEGRATION.md`, these remain:

- Human pilot setup; B1 measured the already-configured, running-stack scripted path in 19.277 seconds.
- Explaining a selected statement without registering a second Oracle connection,
  demonstrated on a real installation.
- Editor/UI and upstream integration beyond the local adapter patch; DataForge's own
  279 tests, typecheck and build passed at the pinned commit in A6.
- DataForge editor/UI regression for worksheet, compilation and confirmation dialogs; B1 passed harness HTTP worksheet/compile and backend confirmation refusal, but viewer assist refusal failed.
- Editor/UI end-to-end coverage and cancellation; B1 passed typed partial-provider failure through real routes, but client abort left a succeeded harness record.

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

### 2026-10-24, Oracle and real local provider (B1, agent stand-ins)

**agent-qualified on test stand-ins (AI agent, not a human)**. Run by Codex on
z4g5; this is backend/route evidence, with no human pilot or production qualification.
B1/NP-05 remains open because three requested checks failed.

| Item | Value |
| --- | --- |
| Harness baseline build | `c5844cb863d4a08923f64839e0bb59d030840b40`, package 0.1.0, with the B1 runner changes in this branch |
| DataForge | Package 1.2.0, local adapter-patch commit `2883f605bc5e0b7632583b62a00a094d957df6a0` on upstream `3404f1905da183e1903a35193a7d5b0df18d0870`; not pushed to DataForge |
| Adapter / protocols | Adapter 0.1.0; adapter request protocol 1.1, harness capability protocol 1.5 (major-compatible) |
| Runtime / metadata / proxy | Node v24.18.1; python-oracledb 4.0.2 thin; disposable `postgres:16-alpine`; `nginx:1.27-alpine` with shipped `deploy/nginx.conf` (only listen/upstream rewritten) |
| Oracle test targets | `load-db-1` D1 19c reports 19.9.1.0.0; `load-db-2` D2 23ai PDB reports 23.26.3.0.0; both `harness_app`, provisioned with `scripts/bootstrap_targets.py` |
| Real provider | `local`, `local_only=true`, `http://127.0.0.1:8080/v1`, `qwen3.8-flash-next-iq3_s`; health checked; key resolved by file-backed secret reference; calls serialized |
| Live result | **13/16 checks passed, 3 failed**, runner exit 1 |
| Setup | **19.277 seconds**, fully scripted on the already-configured/running stack |
| Offline verification | `pytest -q tests/unit tests/test_dataforge_real.py`: 782 passed, 7 skipped; `ruff check` and `ruff format --check` clean; Node tests: 23 passed, 4 live-launcher-only skips |
| Reports (local, git-ignored) | `databases/reports/2026-10-24-b1-dataforge-oracle.md` and `.json` |

Check-by-check evidence:

- PASS capabilities: harness 1.5 accepted by adapter 1.1.
- PASS streaming: 114 network chunks / 114 SSE events through DataForge and nginx,
  real local provider, terminal `succeeded`.
- PASS context/connection registry: selected-source context only, D1 opaque target
  reference and unchanged DataForge connection list. No second Oracle connection
  was registered in DataForge.
- **FAIL registered-profile Oracle explain:** no D1 harness execution record exists.
  The current adapter sends source text and an opaque target reference; it does not
  resolve a harness profile or execute Oracle EXPLAIN. Kiwi profile lookups also
  deliberately reject integration credentials, so the test does not broaden them.
- PASS stale/reviewed diff: a separate real `propose` request produced a proposal;
  stale text/revision refused and matching text/revision accepted as reviewable.
  No proposal was executed or applied to a database.
- PASS D2 worksheet commit/rollback through harness HTTP: seq=1 persisted after
  commit, seq=2 absent after rollback, verified from an independent Oracle session.
- PASS D2 PL/SQL: throwaway procedure created through the harness compile route,
  recompiled, independently verified `VALID`, then dropped.
- PASS confirmation: administrative recompile of that owned throwaway procedure
  without confirmation refused explicitly for missing confirmation.
- PASS teardown: owned marker table and procedure dropped, worksheet closed;
  disposable API/DataForge processes, PostgreSQL and nginx removed.
- **FAIL viewer restriction:** the registered harness viewer's worksheet open was
  refused with 403, but its assist request returned real model text. This tests
  the harness user principal, not DataForge's own viewer-session UI.
- **FAIL cancellation:** client disconnected from DataForge after the first real
  delta. The harness request reached terminal `succeeded` within the 20-second
  post-abort observation window, rather than `cancelled`; no request remained hung.
- PASS partial response: a loopback fault proxy forwarded a real model delta,
  then injected malformed provider SSE; DataForge exposed typed `provider_failure`.
  This is deliberate fault injection, not an observed spontaneous model outage.
- PASS actual stopped-harness outage: nginx returned upstream 502 and DataForge
  exposed typed `harness_error`; DataForge non-AI health still answered.
- PASS disabled mode, invalid integration token, incompatible major protocol refusal.

The setup timer starts once DataForge Express is configured/healthy and the harness
plus nginx are running. It includes minting the test developer/viewer principal
tokens, reading DataForge's connection list, capability negotiation, sending the
selected-statement assist and consuming its successful terminal event. It excludes
initial checkout/install/configuration, PostgreSQL provisioning, Oracle bootstrap
and probes, model health, integration credential issuance and process startup.
There was no human interaction, so this does not establish a human ten-minute setup.

Not exercised: DataForge editor/worksheet/compile UI and confirmation dialogs,
DataForge viewer-session regression, upstream integration, hosted model providers,
other proxies, production, or human qualification. D1 real registered-profile
explain, viewer assist refusal and cancellation remain explicit failed gates.
