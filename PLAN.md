# OracleDBHarness delivery plan

Created 2026-10-22. This is the single active project plan, replacing the nine
previous planning documents. Checkboxes describe outstanding verification or work,
not a claim that existing implementation is absent. Historical reviews, decisions,
compatibility records and evaluation artifacts remain evidence; their verdicts are
not changed by this consolidation. Old plan names in historical records identify
removed documents available in Git history (`git show 2a73d4f:<name>`).

## How to execute this plan

- Tasks have IDs (`B1`, `P2`, `K3`…). Do them in ID order within a section. One task
  per branch, named `plan/<id>-<slug>`, for example `plan/B0-commit-consolidation`.
- Each task lists **Verify** commands and a **Done when** condition. A task is done
  only when every Verify command was run and its output recorded in the completion
  record at the end of this file (commit, commands, pass/fail counts, limitations).
- **STOP** means: finish the current task, write the completion record, and wait for
  the user. **HUMAN** marks work only a named person can do; prepare inputs for it
  and stop.
- If a step's facts disagree with the working tree, record the disagreement and STOP
  rather than guessing.

## Outcome and current baseline

Deliver observation-only production Oracle 19c access, complete pilot qualification,
and qualify Kiwi separately for development targets. The MVP workflows and local
qualification tooling exist. Oracle D1–D4 qualification is recorded for 2026-10-14
in [compatibility](docs/compatibility.md) (python-oracledb 4.0.2, thin and thick).
The 2026-10-19 three-database, ten-user capacity run met proposed criteria; agreed
thresholds and a fresh run are still owed ([capacity](docs/capacity.md)). Pilot
OIDC and real DataForge integration remain separate from fixture checks.

K-7 remains **NOT QUALIFIED**. State as of 2026-10-22, verified against the tree:

- Trials 25–29 (model `qwen3.8-flash-next-iq3_s`, case set `2026-10-18.1`) scored
  15/16/16/15/15 combined. Trial 29: narrow 18/18, full grounding 15/18
  ([review](docs/k7-q5-trial29-review-2026-10-20.md)).
- Wave 2X (Claude; `claude_k7.md` "Wave 2X handoff") added `absence_inference`,
  `control_flow`, the checked answered-at-bound path (`_bound_answer` in
  `service.py`) and the `bounded_fallback` draft. It is committed in `2a73d4f`
  (#40), but **no dated Wave 2X review exists in `docs/`**. Its Part A misses
  (negation look-back hiding, uncited statement lines, lock-handler `CONTINUE`,
  comment-validity predicates) are unfixed.
- The bounded no-tools answer **retry does not exist** in `service.py`, and
  `fallback.py` does not explain Oracle application errors (it quotes them only).
- A 2026-10-21 focused diagnostic, `copilot-eval/cmp-strata-k7-focused-20261021T060834Z.*`
  (harness `952920f` with uncommitted changes, 9/18, KINJ-02 safety failure), is
  untracked and not referenced by any review. It is diagnostic only.
- The "Wave 2Y" outline (untracked, no longer present) reported a 17/18 focused
  result and a bounded fallback that failed to explain ORA-20001. Neither figure
  can be traced to a retained report; treat them as unverified.

## Execution authority and order

This plan supersedes active assignments and file-ownership restrictions in
`claude_tasks.md` and `codex_tasks.md`; those files and `claude_k7.md` remain
historical context and evidence. Codex owns the current execution queue. No other
worker is assumed active; do not delegate without explicit authorization. If the
user names a different executing agent, that agent inherits the queue unchanged.

Sections 1 and 2 prepare the production track. Sections 3 and 4 form a separate
Kiwi development qualification track; they do not block the observation-only
production canary. Section 5 distinguishes pilot gates from production rollout.
External dependencies block only the tasks that require them.

## Fixed constraints

- Production launches with copilot and Kiwi off. Kiwi remains off by default.
- Stage 1 production is observation-only: no worksheets, compile, session writes,
  mutating runbooks or Kiwi. Refuse production targets other than Oracle 19c.
- Exclude pack-licensed access: DBA_HIST_*, ASH, SQL Monitor, DBMS_SQLTUNE,
  DBMS_WORKLOAD_* and DBMS_ADDM.
- Preserve authorization, per-target identity, bounded execution, audit records,
  proposal review and explicit execution. Never automatically execute model output.
- Keep the provider local-only; do not silently change the model, case set, budgets,
  rubric or qualification denominator. Record requested and served identities.
- No added product scope, IDE adapters, MCP tools or distributed workers.
- Work one task at a time. Use task branches; never commit to main or force-push.
  Do not publish, run providers or access real databases merely to update this plan.
- Production preparation uses stand-ins. Real production-shaped database work,
  grants, IdP setup and operational sign-off belong to the named human owners.
  Do not access `databases/` during stand-in implementation work.
- Preserve dated reviews, trial verdicts and compatibility entries. Keep secrets,
  wallets and real host identifiers out of source and reports.

## Standard checks

Referred to below as "standard checks":

```
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
npm --prefix apps/web run typecheck
git diff --check
```

## 1. Reconcile the baseline

- [ ] **B0 Commit the consolidation.** The deletion of the nine old plans, the edits
  to README, docs and task files, and this file are uncommitted on `main`. Move them
  to `plan/B0-commit-consolidation` and open a PR. Repoint live (non-historical)
  references to removed plans: `.env.example` lines 40 and 93,
  `deploy/backup-restore-drill.sh` line 18. Leave dated reviews,
  `docs/compatibility.md` run records and `docs/decisions.md` history unchanged.
  **Verify:** `git status --short -- ':!copilot-eval'` is empty after commit;
  `git grep -n -E "MVP_PLAN|NEXT_PHASE_PLAN|k7-remediation-plan" -- .env.example deploy`
  returns nothing. **Done when:** the PR is open and not merged by the agent.
- [ ] **B1 Triage untracked evaluation output.** About 150 files under
  `copilot-eval/` are untracked. List them by run and decide for each: commit as
  evidence (reports referenced by a review), or add a `.gitignore` rule for scratch
  (`*.log`, `*.pre-review.json`, `*.pre-ai-review.json`). Delete nothing. Scan every
  file to be committed for host names, IPs, wallet paths and credentials first.
  **Verify:** the secret scan from B2 passes on the staged set. **Done when:**
  `git status --short copilot-eval` shows only files deliberately left untracked,
  each listed in the completion record.
- [x] **B2 Secret scan.** Add a repeatable secret/host scan (script under
  `scripts/`, documented in `docs/operations.md`) and run it over tracked files.
  **Verify:** the scan exits 0 on the tree and non-zero on a planted fixture
  string. **Done when:** findings are fixed or recorded with a reason.
- [ ] **B3 Record current checks.** Run the standard checks on the B0 branch and
  record counts. Fix formatting only within the files a task touches.
  **Done when:** every failure is recorded with the failing test IDs.

Exit: a reproducible baseline with failures explicitly recorded and no secret-bearing
artifacts included in changes. Documentation consolidation alone does not pass it.

## 2. Enforce the production boundary

- [ ] **P1** Verify API and console refusals for production worksheets, session
  writes, compile, mutating runbooks, copilot and Kiwi, including direct API
  requests. Add missing regression tests; do not change refusal semantics.
- [ ] **P2** Verify Oracle 19c identity checks fail closed for unsupported production
  targets (23ai/26ai, unknown banner, identity lookup failure).
- [ ] **P3** Check the diagnostic catalog for licensed features (fixed list above)
  and verify restricted grants degrade to explicit permission states rather than
  false healthy results.
- [ ] **P4 Pin the driver.** `services/worker/pyproject.toml` declares
  `oracle = ["oracledb>=2.4"]` and `uv.lock` resolves it unpinned. Change it to
  `oracledb==4.0.2`, run `uv lock`, and check the deployment image installs that
  version. **Verify:** `uv run python -c "import oracledb; print(oracledb.__version__)"`
  prints `4.0.2`; standard checks pass.
- [ ] **P5 HUMAN.** Fleet shape (RAC, Data Guard, CDB/PDB, TCPS) is established by
  the DBA. Do not implement shape-dependent session/blocker diagnostics before it
  is recorded under "Decisions still needed".
- [ ] **P6** Add or verify production manifest and environment examples, TLS/proxy
  settings, exact origin allowlists, secret references and production operations
  procedures. Placeholders only.

Exit: targeted permission/refusal regressions and standard checks pass on stand-ins;
configuration examples contain placeholders and preserve all fixed decisions.

## 3. Finish the K-7 bounded-answer work and review

Allowed edits for K1–K3: `services/api/harness_api/copilot/{service.py,fallback.py}`,
`services/api/harness_api/config.py`, `.env.example`, `docs/copilot.md` and focused
tests under `tests/copilot/`. This supersedes the older task-file prohibition on
editing `service.py`. Keep protocol contracts (`packages/contracts`) unchanged;
any grounding-check change outside these files is a separate, separately reviewed
task.

- [ ] **K0 Review Wave 2X.** No dated review exists for the code committed in
  `2a73d4f`. Publish `docs/k7-wave2x-review-2026-10-<dd>.md` covering the handoff's
  checks: replay of retained Trial 25–29 events, adversarial probes for the four
  unfixed Part A misses, and the T26 KINJ-02 finding it introduced. Also review the
  2026-10-21 diagnostic run (identify its code state from its log and say whether
  it reflects Wave 2X). **Done when:** the review states pass/fail per item and is
  committed. Do not start K1 on a failing review; STOP instead.
- [ ] **K1 Bounded answer retry.** When a forced-answer turn produces no diagnosis,
  allow one no-tools answer retry. It consumes the remaining step/time/token budget;
  do not raise default bounds. Cap retries at one, add a config setting (default on
  or off is the user's decision; default off until decided), and document it in
  `.env.example` and `docs/copilot.md`.
- [ ] **K2 Application-error fallback.** Make the harness fallback state the
  documented meaning of Oracle application errors (ORA-20000 to ORA-20999: raised
  by application code via `RAISE_APPLICATION_ERROR`) and cite a job or action only
  when retained lookup evidence supports it. Do not name the raising source unless
  a lookup returned it, and do not infer its absence from scoped searches.
- [ ] **K3 Tests.** Cover successful retry, exhausted retry, disabled retry, budget
  exhaustion, cancellation, unsupported evidence and non-application-error
  controls. Preserve existing assertions; no case-specific production logic.
  **Verify:** `uv run pytest -q tests/copilot` and the standard checks.
- [ ] **K4 Review.** Review K1–K3 with retained trial replay, new adversarial probes,
  false-positive controls and the full relevant suites. Publish a dated review in
  `docs/`. This does not authorize a provider run. **STOP.**
- [ ] **K5 Rubric.** Publish `docs/k7-grounding-rubric.md` and a separate
  calibration regrade of Trials 25–29, as specified in
  [the acceptance bar §2](docs/k7-acceptance-bar-2026-10-20.md). Resolve the
  T26 KINJ-02 / T29 DIAG-02 pair to one grade; the reviewer for a disputed grade is
  a different agent/model from the first. Original audits stay unchanged. If the
  resolved grade conflicts with the Wave 2X `absence_inference` check, record that
  for the user; do not change the check in this task.

Exit: reviewed bounded behavior, no weakened safety/grounding controls and a stable
rubric. K-7 remains unqualified until the next section passes.

## 4. Qualify Kiwi on an unchanged candidate

The accepted [2026-10-20 decision](docs/k7-acceptance-bar-2026-10-20.md)
is authoritative. S1 means action-changing or safety-relevant error, fabricated
evidence/action or injection compliance. S2 means descriptive imprecision that
leaves the remedy correct and safe.

- [ ] **Q0 Scoring.** Before counting Q5 runs, add optional grounding-review
  `severity` and a pooled scoring mode in `tests/copilot/eval/scoring.py` (none
  exists today). Historical single-trial output must stay byte-identical. Verify
  with deterministic report fixtures: S1 rejection, S2 total and per-case
  concentration limits, narrow/safety failures, missing/disputed reviews, candidate
  changes and the Q6 per-run limits. Incomplete or disputed evidence cannot pass.
  Record candidate commit, configuration, model, rubric and case-set identities so
  three consecutive trials can be verified as one unchanged candidate.
  **Verify:** re-score one retained historical report and diff it against the
  committed output (no difference).
- [ ] **Q1 HUMAN.** Release provider runs only after K4, K5 and Q0 are done and the
  user records run authorization. Retain provenance, usage, budget bounds, lookup
  evidence and all delivered stages.
- [ ] **Q5** Three consecutive focused trials on one unchanged candidate; 54/54
  narrow rubrics, no safety/injection failures, zero S1, at most three S2 across 54
  cases, and no case with S2 in more than one trial. Keep D1 compile/check,
  cleanup, secret-scan and provenance gates. Trials before rubric publication do
  not count.
- [ ] Restart the pooled attempt after any S1, narrow or safety failure or candidate
  change; preserve failed reports and explain the change. **STOP** after each
  failure.
- [ ] **Q6** Full A and B on the same clean commit, each 52/52 rubric and 19/19
  safety, with completion/provenance/repeatability intact. Review all 39 required
  answers per run; require zero S1 and at most two S2 per run.
- [ ] **QL HUMAN-gated lab run.** After Q6, complete the separate real-schema
  evaluation using `tests/copilot/eval/lab_cases.json` (`lab-2026-10-14.1`, nine
  cases, correctness denominator nine). Map `lab:D1:HR`, `lab:D1:OE`, `lab:D1:CO`,
  `lab:D1:SH` and `lab:D2:SALES_LAB` to verified observation-only profiles. A D2
  lab is separate from the Oracle 19c-only production rollout; retain its mutation
  refusals. Obtain run authorization and assign the live-run owner before
  accessing Oracle. Never execute attached examples or perform fixture
  setup/teardown on lab schemas. Review all nine answers against retained live
  dictionary/source evidence; record pass/fail/unknown, reviewer and target
  identity, and resolve every failed or unknown case before sign-off. Retain
  independent pre/post audit verification of zero lab-schema mutations. Keep this
  corpus separate from Q5/Q6 denominators.
- [ ] **Q7** Publish `docs/k7-qualification-result.md` with Q5, Q6 and the nine-case
  lab results, failure patterns, limitations and final review. Reconcile README and
  system caveats against actual evidence. Missing lab review or mutation evidence
  leaves Q7 open.

Exit: Q5, Q6 and Q7 all pass with retained evidence. AI review is labelled as such;
runner completion or a diagnostic result alone cannot qualify Kiwi.

## 5. Complete pilot and production qualification

All rows are HUMAN-owned; the agent may prepare procedures and report templates.

| Gate | Remaining work | Owner / evidence |
| --- | --- | --- |
| NP-01/03 Oracle | Preserve D1–D4 evidence; qualify each production-shaped staging twin, direct grants, process death, isolation, dictionary cost and TCPS where applicable | DBA; append commands, versions and outcomes to docs/compatibility.md |
| NP-02 deployment/store | Verify current PostgreSQL CI and install/restore drill; retain a release-store snapshot and exercise an actual released-version upgrade | Application/operations owner; distinguish synthetic migration tests |
| NP-04 quality | Complete reviewed real-provider qualification under section 4 | Application engineer and DBA reviewer; retained artifacts |
| NP-05 DataForge | Use a pinned real checkout and deployed proxy; verify context, streaming, stale/reviewed diffs and outages in both projects | Integration owner; integrations/dataforge/COMPATIBILITY.md |
| NP-06 identity | Run pilot registration against deployed origin: two accounts, ungranted target, callback, expiry, logout and authorization | IdP owner; browser report, separate from Keycloak fixtures |
| NP-07 capacity | Agree numerical thresholds, workload, resource limits and soak duration before a fresh three-database ten-user run; retain network baseline and recovery/host metrics | Application owner and DBA; docs/capacity.md and run report |
| NP-08 documentation | Reconcile setup, caveats, compatibility and release status against completed gates | Application owner; no unsupported qualification claims |

- [ ] **G1** Assign named owners and availability dates to external dependencies.
- [ ] **G2** Obtain security review and DBA change approval with names and dates.
- [ ] **G3 Canary.** Start a read-only canary only after sections 1–2 (including P4),
  production-shaped staging, deployed identity/TLS, restore, agreed capacity and
  security/DBA approval gates pass. Use one low-risk Oracle 19c database and two
  or three DBAs for a proposed initial two weeks; record the approved
  database-cost budget before starting. Check audit records and database cost daily
  and retain incident reports. Exit requires no unresolved misleading-result or
  access defect, database cost within the agreed budget and named DBA sign-off. On
  a failed gate, disable the target, then have the DBA revoke harness grants or
  lock the account; verify access is refused and retain the incident and recovery
  evidence. Exercise observation workflows on the production canary. Exercise
  write-dependent MVP workflows only on development/staging targets for the
  separate pilot gate.
- [ ] **G4** Expand one qualified production shape at a time. Full pilot release
  still requires every mandatory pilot gate; production observation-only rollout
  keeps copilot/Kiwi disabled independently of K-7 progress.
- [ ] **G5 Upgrades.** Requalify each affected staging shape after every Oracle RU
  or python-oracledb version change before production promotion. Retain old/new
  versions, thin/thick modes where deployed, regression results and DBA approval;
  a historical D1–D4 pass does not qualify an upgraded environment. Changing the
  P4 pin is such a change.

## 6. Controlled production writes — deferred

Stage 1 observation-only remains binding. No write implementation or enablement
follows merely from this plan's creation. Do not start this section without a
user instruction.

- [ ] **W1** Owner confirms Stage 1 exit evidence and the observation period
  (previously proposed as 4–8 weeks), then accepts a controlled-writes ADR.
- [ ] **W2** Define per-target/per-runbook enablement, two-person approval,
  change-ticket references, optional maintenance windows, preview, separate write
  credentials, audit and console behavior before enabling anything.
- [ ] **W3** DBA provisions least privilege on staging twins and rehearses success,
  failure, cancellation and verification; pin statistics options to the agreed
  standard.
- [ ] **W4** Run an approved write canary (previously proposed as about four weeks).
  Session-kill and scheduler changes require separate typed, reviewed runbooks;
  verify SID/SERIAL#/INST_ID where applicable before any future expansion.

## Decisions still needed

- Fleet topology and TCPS requirements (blocks P5, shape-dependent diagnostics).
- Named DBA/security/IdP owners and canary database (blocks G1–G3).
- Agreed capacity thresholds (blocks NP-07, G3).
- Stage 1 observation period; change-ticket format and maintenance-window policy
  (block W1–W2).
- K1 retry default (on/off) once K4 is reviewed.
- Whether the K5 grade or the Wave 2X `absence_inference` check stands if they
  disagree.

These block their dependent tasks only.

## Completion record

2026-10-22: consolidated nine planning documents into this file, renamed originals
with `_old`, then removed them at the user's request. Planning only: no provider,
Oracle, deployment or qualification work was performed by this consolidation.

2026-10-22 review correction: reconciled execution authority, restored canary
exit/rollback and upgrade gates, and made scoring tooling and lab sign-off explicit.

2026-10-22 second review (Claude): checked the baseline against the tree. Replaced
the untraceable Wave 2Y summary with verified state (Trial 29 figures, Wave 2X
committed without a review, retry absent, untracked 2026-10-21 diagnostic). Added
task IDs, Verify/Done-when, STOP/HUMAN markers, B0 (the consolidation is still
uncommitted on main, with live references to removed plans), B1 (untracked
evaluation output), K0 (Wave 2X review), and made the driver pin a concrete task
(P4). Planning only.


2026-10-22 B0 — consolidation committed as `d1abbf4` on
`plan/B0-commit-consolidation`; this completion record is a separate follow-up
commit. Reviewed every modified tracked file before staging: all changes were
plan-reference/documentation consolidation, including comment-only updates in
`.env.example` and `deploy/backup-restore-drill.sh`. Eight tracked old-plan
deletions were committed; the coordinator confirmed that the ninth document was
the untracked Wave 2Y outline already absent from the tree. No additional files
were deleted.

Verification after the consolidation commit:

- `git status --short -- ':!copilot-eval'`: no output (pass).
- `git grep -n -E "MVP_PLAN|NEXT_PHASE_PLAN|k7-remediation-plan" -- .env.example deploy`:
  no output, exit 1 as expected for no matches (pass).
- `git log --oneline -2`: `d1abbf4 Consolidate project plans and update active documentation references`;
  `2a73d4f Add K-7 evidence checks and bounded proposal remediation (#40)`.
- `git diff --check HEAD~1`: no output, exit 0 (pass).
- `uv run ruff format --check .`: `307 files already formatted`, exit 0 (pass).
- `uv run ruff check .`: `All checks passed!`, exit 0 (pass).

Limitations: the two initial uv invocations exited 2 because the default
`~/.cache/uv/CACHEDIR.TAG` was not writable; rerunning with
`UV_CACHE_DIR=/tmp/oracledbharness-b0-uv-cache` passed both checks above. No
`copilot-eval/` or `OrcaServer/` files were staged. Dated reviews, compatibility
run records and decision history remain unchanged. The dispatch explicitly
prohibited pushing or opening a PR, so B0's PR condition remains pending and its
checkbox stays open. No provider, deployment or database work was performed;
B1 was not started.


2026-10-22 B1 — evaluation-output triage completed on
`plan/B1-triage-eval-output`; evidence/ignore commit `c90c008`; this
completion record is the separate second commit. Inventory: 144 initially
untracked files in 42 basename groups (the plan estimated about 150).
No evaluation file was edited, moved or deleted. No source, tests, database
inventory or Orca runtime files were staged or changed.

| Decision | Files | Reason |
| --- | ---: | --- |
| Committed | 60 | Cited run evidence passing manual scan: 23 Markdown reports, 27 explicitly cited logs, 8 AI review records, 2 identity records |
| Ignored | 29 | Uncited logs or pre-review snapshots; exact rooted paths in .gitignore avoid hiding cited/future evidence |
| Left untracked: scan exclusion | 40 | Cited JSON reports/snapshots have non-placeholder local endpoint IP/URL matches; not staged |
| Left untracked: scratch | 15 | No tracked docs/ or claude_k7.md citation; not one of the allowed scratch ignore types |

Citation commands: `git ls-files --others --exclude-standard copilot-eval/`;
`git ls-files docs/ claude_k7.md`; for each basename stem,
`git grep -n -F "$stem" -- <tracked-doc-paths>`; for logs and pre-review
snapshots also `git grep -l -F "$filename" -- <tracked-doc-paths>`.
Only docs/ and claude_k7.md supplied citation authority; PLAN.md references
did not promote the uncited 2026-10-21 diagnostic. All file paths in the
tables below are relative to copilot-eval/. A suffix expands against its row
stem. Every original file is represented once in the decision columns.

| Run/report stem | COMMIT suffixes | IGNORE suffixes | Left untracked suffixes | Citation sources |
| --- | --- | --- | --- | --- |
| `cmp-strata-k7-focused-20261021T060834Z` | — | `.log`, `.pre-review.json` | `.json`, `.md` | None |
| `diag-2026-10-16-focused-1` | `.log` | `.pre-ai-review.json` | `.json` | `claude_k7.md` |
| `diag-2026-10-16-focused-2` | `.log` | `.pre-ai-review.json` | `.json` | `claude_k7.md` |
| `diag-2026-10-16-focused-3` | `.log` | `.pre-ai-review.json` | `.json` | `claude_k7.md` |
| `review-2026-10-15-case-limits` | — | — | `.html` | None |
| `review-2026-10-16-one-block` | — | — | `.html` | None |
| `run-2026-10-15` | — | — | `.json`, `.md` | None |
| `run-2026-10-15-answer-only` | — | — | `.json` | None |
| `run-2026-10-15-budget` | — | — | `.json` | None |
| `run-2026-10-15-case-limits` | — | `.log` | `.json` | None |
| `run-2026-10-15-explain-time` | — | `.log` | `.json` | None |
| `run-2026-10-15-false-positives` | — | — | `.json` | None |
| `run-2026-10-15-last-turn` | — | `.log` | `.json` | None |
| `run-2026-10-15-sampled` | — | — | `.json`, `.md` | None |
| `run-2026-10-15-source-search` | — | `.log` | `.json` | None |
| `run-2026-10-16-one-block` | `.md` | `.log` | `.json`, `.pre-ai-review.json` | `docs/k7-dba-review-2026-10-16.md`, `docs/k7-pre-review.md` |
| `strata-k7-focused-20261016T214747Z` | `.log` | `.pre-ai-review.json` | `.json` | `claude_k7.md` |
| `strata-k7-focused-20261016T222023Z` | `.log` | `.pre-ai-review.json` | `.json` | `claude_k7.md` |
| `strata-k7-focused-20261016T224557Z` | `.log` | `.pre-ai-review.json` | `.json` | `claude_k7.md`, `docs/k7-claude-round6-review-2026-10-16.md` |
| `strata-k7-focused-20261017T000141Z` | `.log` | `.pre-ai-review.json` | `.json` | `claude_k7.md`, `docs/k7-claude-round7-review-2026-10-17.md` |
| `strata-k7-focused-20261017T052221Z` | `.log`, `.md` | `.pre-ai-review.json` | `.json` | `claude_k7.md` |
| `strata-k7-focused-20261017T054408Z` | `.log`, `.md` | `.pre-ai-review.json` | `.json` | `claude_k7.md`, `docs/k7-claude-round9-review-2026-10-17.md` |
| `strata-k7-focused-20261017T061240Z` | `.log`, `.md` | `.pre-ai-review.json` | `.json` | `claude_k7.md`, `docs/k7-claude-round10-review-2026-10-17.md` |
| `strata-k7-focused-20261017T071121Z` | `.log`, `.md` | `.pre-ai-review.json` | `.json` | `claude_k7.md` |
| `strata-k7-focused-20261017T073417Z` | `.log`, `.md` | `.pre-ai-review.json` | `.json` | `claude_k7.md` |
| `strata-k7-focused-20261017T075344Z` | `.log`, `.md` | `.pre-ai-review.json` | `.json` | `claude_k7.md`, `docs/k7-claude-round13-review-2026-10-17.md` |
| `strata-k7-focused-20261017T150834Z` | `.log`, `.md` | `.pre-ai-review.json` | `.json` | `claude_k7.md`, `docs/k7-claude-round14-review-2026-10-18.md` |
| `strata-k7-focused-20261018T005950Z` | `.log`, `.md` | `.pre-ai-review.json` | `.json` | `claude_k7.md` |
| `strata-k7-focused-20261018T014422Z` | `.log`, `.md` | `.pre-ai-review.json` | `.json` | `claude_k7.md`, `docs/k7-claude-round16-review-2026-10-18.md` |
| `strata-k7-focused-20261018T023504Z` | `.log`, `.md` | `.pre-ai-review.json` | `.json` | `claude_k7.md`, `docs/k7-claude-round17-review-2026-10-18.md` |
| `strata-k7-focused-20261018T032443Z` | `.log`, `.md` | `.pre-ai-review.json` | `.json` | `claude_k7.md`, `docs/k7-claude-round18-review-2026-10-18.md` |
| `strata-k7-focused-20261018T180931Z` | `.log`, `.md` | — | `.json`, `.pre-ai-review.json` | `claude_k7.md`, `docs/k7-q5-trial19-review-2026-10-18.md` |
| `strata-k7-focused-20261019T010105Z` | `.log`, `.md` | — | `.json`, `.pre-ai-review.json` | `claude_k7.md`, `docs/k7-q5-trial20-review-2026-10-19.md` |
| `strata-k7-focused-20261019T050700Z` | `.log`, `.md` | — | `.json`, `.pre-ai-review.json` | `claude_k7.md`, `docs/k7-q5-trial21-review-2026-10-19.md` |
| `strata-k7-focused-20261019T155430Z` | `.ai-review.json`, `.log`, `.md` | — | `.json`, `.pre-ai-review.json` | `claude_k7.md`, `docs/k7-q5-trial22-review-2026-10-19.md` |
| `strata-k7-focused-20261019T175628Z` | `.ai-review.json`, `.log`, `.md` | — | `.json`, `.pre-ai-review.json` | `claude_k7.md`, `docs/k7-q5-trial23-review-2026-10-19.md` |
| `strata-k7-focused-20261019T194933Z` | `.ai-review.json`, `.log`, `.md` | — | `.json`, `.pre-ai-review.json` | `claude_k7.md`, `docs/k7-q5-trial24-review-2026-10-19.md` |
| `strata-k7-focused-20261019T231907Z` | `.ai-review.json`, `.log`, `.md` | — | `.json`, `.pre-ai-review.json` | `claude_k7.md`, `docs/k7-q5-trial25-review-2026-10-19.md` |
| `strata-k7-focused-20261020T040546Z` | `.ai-review.json`, `.log`, `.md` | — | `.json`, `.pre-ai-review.json` | `claude_k7.md`, `docs/k7-q5-trial26-review-2026-10-20.md` |
| `strata-k7-focused-20261020T053711Z` | `.ai-review.json`, `.log`, `.md` | — | `.json`, `.pre-ai-review.json` | `claude_k7.md`, `docs/k7-q5-trial27-review-2026-10-20.md` |
| `strata-k7-focused-20261020T144306Z` | `.ai-review.json`, `.identity.json`, `.md` | `.log`, `.pre-ai-review.json` | `.json` | `claude_k7.md`, `docs/k7-q5-trial28-review-2026-10-20.md` |
| `strata-k7-focused-20261020T155124Z` | `.ai-review.json`, `.identity.json`, `.md` | `.log`, `.pre-ai-review.json` | `.json` | `claude_k7.md`, `docs/k7-q5-trial29-review-2026-10-20.md` |

Scan commands and results (before staging, repeated before evidence commit):

```sh
grep -nEi '([0-9]{1,3}\.){3}[0-9]{1,3}|password|passwd|secret|token|wallet|BEGIN .*PRIVATE|@[a-z0-9-]+\.[a-z]|host=|connect[_ -]?string|hostname|https?://|[a-z0-9.-]+\.(local|internal|lan)\b' "$file"
git diff --cached --check
git check-ignore -q --no-index "$file"
```

The grep command ran individually on all 100 evidence candidates; raw match
lines stayed in temporary local files and are not copied here. Hit counts below
count matching lines. The 40 excluded JSON files have 110–282 matching lines
(including endpoint addresses); they remain untracked. Other matches included
token metrics, wallet redaction markers, Oracle version numbers and public
documentation URLs. No excluded content was edited to make it pass.

The 60 committed files were rescanned: 37 had zero hits, and 23 had exactly
one hit each, the Markdown table heading `In/out tokens`. That heading is a
metric label, not a credential. Manual review accepted these 23 false positives;
zero unresolved secret/host findings remain in the committed evidence set.
Additional hostname/path review extracted domain-shaped words and paths with
`(?:[A-Za-z0-9_-]+\.)+[A-Za-z]{2,}|(?:/[A-Za-z0-9_.-]+){2,}`
from committed candidates: matches were Python logger names, report filenames
and repository-relative report paths, with no host names or wallet paths.
The B2 repeatable scan does not exist yet; B1 used the dispatch-prescribed
grep/manual review on the staged set, without starting B2.

| Committed file | grep hit lines | Disposition |
| --- | ---: | --- |
| `diag-2026-10-16-focused-1.log` | 0 | No matches |
| `diag-2026-10-16-focused-2.log` | 0 | No matches |
| `diag-2026-10-16-focused-3.log` | 0 | No matches |
| `run-2026-10-16-one-block.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261016T214747Z.log` | 0 | No matches |
| `strata-k7-focused-20261016T222023Z.log` | 0 | No matches |
| `strata-k7-focused-20261016T224557Z.log` | 0 | No matches |
| `strata-k7-focused-20261017T000141Z.log` | 0 | No matches |
| `strata-k7-focused-20261017T052221Z.log` | 0 | No matches |
| `strata-k7-focused-20261017T052221Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261017T054408Z.log` | 0 | No matches |
| `strata-k7-focused-20261017T054408Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261017T061240Z.log` | 0 | No matches |
| `strata-k7-focused-20261017T061240Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261017T071121Z.log` | 0 | No matches |
| `strata-k7-focused-20261017T071121Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261017T073417Z.log` | 0 | No matches |
| `strata-k7-focused-20261017T073417Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261017T075344Z.log` | 0 | No matches |
| `strata-k7-focused-20261017T075344Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261017T150834Z.log` | 0 | No matches |
| `strata-k7-focused-20261017T150834Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261018T005950Z.log` | 0 | No matches |
| `strata-k7-focused-20261018T005950Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261018T014422Z.log` | 0 | No matches |
| `strata-k7-focused-20261018T014422Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261018T023504Z.log` | 0 | No matches |
| `strata-k7-focused-20261018T023504Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261018T032443Z.log` | 0 | No matches |
| `strata-k7-focused-20261018T032443Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261018T180931Z.log` | 0 | No matches |
| `strata-k7-focused-20261018T180931Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261019T010105Z.log` | 0 | No matches |
| `strata-k7-focused-20261019T010105Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261019T050700Z.log` | 0 | No matches |
| `strata-k7-focused-20261019T050700Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261019T155430Z.ai-review.json` | 0 | No matches |
| `strata-k7-focused-20261019T155430Z.log` | 0 | No matches |
| `strata-k7-focused-20261019T155430Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261019T175628Z.ai-review.json` | 0 | No matches |
| `strata-k7-focused-20261019T175628Z.log` | 0 | No matches |
| `strata-k7-focused-20261019T175628Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261019T194933Z.ai-review.json` | 0 | No matches |
| `strata-k7-focused-20261019T194933Z.log` | 0 | No matches |
| `strata-k7-focused-20261019T194933Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261019T231907Z.ai-review.json` | 0 | No matches |
| `strata-k7-focused-20261019T231907Z.log` | 0 | No matches |
| `strata-k7-focused-20261019T231907Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261020T040546Z.ai-review.json` | 0 | No matches |
| `strata-k7-focused-20261020T040546Z.log` | 0 | No matches |
| `strata-k7-focused-20261020T040546Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261020T053711Z.ai-review.json` | 0 | No matches |
| `strata-k7-focused-20261020T053711Z.log` | 0 | No matches |
| `strata-k7-focused-20261020T053711Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261020T144306Z.ai-review.json` | 0 | No matches |
| `strata-k7-focused-20261020T144306Z.identity.json` | 0 | No matches |
| `strata-k7-focused-20261020T144306Z.md` | 1 | Token metric heading only |
| `strata-k7-focused-20261020T155124Z.ai-review.json` | 0 | No matches |
| `strata-k7-focused-20261020T155124Z.identity.json` | 0 | No matches |
| `strata-k7-focused-20261020T155124Z.md` | 1 | Token metric heading only |

Verification: `git diff --cached --check` before commit: no output, exit 0.
Ignore audit with `git check-ignore -q --no-index` over all 144 initial files:
29 intended ignores matched, 115 others did not (144/144 pass); no cited file
is ignored. `git status --short copilot-eval`: exit 0, exactly the following
55 deliberately untracked files; each reason follows in the table.

```text
?? copilot-eval/cmp-strata-k7-focused-20261021T060834Z.json
?? copilot-eval/cmp-strata-k7-focused-20261021T060834Z.md
?? copilot-eval/diag-2026-10-16-focused-1.json
?? copilot-eval/diag-2026-10-16-focused-2.json
?? copilot-eval/diag-2026-10-16-focused-3.json
?? copilot-eval/review-2026-10-15-case-limits.html
?? copilot-eval/review-2026-10-16-one-block.html
?? copilot-eval/run-2026-10-15-answer-only.json
?? copilot-eval/run-2026-10-15-budget.json
?? copilot-eval/run-2026-10-15-case-limits.json
?? copilot-eval/run-2026-10-15-explain-time.json
?? copilot-eval/run-2026-10-15-false-positives.json
?? copilot-eval/run-2026-10-15-last-turn.json
?? copilot-eval/run-2026-10-15-sampled.json
?? copilot-eval/run-2026-10-15-sampled.md
?? copilot-eval/run-2026-10-15-source-search.json
?? copilot-eval/run-2026-10-15.json
?? copilot-eval/run-2026-10-15.md
?? copilot-eval/run-2026-10-16-one-block.json
?? copilot-eval/run-2026-10-16-one-block.pre-ai-review.json
?? copilot-eval/strata-k7-focused-20261016T214747Z.json
?? copilot-eval/strata-k7-focused-20261016T222023Z.json
?? copilot-eval/strata-k7-focused-20261016T224557Z.json
?? copilot-eval/strata-k7-focused-20261017T000141Z.json
?? copilot-eval/strata-k7-focused-20261017T052221Z.json
?? copilot-eval/strata-k7-focused-20261017T054408Z.json
?? copilot-eval/strata-k7-focused-20261017T061240Z.json
?? copilot-eval/strata-k7-focused-20261017T071121Z.json
?? copilot-eval/strata-k7-focused-20261017T073417Z.json
?? copilot-eval/strata-k7-focused-20261017T075344Z.json
?? copilot-eval/strata-k7-focused-20261017T150834Z.json
?? copilot-eval/strata-k7-focused-20261018T005950Z.json
?? copilot-eval/strata-k7-focused-20261018T014422Z.json
?? copilot-eval/strata-k7-focused-20261018T023504Z.json
?? copilot-eval/strata-k7-focused-20261018T032443Z.json
?? copilot-eval/strata-k7-focused-20261018T180931Z.json
?? copilot-eval/strata-k7-focused-20261018T180931Z.pre-ai-review.json
?? copilot-eval/strata-k7-focused-20261019T010105Z.json
?? copilot-eval/strata-k7-focused-20261019T010105Z.pre-ai-review.json
?? copilot-eval/strata-k7-focused-20261019T050700Z.json
?? copilot-eval/strata-k7-focused-20261019T050700Z.pre-ai-review.json
?? copilot-eval/strata-k7-focused-20261019T155430Z.json
?? copilot-eval/strata-k7-focused-20261019T155430Z.pre-ai-review.json
?? copilot-eval/strata-k7-focused-20261019T175628Z.json
?? copilot-eval/strata-k7-focused-20261019T175628Z.pre-ai-review.json
?? copilot-eval/strata-k7-focused-20261019T194933Z.json
?? copilot-eval/strata-k7-focused-20261019T194933Z.pre-ai-review.json
?? copilot-eval/strata-k7-focused-20261019T231907Z.json
?? copilot-eval/strata-k7-focused-20261019T231907Z.pre-ai-review.json
?? copilot-eval/strata-k7-focused-20261020T040546Z.json
?? copilot-eval/strata-k7-focused-20261020T040546Z.pre-ai-review.json
?? copilot-eval/strata-k7-focused-20261020T053711Z.json
?? copilot-eval/strata-k7-focused-20261020T053711Z.pre-ai-review.json
?? copilot-eval/strata-k7-focused-20261020T144306Z.json
?? copilot-eval/strata-k7-focused-20261020T155124Z.json
```

| Deliberately untracked file | Reason |
| --- | --- |
| `cmp-strata-k7-focused-20261021T060834Z.json` | Uncited scratch outside allowed log/snapshot ignore types; retained unchanged |
| `cmp-strata-k7-focused-20261021T060834Z.md` | Uncited scratch outside allowed log/snapshot ignore types; retained unchanged |
| `diag-2026-10-16-focused-1.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `diag-2026-10-16-focused-2.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `diag-2026-10-16-focused-3.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `review-2026-10-15-case-limits.html` | Uncited scratch outside allowed log/snapshot ignore types; retained unchanged |
| `review-2026-10-16-one-block.html` | Uncited scratch outside allowed log/snapshot ignore types; retained unchanged |
| `run-2026-10-15.json` | Uncited scratch outside allowed log/snapshot ignore types; retained unchanged |
| `run-2026-10-15.md` | Uncited scratch outside allowed log/snapshot ignore types; retained unchanged |
| `run-2026-10-15-answer-only.json` | Uncited scratch outside allowed log/snapshot ignore types; retained unchanged |
| `run-2026-10-15-budget.json` | Uncited scratch outside allowed log/snapshot ignore types; retained unchanged |
| `run-2026-10-15-case-limits.json` | Uncited scratch outside allowed log/snapshot ignore types; retained unchanged |
| `run-2026-10-15-explain-time.json` | Uncited scratch outside allowed log/snapshot ignore types; retained unchanged |
| `run-2026-10-15-false-positives.json` | Uncited scratch outside allowed log/snapshot ignore types; retained unchanged |
| `run-2026-10-15-last-turn.json` | Uncited scratch outside allowed log/snapshot ignore types; retained unchanged |
| `run-2026-10-15-sampled.json` | Uncited scratch outside allowed log/snapshot ignore types; retained unchanged |
| `run-2026-10-15-sampled.md` | Uncited scratch outside allowed log/snapshot ignore types; retained unchanged |
| `run-2026-10-15-source-search.json` | Uncited scratch outside allowed log/snapshot ignore types; retained unchanged |
| `run-2026-10-16-one-block.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `run-2026-10-16-one-block.pre-ai-review.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261016T214747Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261016T222023Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261016T224557Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261017T000141Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261017T052221Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261017T054408Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261017T061240Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261017T071121Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261017T073417Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261017T075344Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261017T150834Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261018T005950Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261018T014422Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261018T023504Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261018T032443Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261018T180931Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261018T180931Z.pre-ai-review.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261019T010105Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261019T010105Z.pre-ai-review.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261019T050700Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261019T050700Z.pre-ai-review.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261019T155430Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261019T155430Z.pre-ai-review.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261019T175628Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261019T175628Z.pre-ai-review.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261019T194933Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261019T194933Z.pre-ai-review.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261019T231907Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261019T231907Z.pre-ai-review.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261020T040546Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261020T040546Z.pre-ai-review.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261020T053711Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261020T053711Z.pre-ai-review.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261020T144306Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |
| `strata-k7-focused-20261020T155124Z.json` | Scan exclusion: local endpoint IP/URL; retained unchanged, not staged or ignored |

`git show --stat HEAD` for `c90c008`: exit 0; file statistics below
(author metadata omitted to avoid copying personal contact details).

```text
 .gitignore                                         |  31 ++++++
 copilot-eval/diag-2026-10-16-focused-1.log         |   9 ++
 copilot-eval/diag-2026-10-16-focused-2.log         |   9 ++
 copilot-eval/diag-2026-10-16-focused-3.log         |   9 ++
 copilot-eval/run-2026-10-16-one-block.md           |  95 ++++++++++++++++++
 .../strata-k7-focused-20261016T214747Z.log         |   9 ++
 .../strata-k7-focused-20261016T222023Z.log         |   9 ++
 .../strata-k7-focused-20261016T224557Z.log         |   9 ++
 .../strata-k7-focused-20261017T000141Z.log         |   9 ++
 .../strata-k7-focused-20261017T052221Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261017T052221Z.md | 110 ++++++++++++++++++++
 .../strata-k7-focused-20261017T054408Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261017T054408Z.md | 108 ++++++++++++++++++++
 .../strata-k7-focused-20261017T061240Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261017T061240Z.md | 109 ++++++++++++++++++++
 .../strata-k7-focused-20261017T071121Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261017T071121Z.md | 107 ++++++++++++++++++++
 .../strata-k7-focused-20261017T073417Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261017T073417Z.md | 109 ++++++++++++++++++++
 .../strata-k7-focused-20261017T075344Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261017T075344Z.md | 111 +++++++++++++++++++++
 .../strata-k7-focused-20261017T150834Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261017T150834Z.md | 109 ++++++++++++++++++++
 .../strata-k7-focused-20261018T005950Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261018T005950Z.md | 109 ++++++++++++++++++++
 .../strata-k7-focused-20261018T014422Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261018T014422Z.md | 106 ++++++++++++++++++++
 .../strata-k7-focused-20261018T023504Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261018T023504Z.md | 108 ++++++++++++++++++++
 .../strata-k7-focused-20261018T032443Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261018T032443Z.md | 109 ++++++++++++++++++++
 .../strata-k7-focused-20261018T180931Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261018T180931Z.md | 110 ++++++++++++++++++++
 .../strata-k7-focused-20261019T010105Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261019T010105Z.md | 109 ++++++++++++++++++++
 .../strata-k7-focused-20261019T050700Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261019T050700Z.md | 105 +++++++++++++++++++
 ...rata-k7-focused-20261019T155430Z.ai-review.json |   7 ++
 .../strata-k7-focused-20261019T155430Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261019T155430Z.md | 107 ++++++++++++++++++++
 ...rata-k7-focused-20261019T175628Z.ai-review.json |   7 ++
 .../strata-k7-focused-20261019T175628Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261019T175628Z.md | 107 ++++++++++++++++++++
 ...rata-k7-focused-20261019T194933Z.ai-review.json |   7 ++
 .../strata-k7-focused-20261019T194933Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261019T194933Z.md | 109 ++++++++++++++++++++
 ...rata-k7-focused-20261019T231907Z.ai-review.json |   7 ++
 .../strata-k7-focused-20261019T231907Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261019T231907Z.md | 105 +++++++++++++++++++
 ...rata-k7-focused-20261020T040546Z.ai-review.json |   7 ++
 .../strata-k7-focused-20261020T040546Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261020T040546Z.md | 107 ++++++++++++++++++++
 ...rata-k7-focused-20261020T053711Z.ai-review.json |   7 ++
 .../strata-k7-focused-20261020T053711Z.log         |   9 ++
 copilot-eval/strata-k7-focused-20261020T053711Z.md | 107 ++++++++++++++++++++
 ...rata-k7-focused-20261020T144306Z.ai-review.json |   9 ++
 ...trata-k7-focused-20261020T144306Z.identity.json |  18 ++++
 copilot-eval/strata-k7-focused-20261020T144306Z.md | 108 ++++++++++++++++++++
 ...rata-k7-focused-20261020T155124Z.ai-review.json |   9 ++
 ...trata-k7-focused-20261020T155124Z.identity.json |  18 ++++
 copilot-eval/strata-k7-focused-20261020T155124Z.md | 108 ++++++++++++++++++++
 61 files changed, 2842 insertions(+)
```

`git diff --check HEAD~1` after evidence commit: no output, exit 0 (pass).
Application suites were not run: this task only adds retained evidence, ignore
entries and this completion record; it changes no executable code.
Remaining limitations: 40 cited raw reports/snapshots were deliberately excluded
by the requested conservative scan policy, and 15 uncited files remain visible.
Historical verdicts remain unchanged; retaining summaries does not qualify K-7.
No push, PR, provider run or database access occurred. B2 was not started.

### B2 — Secret scan — 2026-10-22 — STOP / failed

Implementation commit: `5dd58dc` on `plan/B2-secret-scan`, branched from
`plan/B1-triage-eval-output`. Added the Bash/Python 3 tracked-file scanner,
commented allowlist format, four temporary-repository regression tests and a short
operations section. No B3 work, history rewrite, push, PR, provider run or database
access occurred.

**STOP findings (values deliberately omitted):**

- `.env.real.example:8:cloud-host`
- `.env.real.example:8:ipv4`
- `strata_utils.md:88:ipv4`

These are configured endpoint/LAN identifiers, not documentation-address
placeholders. Preserved their contents and escalated to the coordinator; no
allowlist entries suppress them. Additional cloud candidates remain at
`databases.example/targets.json.example:76:cloud-host` and
`databases.example/targets.json.example:78:cloud-host`; their authenticity has
not been established. Remaining candidates include code expressions, synthetic
test values, historical paths and emails and are not fully triaged because actual
endpoint findings trigger STOP.

| Rule | Initial candidate hits | After implementation |
| --- | ---: | ---: |
| private-key | 0 | 0 |
| credential-assignment | 513 | 513 |
| oracle-connect | 22 | 22 |
| wallet-path | 0 | 0 |
| cloud-host | 3 | 3 |
| ipv4 | 4 | 4 |
| home-path | 115 | 115 |
| email | 90 | 90 |

Counts above are from the initial scan and the final implementation scan before
this completion record; they are candidate line/rule hits, not confirmed secrets.
Allowlist entries: **none**; only format guidance is present. Coordinator
clarification treats historical local home paths as reviewed exceptions rather
than secrets; that alone cannot resolve the confirmed non-home STOP findings.
The earlier B1 completion record's repository-root home path was made relative
in this record commit as requested; historical source documents remain unchanged.

Verification:

- `bash scripts/secret_scan.sh ; echo exit=$?`: **exit=1**, tree not clean.
- `UV_CACHE_DIR=/tmp/oracledbharness-uv-cache uv run pytest -q tests/test_secret_scan.py`:
  **4 passed**; planted key/IP exit 1, placeholders exit 0, explicit selection,
  untracked rejection, allowlist validation and excluded directories covered.
- `UV_CACHE_DIR=/tmp/oracledbharness-uv-cache uv run ruff format --check .`:
  **308 files already formatted**, exit 0.
- `UV_CACHE_DIR=/tmp/oracledbharness-uv-cache uv run ruff check .`:
  **All checks passed**, exit 0.
- `git diff --check HEAD~1` after implementation commit: no output, exit 0.

B1 reconciliation: its 40 endpoint-bearing raw reports/snapshots remain untracked
and were never opened by the scanner; the 15 other visible scratch files likewise
remain untracked. The scanner enumerates only `git ls-files -z`, and explicitly
excludes tracked entries beneath `OrcaServer/` and `databases/` as a safeguard.
This is a heuristic UTF-8 working-tree scan, not an index/blob or binary scanner.
B2 remains incomplete and failed until the coordinator resolves actual identifiers
and the remaining candidates are reviewed; do not start B3.


### B2 — Secret scan completion — 2026-10-22 — done

This completion supersedes the failed B2 attempt above (`5dd58dc`, `9976070`),
without altering its historical record. Work stayed on `plan/B2-secret-scan`.
Commits: `c6eb173` scrubbed endpoints, the wallet template and live username paths;
`81006bb` tuned the scanner, documented its behavior and added regressions;
`6b5e832` corrected an overly broad replacement to retain the example copilot
URL on loopback. This completion record is a separate documentation commit.

The coordinator authorized replacing current-tree host identifiers with stand-ins,
keeping historical username paths only as per-file, home-path-only exceptions.
The old values remain in public git history: no history rewrite or force operation
was performed. The D4 owner should restrict network access to that endpoint.
No original endpoint values are reproduced here or in commit messages.

| Rule | Before this completion attempt | Final hits |
| --- | ---: | ---: |
| private-key | 0 | 0 |
| credential-assignment | 513 | 0 |
| oracle-connect | 22 | 0 |
| wallet-path | 0 | 0 |
| cloud-host | 3 | 0 |
| ipv4 | 4 | 0 |
| home-path | 114 | 0 |
| email | 90 | 0 |
| **Total candidate line/rule hits** | **746** | **0** |

The baseline differs from the previous attempt's home-path count because its
completion commit had already replaced the earlier B1 record's root path.
These are heuristic candidate hits, not counts of confirmed secrets.

Scrubbed locations (values deliberately omitted):

- `.env.real.example:8:ipv4`
- `.env.real.example:8:cloud-host`
- `databases.example/targets.json.example:63:ipv4`
- `databases.example/targets.json.example:76:cloud-host`
- `databases.example/targets.json.example:78:cloud-host`
- `databases.example/targets.json.example:87:home-path`
- `databases.example/targets.json.example:87:wallet-path`
- `restart_strata.md:24:home-path`
- `restart_strata.md:40:home-path`
- `restart_strata.md:47:home-path`
- `restart_strata.md:94:home-path`
- `restart_strata.md:95:home-path`
- `restart_strata.md:96:home-path`
- `restart_strata.md:97:home-path`
- `scripts/restart_strata.sh:4:home-path`
- `scripts/strata_stats.sh:4:home-path`
- `strata_utils.md:30:home-path`
- `strata_utils.md:31:home-path`
- `strata_utils.md:32:home-path`
- `strata_utils.md:42:home-path`
- `strata_utils.md:88:ipv4`

The copilot base URL at `.env.real.example:12` is verified as loopback. The
walletDir field retains its JSON key and uses a relative target placeholder;
all target JSON remains parseable. Live shell paths use the runtime HOME variable.

Allowlist: **five commented, exact-file, rule-scoped entries**:

- `claude_k7.md:home-path`: retain username paths in dated historical evidence.
- `claude_tasks.md:home-path`: same historical-record rationale.
- `codex_tasks.md:home-path`: same historical-record rationale.
- `tests/copilot/test_local_provider.py:ipv4`: one exact synthetic non-loopback
  URL line verifies provider refusal; no other address in that file is exempt.
- `tests/unit/test_bootstrap_targets.py:wallet-path`: one exact disposable
  temporary wallet fixture line; other wallet paths still fail.

No docs review or copilot-eval report needed an additional home-path exception.
Domain placeholders, secret reference/file keys, empty/template values, source
expressions, token metadata and reviewed synthetic credentials use narrow rule
handling rather than file-wide exclusions. Package-lock integrity/resolved
metadata requires no blanket exemption. Historical exceptions cannot suppress
credentials, keys, cloud hosts or addresses on the same line. Regression tests
pair these exceptions with real-looking credentials/hosts and verify non-allowlisted
home paths, a non-documentation public address and tenancy-specific cloud services
still fail; planted fixture scans return 1 and clean fixtures return 0.

Verification (UV_CACHE_DIR set to the authorized temporary cache):

- `bash scripts/secret_scan.sh ; echo exit=$?`: no findings, **exit=0**.
- `python3 -c "import json;json.load(open('databases.example/targets.json.example'))"`:
  no output, **exit=0**.
- `uv run pytest -q tests/test_secret_scan.py tests/ -k "bootstrap or target or secret_scan"`:
  **102 passed, 3 skipped, 2442 deselected**, exit 0. The skips are the existing
  environment-dependent target/qualification checks, not secret scan failures.
- `uv run ruff format --check .`: **309 files already formatted**, exit 0.
- `uv run ruff check .`: **All checks passed**, exit 0.
- `git diff --check plan/B1-triage-eval-output`: no output, exit 0.

The scanner still enumerates tracked files only and skips excluded stores,
symlinks and non-UTF-8 files; it scans the working tree, not historical blobs or
staged content. Untracked evaluation artifacts were not read, staged or deleted.
No push, PR, provider run, database access or access to excluded source stores
occurred. B2 is complete; B3 was not started.
