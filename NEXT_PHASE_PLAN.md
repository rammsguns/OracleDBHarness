# Next phase: pilot qualification and release readiness

Prepared 2026-09-12; replanned against local commit `598870a` after restart recovery
and PostgreSQL/restore CI landed. This is the active follow-up to
[MVP_PLAN.md](MVP_PLAN.md), milestone 6. It is a proposed delivery plan, not evidence
that its gates have passed. Where a gate now has evidence, the row says so and names
what the evidence covers.

## Current execution plan

Local preparation is now complete for every remaining gate. NP-04's runner and
scoring fixes, NP-01/03's process-death and isolation checks, NP-05's
adapter-against-a-real-harness checks, NP-06's browser sign-in harness and NP-07's
capacity runner all exist and pass against the stand-in — see the dated
`### Validation` entries below, the last two of which merged as
[#17](https://github.com/rammsguns/OracleDBHarness/pull/17). No further
implementation is required to start a qualification run. What remains is the same
for every gate: a real environment and a named owner, then the run itself.

CI for `3ab648b` is green: [run 34909514087](https://github.com/rammsguns/OracleDBHarness/actions/runs/34909514087),
five jobs (API and worker, web console, DataForge adapter, sign-in against
Keycloak, Compose install and restore). This verifies the configured CI jobs,
including PostgreSQL checks, the Compose install/restore drill, the DataForge
adapter run against a disposable live harness process, and the browser sign-in
fixture run against Keycloak. It does not establish Oracle restart behavior,
browser login against the pilot's own identity provider, real-provider quality or
DataForge integration itself. Earlier validation entries below remain historical
run records.

### First milestone: real-provider evaluation

Suggested owner: application/integration engineer, with a DBA reviewer. Planning
allowance: 3-5 engineering days for implementation and fixture validation; provider
execution and review depend on access and reviewer availability.

1. Define a versioned evaluation case format and at least 30 cases under
   `tests/copilot/`, covering explain, fix, draft, test blocks, tuning, inaccessible
   objects, stale source and embedded instructions. Give each case an ID, permitted
   context, expected behavior and review rubric. Fix the explain/fix denominator
   before the run.
2. Add a separate opt-in runner and document its entry point in
   `tests/copilot/README.md`. Exercise the harness context, authorization, streaming
   and proposal paths in `services/api/harness_api/copilot/`, not only the provider
   adapter. Reject the fake provider in qualification mode and require explicit
   provider/model configuration. Keep ordinary pytest runs deterministic and free.
3. Add preflight checks for credentials, approved context and a configured spend
   ceiling. Bound output and requests; reserve a conservative per-request cost
   before dispatch and stop when the remaining budget is insufficient. Record
   provider usage and pricing assumptions. Treat timeouts, partial streams and
   missing usage as incomplete evidence.
4. Emit a report with harness commit, case-set version, requested and reported model
   identifiers, per-case outcome, latency, usage, estimated cost and DBA review
   fields. Exclude credentials and unauthorized context. Unreviewed, skipped or
   incomplete cases cannot count as passes.
5. Verify configuration, fake-provider refusal, budget exhaustion, provider failures,
   report completeness and redaction with controlled responses. Then run the corpus
   against the configured real provider and record DBA judgments and failure patterns.

Implementation exit: the runner and corpus are reviewable, negative-path checks
pass, and the documented command cannot silently qualify fixtures. Qualification
exit: at least 30 completed cases, every authorization/no-automatic-execution case
passes, and at least 90% DBA-reviewed correctness on explain/fix cases. Runner
completion alone does not close NP-04.

**Status: implementation exit met, with scoring fixes merged; qualification exit not
started.** Steps 1-4 and the controlled-response half of step 5 were built in `e25dc4f`.
Review of that commit found the scorer could report QUALIFIED for a run that should not
qualify (below); the fixes merged in `a6303f8` (#12) with green CI on the pull request and
on `main`. See `tests/copilot/README.md` and *Validation* below. What landed in `e25dc4f`:

- `tests/copilot/eval/cases.json`, case-set version `2026-09-12.1`: 39 cases (36 reach
  the provider) across explain 6, fix 7, draft 4, test blocks 3, tuning 4, inaccessible
  objects 3, stale source 2, embedded instructions 4 and authorization 6. The
  correctness denominator (13 explain/fix cases) is declared in the file and enforced
  by the loader, as are the safety gate (12 cases) and which cases may be judged
  structurally rather than by a DBA.
- `python -m tests.copilot.eval` with `validate`, `rehearse` (fixture provider, report
  marked not-evidence), `run` and `score`. `run` serves a throwaway harness over real HTTP
  and drives the router, context policy, SSE stream, proposal capture and apply check.
  It refuses the fixture provider, an unnamed model, a missing key, missing prices or
  ceiling, unapproved context categories and a ceiling below the worst case; checks the
  key and model with a lookup that generates nothing; reserves a byte-bounded worst-case
  cost per request with provider retries disabled; and aborts if fixture answers appear.
  Timeouts, provider errors, partial or truncated streams, missing usage and a model
  mismatch are recorded as incomplete.
- The provider adapter gained `HARNESS_COPILOT_MAX_OUTPUT_TOKENS`,
  `HARNESS_COPILOT_REQUEST_TIMEOUT_SECONDS` and `HARNESS_COPILOT_PROVIDER_MAX_RETRIES`,
  records cache-creation tokens, and an access check. A `copilot` extra installs the SDK.

Review findings on `e25dc4f`, and their resolution:

- **P1, safety checks outside the safety group.** Only `noDatabaseOperation` was enforced
  globally; a leaked credential, context outside the permitted set or a failed apply
  check in a case outside the safety group - or inside the 10% explain/fix allowance -
  still qualified. The runner now classifies every structural check as a safety or an
  answer check. Any safety-check failure in any case blocks qualification and is named
  by case and check in the gate result, reasons and Markdown; `noDatabaseOperation` and
  `credentialNotExposed` must be present for every case that ran. `noExecutionClaim` (a
  first-person claim to have executed or compiled something) is a safety check too.
  Answer checks - the must-not-match patterns, proposal presence, the request record -
  still fall within the correctness allowance.
- **P2, outstanding DBA reviews.** A pending review was listed but did not block, and a
  structurally failed case hid a missing review. Review completion is now computed from
  the review fields for every completed case that requires one: verdict, reviewer, notes
  and, for a failure, a failure pattern. Structural-only cases need none.
- **P2, budget-stopped runs.** A run stopped by its budget with 30 or more completed
  cases qualified. `stoppedForBudget` or any skipped case now blocks qualification.
  `--allow-partial-run` only permits starting such a run; a run that completes within its
  ceiling is scored normally. CLI help and the README say so.

Still owed for NP-04: provider configuration, a data-sharing approval and a spend
allowance with named owners; the paid run (worst-case reservation for the full case set
is about $8.50 at $5/$25 per million tokens and 8000 output tokens); DBA review of every
reviewed case; `score`; and recording the result and failure patterns.

### Remaining delivery order

These are suggested roles, not assigned people or dated commitments. Environment
preparation can proceed alongside runner implementation.

| Order | Work package | Suggested owner | Dependency | Completion evidence |
| --- | --- | --- | --- | --- |
| 1 | Preserve green CI and capture deployment evidence (NP-02) | Application engineer | Current CI run | Link green run and drill timings; retain an actual release-store snapshot for a later released-version upgrade test. Identify synthetic migration coverage separately. |
| 2 | Build and evaluate the provider runner (NP-04) — runner built, scoring fixes merged | Integration engineer + DBA | Provider configuration, approved context and budget for the paid run | Runner checks and scoring regressions (done, `a6303f8`), reviewed case report and both quality gates above (owed). |
| 3 | Qualify Oracle recovery, grants and isolation (NP-01/03) — checks prepared, not run | Application engineer + DBA | Isolated schemas, direct grants, PDB and independent second database | Process-death cases before dispatch, during read/write and during commit; no replay or false success; restricted panels degrade correctly; target identity and state remain isolated. The checks and procedure exist (*Validation*, 2026-09-13); the Oracle run itself is owed. |
| 4 | Complete browser identity and DataForge integration (NP-05/06) — browser run and adapter-vs-real-harness checks prepared, not run against DataForge or the pilot | Integration engineer + identity owner | Pilot OIDC registration, deployed origin/proxy, pinned DataForge checkout; NP-04 for full provider flow | Browser callback/expiry/logout/access checks; editor context, incremental streaming, reviewed/stale diffs and outage recovery; both projects' regressions; measured setup time. The browser harness and the adapter-against-a-real-harness checks exist (*Validation*, 2026-09-13 and 2026-09-14); the DataForge checkout and the pilot browser run are owed. |
| 5 | Measure capacity and complete the pilot (NP-07) | Application engineer + developer + DBA | Functional gates above and three independent databases | Ten concurrent users, agreed latency/error thresholds, connection/queue limits, saturation and soak recovery; developer and DBA complete all six MVP workflows. |

For Oracle restart qualification, use a disposable process and observe persisted
records and database effects from an independent connection. Account for the old
connection's eventual cleanup; a stand-in fault hook or a killed database session
alone is not proof of recovery after application process death.

### Dependencies and release decision

- Assign a named person and availability date to Oracle accounts/grants/PDB/extra
  databases, provider configuration and spend allowance, DBA review, DataForge
  checkout, OIDC registration and a pilot host with Docker. Their current
  availability has not been rechecked by this planning review.
- Before load testing, record the network baseline, numerical acceptance thresholds,
  workload mix, soak duration and resource limits. Separate database time from
  application overhead; agree thresholds before evaluating results.
- For each gate retain the commit, environment/version identifiers, command or
  manual procedure, pass/fail/skip counts, report location, limitations and reviewer.
  Append real-environment runs to `docs/compatibility.md` and DataForge runs to
  `integrations/dataforge/COMPATIBILITY.md`.
- Reconcile stale MVP and compatibility annotations when attaching evidence.
  PostgreSQL now has green CI; browser redirect qualification remains separate
  from Node-based identity tests.

Release requires recorded evidence for every mandatory gate and no unresolved
defect risking incorrect writes, access leakage or misleading outcomes. If an
external dependency is unavailable, finish local preparation and leave its gate
blocked. Re-estimate the remaining 3-4 week allowance after dependencies have owners
and dates; it is not elapsed time from this document's date.

## Outcome and scope

Deliver a repeatable internal pilot in which a developer and a DBA complete the
six MVP workflows, including the real DataForge copilot path, with recorded
compatibility, recovery, deployment and performance evidence. Keep production
observation-only and retain the current single execution-owner deployment.

The existing implementation is the starting point. Do not add another IDE adapter,
MCP tools, production mutations, management-pack collectors or distributed workers
during this phase. A web-only pilot can be evaluated separately if DataForge is
blocked, but it does not satisfy the full MVP integration gate.

## Issue review

`gh issue list --state all --limit 100` returned no issues on 2026-09-12 for
`rammsguns/OracleDBHarness`. The identifiers below are local planning IDs, not
GitHub issue numbers. No GitHub issues were created. This review combines source
inspection, existing qualification records and the local checks below; it is not
a complete code audit.

| ID | Priority | Finding and evidence | Required result |
| --- | --- | --- | --- |
| NP-01 | **Done, stand-in only** | Implemented in `services/api/harness_api/recovery.py`, run from `build_state` before the execution service exists. Covered by `tests/unit/test_restart_recovery.py` (23 checks) and `tests/integration/test_restart.py` (8), including a statement held in flight while its process is abandoned. | Met against the stand-in: never-dispatched work resolves to `cancelled`, interrupted reads to `failed`, interrupted writes to `outcome_unknown` with `verificationRequired`, stale worksheet records closed, nothing redispatched. The equivalent run on Oracle is still owed (step 2); its scenarios, which kill a real API process and read the outcome through an independent session, are in `tests/integration/test_process_death.py` and pass against the stand-in only. |
| NP-02 | **Implemented; green CI at `598870a`** | The `python` job runs against a `postgres:16-alpine` service container, and `HARNESS_REQUIRE_POSTGRES` turns a missing database into a failed job rather than a silent skip of the only checks that cover the pilot store. A new `deployment` job runs `deploy/backup-restore-drill.sh`: clean install, seed, dump, restore into a fresh database, cut the API over. | Met for migration, persistence, installation and restore, on every build. Upgrade *from a released version* is still only covered synthetically — no earlier build's store exists to upgrade. |
| NP-03 | P1; **checks prepared, run owed** | One Oracle 19.9 non-CDB/thin instance has passed; restricted grants, a PDB and independent target isolation remain unqualified. The recorded run substituted `SELECT_CATALOG_ROLE` for seven direct SYS-view grants. Checks for a restricted account, the container identity and the second target's database identity now exist, with `HARNESS_QUAL_REQUIRE` to make a missing environment fail the run; none has met a database. | Run the reviewed grants with appropriate DBA support and qualify restricted/developer accounts, a second database and a PDB; record exact versions and skipped checks. |
| NP-04 | P1; **runner implemented, run owed** | `tests/conftest.py` explicitly sets `copilot_provider="fake"`, so the default suite cannot be provider evidence. An opt-in runner and a 39-case set now exist under `tests/copilot/eval/`, with their negative paths in `tests/copilot/test_evaluation_runner.py`. No paid run or DBA review has happened. | Create a separate opt-in provider evaluation path that verifies the actual provider/model and rejects fixture results as qualification evidence. Run the DBA-reviewed case set. |
| NP-05 | P1; **adapter checked against a real harness, DataForge checkout owed** | DataForge adapter tests used only a stub; no real DataForge commit, proxy streaming path or setup time has been qualified. `python -m tests.dataforge_live run` now starts a disposable harness process and runs the real adapter and route code against it over real HTTP, and `test/proxy-streaming.test.ts` shows a buffering proxy is distinguishable from a passthrough one. See `integrations/dataforge/COMPATIBILITY.md`. | Integrate a pinned DataForge revision and pass its published release gates, including unchanged execution/transaction behavior. |
| NP-06 | P1; **browser run prepared, pilot run owed** | Keycloak sign-in is tested under Node; browser redirect and the pilot identity registration have not been exercised. `python -m tests.browser` now drives Chromium through sign-in, callback, API access, refused identities, sign-out and expiry, in `rehearsal`, `fixture` (CI, Keycloak) and `pilot` modes; only `pilot` can report QUALIFIED. | Complete sign-in through the deployed console in a browser and verify API authorization, token expiry and rejected identities. |
| NP-07 | P1; **runner prepared, thresholds and run owed** | Ten concurrent users over three databases is a release target with no measurements. `python -m tests.capacity` and docs/capacity.md now define the run; its thresholds are proposals, and it has rehearsed only against the stand-in, where it found two defects (below). | Measure mixed-workload load, connection limits, queueing, cancellation and recovery; prove no cross-user or cross-target contamination. |
| NP-08 | P2 | The historical MVP milestone/backlog text still says no Oracle environment was obtained, contrary to the 2026-09-11 evidence. The adapter compatibility file repeats the old claim. | Reconcile current annotations and link this plan; keep the original scope and acceptance criteria visible. Addressed by this planning change. |
| NP-09 | **Closed, not a fault** | The configured interpreter is present and working. The 2026-09-12 failure was the filesystem sandbox, the same cause as the web-test failure recorded below, not a missing environment. | Met: lint, format, types, the full pytest suite and contract freshness all pass locally. No environment repair was needed. |

P1 means a full-pilot gate, not a confirmed exploitable defect. P2 means supporting
work. NP-01 and NP-04 were the source-confirmed implementation/test-path gaps; NP-01 is
now implemented and NP-04 is next. The remaining P1 items are missing qualification
evidence, and no amount of local work closes them.

## Delivery sequence

Suggested effort is 3-4 weeks for two engineers plus part-time DBA and DataForge
support, **after** dependencies are available. This is a planning allowance, not a
commitment; re-estimate after the first recovery and deployment runs.

### 1. Establish the pilot configuration and restore the baseline

Suggested owners: application engineer and DBA. Complete NP-09 and prepare NP-02,
NP-03, NP-04, NP-05 and NP-06.

**Status: the local half is done, the environment half is not.** The baseline runs
clean (see *Validation*, below). Every external dependency in this step -- Oracle
accounts, a second and third database, a PDB, DBA support, disposable PostgreSQL, pilot
OIDC registration, a DataForge checkout, a provider budget -- is still unobtained and
still blocks the steps that need it.

- Record the pilot Oracle versions, endpoints, account roles, driver/network mode,
  identity provider, DataForge commit, model and metadata-store version.
- Obtain isolated Oracle schemas, a genuinely independent second database, a third
  load-test database, a PDB, and DBA support for reviewed direct grants.
- Provide disposable PostgreSQL, pilot OIDC registration, a DataForge checkout and
  a provider configuration with an explicit data-sharing policy and spend budget.
- Restore Python tooling; run lint, formatting, types, tests and contract freshness,
  plus web tests/typecheck/build and adapter tests/typecheck.

Exit: baseline failures are triaged and dependencies have named owners. Missing
environments remain explicit blockers. Thick mode/TCPS are mandatory if the pilot
uses them; otherwise keep them outside the tested matrix rather than claiming support.

### 2. Make restart and deployment behavior demonstrable

Suggested owner: application engineer. Complete NP-01 and NP-02 first.

**Status: restart behavior is implemented and tested against the stand-in; the
deployment coverage is also implemented and green in CI.** What landed:

- Schema version 4 adds `executions.owner_id` / `dispatched_at`,
  `worksheet_sessions.owner_id` / `commit_requested_at` and an `execution_runtimes`
  table, with a registered 3-to-4 migration.
- The dispatch marker is committed before the engine is handed a request, at all four
  dispatch sites. That is what lets a restart separate "never reached a connection"
  from "was in flight" instead of treating every interrupted write as uncertain.
- A commit carries its intent on the session record, because a commit has no execution
  record of its own. A process that dies between the marker and the answer leaves a
  session whose durability is reported as unknown rather than as a clean rollback.
- One execution service per store is enforced, not assumed. The claim is serialized on a
  single row, because reading the live runtimes first is not enough: under READ COMMITTED
  two simultaneous startups cannot see each other's uncommitted row, so both would find no
  previous owner and both would serve. A superseded process is refused every operation that
  could change the database durably -- commit, write, new session lease -- checked against
  the store rather than against a flag that is up to one heartbeat stale. A late answer from
  a statement whose record was already reconciled is refused, not written over the verdict.
- `GET /api/v1/admin/reconciliation` is the operator path, with
  `POST /api/v1/admin/executions/{id}/verification` for an uncertain write and
  `POST /api/v1/admin/worksheets/{id}/commit-verification` for an interrupted commit, which
  has no execution record of its own. Both lists and the count always agree. An execution
  keeps its `outcome_unknown` state after a human verification; the finding is recorded
  beside it.

**Status of the deployment half: done, and now continuous rather than a one-off.**

- The `python` CI job gets a `postgres:16-alpine` service container, matching the major
  version `deploy/compose.yaml` runs, and installs the new `postgres` extra. Nine checks
  that previously only ever skipped now run: the v1-to-current migration with
  representative records, that an upgrade loses nothing, actor-scoped idempotency as a
  *behaviour* rather than a constraint name, migration-failure rollback, version and
  missing-column refusals, reconciliation over aware timestamps, the outstanding-commit
  JSON query, and the concurrent store claim.
- A missing database now fails the job. A skipped store check is indistinguishable from a
  passing one in a summary line, and these exist precisely because SQLite cannot stand in.
- `deploy/backup-restore-drill.sh` performs the install-and-restore drill and prints its
  timings; the `deployment` job runs it on a clean runner every build. It restores a store
  that deliberately contains an execution left in flight, so the restore also proves the
  API reconciles restored work instead of falling over on it.
- `HARNESS_METADATA_URL` became overridable in `deploy/compose.yaml`, because a restore does
  not always land in a database of the same name.

Still owed in this step: the same restart run against **Oracle**, and an upgrade from an
actually released store rather than one synthesised by stripping columns back out.

- Define persisted state transitions for process death before dispatch, during a
  read, during DML/PLSQL and during commit. A persisted `queued` state alone must
  not be treated as proof that nothing reached Oracle.
- Reconcile orphaned execution and worksheet records at startup for the supported
  single-owner deployment, with an audit trail and an operator verification path.
- Test abrupt process termination and restart against persistent metadata, then
  repeat on Oracle. Assert no automatic write replay, no reusable stale lease,
  no permanently running orphan and no false success for an uncertain write.
- Add PostgreSQL CI coverage for v1-to-current migration with representative
  records, actor-scoped idempotency and restart persistence. Exercise migration
  failure and schema-version refusal behavior.
- Install Compose from the guide on a clean environment; back up metadata, restore
  to a fresh database and verify profiles, grants, history and audit continuity.
  Record secret provisioning separately and measure recovery time/data loss.

Exit: recorded restart, upgrade and restore runs pass; operators can act on unknown
outcomes using a concrete procedure.

### 3. Qualify Oracle access and browser identity

Suggested owners: DBA and application engineer. Complete NP-03 and NP-06.

- Run qualification, API and workflow suites on the selected configurations.
- Add a restricted-account path that expects individual panels to report missing
  privileges; the current full-grant fixture suite is not that test.
- Exercise target identity and transaction isolation on independent databases,
  permission revocation, real blocking/cancellation and production mutation refusal.
- Complete browser sign-in through the deployed origin/proxy, including callback,
  expiry, wrong audience/unregistered account, logout and direct API access controls.
- Append exact environment, commit, commands, pass/fail/skip counts and limitations
  to `docs/compatibility.md`. Add deadlock and cursor-invalidation cases if needed
  for the agreed pilot workloads; otherwise retain the documented limitations.

Exit: every configuration offered to the pilot has explicit evidence and no
unresolved authorization, target-isolation or misleading-outcome defect.

### 4. Prove the real copilot workflow

Suggested owners: integration engineer and DBA reviewer. Complete NP-04 and NP-05.
Preparation can overlap steps 2-3.

- Introduce an opt-in evaluation runner and at least 30 representative cases for
  explanations, fixes, drafts, test blocks, tuning, inaccessible objects, stale
  documents and malicious embedded instructions. Keep deterministic fixture tests.
- Require recorded provider/model provenance, per-case results, latency, usage and
  cost; stop at the configured budget. Do not count canned answers as model evidence.
- Require all authorization/no-automatic-execution cases to pass and at least 90%
  DBA-reviewed correctness on explain/fix cases; publish remaining failure patterns.
- Connect the pinned DataForge revision; verify selection, scoped context, streaming
  through the actual proxy, reviewed diffs, stale-edit refusal and outage recovery.
- Measure the ten-minute setup target on already-running services. Run both
  projects' regressions and confirm DataForge retains credential/session ownership
  and its existing role, transaction and confirmation behavior.

Exit: append the tested DataForge/harness/protocol/provider versions and evidence
to the compatibility records; the actual editor-to-provider workflow passes.

### 5. Measure load and conduct the pilot

Suggested owners: application engineer, developer pilot user and DBA. Complete
NP-07 after the functional gates above.

- Agree application-overhead/metadata latency targets after measuring the network
  baseline, before judging the load run. Report database execution time separately.
- Run ten concurrent users across three distinct registered databases, mixing
  metadata, bounded reads, explicit transactions, cancellation and copilot requests.
- Record p50/p95 latency, errors, active/queued work, connections and resource use.
  Include saturation and a bounded soak; verify limits and recovery after load stops.
- Have one developer and one DBA complete all six MVP workflows. Record defects,
  retest fixes, and publish installation, recovery and compatibility evidence.

Exit: no unresolved defects risking incorrect database changes, access leakage or
misleading outcomes; all mandatory gates have evidence. A blocked external gate
keeps the full MVP release blocked, even when fixture regressions pass.

## Validation

### 2026-09-12, planning review

- GitHub issue lookup succeeded and returned an empty list for all states.
- Web TypeScript check passed; all 20 DataForge adapter tests passed.
- Python tests could not start inside the filesystem sandbox. Recorded at the time as a
  missing interpreter; see NP-09 for the correction.
- Web tests passed on retry outside the filesystem sandbox: 45 passed, 3 Keycloak
  integration tests skipped. The initial sandbox run could not load Vite config.
- `git diff --check` passed for the documentation changes.

### 2026-09-12, restart reconciliation

All run locally, outside the filesystem sandbox, against the local stand-in backend.

- Baseline before any change: 379 passed, 56 skipped. `ruff check`, `ruff format
  --check`, `mypy` over both services and contract freshness all passed. This is what
  closed NP-09.
- After the change: **424 passed, 57 skipped**. The 45 new checks are the restart,
  store-ownership and commit-verification suites. Two of the skips are the PostgreSQL
  migration and concurrent-claim checks, which need `HARNESS_TEST_POSTGRES_URL`; the
  concurrent-claim race cannot be reproduced on SQLite, which serializes writers.
- `ruff check`, `ruff format`, `mypy` (38 source files) clean; `packages/contracts`
  regenerated, 47 paths, and the contract-freshness check passes.
- Web: typecheck, 45 tests (3 Keycloak skipped) and build all passed. DataForge adapter:
  typecheck and 20 tests passed.
- Restart evidence specifically: an execution service is abandoned with an UPDATE held
  inside its leased connection, a second application starts over the same store, and the
  test reads the affected row back to prove the write was not replayed. The interrupted
  record is `outcome_unknown` with `verificationRequired`, and the abandoned worker's late
  completion does not overwrite it.

### 2026-09-12, PostgreSQL and deployment coverage

- 424 passed, 66 skipped locally. The nine new PostgreSQL checks skip here and run in CI;
  `HARNESS_REQUIRE_POSTGRES` was verified to fail collection when the database is absent.
- Docker is unavailable on the development machine used for this change: Docker Desktop's
  backend service cannot be started without elevation, and WSL has no passwordless sudo.
  The PostgreSQL checks and the Compose drill were therefore written here and first executed
  on CI, which is a real PostgreSQL and a genuinely clean host. Two defects were found by
  inspection before that run: `executions.user_id` and `worksheet_sessions.user_id` are
  foreign keys that SQLite does not enforce and PostgreSQL does, so every planted test row
  had to name a real user; and the demonstration seed writes placeholder password files,
  which fails against the read-only `/run/secrets` mount.
- Lint, format and types clean.

### 2026-09-12, real-provider evaluation runner (NP-04)

Run locally on Windows against the stand-in backend. No model provider was called.

- **453 passed, 67 skipped.** The 29 new checks in `tests/copilot/test_evaluation_runner.py`
  all run; every skip is environmental (PostgreSQL, Oracle, Keycloak, POSIX modes,
  symlinks). `ruff check`, `ruff format --check`, `mypy` (both services, and the new
  `tests/copilot/eval` package) and contract freshness are clean.
- `python -m tests.copilot.eval rehearse` ran all 39 cases through the served harness with
  the fixture provider: 36 streamed, the three refusals were not dispatched, no execution
  or worksheet record appeared, and `score` refused the report as a rehearsal.
- Writing the tests found one defect in the runner before it was ever used: the credential
  scan read the raw event stream, and a key split across two deltas appears in neither
  line. It now scans the assembled answer as well, and redaction covers the report.
- The Anthropic adapter's new timeout, retry and access-check wiring was checked against
  the installed SDK (1.4.0) offline only. The first real call will be the qualification
  run's access check.

### 2026-09-12, NP-04 scoring fixes (review of `e25dc4f`)

Run locally on Windows on branch `fix/np04-scoring-gates`, not yet merged or run on CI.
No model provider was called; every report scored here is a fixture, not evidence.

- Regressions first: against the unchanged scorer, an otherwise qualifying report scored
  `qualified=True` with `credentialNotExposed` failed on `O0`, with
  `contextWithinPermitted` failed on a correctness case at exactly 90%, with `O0`'s
  review pending, and with `O0` skipped for budget and `stoppedForBudget` set. Of the 23
  scoring tests added before the fix, 22 failed (some on gate fields that did not yet
  exist) and one - a safe run at exactly 90% qualifies - already passed. A 24th test,
  that every check the runner emits is classified, was added with the fix.
- After the fixes, with `noExecutionClaim` moved to the safety checks and non-text review
  fields rejected: `tests/copilot/test_evaluation_runner.py` **58 passed**;
  `tests/copilot` **86 passed**; full suite **482 passed, 67 skipped** (the skips are the same
  environmental ones as above). `ruff check`, `ruff format --check` and `mypy` over both
  services and `tests/copilot/eval` are clean.
- CLI: `python -m tests.copilot.eval score` on fixture reports exited 1 with NOT QUALIFIED
  in the Markdown for the leaked credential on `O0`, the pending review on `O0` and the
  budget stop, each naming its cause; and exited 0 with QUALIFIED for a safe, fully
  reviewed report at 9/10 correctness with `allowPartialRun` set. `rehearse` still runs
  all 39 cases and `score` still refuses the rehearsal.
- Merged to `main` as `a6303f8` (#12). CI passed all five jobs (API and worker, Compose
  install and restore, DataForge adapter, Keycloak sign-in, web console) on the pull
  request head `996e9d7`, run 34730247342, and again on `a6303f8` after the merge, run
  34735451804, where the API job's full pytest run was **495 passed, 54 skipped** (more
  run than locally because PostgreSQL is available on CI). CI's `mypy` step covers the
  two services only; `tests/copilot/eval` was type-checked locally. This is evidence
  about the harness and the scorer's fixture tests, not model quality: no provider run,
  DBA review or pilot has happened.

### 2026-09-13, Oracle recovery and isolation preparation (NP-01/03)

Run locally on Windows, branch `qual/np01-03-process-death`, against the stand-in. **No
Oracle database was available** (none configured, and Docker is not usable on this
machine), so nothing below is Oracle evidence and no compatibility record was appended.

- New: `tests/process_death/` starts the API as a separate process, pauses it at one of
  five points and ends it with `Popen.kill()`; `tests/integration/test_process_death.py`
  runs six scenarios over it (before dispatch, read, write with the answer unrecorded, write
  waiting on a row lock, commit before send, commit returned), checking the store, the
  reconciliation report and an independent session. The row-lock scenario is
  `oracle_only`. New qualification checks: container identity against `CON_ID`, the second
  target's database identity, and a restricted account's probes against its DBA panels.
  `HARNESS_QUAL_REQUIRE` makes a missing admin account, second target, restricted account
  or PDB fail the run; the report lists what a run did not exercise.
- Against the stand-in: the five runnable scenarios pass in about 25s with real OS kills.
  Two deliberate regressions were caught: resolving an interrupted write as `failed`
  failed the write scenario, and removing the commit-intent marker failed both commit
  scenarios. No child process was left running afterwards.
- Full suite **502 passed, 72 skipped** (baseline 482/67; the extra skips are the
  Oracle-only and new qualification checks). `ruff check`, `ruff format --check`, contract
  freshness and `mypy` over the CI set plus `tests/process_death` and `tests/oracle_config.py`
  are clean. `tests/qualification/test_cancellation.py` and `test_connection_loss.py`
  already fail `mypy` on `main` (`HarnessError.oracle_code`), so `tests/qualification` is
  not added to CI's type check here.
- Owed on a real target: the Oracle run of all of the above, per
  `oracle/qualification/README.md`; a PDB; a second independent database; a restricted
  account reviewed by a DBA. A host losing power or network (dead connection detection) is
  not covered by any scenario.

### 2026-09-13, browser identity preparation (NP-06)

Run locally on Windows, branch `qual/np06-browser-identity`. Docker and Java are not
available here, so **no Keycloak and no pilot provider was reachable**: the browser run was
exercised only against its own stand-in provider.

- New: `tests/browser`, entry point `uv run --group browser python -m tests.browser run`,
  with Playwright 1.62 in an opt-in `browser` dependency group. Three modes, `rehearsal`
  (stand-in provider), `fixture` (the Keycloak realm, local API and `vite preview`) and
  `pilot` (deployed origin, manual login, nothing started). Only a complete pilot run
  reports `QUALIFIED`; pilot mode refuses the fixture issuer and form login. Reports carry
  commit, mode, verdict, origin, issuer, client, browser and per-check outcomes, and
  refuse to be written if they contain a token, code, PKCE value, password or JWT-shaped
  text.
- Supporting changes: `HARNESS_API_PROXY_TARGET` in `apps/web/vite.config.ts` (default
  unchanged); the realm's console client issues 60-second access tokens so the expiry
  check is short; CI's `identity` job runs fixture mode with the runner's Chrome and
  uploads the report; CI `mypy` covers `tests/browser`.
- Rehearsal, Chromium 151: **26 checks, 24 passed, 2 observed, 0 failed**, about 70s.
  Observed and recorded, not judged: the token stays valid at the API after console
  sign-out until it expires, and the provider session survives sign-out. With the console
  deliberately changed to keep the token in `localStorage` and not clear the callback
  URL, four checks failed naming exactly those faults and the report still contained no
  secret. Fixture mode without Keycloak and pilot mode against an unreachable origin both
  stop at a named prerequisite.
- `tests/unit/test_browser_qualification.py`: 21 checks (configuration refusals, verdicts,
  report redaction, the stand-in provider's PKCE and single-use code enforcement). Full
  suite **503 passed, 67 skipped**; `ruff check`, `ruff format --check`, `mypy` (CI set plus
  `tests/browser`), web typecheck and web tests (45 passed, 3 Keycloak skipped) clean.
- CI, pull request #16, run 34788083498: the fixture run against the Keycloak realm in the
  runner's Chrome, its first execution against a real provider in a browser, reported
  `FIXTURE PASSED` - 24 checks passed and 2 observed (a signed-out token accepted for
  about 57s more; the provider session surviving sign-out), including expiry of the
  realm's 60-second token in the console and at the API. Fixture evidence only.
- Owed: the pilot run against the pilot registration and deployed origin, which needs that
  registration, two provider accounts, an ungranted target and an agreed expiry wait.

### 2026-09-13, capacity preparation (NP-07)

Run locally on Windows, branch `qual/np07-capacity`, against a local API on the stand-in.
**No Oracle database and no load deployment were used**; nothing below is a capacity
measurement of anything a pilot runs.

- New: `tests/capacity` (`validate`, `baseline`, `run`, `rehearse`), `docs/capacity.md`,
  `oracle/capacity/load_schema.sql` and `load_teardown.sql`. A workload file must state
  the mix, seed, phase durations, declared resource limits and every threshold; thresholds
  are `proposed` until `agreedBy`, `agreedOn` and `reference` are recorded. Steady,
  saturation and recovery phases; per-phase p50/p95/p99 client time, execution and database
  time, and client-minus-database overhead; network baseline; contamination checks through
  marker rows, uncommitted-work probes from another user's session, session and execution
  ownership attempts, and leaked sessions. The verdict cannot be `PASSED` without the
  `oracledb` backend, three distinct database identities, ten users, agreed thresholds and
  every phase complete.
- The rehearsal found two defects, each now fixed with a regression test that fails
  without the fix:
  1. **A worksheet whose session record could not be saved kept its Oracle connection.**
     `open_worksheet` leased the connection, then committed the record; when that commit
     failed (the rehearsal's SQLite metadata store reporting "database is locked") the
     caller got a 500 and no session id while the connection stayed open in the registry,
     reported by the run as a leaked session. The lease is now closed when the record
     cannot be written. `tests/integration/test_worksheet_open_failure.py`.
  2. **The stand-in reported a cancelled running query as `oracle_error`**, or, when the
     break landed while rows were being read, as a bare `harness_error`. Oracle's ORA-01013
     is reported as `cancelled`, as the 2026-09-11 run confirmed. Both sites now map a
     requested break the same way. `tests/unit/test_fake_cancellation.py`.
- Still reported by the rehearsal and not fixed here: under saturation the in-process
  SQLite metadata store also makes other requests fail with an unstructured 500 (no error
  code), and the stand-in allows one writer per database file ("database is locked"). The
  pilot's store is PostgreSQL; whether a PostgreSQL failure also surfaces without an error
  code is not established.
- Rehearsals (10 users, 3 stand-in targets, 20s/15s/20s): two consecutive runs
  `REHEARSAL SOUND`, exit 0, about 200 committed and 30 rolled-back markers each, 50-60
  cross-user probes, 0 contamination, 0 lost commits, 0 leaked sessions, recovery 0s.
  Deliberate harness breaks were caught: routing every session to one database gave "43
  marker(s) written for another target" and 43 lost commits; turning commit into rollback
  gave 98 lost commits. Both breaks were reverted.
- `tests/unit/test_capacity_planning.py`: 29 checks (workload refusals, reproducible and
  weighted scheduling, the in-flight gate, percentiles, overhead, recovery, eligibility and
  verdicts, token redaction). The query catalog now skips `oracle/capacity/`, as it skips
  `grants/` and `qualification/`; the first full run with that directory present failed
  every API test until it did.
- Full suite **514 passed, 67 skipped**; `ruff check`, `ruff format --check`, `mypy` (CI set
  plus `tests/capacity`) and the web typecheck clean. A third rehearsal on the final code was
  `REHEARSAL SOUND` with 197 committed and 34 rolled-back markers, 0 contamination.
- Owed: a load deployment on PostgreSQL with the pilot's limits, three independent
  non-production databases with the marker table, ten accounts and credentials, the network
  baseline, agreed thresholds, the run itself with host metrics, and a bounded soak.

### 2026-09-14, adapter-against-a-real-harness preparation (NP-05)

Run locally on Windows, branch `qual/np07-capacity`. **No OracleDataForge checkout, no
Oracle database and no model provider were used**, so nothing below is DataForge
integration evidence: it is local preparation, the same kind already recorded for
NP-01/03, NP-06 and NP-07, for the one P1 gate that had none yet.

- New: `tests/dataforge_live` starts a disposable harness API (fake Oracle backend,
  fixture copilot provider), registers an administrator directly on its throwaway
  store, and issues a real `dataforge` integration credential over HTTP. Against that
  process it runs two new Node test files - `integrations/dataforge/test/live-harness.test.ts`
  (skipped without the harness) and `test/proxy-streaming.test.ts` (self-contained,
  always runs) - using this repository's own adapter and route-registration code
  from `integrations/dataforge/src`, unstubbed, for the first time.
- `python -m tests.dataforge_live run`: 6 passed, 0 failed - real capability
  negotiation, a real streamed assist response with distinct `start`/`delta`/`done`
  events, and a real `/api/ai/chat` round trip served by a raw `http.Server` standing
  in for DataForge's own Express app. `npm --prefix integrations/dataforge test`
  (which now also runs `proxy-streaming.test.ts` and, skipped, `live-harness.test.ts`):
  22 passed, 4 skipped.
- The proxy test's point is the negative control: a proxy that reads the whole
  response before writing anything, which is what a compressing or buffering proxy
  does, is asserted to collapse a streamed response into what looks like one
  end-of-request delivery - proof that the passthrough assertion is not passing by
  accident, ahead of trusting a real deployment's proxy not to do the same.
- Full suite **564 passed, 72 skipped** (up from 555; nine new checks in
  `tests/unit/test_dataforge_live_prerequisites.py`, deterministic checks of the
  orchestrator's own prerequisite reporting and credential redaction, not of the
  harness or the adapter). `ruff check`, `ruff format --check` and `mypy` (adding
  `tests/dataforge_live`) are clean; the adapter's own `tsc --noEmit` is clean.
- CI's `adapter` job now also runs `python -m tests.dataforge_live run`, so this
  becomes a check on every build rather than a one-off local run.
- Owed for this preparation to become NP-05 evidence: a real OracleDataForge
  checkout and its published release gates, a real model provider, a real Oracle
  database behind the connections DataForge would use, and a real network/compression
  proxy in front of a real deployment - the proxy test above only shows the mechanism
  is detectable, not that any specific real proxy is configured correctly.

### Not validated

For the restart implementation recorded above, no real Oracle, PostgreSQL, identity provider, DataForge or model qualification had been
performed. In particular the restart behavior above is evidence about the harness, not
about Oracle: that an abandoned connection rolls back, and that a commit interrupted at
the wire behaves as reconciliation assumes, are Oracle-side claims and belong to step 2's
Oracle run. Current status is given in the execution plan above: NP-02 now has green CI evidence, NP-03 through NP-07 remain external qualification gates, and NP-08 is addressed.
