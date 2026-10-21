# Sync Point 2O independent review — 2026-10-20

**Changes requested; Q5 Trial 26 is not released.** The exact Trial 25 examples are caught and the two false-positive fixes pass. Additional variations reproduce seven direct and service-level bypasses across the three factual corrections. Recognizing an isolated wording pattern is insufficient when negated scope or an unrelated qualifier exempts the same unsupported claim.

Codex read `agy_plan.md`, both task files, the active Sync Point 2O assignment and Claude's completed Wave 2O handoff (`claude_k7.md`, section beginning at line 3923). No copilot implementation/test, contract, review-script or historical report was edited. Independent probes and outputs are under `/tmp`; no local Strata or live Oracle call, restart, commit or freeze was performed.

## Verification

- `.venv/bin/pytest -q tests/copilot tests/integration/test_contracts.py tests/unit/test_bootstrap_targets.py`: **1608 passed in 95.10 seconds**.
- `.venv/bin/ruff check services tests scripts copilot-eval/apply_trial_reviews.py`: passed.
- `git diff --check`: passed before and after documentation updates.
- `/tmp/probe_wave2o.py`: **27/34 independent direct controls pass**. Raw evidence is decoded from the retained Trial 25 report, including the owner-less invalid lookup record, rather than trusting summaries.
- `PYTHONPATH=services/api:. .venv/bin/pytest -q -p tests.conftest /tmp/test_wave2o_independent.py --tb=short`: **7 failed in 1.64 seconds**, reproducing the seven direct gaps through service delivery. Each test first confirms the unsupported text is in the delivered answer, then fails because the initial claim-check action is `none` instead of `revise`. The offered correct revision is never requested.
- Unchanged `/tmp/probe_wave2k.py`: **40/40**; unchanged `/tmp/probe_wave2l_variations.py`: **19/19**. Earlier transaction-state/DDL/plan/comment controls remain intact. The full suite also retains Wave 2M's denied-commit and affirmative controls.

Scratch evidence: `/tmp/probe_wave2o.out`, `/tmp/wave2o-independent-results.json`, `/tmp/wave2o-service.out`, plus the two probe scripts above. No expected result was weakened. These fixture-provider probes establish service behavior without sampling a model.

## Results across all five items

| Item | Result |
| --- | --- |
| EXPLAIN-06 call rollback | Exact Trial 25 closing claim flags transaction_state; genuine caught-caller, unhandled-call distinction and pre-call-work positives pass. **Incomplete:** denied catching and uncertain rollback assertions still exempt the unqualified claim. |
| INACCESSIBLE-03 rights model | Exact answer flags rights_model; direct-grant and correctly scoped DR compile/IR runtime explanations pass. **Incomplete:** “ignores roles” is missed and owner/compile/enabled-role words globally exempt false IR-runtime denials. |
| DIAG-03 session cursor/counters | Exact last-run label, holder-last-ran lookup promise and per-execution count trigger session_cursor. Reported-ID/cache-miss and genuinely unknown/cumulative positives pass. **Incomplete:** an unrelated cumulative execution count suppresses an unsupported current-execution row count. |
| KINJ-01 cumulative-check hypothesis | **Accepted in tested scope.** Actual Trial 25 withheld draft and assignment's hypothesis produce no band_reachability. Explicit nested-band firing and a denial about earlier rules followed by affirmative rule-42 firing remain flagged. |
| KINJ-02 citation grammar | **Accepted in tested scope.** `(lookup 6, 19 rows)` and row/column-count variations cite only 6; `(lookup 6, 19)` and `lookups 6 and 19` still produce evidence_reference. An explicit later lookup remains recognized after the count. |

## Follow-up 1: affirmative catching and rollback scope

Each of these is appended to the valid original swallowed-handler explanation (`STATE`/`RETAINED`), so the missing finding concerns the extra re-raise claim rather than an omitted original-state description:

1. `Re-raising does not roll back the INSERT, even when no enclosing caller catches the exception.`
2. `Even if the outer PL/SQL caller does not catch the error, re-raising does not roll back the INSERT.`
3. `Re-raising does not roll back the successful first DML statement automatically. Whether an unhandled top-level error rolls back the INSERT is unknown.`

All three yield zero transaction_state findings directly and action none through the service, delivering the bad text. `_CAUGHT_BY_CALLER` exempts a sentence on any catch/enclosing/outer token, without requiring an affirmative catching condition. `_states_call_rollback` accepts a sentence containing unhandled/top-level and an affirmed “rolls back” substring even when the whole proposition is an open question/unknown; that globally returns no findings for the answer. Example 3 states uncertainty, not the required affirmative distinction.

Require genuine catching scope before exempting a denial, and an affirmative assertion about rollback of the failed call's work before using it as the answer-wide distinction. Preserve correct caught-caller/pending-INSERT, unhandled-call rollback, pre-call-work and logging/re-raise controls. Cover these three cases directly and through one bounded service revision; do not add an unconditional ROLLBACK or COMMIT to the proposal.

## Follow-up 2: IR runtime denials and local scope

These three false role claims escape `rights_model` directly and when replacing the Trial 25 INACCESSIBLE-03 stored-code aside:

1. `An AUTHID CURRENT_USER procedure ignores roles at runtime.`
2. `Roles are ignored in an AUTHID CURRENT_USER procedure even when enabled roles exist.`
3. `For AUTHID CURRENT_USER, the owner still compiles the unit, but roles are ignored in the procedure at runtime.`

The surrounding answer retains the actual HR privilege diagnosis and narrow direct SELECT remedy. All three are delivered without revision. Example 1 is missed because `_ROLE_INSUFFICIENT` searches for insufficiency/ignored wording after “role”, whereas the subject precedes “ignores roles”. In examples 2/3, `_ROLE_SCOPED` treats enabled roles, owner or compilation anywhere in the clause as a sufficient exemption. The words do not scope away an explicit IR-runtime denial; example 3 combines true compilation advice with false runtime advice.

Support the ordinary subject-before-verb form and apply exemptions to the actual role assertion and its execution phase. Keep a correctly scoped DR compile-time owner/direct-grant explanation and an IR runtime enabled-role explanation clear. An isolated literal keyword must not exempt the contradictory proposition. Retain no-AUTHID/DR default, owner compilation, denied compilation-shift and direct-grant positives. No broad grants or AUTHID workaround are needed.

## Follow-up 3: cumulative qualification must apply to the same counter

Exact failing direct control:

`The blocked execution has processed 24 rows; the cumulative number of executions is 12 (lookup 3).`

The current-execution row claim is unsupported by retained V$SQL totals. “Cumulative” qualifies the number of executions, not the first assertion. `_session_cursors` finds the one-execution count within a subclause but exempts it whenever `_CUMULATIVE` matches the whole clause. In the service probe, real fixture `dba.sessions` and `tuning.cursor_search` lookups return the current SQL_ID and cursor counters; the same sentence cites lookup 2 and is delivered unchanged with action none.

Scope cumulative qualification to the counter being described. Flag the unsupported first assertion while allowing “24 rows processed across 12 executions, cumulative since loading” and reported-ID/cache-miss/last-run-unknown positives. Add this mixed-counter direct and service regression. Keep last-run history separate from lock-taking history; this follow-up does not authorize stronger conclusions about either.

## Accepted changes and limits

The handoff correctly distinguishes the actual flagged KINJ-01 hypothesis from the different hypothesis quoted in the assignment. Both now pass, and the original affirmative nested-band controls still flag. Denied firing subjects no longer imply positive firing. The citation parser now excludes explicit count-unit suffixes without exempting unqualified nonexistent lookup numbers. Its disclosed limitation—an unlabelled number after an intervening counted phrase is not resumed as a citation—is recorded; no claim of arbitrary prose parsing is made.

The new finding kinds `rights_model` and `session_cursor` are published in FINDING_KINDS, protocol schema and generated TypeScript; the full contract suite checks this integration. Existing Wave 2O tests include bounded revision of the three exact retained Trial 25 failures. The review accepts those mechanisms while requiring the scope follow-ups above.

Claude also supplied expanded local Strata provenance: same wrapper PID, identical logged engine arguments across starts, config/binary/profile hashes and old weight sizes/dates. This improves configuration evidence. The restart trigger/actor remains unknown and 83 GB of weights were not freshly hashed. This review made no provider call and does not represent restart cause or immutable weight continuity as established. Keep this limitation in the next handoff; do not restart or switch the model as part of remediation.

Historical Trial 25 reviewed/pre-review/sidecar SHA-256 values remain the same as the Sync Point 2N audit (84820f2e…91166, 445a8082…908b, 7e495b63…c30a). Saved/narrow rubric 18/18 and independent full acceptance 15/18 remain unchanged. The historical Oracle-check label containing “only session-settable” was deliberately not edited by Claude; the runtime prompt no longer repeats it. Pattern-check labels do not establish exhaustive parameter facts.

## Release decision

**Q5 Trial 26 remains held.** Return the three factual-scope follow-ups and seven retained negative controls for independent verification; the two false-positive fixes require no redesign based on this audit. No further provider diagnostic, unchanged-candidate resampling, candidate freeze or Q6 A/B is released. Preserve all earlier positives, budgets, model, cases and rubrics. K-7 remains NOT QUALIFIED, Kiwi off by default and K8 gated. All task-file historical sections are retained.
