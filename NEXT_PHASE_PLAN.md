# Next phase: pilot qualification and release readiness

Prepared 2026-09-12 against local commit `263d2f2`; updated the same day with the
first implementation results. This is the active follow-up to
[MVP_PLAN.md](MVP_PLAN.md), milestone 6. It is a proposed delivery plan, not evidence
that its gates have passed. Where a gate now has evidence, the row says so and names
what the evidence covers.

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
| NP-02 | P1 | Unchanged: the PostgreSQL migration test is still opt-in and absent from `.github/workflows/ci.yml`. Installation and backup restoration still have no recorded evidence. Schema version 4 added a migration step, so the untested path is now longer, not shorter. | Exercise PostgreSQL migration and API persistence in CI using a disposable database; demonstrate clean Compose installation, upgrade and restore. |
| NP-03 | P1 | One Oracle 19.9 non-CDB/thin instance has passed; restricted grants, a PDB and independent target isolation remain unqualified. The recorded run substituted `SELECT_CATALOG_ROLE` for seven direct SYS-view grants. | Run the reviewed grants with appropriate DBA support and qualify restricted/developer accounts, a second database and a PDB; record exact versions and skipped checks. |
| NP-04 | P1 | `tests/copilot/README.md` suggests environment variables switch the suite to a real provider, but `tests/conftest.py` explicitly sets `copilot_provider="fake"`. | Create a separate opt-in provider evaluation path that verifies the actual provider/model and rejects fixture results as qualification evidence. Run the DBA-reviewed case set. |
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
deployment half is untouched.** What landed:

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

Still owed in this step: the same restart run against Oracle, PostgreSQL migration and
restart-persistence coverage in CI, and a clean Compose install, upgrade and restore.

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

### Not validated

No real Oracle, PostgreSQL, identity provider, DataForge or model qualification has been
performed. In particular the restart behavior above is evidence about the harness, not
about Oracle: that an abandoned connection rolls back, and that a commit interrupted at
the wire behaves as reconciliation assumes, are Oracle-side claims and belong to step 2's
Oracle run. NP-02 through NP-08 remain open.
