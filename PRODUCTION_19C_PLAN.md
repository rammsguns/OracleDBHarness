# Production on Oracle 19c: fix current issues, then go live

Prepared 2026-10-20 against `952920f` plus the uncommitted working tree. This is a
proposed plan, not evidence. Nothing below is done unless its task says so.

This plan is written to be executed by a coding agent (currently opencode with
DeepSeek V4.1 Flash; the model may change). Read **Rules for the executing agent**
before starting any task, and re-read it whenever you resume.

## Goal and fixed decisions

Use OracleDBHarness against real **production Oracle 19c** databases, and only 19c,
in two stages:

- **Stage 1: read-only.** Phases 0–6 below. Production is observation-only.
- **Stage 2: controlled writes.** Phases W0–W5 below. Typed, approved, audited write
  runbooks only. Stage 2 cannot start until Stage 1's exit gate (W-gate) is met, and
  D-2 stays in force until the owner accepts the W0 ADR.

These decisions are fixed. The agent must not change them, work around them, or
reopen them. Only the repository owner can change them, by editing this section.

| ID | Decision |
| --- | --- |
| D-1 | **Production goes live with the copilot and Kiwi off.** K-7 is NOT QUALIFIED (see [convergence analysis](docs/k7-convergence-analysis-2026-10-20.md)); production does not wait for it. |
| D-2 | **Production is observation-only** (ADR invariant 4): no worksheets, no compile, no mutating runbooks, no session writes, no Kiwi. |
| D-3 | **Production targets must be Oracle 19c.** Anything else is refused. |
| D-4 | **No pack-licensed views:** never `DBA_HIST_*`, ASH, SQL Monitor, `DBMS_SQLTUNE`, `DBMS_WORKLOAD_*` or `DBMS_ADDM`. |
| D-5 | **No new product scope:** no new features, IDE adapters, MCP tools or distributed workers. Only what a task below names. |

## Rules for the executing agent

1. **Do one task at a time, in order.** A task is done only when every **Verify**
   command passes and every **Done when** line is true. Then tick its checkbox in this
   file, in the same commit as the work.
2. **Never connect to a real Oracle database, and never create, edit or read files
   under `databases/`.** The agent works only against the local stand-in backend.
   Steps marked **HUMAN** (Oracle runs, grants, IdP, hosts, sign-off) are done by a
   person; the agent stops there, as rule 6 says.
3. **Never commit secrets or host identifiers:** passwords, wallets, real IPs or
   hostnames, cloud service names, or paths under `/home/`. The repo is **public**.
   Run the secret check (task 0.1) before every commit.
4. **Git:** never commit to `main` and never force-push. Use one branch per task
   group (`prod19c/phase0-scrub`, ...), and one PR per group, with `gh pr create`.
   End commit messages with the attribution line the user's tooling asks for.
5. **Before each commit, run the standard checks. All must pass:**
   ```bash
   export PATH=$HOME/.local/bin:$PATH UV_CACHE_DIR=$HOME/.cache/uv-oracledbharness
   uv run ruff format --check .
   uv run ruff check .
   uv run pytest -q
   npm --prefix apps/web run typecheck
   ```
   Baseline on 2026-10-20: 2434 passed, 83 skipped; ruff check clean; typecheck
   clean; `ruff format --check` reports 22 files (fixed in 0.4). Pytest must never
   lose a passing test. If a check fails and you cannot fix it within the task's
   scope, stop and report.
6. **Stop and ask the owner** when a task says **STOP**, when a fixed decision seems
   to block the work, or when the code does not match what a task describes. Do not
   guess. Write what you found under **Open questions** at the end of this file.
7. **Keep changes small and in the local style.** Match the surrounding naming,
   comment density and test layout (`tests/unit`, `tests/integration`). Do not
   reformat, rename or "clean up" code a task does not name.
8. **Don't edit history:** never rewrite the dated review docs, the trial verdicts,
   or `docs/compatibility.md` entries. Only append.

## Where things stand (2026-10-20)

| Area | State |
| --- | --- |
| Tests | 2434 passed, 83 skipped; ruff check and web typecheck clean; 22 files fail `ruff format --check` |
| Working tree | 63 modified files (+4940/−261) and ~150 untracked on `main`. `copilot/service.py` and `context.py` import 9 untracked modules. Nothing is backed up or in CI |
| Public repo leak risk | Untracked `.env.real.example` and `databases.example/targets.json.example` name a real remote IP and port, an ADB host and service name, and a `/home/kiwi` wallet path |
| Production guard gap | `policy.py:_check_environment` refuses `PERSISTENT_WRITE`/`ADMINISTRATIVE` on production, but worksheets depend only on `worksheetsEnabled`. A production target with that flag set would allow worksheet session writes. The copilot service does not check the target's environment |
| Version scope | `policy.py:_check_version` enforces each operation's minimum version; nothing restricts production to 19c |
| RAC | No diagnostic uses `GV$`. `oracle/diagnostics/dba/sessions.sql` and `blocking.sql` see one instance only |
| Licensing | No diagnostic references pack views today (checked by grep); no test enforces it |
| Oracle 19c evidence | 19.9.1.0.0 non-CDB only (thin and thick, AL32UTF8 and WE8MSWIN1252). Untested: a current RU, a 19c PDB, RAC, Data Guard, SE2, on-prem TCPS, direct-grant-only accounts, production-sized dictionaries |

---

## Phase 0: fix the current issues (agent)

### [ ] 0.1 Scrub host identifiers and add a secret check
Branch `prod19c/phase0-scrub`.
- In `.env.real.example`, set `HARNESS_ALLOWED_ENDPOINTS=oracle-19c-a.internal:1521,oracle-19c-b.internal:1521`.
- In `databases.example/targets.json.example`, replace every real `host`,
  `serviceName`, `walletDir` and port with placeholders (`oracle-19c-a.internal`,
  `ORCLPDB1`, `/path/to/wallet`). Keep the file's structure and keys exactly.
- Add `scripts/check_no_host_leaks.sh`. It greps the files staged for commit for
  IPv4 addresses other than `127.0.0.1` and `0.0.0.0`, `oraclecloud.com`, and
  `/home/`, and exits 1 on a match. It must allow the same placeholders in docs.
- Add `tests/unit/test_example_files.py`, which asserts that both example files
  contain none of those patterns.

**Verify:** `bash scripts/check_no_host_leaks.sh` exits 0, and
`uv run pytest -q tests/unit/test_example_files.py` passes.
**Done when:** `git grep -nE '172\.99\.|oraclecloud\.com|/home/kiwi'` on the branch
returns nothing outside `docs/compatibility.md` history. If it matches inside history
docs, **STOP** and list the matches in Open questions.

### [ ] 0.2 Separate source from scratch
Same branch.
- Add `copilot-eval/` to `.gitignore`.
- `git rm`/delete `strata_ultils.md` (byte-identical to `strata_utils.md`, checked
  with `cmp`).
- Move `claude_k7.md`, `claude_tasks.md`, `codex_tasks.md`, `agy_plan.md` and
  `restart_strata.md` to `docs/k7/notes/`.
- Move every `docs/k7-*-review-*.md` to `docs/k7/reviews/`, and update every link to
  them (`grep -rn "k7-.*-review" --include=*.md .`). Keep
  `docs/k7-acceptance-bar-*`, `k7-convergence-*`, `k7-remediation-plan.md`,
  `k7-qualification-success-plan.md` and `k7-completion-tasks.md` where they are.

**Verify:** a link check finds no broken relative `.md` links, for example
`grep -rhoE '\]\(([^)]+\.md)' --include=*.md .`, resolving each path against the
linking file.
**Done when:** the repo root contains only the project's own plans and docs.

### [ ] 0.3 Commit the existing work as reviewable PRs
Do not change behaviour in this task, only commit what is already there. Make one
branch and one PR for each group, in this order:
- (a) `prod19c/phase0-sql-headers`: `oracle/diagnostics/**` (`@required` headers),
  `services/worker/harness_worker/catalog.py`, `tests/unit/test_metadata_schema.py`
- (b) `prod19c/phase0-contracts`: `packages/contracts/**`,
  `tests/integration/test_contracts.py`
- (c) `prod19c/phase0-k7`: the 9 new `services/api/harness_api/copilot/*.py`
  modules, the modified copilot files, `config.py`, the backends `fake.py` and
  `etl_fixture.py`, and `tests/copilot/**`
- (d) `prod19c/phase0-web`: `apps/web/**`
- (e) `prod19c/phase0-tooling`: `scripts/bootstrap_targets.py`,
  `scripts/k7_oracle_checks.sh`, `scripts/restart_strata.sh`,
  `scripts/strata_stats.sh`, `oracle/grants/verify_harness_grants.sql`,
  `oracle/qualification/05_*`, `06_*`, `oracle/standards.example.json`,
  `tests/unit/test_bootstrap_targets.py`, and the modified docs and plans.

If one group's tests fail without another group, merge in dependency order, or put
both groups in one PR and say why in its description.
**Verify:** CI is green on each PR (`gh pr checks`).
**Done when:** `git status --short` on `main` after merging is empty, apart from
ignored files.

### [ ] 0.4 Formatting
Branch `prod19c/phase0-format`: run `uv run ruff format .`, and nothing else.
**Verify:** `uv run ruff format --check .` reports 0 files, and pytest is unchanged.

**HUMAN gate 0:** the owner reviews and merges the PRs from 0.1 to 0.4.

---

## Phase 1: enforce production rules in code (agent)

Branch `prod19c/phase1-guards`. Each task adds tests first, then the code.

### [ ] 1.1 Production can never get session writes or worksheets
- In `services/api/harness_api/policy.py`, `_check_environment`: when `production`
  is true, refuse `PERMISSION_WORKSHEET` and `RiskClass.SESSION_WRITE` even if
  `profile.worksheets_enabled` is true. Use the same message style as the existing
  production refusal.
- At target create and update (find the admin targets router under
  `services/api/harness_api/routers/`), reject `worksheetsEnabled: true` or
  `mutatingRunbooksEnabled: true` when `environment` is `production`. Return HTTP 422
  with a clear message.
- Tests in `tests/unit/test_policy.py` and an integration test cover: production with
  the flags forced true in the store is still refused; create and update are refused;
  development and test behave as before.

**Verify:** the new tests pass and the full suite passes.

### [ ] 1.2 Copilot and Kiwi refused on production targets
- In the copilot service entry point (`services/api/harness_api/copilot/service.py`)
  and the Kiwi lookup path, refuse any request whose target environment is
  `production`, whatever `HARNESS_COPILOT_ENABLED` and `HARNESS_KIWI_ENABLED` say.
  Return the typed refusal the copilot already uses for policy denials. If there is
  none, **STOP** and ask.
- The capabilities endpoint reports copilot as unavailable for production targets.
- Add tests to `tests/copilot/`.

**Verify:** the new tests pass and the full suite passes.

### [ ] 1.3 Production targets must be Oracle 19c
- Add `production_oracle_releases: tuple[int, ...] = (19,)` to `config.py`, set from
  `HARNESS_PRODUCTION_ORACLE_RELEASES` (comma-separated). Follow the existing
  settings pattern.
- When a target's identity is proven (`POST /api/v1/targets/{id}/test`), and before
  every dispatch, refuse a `production` target whose proven major version is not in
  the list. Use a typed diagnostic `unsupported_release` that names the version found
  and the allowed list.
- Store and return the full version string (`VERSION_FULL` on 19c; it exists from
  18c) in the target identity, and show it in the console's target details.
- The stand-in backend must be able to report a configurable version, so the tests
  can cover 19 (allowed) and 23 (refused).
- Document the setting in `docs/setup.md` and `.env.example`.

**Verify:** the new tests pass, the full suite passes, and `npm --prefix apps/web run
typecheck` passes.

### [ ] 1.4 Licensing guard test
Add `tests/unit/test_no_pack_views.py`. It scans every `*.sql` under
`oracle/diagnostics` and `oracle/runbooks`, and every Python string under
`services/` that holds SQL, for the D-4 names (case-insensitive), and fails on a
match. Allowlist only comments that say a view is *not* used.
**Verify:** it passes on the current tree, and fails if you temporarily add
`SELECT * FROM DBA_HIST_SNAPSHOT` to a scratch copy. Do not commit that copy.

### [ ] 1.5 RAC-aware sessions and blockers. **STOP first:** needs the owner's answer to Q-3
- If production has RAC: add `GV$` variants of
  `oracle/diagnostics/dba/sessions.sql` and `blocking.sql` (with `INST_ID`, and
  blockers matched on `BLOCKING_INSTANCE`). Choose them when the identity probe
  reports `CLUSTER_DATABASE = TRUE`. Seed the stand-in with matching `GV$` tables and
  test both paths.
- If production has no RAC: the identity probe records `CLUSTER_DATABASE`, and a
  production target with it true is refused with `unsupported_topology`.

### [ ] 1.6 Production manifest template
Add `databases.example/targets.production.json.example` with placeholder 19c targets
only: `environment: production`, both flags false, and placeholder hosts. Make
`scripts/bootstrap_targets.py` refuse a production entry with either flag true.
Test it in `tests/unit/test_bootstrap_targets.py`.

**HUMAN gate 1:** the owner reviews and merges Phase 1.

---

## Phase 2: qualify on production-shaped 19c (HUMAN, with a DBA)

The agent may prepare checklists and docs for this phase, but never runs anything
against Oracle.

- [ ] 2.1 **Fleet inventory.** For each production database record: RU, CDB/PDB, RAC
  nodes, Data Guard role, edition, character set, TCP or TCPS, network path, object
  count, `ALL_SOURCE` row count. Answers Q-3.
- [ ] 2.2 **Staging twins:** one refreshed clone per distinct shape, at minimum a 19c
  PDB at the production RU.
- [ ] 2.3 **Run on each twin** with `HARNESS_QUAL_REQUIRE` set: `tests/qualification`,
  `tests/integration`, `tests/e2e`, the K-4 catalog and process death. Use thin mode
  unless production needs thick; if thick, pin and test the Instant Client version.
- [ ] 2.4 **Least privilege:** direct grants from `oracle/grants/harness_roles.sql`, no
  `SELECT_CATALOG_ROLE`. Check them with `verify_harness_grants.sql`. Add an Oracle
  profile (`SESSIONS_PER_USER`, `IDLE_TIME`, `FAILED_LOGIN_ATTEMPTS`), a Resource
  Manager consumer group that caps CPU and parallelism, and a unified audit policy on
  the account.
- [ ] 2.5 **Dictionary-scale cost:** time every diagnostic on the biggest twin
  (elapsed time, buffer gets, plan). Look hardest at `object_source_search`,
  `object_dependencies`, `V$SQL` scans and the scheduler views. Set timeouts and page
  sizes from what you measure. Fix or disable anything over budget. *(Agent follow-up
  task 2.5a: apply the timeouts and page sizes the DBA records, as a normal PR.)*
- [ ] 2.6 **TCPS** with the production CA or wallet, if production uses it.
- [ ] 2.7 Append each run to `docs/compatibility.md` in the "Recording a run" format.

Exit: every production shape has a recorded run, with no unresolved defect that
could mislead, leak access or load production.

## Phase 3: deployment hardening

- [ ] 3.1 *(HUMAN)* Register the console with the corporate IdP. Run
  `python -m tests.browser` in `pilot` mode against the deployed origin (NP-06).
  Create accounts and grants for the named DBAs only.
- [ ] 3.2 *(agent)* Add `deploy/.env.production.example`: `HARNESS_ENV=production`,
  copilot and Kiwi off, conservative `HARNESS_MAX_ROWS` and
  `HARNESS_STATEMENT_TIMEOUT_SECONDS`, and a placeholder allowlist. Add a startup
  check in `config.py`: when `HARNESS_ENV=production`, refuse to start with
  `HARNESS_AUTH_MODE=dev`, an empty `HARNESS_ALLOWED_ENDPOINTS`, a SQLite metadata
  URL, or the copilot or Kiwi enabled. Test it.
- [ ] 3.3 *(HUMAN)* PostgreSQL 16; full suite with `HARNESS_REQUIRE_POSTGRES`; scheduled
  backups; `deploy/backup-restore-drill.sh` on the production host; agreed audit
  retention.
- [ ] 3.4 *(HUMAN)* TLS in front of the loopback-bound console; allowlist set to exactly
  the production listeners; secrets as mode-600 files or in the vault; database
  firewall open to the harness host only.
- [ ] 3.5 *(HUMAN)* Agree the NP-07 latency and error thresholds, then run the ten-user
  load on the twins and record the database-side cost (sessions, CPU, shared pool).
- [ ] 3.6 *(agent, then HUMAN)* Write `docs/operations.md` sections for the production
  on-call runbook: alerts, disabling a target, revoking access. A person then runs the
  restart drill on the production host.
- [ ] 3.7 *(HUMAN)* Security review and DBA change approval, with names and dates.

## Phase 4: canary (HUMAN, 2 weeks)

One low-risk production 19c database, two or three DBAs, observation features only.
Check the audit log and database cost daily. **Rollback:** disable the target, then
revoke the grants or lock the account. Nothing was written, so nothing needs undoing.
Exit: no misleading-result or access defect, cost within budget, DBA sign-off.

## Phase 5: expand (HUMAN)

Add the remaining 19c databases one shape at a time, each after its twin has a
recorded run. Re-qualify after every RU upgrade or python-oracledb bump.
python-oracledb stays pinned at 4.0.2.

## Phase 6: copilot and Kiwi (separate track, development targets only)

Not part of the production rollout. Follow
[docs/k7-acceptance-bar-2026-10-20.md](docs/k7-acceptance-bar-2026-10-20.md). Using
the copilot on production would need a new ADR and a change to D-1.

---

# Stage 2: controlled writes on production

**Do not start any W task until the W-gate is ticked by the owner.** Until the W0
ADR is accepted, D-2 still applies and production stays observation-only. Every
Stage 1 rule for the executing agent still applies, and rule 2 (never connect to
Oracle) is unchanged: the agent builds and tests against the stand-in only.

### Fixed decisions for Stage 2

| ID | Decision |
| --- | --- |
| W-D1 | **Only typed runbooks write to production.** Free-form worksheets, DML and DDL stay refused on production permanently. |
| W-D2 | **PL/SQL compile and deploy on production stay out of the harness.** They go through the team's release pipeline. |
| W-D3 | **Every production write needs two people.** A requester and a different approver, both with the `dba` role and a grant on that target. Nobody can approve their own request. |
| W-D4 | **Writes are enabled per runbook and per target**, never by one global switch, and are off by default. |
| W-D5 | **Writes use a separate Oracle account** (`HARNESS_OPS`) with only the grants its enabled runbooks need. The Stage 1 read account never gets write privileges. |
| W-D6 | **No automatic retry.** A write whose outcome is unknown stays `outcome_unknown` until a person verifies it (the existing reconciliation path). |

### Write steps, in order of risk

| Step | Runbooks | When |
| --- | --- | --- |
| W-a | `recompile_object`, `gather_table_stats` (they exist, typed and verified) | First |
| W-b | New: kill a blocking session; enable or disable a scheduler job | After W-a has run cleanly in production for an agreed period |
| — | Compile or deploy PL/SQL; free-form SQL | Never (W-D1, W-D2) |

### [ ] W-gate: Stage 1 exit (HUMAN, owner ticks)

- Phase 4 canary done, and Phase 5 running on the target databases.
- At least the agreed period (suggested 4–8 weeks) in read-only production, with no
  misleading-result, access or cost defect open.
- DBA and security reviewers agree that Stage 2 may start, with names and dates.

## Phase W0: decision record (agent drafts, HUMAN accepts)

### [ ] W0.1 Draft ADR "Controlled writes on production 19c"
Add it to `docs/decisions.md` with the next free ADR number and status
**Proposed**. Contents:
- What replaces invariant 4: W-D1 to W-D6.
- The approval lifecycle (W1.2).
- Per-target and per-runbook enablement (W1.1).
- The separate write account (W-D5).
- What is still forbidden, and why.
- How a write is reconciled after a crash (`outcome_unknown`).

The agent writes no code in this task.
**STOP:** the owner, the DBA and security must accept the ADR. The owner changes D-2
in the Stage 1 table to "superseded by ADR-00NN for typed runbooks", and marks the
ADR **Accepted**.

## Phase W1: approval and enablement (agent, stand-in only)

Branch `prod19c/w1-approvals`. Tests first, then code, as in Stage 1.

### [ ] W1.1 Per-target, per-runbook write enablement
- Add a store table `target_write_enablement` (target id, runbook id, enabled by,
  enabled at, change reference). Bump `SCHEMA_VERSION` in
  `services/api/harness_api/db.py` and register a migration, following the
  existing pattern there. Add a frozen-store upgrade test like the schema-v5 one in
  `tests/unit/test_metadata_schema.py`.
- Admin routes in `routers/admin.py` to enable and disable a runbook on a target.
  Enabling requires the `administrator` role and a change reference. Each action is
  written to the append-only audit.
- In `policy.py:_check_environment`, a production `ADMINISTRATIVE` runbook is allowed
  **only** when that pair is enabled **and** the request carries an approved approval
  (W1.2). Everything that task 1.1 refuses (worksheets, `SESSION_WRITE`,
  `PERSISTENT_WRITE` from free-form paths) stays refused. Add tests that prove each
  refusal still holds.

### [ ] W1.2 Two-person approval
- Add a store table `write_approvals`: id, target, runbook, parameters (exact
  values), parameter hash, requester, approver, change reference, status
  (`requested` → `approved` | `rejected` | `expired` → `executed`), timestamps, and
  an expiry (default 60 minutes, `HARNESS_WRITE_APPROVAL_TTL_SECONDS`).
- Routes: request, approve, reject, list pending. Approval is refused when the
  approver is the requester, lacks `dba` or the target grant, the request has
  expired, or the parameters changed (hash mismatch).
- Running the runbook consumes the approval exactly once. A second use, or a use
  with different parameters, is refused. Two concurrent executions of one approval:
  exactly one wins (test it with the pattern in `tests/unit/test_execution_races.py`).
- Every state change is audited with who and when.

### [ ] W1.3 Optional maintenance windows
- Per target: an optional list of weekly windows (day, start, end, time zone).
  Outside a window a production write is refused with `outside_maintenance_window`.
  If no window is set, writes are allowed at any time once approved.
- Test the edges at window start and end, and across a time-zone change.

### [ ] W1.4 Preview before execution
- `POST /runbooks/{id}/preview` returns exactly what would run: the resolved
  statement, binds, the target, the objects affected and the verification that will
  follow. It uses a read-only lookup and changes nothing.
- An approval stores the preview it was approved against. Execution re-runs the
  preview and refuses (`preview_changed`) if it differs, for example because the
  object is already valid or has been dropped.

### [ ] W1.5 Separate write credential
- A target gains an optional `writeSecretReference` and `writeUsername`. Production
  write runbooks connect only with those; reads keep using the Stage 1 account. With
  no write credential, production writes are refused (`no_write_credential`).
- The identity probe checks that the write account is a different user from the read
  account, and refuses if they are the same.
- Never log either credential (extend `tests/unit/test_secret_containment.py`).

### [ ] W1.6 Console
In `apps/web`: a "Request change" action on the runbook screens for production
targets, a pending-approvals view for DBAs, the approval page showing the preview
and change reference, and a clear production banner on every write screen. Add web
tests in the existing style. **Verify:** `npm --prefix apps/web run typecheck` and
the web tests pass.

**HUMAN gate W1:** the owner reviews and merges. Security reviews the approval code
specifically.

## Phase W2: Oracle side (HUMAN, with a DBA)

- [ ] W2.1 Create `HARNESS_OPS` on each staging twin with only what W-a needs:
  `ALTER ANY PROCEDURE` or object-level compile rights as the DBA prefers, and
  `ANALYZE ANY` or `DBMS_STATS` on the named schemas. Prefer per-schema rights to
  `ANY` privileges wherever 19c allows it. Add the reviewed grant script to
  `oracle/grants/` (the agent can draft it as `harness_ops_roles.sql`; the DBA
  reviews it).
- [ ] W2.2 Give the ops account a profile, a Resource Manager group and a unified
  audit policy covering all its actions.
- [ ] W2.3 Rehearse every W-a runbook on each twin: success, the verification
  failing, the object locked, a lock timeout, the session killed mid-run, and the
  process killed mid-run (must end as `outcome_unknown` with nothing re-dispatched).
  Record each run in `docs/compatibility.md`.
- [ ] W2.4 Check the gather-statistics options are pinned to the team's standard
  (estimate percent, method_opt, cascade, no_invalidate), so the harness never
  overrides the DBA's statistics preferences. *(Agent follow-up W2.4a: make the
  runbook take its defaults from `DBMS_STATS.GET_PREFS` instead of literals, if it
  doesn't already, with tests.)*

## Phase W3: write canary (HUMAN, about 4 weeks)

- One production 19c database, W-a runbooks only, two or three named DBAs, writes
  inside a maintenance window.
- Review every executed approval against its change ticket, weekly.
- **Rollback:** disable the enablement rows, then lock `HARNESS_OPS`. Stage 1
  read-only use continues untouched.
- Exit: no wrong-object, wrong-target, unapproved or unaudited write; every
  `outcome_unknown` reconciled; DBA and security sign-off.

## Phase W4: new typed runbooks (agent, then HUMAN)

Only after W3 exits. One runbook per PR, each with a `RunbookSpec` in
`services/api/harness_api/runbooks.py`, a reviewed SQL file in `oracle/runbooks/`,
a verification rule, risk `ADMINISTRATIVE`, and tests against the stand-in. Each one
then goes through W2.3 (rehearsal) and a W3-style canary before it can be enabled on
another production target.

- [ ] W4.1 `kill_blocking_session`: takes SID, SERIAL# and, on RAC, INST_ID. The
  preview re-reads `V$SESSION`/`GV$SESSION` and shows the user, program, SQL_ID and
  lock held. Execution is refused if SERIAL# changed. Verification checks the session
  is gone or marked KILLED. Never allowed on background or SYS sessions.
- [ ] W4.2 `set_scheduler_job_enabled`: owner, job name, enabled true or false. The
  preview shows the current state and next run. Verification re-reads
  `ALL_SCHEDULER_JOBS`.

## Phase W5: expand (HUMAN)

Enable each runbook on each further production target separately, with its own
change reference. Re-rehearse after every RU upgrade.

---

## Questions for the owner

| ID | Question | Blocks |
| --- | --- | --- |
| Q-1 | Confirm D-1: production with the copilot and Kiwi off | Phase 1.2 |
| Q-2 | Confirm D-2 for Stage 1: no recompile or gather-statistics runbooks on production until the W0 ADR | Phase 1.1 |
| Q-3 | Does production 19c use RAC? Data Guard? PDBs? TCPS? | 1.5, Phase 2, W4.1 |
| Q-4 | Named DBA reviewer, security reviewer, IdP owner, canary database | Phases 2–4, W-gate |
| Q-5 | How long must Stage 1 run before the W-gate (suggested 4–8 weeks)? | W-gate |
| Q-6 | Change-ticket system and reference format (for example `CHG0012345`), and whether maintenance windows are required | W1.1–W1.3 |

## Open questions

*(The executing agent appends findings here, with the task ID and date.)*
