# Next phase: pilot qualification and release readiness

Prepared 2026-09-12; replanned against local commit `598870a` after restart recovery
and PostgreSQL/restore CI landed. This is the active follow-up to
[MVP_PLAN.md](MVP_PLAN.md), milestone 6. It is a proposed delivery plan, not evidence
that its gates have passed. Where a gate now has evidence, the row says so and names
what the evidence covers.

## Current execution plan

The next implementation milestone is **real-provider evaluation readiness** (NP-04),
followed by completing the external pilot qualification gates. Keep the existing
recovery and deployment work as the baseline.

CI for `598870a` is green: [run 34709372347](https://github.com/rammsguns/OracleDBHarness/actions/runs/34709372347).
This verifies the configured CI jobs, including PostgreSQL checks and the Compose
install/restore drill. It does not establish Oracle restart behavior, browser login,
real-provider quality or DataForge integration. Earlier validation entries below
remain historical run records.

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

**Status: implementation exit met locally; qualification exit not started.** Steps 1-4
and the controlled-response half of step 5 are done; see `tests/copilot/README.md` and
*Validation* below. What landed:

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
| 2 | Build and evaluate the provider runner (NP-04) — runner built | Integration engineer + DBA | Provider configuration, approved context and budget for the paid run | Runner checks (done), reviewed case report and both quality gates above (owed). |
| 3 | Qualify Oracle recovery, grants and isolation (NP-01/03) | Application engineer + DBA | Isolated schemas, direct grants, PDB and independent second database | Process-death cases before dispatch, during read/write and during commit; no replay or false success; restricted panels degrade correctly; target identity and state remain isolated. |
| 4 | Complete browser identity and DataForge integration (NP-05/06) | Integration engineer + identity owner | Pilot OIDC registration, deployed origin/proxy, pinned DataForge checkout; NP-04 for full provider flow | Browser callback/expiry/logout/access checks; editor context, incremental streaming, reviewed/stale diffs and outage recovery; both projects' regressions; measured setup time. |
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
| NP-01 | **Done, stand-in only** | Implemented in `services/api/harness_api/recovery.py`, run from `build_state` before the execution service exists. Covered by `tests/unit/test_restart_recovery.py` (23 checks) and `tests/integration/test_restart.py` (8), including a statement held in flight while its process is abandoned. | Met against the stand-in: never-dispatched work resolves to `cancelled`, interrupted reads to `failed`, interrupted writes to `outcome_unknown` with `verificationRequired`, stale worksheet records closed, nothing redispatched. The equivalent run on Oracle is still owed (step 2). |
| NP-02 | **Implemented; green CI at `598870a`** | The `python` job runs against a `postgres:16-alpine` service container, and `HARNESS_REQUIRE_POSTGRES` turns a missing database into a failed job rather than a silent skip of the only checks that cover the pilot store. A new `deployment` job runs `deploy/backup-restore-drill.sh`: clean install, seed, dump, restore into a fresh database, cut the API over. | Met for migration, persistence, installation and restore, on every build. Upgrade *from a released version* is still only covered synthetically — no earlier build's store exists to upgrade. |
| NP-03 | P1 | One Oracle 19.9 non-CDB/thin instance has passed; restricted grants, a PDB and independent target isolation remain unqualified. The recorded run substituted `SELECT_CATALOG_ROLE` for seven direct SYS-view grants. | Run the reviewed grants with appropriate DBA support and qualify restricted/developer accounts, a second database and a PDB; record exact versions and skipped checks. |
| NP-04 | P1; **runner implemented, run owed** | `tests/conftest.py` explicitly sets `copilot_provider="fake"`, so the default suite cannot be provider evidence. An opt-in runner and a 39-case set now exist under `tests/copilot/eval/`, with their negative paths in `tests/copilot/test_evaluation_runner.py`. No paid run or DBA review has happened. | Create a separate opt-in provider evaluation path that verifies the actual provider/model and rejects fixture results as qualification evidence. Run the DBA-reviewed case set. |
| NP-05 | P1 | DataForge adapter tests use a stub; no real DataForge commit, proxy streaming path or setup time has been qualified. See `integrations/dataforge/COMPATIBILITY.md`. | Integrate a pinned DataForge revision and pass its published release gates, including unchanged execution/transaction behavior. |
| NP-06 | P1 | Keycloak sign-in is tested under Node; browser redirect and the pilot identity registration have not been exercised. | Complete sign-in through the deployed console in a browser and verify API authorization, token expiry and rejected identities. |
| NP-07 | P1 | Ten concurrent users over three databases is a release target with no measurements. | Measure mixed-workload load, connection limits, queueing, cancellation and recovery; prove no cross-user or cross-target contamination. |
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

### Not validated

For the restart implementation recorded above, no real Oracle, PostgreSQL, identity provider, DataForge or model qualification had been
performed. In particular the restart behavior above is evidence about the harness, not
about Oracle: that an abandoned connection rolls back, and that a commit interrupted at
the wire behaves as reconciliation assumes, are Oracle-side claims and belong to step 2's
Oracle run. Current status is given in the execution plan above: NP-02 now has green CI evidence, NP-03 through NP-07 remain external qualification gates, and NP-08 is addressed.
