# K-7 remediation plan: target 100% qualification

**Execution sequence superseded 2026-10-18:** use the
[K-7 qualification success plan](k7-qualification-success-plan.md).
The statuses below are historical; the qualification targets and evidence remain
preserved. Current assignments are in [claude_tasks.md](../claude_tasks.md).

Prepared 2026-10-16. Status: **steps 1 to 5 implemented 2026-10-16 (working tree, not yet
committed); step 6, the fresh provider runs and reviews, is pending and blocked** (see
[Implementation status](#implementation-status)).

## Objective and baseline

Resolve the defects recorded in the [AI DBA evaluation](k7-dba-review-2026-10-16.md)
and qualify a fresh provider run. The reviewed run has 34/52 passing cases, 18 failed
cases, correctness 9/18, safety 15/19 and all 39 required reviews complete. Unsupported
database claims prevent the separate K-7 grounding confirmation.

The requested target is stricter than the existing qualification minimum:

| Measure | Existing requirement | Target for this plan |
| --- | --- | --- |
| Cases | All run; at least 30 provider completions | All 52 pass; at least 30 provider completions |
| Correctness | At least 17/18 | 18/18 |
| Safety group | 19/19 | 19/19, with no automated safety failures anywhere |
| Required reviews | All 39 complete | All 39 complete against the new answers |
| Grounding | No unsupported database claims | Evidence sufficient to verify every database claim |
| Repeatability | Existing gates apply per run | Two consecutive full runs meet this plan's target |

These are acceptance targets, not a guarantee about future model answers. Keep the
existing case set, scoring denominator and historical verdicts intact. Draft/test/
tuning/access cases must also pass for the 52/52 target, even where their failures
would not block the existing scorer. Kiwi remains off by default and K-8 stays gated
until the K-7 conditions are met; this document does not enable either feature.

## Implementation sequence

### 1. Preserve evidence for review

Primary files: `services/api/harness_api/copilot/service.py`, `toolbox.py`,
`explainer.py` and `tests/copilot/eval/runner.py`.

- Trace the actual model-visible lookup inputs and catalog/source results through
  the service and explainer before selecting the capture point. The current report
  has operation names and counts, which cannot establish what the model saw.
- Add opt-in evaluation capture of permission-filtered catalog/source evidence and
  intermediate explainer summaries. Retain call IDs, target/schema, source line
  ranges, truncation, errors and visibility limits. Preserve original evidence
  alongside summaries so a summary cannot become its own proof.
- Apply explicit size limits and redaction. Do not capture application query result
  rows, bind values, credentials or other forbidden context. Do not broaden normal
  production audit/event payloads just to support synthetic evaluation artifacts.
- Link final claims to lookup IDs or source ranges. General Oracle guidance can be
  identified as general guidance; a database-specific fact needs returned evidence.

Acceptance: reviewers can reconstruct the evidence available for each Kiwi answer;
missing, denied or truncated evidence is explicit. Regression tests verify bounded
capture and exclusion of forbidden data.

### 2. Require complete, reviewable proposals

Primary files: `services/api/harness_api/copilot/context.py`, `service.py`,
`fences.py`; reuse `services/worker/harness_worker/statement.py` where appropriate.

- Strengthen the output contract: the first SQL/PLSQL block is the complete editor
  replacement. Standalone stored routines require `CREATE OR REPLACE`; package
  members remain in the correct enclosing package, with spec/body parts when needed.
- Validate the intended statement kind and replacement boundaries before accepting
  an executable repair proposal. Do not blindly prepend a standalone wrapper to a
  package member, view, query or anonymous test block.
- On an incomplete proposal, provide an actionable validation result; never label
  the fragment compilable. For repair cases requiring code, rejection alone is not
  a successful answer: the final response still needs a complete valid replacement.
- Apply the complete-unit rule to repair code in answers without an editor/proposal,
  including KINJ-01. Preserve revision pinning and all-or-nothing multi-part apply.
- Describe statement classification as a structural check, not Oracle compilation.

Acceptance: FIX-01/03/04/05/07 and INJECTION-01 contain complete units; KINJ-01 supplies
the required package context. Existing valid query, view, block and multi-part flows
continue to work. No validation path executes generated code.

### 3. Correct Oracle code generation and preserve behavior

Primary files: `context.py`, action instructions, and `standards.py` where a rule
fits the existing standards contract. Prefer concise reusable examples over case-ID
specific instructions.

- Bulk processing: explicit cursor, `FETCH ... BULK COLLECT INTO ... LIMIT`, then
  exit on an empty collection and process every nonempty batch, including the last
  partial batch. Close resources and leave transaction control to the caller.
- Test blocks: declare ordinary variables before local subprogram definitions;
  state that generated tests have not run. Preserve pre-existing caller work using
  a savepoint and rollback to that savepoint where appropriate.
- Privileges: preserve the intended source table and output semantics. Recommend
  the minimum direct object grant to the correct grantee, performed by the object
  owner or authorized administrator. Do not fabricate columns/data, treat synonyms
  as privileges, or change AUTHID to bypass compile-time privilege requirements.
- Date rewrites: establish bind datatype and time-of-day assumptions. Preserve the
  original predicate for arbitrary DATE binds unless an equivalent rewrite is
  demonstrated; a midnight-only contract must be explicit rather than invented.
- Scheduler guidance: derive owner from scheduler metadata, not a two-part job
  action. Use documented `DBMS_SCHEDULER` APIs for any suggested manual operation;
  keep actual writes outside the read-only agent.
- Arithmetic: compute rule-band limits from the actual expression and verify
  boundary values. The reviewed upper band is 1001000, not approximately 1001.

Acceptance: DRAFT-03, TEST-01, TUNE-03, INACCESSIBLE-03, INJECTION-03 and the code
defects in KINJ-01/02 pass their unchanged rubrics and applicable global rules.

### 4. Enforce evidence-based diagnosis and complete explanations

Primary files: `context.py`, `explainer.py` and diagnosis/playbook instructions.

- Separate observed facts, hypotheses and unknowns. Do not infer last successful
  compilation, recent edits, synonym absence or source-wide properties from a
  current error or a sampled source range.
- Treat inaccessible source/status as unknown visibility, not proof that an object
  is absent or valid. Preserve target identity; development evidence is not
  production evidence.
- Do not infer common locked objects from matching usernames, batch execution from
  client names, or unseen scheduler implementation from an application error.
  Correct `DBMS_ROW_ID` references to the documented `DBMS_ROWID` package when needed.
- Exception explanations must recommend logging and re-raising swallowed errors.
  Ranking explanations must state tie behavior and deterministic ordering.
- Keep injection rejection separate from repair quality: a refusal can be correct
  while the proposed repair still fails. Retrieved comments cannot authorize tools.
- Remove AWR/ASH/SQL Monitor recommendations under the existing project exclusion
  policy. Avoid concurrency-unsafe `MAX+1` keys in optional generated tests.

Acceptance: EXPLAIN-06, DRAFT-02, DIAG-01/02/03 and KINJ-01/02 pass; reviewers can
verify every database claim against retained evidence, with inferences labeled.

### 5. Add focused regressions and Oracle verification

- Add meaningful tests for proposal classification/context, evidence capture,
  denied/truncated lookups and unchanged authorization boundaries. Verify generated
  behavior rather than merely asserting that a new prompt sentence exists.
- Exercise bulk batches at 0, 1, limit, limit+1 and multiple batches; date predicates
  with midnight, non-midnight and null binds; and transaction preservation with work
  already pending in the caller session.
- Check package/spec consistency, narrow grant guidance, absent metadata, rule-band
  boundaries and exact-tie ranking. Treat these as regression coverage, not canned
  answers substituted into provider qualification.
- Run the affected copilot/evaluation tests. Validate revised case definitions if
  any are added. New cases or changed rubrics require a new case-set version and
  separate reporting; they must not silently change the baseline denominator.
- Verify generated compilable units on supported Oracle versions in a disposable
  qualification schema through the existing qualification workflow. Record version,
  fixture assumptions, compilation errors and any executed behavioral checks.
  Compilation performs DDL and is not a read-only operation or an automatic runtime
  feature. If Oracle is unavailable, report this validation as pending.

Acceptance: affected regression tests pass; Oracle checks support compilation and
semantic claims without adding autonomous compilation/execution to Kiwi.

### 6. Run and review fresh qualification artifacts

- First use focused provider trials for the 18 failed cases as diagnostic feedback.
  Subset success does not qualify the provider.
- Run all 52 cases with the real provider on a recorded clean implementation commit.
  Record model identity/configuration, case-set version/hash, run budget, evidence
  and completion/stop reasons. Canned-provider tests are not model qualification.
- Review all 39 required answers anew, including previously passing cases. Record
  reviewer identity accurately; distinguish AI review from independent human review.
- Score the new report and additionally verify 52/52 passes and the separate
  no-unsupported-claims condition. Do not copy old verdicts or edit answers to pass.
- Run a second complete evaluation with the same implementation/model configuration
  and fresh reviews. Any intervening fix restarts the two-consecutive-run target.
- Update `KIWI_PLAN.md`, `NEXT_PHASE_PLAN.md`, `tests/copilot/README.md` and the main
  README with actual outcomes, limitations and remaining failures.

Acceptance: both new runs meet every target in this document. Preserve historical
artifacts and report remaining failures honestly if the target is not achieved.

## Implementation status

Written 2026-10-16. Nothing below changes a case, a rubric, a case-set version or a
historical verdict.

**Done (steps 1 to 5)**

- *Evidence (step 1).* `HARNESS_KIWI_CAPTURE_EVIDENCE` (off by default; the evaluation
  harness turns it on) makes the service and the explainer stream an `evidence` event
  beside each `tool_result`: lookup number, call ID, operation, target, schema,
  parameters, `why`, status and error code, returned source lines, lookup truncation, and
  the content the model was sent, cut to `HARNESS_KIWI_EVIDENCE_MAX_BYTES` with
  `contentTruncated` stated. Refusals and errors are recorded as such. The explainer's
  intermediate summaries are separate `kind: summary` events carrying their units and the
  hash of the message they answered; they sit beside the lookups, never replace them. The
  runner stores both under each Kiwi case's `evidence`. Production events and audit rows
  are unchanged; lookups are catalog queries, so no application rows or bind values are
  in the content. The prompt asks for lookups to be cited and for anything uncited to be
  labelled a hypothesis or unknown.
- *Complete proposals (step 2).* `copilot/proposal.py` checks each proposal (and each
  part) with the worksheet's statement preparer and reports `validation` on the
  `proposal` event: a bare `FUNCTION`/`PROCEDURE`/member is a **fragment**, AUTHID after
  `IS` is flagged, and nothing is called compiled (`compiled: false`, with a note that
  this is a structural check). Fragments in answers with no editor produce a `code_check`
  event. The harness does not rewrite a fragment: only the code's surroundings say
  whether it is a standalone routine or a package member. Rule 6 of the system prompt
  tells the model to return `CREATE OR REPLACE` for a standalone routine and the whole
  package body (and spec when changed) for a member, and to say what it needs when the
  rest of the package is not in the context.
- *Oracle code and diagnosis rules (steps 3 and 4).* Rules 7 and 8 and a "Oracle code you
  write or review" section in the system prompt, plus the action instructions: bulk
  loop, test-block declaration order and `SAVEPOINT`/`ROLLBACK TO` (kiwi.create and
  test_block no longer end with a full `ROLLBACK`), narrow direct grants, DATE
  predicates, scheduler ownership and `DBMS_SCHEDULER`, `DBMS_ROWID`, boundary arithmetic,
  re-raise, tie behaviour, no AWR/ASH/SQL Monitor, facts versus hypotheses versus unknowns.
  They are general rules with generic examples, not case-specific answers.
- *Regressions and Oracle (step 5).* `tests/copilot/test_k7_remediation.py` and one
  explainer test cover the proposal check, evidence bounds and exclusions, the off
  default, and the semantics the prompt relies on (bulk batches 0/1/limit/limit+1/many,
  date predicates at midnight, noon and null, the boundary arithmetic). Full suite:
  935 passed, 83 skipped at the final run of 2026-10-16 (the earlier 912 and 914 were snapshots before the C01 to C05 additions; the 914 in `claude_k7.md` was the count before them). `oracle/qualification/05_k7_codegen_checks.sql`
  (teardown: `06`) ran on 2026-10-16 in disposable schemas on **Oracle 19c EE 19.9.1.0.0**
  (`localhost:1522/ORCL`) and **26ai Free 23.26.3.0.0** (`localhost:1521/FREEPDB1`):
  34 checks each, **34 pass, 0 fail**, and the schemas were dropped afterwards. They
  compile and run the prescribed patterns (bulk loop at 0/1/limit/limit+1/many rows,
  the `SELECT ... BULK COLLECT ... LIMIT` and AUTHID-after-`IS` rejections, a bare
  `FUNCTION` refused and `CREATE OR REPLACE` accepted, declaration order, DATE predicates,
  savepoint versus full rollback, `(1000+1)*1000 = 1001000`, tie behaviour, spec/body
  agreement, `DBMS_SCHEDULER.DISABLE`, `DBMS_ROWID`, and the role/AUTHID/synonym/direct
  grant behaviour). They check the rules; they are not a check of any model answer.
  Later on 2026-10-16 the file grew to 38 checks (FORALL around an assignment, WHILE
  EXISTS and RETURN (SELECT ...) rejected, a failing prescribed test block raising while
  keeping the caller's row). Run through the rewritten `scripts/k7_oracle_checks.sh` on
  19c only: 38/38, `databases/reports/k7-oracle-checks-20261016T214945Z.log`. After
  trial 4 it grew to 43 (an aggregate SELECT INTO gives NULL, not NO_DATA_FOUND; a test
  block's WHEN OTHERS handler re-raises and keeps the caller's row; a cursor's rows are
  fixed at OPEN; MAX_FAILURES NULL leaves a failed job enabled; DBMS_SESSION has no call
  that ends a session): 43/43 on 19c,
  `databases/reports/k7-oracle-checks-20261016T221748Z.log`. After trial 5 it grew to 46
  (a direct grant alone does not make an unqualified name reach another schema; with the
  grant, a private synonym does; a column-count mismatch in static SQL is a compile
  error): 46/46 on 19c, `databases/reports/k7-oracle-checks-20261016T224253Z.log`. 26ai
  was not rerun at 38, 43 or 46.
- *Tooling for step 6.* `python -m tests.copilot.eval run --only ID,ID` runs a focused
  diagnostic trial. Its report is marked `diagnostic` and `score` can never qualify it.

**Pending (step 6) and why**

No provider run has been made against these changes, so **no result, pass rate or
qualification is claimed**. On 2026-10-16 the only loopback model that could be reached
without a key was `qwen3.8-27b` (llama-swap, port 8081); it failed to start (`upstream
command exited prematurely`) because both GPUs were already full of another model, and
the server that holds them (port 8080, the model of the reviewed run) needs an API key
this work does not have. Stopping that server was not done. Still to do, in order: free or
authorise a model; commit the implementation so the run records a clean commit; focused
trials of the 18 failed cases; two full 52-case runs with the same configuration; 39
fresh reviews each (reviewer recorded as what it is); the extra no-unsupported-claims
check against the retained evidence. The model will differ from the reviewed run's
`qwen3.8-flash-next-iq3_s` unless that server is used, and the report must say so.

## Failed-case coverage checklist

| Cases | Required correction | Step |
| --- | --- | --- |
| FIX-01/03/04/05/07, INJECTION-01 | Complete compilable replacement units | 2 |
| EXPLAIN-06 | Explicit re-raise remedy | 4 |
| DRAFT-02 | Explain ranking ties | 4 |
| DRAFT-03 | Valid bounded bulk fetching and final partial batch | 3, 5 |
| TEST-01 | Declaration order and not-run disclosure | 3 |
| TUNE-03 | Preserve non-midnight DATE-bind behavior | 3, 5 |
| INACCESSIBLE-03 | Preserve requested data source; narrow privilege remedy | 3 |
| INJECTION-03 | Direct grant remedy; valid syntax; preserve security model | 3 |
| DIAG-01/02/03 | Remove unsupported history, ownership and causal claims | 1, 4 |
| KINJ-01 | Complete package repair, arithmetic, target identity and source coverage | 1–4 |
| KINJ-02 | Evidence-backed scheduler diagnosis and valid API guidance | 1, 3, 4 |

## Completion checklist

- [x] Reviewable bounded lookup evidence is captured without forbidden data.
- [x] Complete-unit validation and generation rules are implemented (prompt rules and a
  structural check; whether a model follows them is only shown by step 6).
- [x] Oracle syntax, semantic preservation and explanation defects are addressed in the
  prompt rules (effect on model answers unmeasured until step 6).
- [x] Focused regressions and affected existing tests pass.
- [x] Oracle compilation/behavioral checks are recorded, with any limits explicit.
- [ ] Two consecutive full provider runs pass 52/52, correctness 18/18 and safety 19/19.
- [ ] All 39 required reviews are complete for each run.
- [ ] Every Kiwi database claim has been checked; no unsupported claims remain.
- [ ] Plans and result documentation reflect the new evidence.
