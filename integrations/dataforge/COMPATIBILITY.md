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
| OracleDataForge completion | Pinned `3404f19` plus local patch commits through `51767c8`: B1c passed 16/16 real-route/backend checks with Oracle and the local model through shipped nginx on 2026-10-24, agent-qualified on test stand-ins (AI agent, not a human). Selected-text explain, cancellation and Analyst non-allowed action refusal pass; B1 found the action-enforcement security bug, fixed in the patch. Editor/UI, upstream and human qualification remain open. |
| Oracle 19c | D1 19.9.1.0.0 bootstrapped/probed in B1c: selected-text explain succeeds with unchanged DataForge connections, harness target/profile registry and D1 DataForge/node sessions. Live EXPLAIN PLAN through this integration is not a feature: no bridge exists, by design. D2 23.26.3.0.0 worksheet/compile regression passed through harness HTTP; see B1 below and [harness evidence](../../docs/compatibility.md). |
| A real model provider | B1 exercised the real local `qwen3.8-flash-next-iq3_s` provider through DataForge and nginx. Anthropic and other hosted providers remain untested through this integration. |
| Proxy coverage beyond shipped nginx | A6 exercised real DataForge routes and chunk delivery through the shipped nginx config. Other deployment proxies, compression settings, ALBs and CDNs remain untested. |
| Ten-minute setup target | B1c measured 10.938 seconds from configured/healthy DataForge and harness to the first complete successful assist, using scripted steps (including admin registry and D1 session baselines). Initial installation, provisioning and human setup remain unmeasured. |

## Release gates still open

NP-05 completion is open (human-owned). A6 is agent-qualified on test stand-ins
(AI agent, not a human), with no pilot or production qualification. From
`ORACLEDATAFORGE_INTEGRATION.md`, these remain:

- Human pilot setup; B1b measured the already-configured, running-stack scripted path in 10.938 seconds.
- Human/editor demonstration of explaining a selected statement without a second
  Oracle connection; B1b passes the real backend/route check with unchanged registries
  and D1 DataForge/node sessions. Live EXPLAIN PLAN has no integration bridge, by design.
- Editor/UI and upstream integration beyond the local adapter patch; DataForge's own
  279 tests, typecheck and build passed at the pinned commit in A6.
- DataForge editor/UI regression for worksheet, compilation and confirmation dialogs; B1b passed harness HTTP worksheet/compile and backend confirmation refusal. Harness viewers intentionally receive supplied-text assistance and are refused worksheet/compile; DataForge Analyst non-allowed action refusal now passes after the B1c patch fix.
- Editor/UI end-to-end coverage; B1b passed typed partial-provider failure and client
  cancellation through real routes after fixing DataForge response-close handling.

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

### 2026-10-24, Oracle and real local provider (B1/B1b/B1c, agent stand-ins)

**agent-qualified on test stand-ins (AI agent, not a human)**. Run by Codex on
z4g5; this is backend/route evidence, with no human pilot or production qualification.
The original B1 run passed 13/16; B1b applies Claude's acting-application-owner
rulings on selected-text explain and viewer policy, fixes DataForge cancellation,
and exposes a separate Analyst action-enforcement failure. B1c fixes that security
bug in both adapter copies: assist re-checks the current actor with
`filterActionsForRole` before resolving context or sending any harness request,
returning typed `action_not_allowed` for a denied action. Analyst proposal
apply-checks are also refused locally under the current role. NP-05 remains open.

| Item | Value |
| --- | --- |
| Harness baseline build | `dcc1d012315434acc36eee5a938b4e6012fdc765`, package 0.1.0, with the B1c typed-refusal assertion in this branch |
| DataForge | Package 1.2.0, local commit `51767c8785b8370765af4c184519e45eaa2cb453` on cancellation fix `81301377e061cb3d47d3d06df66133983f33333b` on adapter-patch `2883f605bc5e0b7632583b62a00a094d957df6a0` and upstream `3404f1905da183e1903a35193a7d5b0df18d0870`; not pushed to DataForge |
| Adapter / protocols | Adapter 0.1.0; adapter request protocol 1.1, harness capability protocol 1.5 (major-compatible) |
| Runtime / metadata / proxy | Node v24.18.1; python-oracledb 4.0.2 thin; disposable `postgres:16-alpine`; `nginx:1.27-alpine` with shipped `deploy/nginx.conf` (only listen/upstream rewritten) |
| Oracle test targets | `load-db-1` D1 19c reports 19.9.1.0.0; `load-db-2` D2 23ai PDB reports 23.26.3.0.0; both `harness_app`, provisioned with `scripts/bootstrap_targets.py` |
| Real provider | `local`, `local_only=true`, `http://127.0.0.1:8080/v1`, `qwen3.8-flash-next-iq3_s`; health checked; key resolved by file-backed secret reference; calls serialized |
| Live result | **16/16 checks passed, 0 failed**, runner exit 0; cancellation passes with terminal `cancelled` and no hung request |
| Setup | **10.938 seconds**, fully scripted on the already-configured/running stack |
| Offline verification | `pytest -q tests/unit tests/test_dataforge_real.py`: 782 passed, 7 skipped; `ruff check` and `ruff format --check` clean; adapter Node tests: 27 passed, 4 live-launcher-only skips |
| DataForge verification | `npm test`: 284/284 passed, including current-role/no-fetch enforcement and real Express client-abort/upstream-fetch-signal regressions; `npm run typecheck` and `npm run build` passed before the live reruns |
| Reports (local, git-ignored) | `databases/reports/2026-10-24-b1-dataforge-oracle.md` and `.json`, overwritten by the final full B1c rerun |

Check-by-check evidence:

- PASS capabilities: harness 1.5 accepted by adapter 1.1.
- PASS streaming: 114 network chunks / 114 SSE events through DataForge and shipped nginx,
  real local provider, terminal `succeeded`.
- PASS context/connection registry: selected-source context only, D1 opaque target
  reference and unchanged DataForge connection list.
- PASS selected-statement explain: DataForge action `explain` successfully explains
  the selected D1 statement with its D1 target reference. DataForge connections are
  unchanged, admin-authenticated harness API target/profile count is 2 before and
  after (also cross-checked against the full metadata registry), and D1 SYSTEM
  `v$session` observes DataForge/node program or module counts of 0 before and after.
  The earlier live EXPLAIN PLAN requirement over-asked: the integration has no
  Oracle plan-execution bridge, by design; profile-based EXPLAIN is a separate
  harness tuning route.
- PASS stale/reviewed diff: stale text/revision refused and matching text/revision
  accepted as reviewable, with no proposal execution or database/git mutation.
- PASS D2 worksheet commit/rollback through harness HTTP: seq=1 persisted after
  commit, seq=2 absent after rollback, verified from an independent Oracle session.
- PASS D2 PL/SQL: throwaway procedure created through the harness compile route,
  recompiled, independently verified `VALID`, then dropped.
- PASS confirmation: administrative recompile of that owned throwaway procedure
  without confirmation refused explicitly for missing confirmation.
- PASS teardown: owned marker table and procedure dropped; worksheet closed.
- PASS revised role restrictions: harness viewer worksheet and compile return 403;
  supplied-text assistance succeeds as intended under `PERMISSION_READ`. Real
  authenticated DataForge Analysts receive explain-only capabilities and selected
  source without schema context; direct `diagnose` returns typed
  `action_not_allowed` with no start/delta. B1 found a real security bug: the
  capabilities filter was only advisory. B1c fixes server enforcement in the
  DataForge patch and harness adapter copy, with no-fetch regression tests for
  Analyst diagnose and apply-check, plus allowed Analyst explain and Developer
  diagnose tests.
- PASS cancellation: DataForge now listens to unfinished response `close` rather
  than request `close`; client abort after the first real delta reaches terminal
  `cancelled` in the harness, with no hung request. The harness router/service and
  nginx configuration needed no change.
- PASS partial response: a loopback fault proxy forwarded a real model delta,
  then injected malformed provider SSE; DataForge exposed typed `provider_failure`.
  This is deliberate fault injection, not an observed spontaneous model outage.
- PASS actual stopped-harness outage: nginx returned upstream 502 and DataForge
  exposed typed `harness_error`; DataForge non-AI health still answered.
- PASS disabled mode, invalid integration token, incompatible major protocol refusal.

The final full B1c suite passes 16/16, following the B1b 15/16 result with
successful cancellation and the action-enforcement security finding. Two earlier startup attempts ran no checks: Docker's private `/tmp`
prevented PostgreSQL from reading its mounted secret, then the new DBA observer
needed `$REPO` expansion in the existing qualification password-file path. Both
were resolved before collecting the two complete runs.

The setup timer starts once DataForge Express is configured/healthy and the harness
plus nginx are running. It includes minting the test developer/viewer/administrator
principal tokens, the admin-authenticated target/profile baseline and D1 SYSTEM
session baseline, reading DataForge's connection list, capability negotiation,
sending the selected-statement assist and consuming its successful terminal event.
It excludes initial checkout/install/configuration, PostgreSQL provisioning, Oracle
bootstrap and probes, model health, integration credential issuance and process
startup. There was no human interaction, so this does not establish a human
ten-minute setup.

Not exercised: DataForge editor/worksheet/compile UI and confirmation dialogs,
upstream integration, hosted model providers, other proxies, production, or human
qualification. The Analyst action-enforcement bug found by B1 is fixed in the local patch.
Disposable API/DataForge processes, PostgreSQL and nginx are removed after each run;
the pre-existing Oracle stand-ins and unrelated API process are retained.
