# Compatibility matrix

Nothing in this table is a certification. It records what has actually been run.

## Current upstream status

**merged upstream**: [PR #77](https://github.com/rammsguns/OracleDataForge/pull/77), branch
`feat/harness-assist`, head `a40cf9c8a91046371e10598100f55021067624d6` on main
`3404f1905da183e1903a35193a7d5b0df18d0870`, merged 2026-10-24 as `431147a5ff69a3aecbb9ee2d3906b62af428994c`
by Claude (AI agent, acting release owner, not a human) after green CI and an
independent agy review (CHANGES REQUESTED, fixed, then APPROVE). Earlier entries
below preserve the exact commits and qualification boundaries of historical runs.

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
| OracleDataForge completion | Pinned `3404f19` plus local patch commits through `51767c8`: B1c passed 16/16 real-route/backend checks with Oracle and the local model through shipped nginx on 2026-10-24, agent-qualified on test stand-ins (AI agent, not a human). Selected-text explain, cancellation and Analyst non-allowed action refusal pass; B1 found the action-enforcement security bug, fixed in the patch. B2 now passes 6/6 browser scenarios at local DataForge `6f3439a`. Upstream: merged into DataForge main as `431147a` (PR #77, B3); BF2 re-ran the final upstream head: 17/17 backend checks and 7/7 browser scenarios passed on 2026-10-25 UTC, including the last review fixes. Human qualification remains open. |
| Oracle 19c | D1 19.9.1.0.0 bootstrapped/probed in B1c: selected-text explain succeeds with unchanged DataForge connections, harness target/profile registry and D1 DataForge/node sessions. Live EXPLAIN PLAN through this integration is not a feature: no bridge exists, by design. D2 23.26.3.0.0 worksheet/compile regression passed through harness HTTP; see B1 below and [harness evidence](../../docs/compatibility.md). |
| A real model provider | B1 exercised the real local `qwen3.8-flash-next-iq3_s` provider through DataForge and nginx. Anthropic and other hosted providers remain untested through this integration. |
| Proxy coverage beyond shipped nginx | A6 exercised real DataForge routes and chunk delivery through the shipped nginx config. Other deployment proxies, compression settings, ALBs and CDNs remain untested. |
| Ten-minute setup target | B1c measured 10.938 seconds from configured/healthy DataForge and harness to the first complete successful assist, using scripted steps (including admin registry and D1 session baselines). Initial installation, provisioning and human setup remain unmeasured. |

## Release gates still open

NP-05 completion is open (human-owned). A6/B1/B2 are agent-qualified on test stand-ins
(AI agent, not a human): B1c 16/16 Oracle/local-provider checks and B2 6/6 browser
scenarios historically. BF2 re-ran merged upstream `431147a` on 2026-10-25 UTC:
17/17 backend checks and 7/7 browser scenarios, including disabled guards, CRLF/
multiline SSE through shipped nginx, and hidden Assist when disabled.
There is no pilot or production qualification. From
`ORACLEDATAFORGE_INTEGRATION.md`, these remain:

- Human pilot setup; B1c measured the already-configured, running-stack scripted path in 10.938 seconds.
- Human/editor demonstration of explaining a selected statement without a second
  Oracle connection; B1c passes the real backend/route check with unchanged registries
  and D1 DataForge/node sessions. Live EXPLAIN PLAN has no integration bridge, by design.
- DataForge editor/UI regression for worksheet, compilation and confirmation dialogs; B1c passed harness HTTP worksheet/compile and backend confirmation refusal. Harness viewers intentionally receive supplied-text assistance and are refused worksheet/compile; DataForge Analyst non-allowed action refusal now passes after the B1c patch fix.
- Broader editor/UI coverage beyond the six B2 scenarios; B1c passed typed partial-provider failure through real routes, and B2 verifies visible cancellation plus its harness record.

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
| DataForge | Package 1.2.0, local commit `51767c8785b8370765af4c184519e45eaa2cb453` on cancellation fix `81301377e061cb3d47d3d06df66133983f33333b` on adapter-patch `2883f605bc5e0b7632583b62a00a094d957df6a0` and upstream `3404f1905da183e1903a35193a7d5b0df18d0870`; not pushed to DataForge. A later review fix, `627825e` (trim the role before action filtering, from the agy review), passed DataForge's 284 tests offline and was not re-run live. |
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

### 2026-10-24, B2 — DataForge assist panel and Playwright UI

**agent-qualified on test stand-ins (AI agent, not a human)**. Codex built the
worksheet Assist panel in DataForge and drove its built frontend in Chromium
against the B1 disposable Oracle/model stack. Final result: **6/6 passed**.
NP-05 human qualification remains open; B3 owns upstreaming the local commits.

| Item | Evidence |
| --- | --- |
| DataForge | Package 1.2.0, local commit `6f3439a2d8f0fbaa3c418fe5fb04c40a8e85b926`, on `cbc9c1a` and `dc3a00e`, based on `627825e`; not pushed |
| Harness baseline during run | `a26f21ec02235ce4ef3f59a06f9d25213cd23bc6`, package 0.1.0 |
| Adapter / protocols | Adapter 0.1.0; request protocol 1.1; harness capability protocol 1.5 |
| Runtime | Node v24.18.1; Python 3.11.16; Playwright 1.62.0; Chromium 151.0.7922.34; python-oracledb 4.0.2 thin |
| Stand-ins | D1 19.9.1.0.0; D2 23.26.3.0.0; harness_app only; UI query on D1; no test DDL |
| Provider / proxy | Real local `qwen3.8-flash-next-iq3_s`, serialized calls; shipped nginx config in nginx:1.27-alpine; disposable postgres:16-alpine |
| Verification | Harness pytest: 787 passed, 7 skipped; ruff check/format clean; CI mypy: 93 files clean; Node adapter tests: 27 passed, 4 live-launcher-only skips; DataForge: 289 tests passed, typecheck/build passed (existing bundle-size warning) |
| Reports, local git-ignored | `databases/reports/2026-10-24-b2-dataforge-ui.md` and `.json` |

| Browser scenario | Result |
| --- | --- |
| Sign-in and outgoing context | PASS: named Administrator signs in with DataForge Basic auth, selects a D1 worksheet statement, opens Assist and reviews server-resolved selection, target, schema and complete editor buffer before sending explain. Browser sends no role or actor assertions. |
| Streamed answer | PASS: **159 distinct DOM answer renders / 281 delta events** before successful completion; provider/model shown. |
| Proposal/diff/apply/stale | PASS: broken column repaired in a real proposal; DiffView reviewed; apply-check accepted and buffer replaced without execution. Editing the buffer then applying the old proposal was refused with already-applied and document-changed reasons shown. |
| Cancellation | PASS: AbortController cancels after real deltas; UI says cancelled and the harness CopilotRequest independently reaches cancelled. No automatic replay. |
| Outage and worksheet | PASS: stopped the harness API; Assist shows unavailable (502); DataForge's own D1 connection runs SELECT 1 FROM dual and displays one row. |
| Analyst | PASS: separate named Analyst sees exactly explain in the documentation Assist panel; existing worksheet restriction retained. |

The UI reuses SqlEditor selection callbacks and DiffView. A new same-origin
`/api/ai/context-preview` route uses the existing permission resolver without a
provider call. fetch/ReadableStream decodes SSE incrementally; typed errors remain
readable, cancellation never retries, and apply checks the current editor id,
target, revision and full buffer. Target/account/editor changes unmount the panel,
abort its stream and prevent late apply responses from changing another editor.
DataForge unit tests cover frame splits, cancellation, typed errors, preview
authorization and outage classification. The runner imports optional drivers lazily.

Screenshots under `databases/reports/2026-10-24-b2-ui/`:

- `01-sign_in_context.png`
- `02-streamed_answer.png`
- `03-proposal-review.png`
- `03-proposal_stale_refusal.png`
- `04-cancelled.png`
- `05-outage_worksheet.png`
- `06-analyst_explain_only.png`

Earlier attempts exposed runner issues: authenticated health polling needed Basic
auth; a native option visibility wait timed out despite the correct Analyst UI;
an optional pre-assist reconnect experiment timed out and was interrupted and
cleaned up. The final full run uses the ordinary lazy connection path and passes
all six scenarios in 77.0 seconds. These are scripted scenario timings, not a human
setup measurement. All screenshots were visually inspected for secrets; none
showed tokens or credentials. Owned API/DataForge/browser processes,
PostgreSQL/nginx containers, secret files and transient logs were cleaned up.
The pre-existing Oracle stand-ins and shared model were retained.

Not exercised here: human or production qualification, upstream integration,
compile/confirmation UI, hosted providers, or other proxies. B1's backend
worksheet/compile/confirmation evidence remains separate.

### 2026-10-24, upstream PR preparation (B3)

PR #77 and its head are recorded above. All eight original feature/fix commits were
preserved as a reviewable series and missing co-author trailers normalized; two
additional commits document/gate opt-in behavior and add adapter regression checks.
The resolver no longer claims a hard-coded Oracle version. Disabled installations
hide assistance controls; invalid configuration is logged without URL/credential
contents. No live database or model calls were made for this preparation.

Local DataForge checks: `npm ci`, `npm test` (**292 passed**), `npm run typecheck`
and `npm run build` passed, covering every command in its CI workflow. Build emits
the existing large-chunk advisory. Harness checks: `pytest -q tests/test_dataforge_real.py`
**7 passed**; Node adapter tests **39 passed, 4 live-launcher-only skips**;
adapter TypeScript check, `ruff check` and `ruff format --check` passed.

The spike patch is refreshed to the exact diff against upstream main, preserving
the existing pinned-checkout reproduction workflow rather than replacing it with
an online-only pointer. It includes no machine paths, credentials or live bench
configuration. Upstream CI passed (build job, run `40121765646`); the independent review and merge followed (below). No human sign-off is claimed.

### 2026-10-24, B3b independent review fixes

PR #77 head `a40cf9c8a91046371e10598100f55021067624d6` adds the missing disabled apply-check
guard and aligns server SSE parsing with CRLF boundaries, multiline data and
comment handling. Unit tests cover all four public adapter methods refusing
network access while disabled, every stream chunk split, and the existing
behavior of dropping unterminated trailing events.

The two post-B2 changes (Assist hidden when disabled; the resolver no longer
hard-codes the Oracle version) and these review fixes were not re-run live
against the Oracle stand-ins; they are covered by unit tests only. DataForge:
300 tests passed, typecheck and build passed (existing large-chunk advisory).
Harness Node suite: 47 passed and 4 live-launcher-only skips.
Harness `ruff check` and `ruff format --check` passed (394 files);
`pytest -q tests/test_dataforge_real.py` passed all 7 tests. Upstream CI
build passed in run `40122712957`. The agy re-review approved, and PR #77 was merged
as `431147a5ff69a3aecbb9ee2d3906b62af428994c` by Claude (AI agent, acting release owner, not a human).

### 2026-10-25 UTC, BF2 — final upstream head live re-run

**agent-qualified on test stand-ins (AI agent, not a human)**. Codex re-ran B1 and
B2 against merged upstream `431147a5ff69a3aecbb9ee2d3906b62af428994c` (package
1.2.0), with no DataForge source changes. The checkout was clean before fetch and
detached checkout; no stash was needed. This was 2026-10-24 in America/Mexico_City.
Human pilot, production and NP-05 human qualification remain open.

| Item | Result / boundary |
| --- | --- |
| Harness baseline | `4eddf091321226d2c1b44d0154f230130892e527`, with the BF2 test-runner changes in this branch |
| Adapter / protocol | Adapter 0.1.0; request protocol 1.1; harness capability protocol 1.5 |
| Runtime | Node v24.18.1; Python 3.11.16; python-oracledb 26.0.1 thin; Playwright 1.62.0; Chromium 151.0.7922.34 |
| Stand-ins | D1 19.9.1.0.0 and D2 23.26.3.0.0, `harness_app`; P1 was never selected |
| Model / proxy | Real local `qwen3.8-flash-next-iq3_s`, serialized calls; shipped nginx config in nginx:1.27-alpine; disposable postgres:16-alpine |
| B1 | **17/17 passed**, exit 0: all original 16 checks plus live CRLF/multiline SSE reframing; original disabled-mode check expanded to all four public adapter methods |
| B2 | **7/7 passed**, exit 0: all original six scenarios plus Assist hidden when disabled, checked for Administrator worksheet and Analyst documentation views |
| Streaming | B1: 196 chunks / 196 supported SSE events; B2: 148 distinct visible answer renders / 297 deltas |
| Setup | 20.252 seconds on the already-configured running stack, same B1 timer boundary; no human setup measurement |
| Local verification | `npm ci` and `npm run build` passed at final upstream head; existing bundle-size advisory. Harness `ruff check .`, `ruff format --check .` passed (403 files); `pytest -q tests/unit`: **845 passed, 8 skipped** |
| Evidence, local and uncommitted | `databases/reports/2026-10-25-bf2-dataforge-final/`: `b1.md`/`.json`, `b2.md`/`.json`, console logs, `unit.log`, runtime and checkout metadata, eight screenshots, exact commands in `README.md`, and earlier attempts |

The three previously unit-only fixes now have live evidence:

- Disabled capabilities report disabled/unreachable; chat emits typed
  `integration_disabled`; context-preview returns HTTP 403 with that error;
  apply-check returns `canApply: false` with `integration_disabled`.
- A loopback reframer forwards real harness/local-model events, rewriting their
  framing to CRLF, multiline JSON `data:` fields, comments and optional field
  spaces **before shipped nginx**. All **64 supported events** arrived unchanged
  through nginx and DataForge, in 64 downstream chunks, ending in successful
  `done`. One `claim_check` extension was intentionally filtered by DataForge's
  known-event allowlist. Writes split inside CRLF; network read boundaries may
  coalesce, so exhaustive split coverage remains a unit-test claim.
- With the same built frontend and server restarted disabled, the Administrator
  worksheet has no Assist button/panel and the Analyst has no documentation
  Assist panel. The browser first verifies authenticated, disabled capabilities.

The original backend regression checks again cover selected D1 statement explain,
unchanged connection/target registries and zero DataForge/node D1 sessions before
and after; D2 commit/rollback and owned procedure compilation; explicit confirmation
refusal; viewer/Analyst restrictions; cancellation; injected partial-provider
failure; actual stopped-harness outage; invalid token and incompatible protocol.
The browser again reviews outgoing context and a real proposal, applies only to
the editor buffer, refuses a stale apply, independently verifies cancellation in
the harness, and runs D1 `SELECT 1 FROM dual` during the harness outage.

Earlier attempts are retained rather than hidden: one runner edit error in the
invalid-token check; an SSE assertion that initially included intentionally
filtered extension events; and screenshot report paths that failed for relative
CLI paths and this worktree's shared `databases` symlink. Only test-runner fixes
were needed. The final runs above pass in full. All eight screenshots were visually
reviewed and contain no credentials. Disposable processes, containers and temporary
secret files were removed; pre-existing Oracle stand-ins and the shared model were
retained. No push or upstream source change was made.

Not exercised: human/production qualification, hosted providers, other proxies,
compile/confirmation UI, or a separate live assertion of resolver version metadata.
