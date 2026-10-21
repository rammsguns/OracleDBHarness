# K-7 review and completion assignments

Reviewed 2026-10-16 against the current working tree and
[Claude's handoff](../claude_k7.md). This is a task assignment for the collaborators;
it does not claim that another agent has received or started these tasks.

## Review conclusion

Claude implemented the main prompt rules, opt-in evidence capture, structural proposal
checks, diagnostic subset runs and Oracle pattern-check scripts. These are useful
changes, but steps 1–5 are not fully closed: validation is not visible to the console
or preserved by the runner, and the checker overstates completeness. No fresh model
qualification has occurred. The existing NOT QUALIFIED result remains in force.

The full-suite result of 914 passed / 83 skipped and two Oracle runs of 34/34 are
**reported by Claude**. This review inspected the scripts but did not independently
rerun Oracle or the full suite. The Oracle checks validate prescribed examples, not
actual generated answers. The remediation plan still quotes an earlier 912-pass
snapshot; reconcile the final record when implementation is frozen.

Independent validation in this review: `tests/copilot/test_k7_remediation.py` and
`tests/copilot/test_kiwi_explainer.py` passed **57/57** in 9.31 seconds outside the
sandbox (the initial sandboxed HTTP-client run stalled and was interrupted).
The three completeness counterexamples below were independently reproduced even
though those tests pass. `git diff --check` is clean.

## Findings requiring action

### F1 — High: structural validation reports incomplete units as complete

`services/api/harness_api/copilot/proposal.py:55` uses the worksheet classifier as a
completeness check. Independently reproduced through `check_proposal`:

```text
CREATE OR REPLACE FUNCTION f RETURN NUMBER IS
  -> complete: true

BEGIN NULL;
  -> complete: true

CREATE OR REPLACE PROCEDURE p IS BEGIN NULL; END; SELECT 1 FROM dual;
  -> complete: true
```

The preparer identifies broad statement shape; it does not establish a complete
PL/SQL unit or reject every trailing statement. `compiled: false` is correct but
does not make the `complete: true` assertion accurate. Add conservative boundary
checks or represent unverified completeness explicitly. Do not claim to have built
an Oracle parser, and do not use naive END-counting that rejects valid nested code.
Keep the compile endpoint's existing behavior outside this remediation unless a
separate change is justified.

### F2 — High: validation warnings disappear before users/reviewers see them

- `apps/web/src/views/CopilotDrawer.tsx:64` has no validation field on its proposal
  interface; its event handler ignores `code_check` and its proposal UI shows no
  structural problems. A fragment is offered with the same Apply control as valid code.
- `tests/copilot/eval/runner.py:703` saves proposal text/rationale but drops validation;
  `code_check` findings and multi-part validation are also not retained in that record.
- `service.py:1030` checks revision/ownership on apply but does not inspect structural
  validation. Decide and document the policy: visibly flag incomplete editor-only
  edits at minimum; if rejecting them, enforce the same rule on the server and on all
  parts. Editing a buffer is not database execution and should not acquire a new
  permission flow.

### F3 — Medium: Oracle checks are not a reliable automated pass/fail command

`oracle/qualification/05_k7_codegen_checks.sql:17` continues after SQL errors; verdict
helpers print FAIL without failing the process, and the script ends with plain EXIT.
It also drops pre-existing K7Q_OWNER/K7Q_USER/K7Q_READ at setup. Its header says it
drops everything, but teardown is a separate script.

Return a nonzero result for failed checks, SQL/connect errors, missing sections or
an unexpected check count. Refuse existing object-name collisions or use unique names.
Provide a repeatable wrapper that records the Oracle version, output, check count and
cleanup result without credentials. Correct the header and document cleanup on failure.
Do not run the current destructive setup merely to repeat this review.

### F4 — Medium: evidence and implementation provenance need closure

- `toolbox.py:129` bounds content but can cut off model-visible evidence. Summary
  evidence in `explainer.py:541` retains an input hash rather than the input itself.
  Verify that a reviewer can reconstruct the summary input from retained material;
  otherwise capture bounded permitted inputs or explicitly mark the claim unreviewable.
  A hash alone cannot establish what the model saw.
- Add end-to-end coverage that evidence survives SSE -> report -> review, including
  truncation, refusals, errors, source ranges and summary linkage. Existing string
  assertions and Python arithmetic examples do not prove real-provider answer quality.
- `runner.py:1182` ignores untracked files when recording dirty state. New executable
  modules/tests/scripts are currently untracked. Ensure the qualification snapshot
  includes all implementation files; prevent an untracked source module from yielding
  a misleading clean-commit claim. Historical evaluation artifacts may stay outside
  the tracked implementation.

## Assigned tasks and acceptance criteria

Owners are explicit handoff roles. Claude owns implementation; Codex owns independent
review; the project operator owns model access and the final commit. No running model
should be stopped and no GPU workload displaced as part of these assignments.

| ID | Priority / owner | Task | Acceptance / dependency |
| --- | --- | --- | --- |
| K7-C01 | P1 / Claude | Correct proposal completeness semantics and boundary checks (F1). | Reproductions above no longer report complete; valid nested routines, anonymous blocks, query/view replacements and package parts remain supported. Add behavioral regressions. |
| K7-C02 | P1 / Claude | Surface validation and loose-code findings in the console; preserve them and all parts in reports (F2). | Single/multi-part and no-editor warnings are visible and saved; apply policy is explicit and consistent; UI/API tests verify behavior and stale-source checks still hold. |
| K7-C03 | P1 / Claude | Verify evidence completeness, summary reconstruction and report persistence (F4). | A scripted diagnosis and both explainer actions have reviewable lookup/summary provenance; truncation cannot silently support a claim; default-off and forbidden-data exclusions still hold. |
| K7-C04 | P1 / Claude | Make implementation provenance reliable (F4). | Untracked source changes prevent a clean qualification claim; unrelated historical report artifacts do not contaminate source identity; all new implementation files are listed for the commit. |
| K7-C05 | P2 / Claude | Harden Oracle pattern checks and preserve validation records (F3). | Failures return nonzero; name collisions are safe; version/count/output and cleanup are recorded. Reconcile 912/914 documentation using the final test run. |
| K7-O01 | P1 / Project operator | Supply an available local provider endpoint/model and configured secret reference or key environment variable. | A minimal authenticated request succeeds; exact reported model identity recorded. Recheck Claude's reported 8080-key/8081-GPU obstacle rather than assuming it is unchanged. Never put a key in Markdown or command output. |
| K7-R01 | P1 / Codex reviewer | Review C01–C05 and rerun affected backend/evaluation and console checks. | Findings closed with code/test evidence; distinguish structural validation from Oracle compilation. Depends on C01–C05. |
| K7-O02 | P1 / Project operator | Freeze and commit the complete reviewed implementation. | Recorded commit contains new modules, tests, scripts and docs; tracked source is clean. Depends on R01. Do not blanket-add copilot-eval artifacts. |
| K7-C06 | P1 / Claude | Run focused trials of all 18 previously failed cases using --only; remediate remaining defects. | New answers reviewed; all 18 pass applicable rubrics/global rules. Diagnostic reports remain explicitly nonqualifying. Depends on O01; any fixes return through R01/O02 before full runs. |
| K7-C07 | P1 / Claude | Produce full provider run A. | All 52 cases attempted with the frozen commit, fixed case set, model/configuration, sufficient budget and retained evidence. Depends on O01, O02 and C06. |
| K7-R02 | P1 / Codex DBA reviewer | Review run A, all 39 required cases plus exhaustive Kiwi grounding. | 52/52 overall, 18/18 correctness, 19/19 safety, no unsupported claims; accurately identify AI review. Check actual generated code against Oracle as needed through the separate qualification workflow; pattern checks do not certify model code. |
| K7-C08 | P1 / Claude | Produce full provider run B under identical implementation/model configuration. | Second fresh complete report; no intervening implementation/configuration changes. Depends on successful R02. |
| K7-R03 | P1 / Codex DBA reviewer | Independently review run B and record the final decision. | Fresh 39-case review and grounding check; same 100% target as run A. Any fix restarts the consecutive-run requirement. |
| K7-C09 | P2 / Claude | Publish final result documentation. | KIWI_PLAN, NEXT_PHASE_PLAN, tests/copilot/README, README and remediation checklist link both reports, hashes, reviewer identities and limitations. Depends on R03; no default-on or K-8 claim before actual gates pass. |

## Focused trial list

Use the configured local provider with the runner's documented model/endpoint/key
arguments and this exact selection:

```text
EXPLAIN-06,FIX-01,FIX-03,FIX-04,FIX-05,FIX-07,DRAFT-02,DRAFT-03,TEST-01,TUNE-03,INACCESSIBLE-03,INJECTION-01,INJECTION-03,DIAG-01,DIAG-02,DIAG-03,KINJ-01,KINJ-02
```

Subset trials can run during remediation on a dirty tree if reported as diagnostics;
full qualification must use the frozen clean implementation. Neither historical
verdicts nor focused-trial verdicts carry over to new full-run answers.

## Execution order

Claude: C01–C05 -> Codex: R01 -> operator: O02 -> Claude: C06 -> resolve any new
failures and freeze again -> C07 -> Codex: R02 -> C08 -> Codex: R03 -> Claude: C09.
Operator O01 can proceed alongside implementation review. No real-provider run is
started by this document, and no credential or running-model changes were made.
