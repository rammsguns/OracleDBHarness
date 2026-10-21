# Sync Point 2L independent review — 2026-10-19

**One follow-up remains; Q5 Trial 25 is not released.** All original direct controls and all 18 original service probes pass. DDL negation, plan-attribution scope and comment-preservation corrections also pass additional variations. The transaction-state correction still accepts negated commit/partial-work claims as satisfying the state obligation. Two additional service probes confirm those answers are delivered without revision.

Codex read `agy_plan.md`, the active Sync Point 2L assignment and Claude's completed Wave 2L handoff in `claude_k7.md` (section beginning at line 3567) before verification. No copilot implementation/test, contract or review-script files were edited. New probes/outputs were under `/tmp`. No live Strata or Oracle call, report rewrite, commit or freeze was performed.

## Validation

- `.venv/bin/pytest -q tests/copilot tests/integration/test_contracts.py tests/unit/test_bootstrap_targets.py`: **1512 passed in 104.25 seconds**.
- `.venv/bin/ruff check services tests scripts copilot-eval/apply_trial_reviews.py`: passed.
- `git diff --check`: passed before and after documentation updates.
- Unchanged `/tmp/probe_wave2k.py`: **all 39 prior direct controls pass**. The script now prints **40/40**: the newly detected edit-description case also executes its existing conditional assertion that correction preserves the proposal. The count change is an additional passing assertion, not a removed or weakened expectation.
- `PYTHONPATH=services/api:. .venv/bin/pytest -q -p tests.conftest /tmp/test_wave2k_independent.py --tb=short`: **18 passed in 5.49 seconds**.
- `/tmp/probe_wave2l_variations.py`: **17/19 passed**; both failures are negated commit-state statements.
- `PYTHONPATH=services/api:. .venv/bin/pytest -q -p tests.conftest /tmp/test_wave2l_transaction_boundary.py --tb=short`: **2 failed in 0.78 seconds**, confirming the same gap in production service delivery.

The original controls still use Trial 24 raw lookup facts and supplied source/plan attachments. Additional variations exercise the same general checks. Partial-removal comment positives use benign comments and compare actual source/proposal text. No model was sampled. Historical Trial 24 reviewed/pre-review/sidecar hashes remain identical to the Sync Point 2J audit; saved 16/18 and independent 13/18 are unchanged.

## Results by follow-up

| Follow-up | Independent result |
| --- | --- |
| Transaction-state negation/dismissal | Exact “statement-level rollback is irrelevant” and “inserted rows do not remain” controls now yield transaction_state. Correct retained-INSERT/DELETE-rollback explanations pass, including “are not rolled back.” Single-DML/propagating sources remain unaffected. **Incomplete:** commit/partial-work matches are still accepted despite explicit negation. |
| Mandatory-DDL “not only” | Accepted in tested scope. The exact not-only assertion, not-just and not-merely variants produce required_change. Real does-not-require/neither/needs-no denials pass. The retained mandatory-DDL negative still flags. |
| Bind attribution with unrelated uncertainty | Accepted in tested scope. The exact although/performance unknown, semicolon/index unknown and but/optimal-plan unknown variants flag plan_expression. Genuine conversion uncertainty and hedged conversion/check wording pass. Explicit bind-wrapping plan control remains clear. |
| Comment preservation after acknowledged removal | Accepted in tested scope. The exact zero-comments-remaining claim is corrected directly and in service delivery; proposal text remains intact. A genuine partial removal with preserved remaining comments passes, while an unlimited all-comments-preserved claim still corrects. All-comments-retained and simple acknowledgment positives pass. |

## Remaining failure: negated commit state closes the obligation

Use the exact supplied EXPLAIN-06 source: successful INSERT followed by DELETE, with `WHEN OTHERS THEN NULL`, no transaction control. Each answer below returns **zero findings** from `missing_remedies`:

1. `Log the failure and re-raise it. The caller will never commit duplicates.`
2. `Log the failure and re-raise it. The caller cannot commit partial work because Oracle rolls back the entire transaction.`

The first denies the risk without explaining what remains in the caller's transaction. The second explicitly gives the wrong transaction state: a swallowed failure of the later statement does not automatically roll back the successful earlier INSERT. Neither supplies the affirmative partial-success explanation the source requires.

The service probes offer each bad answer as the first model reply and a correct retained-INSERT/statement-level-rollback answer as the next reply. In both, the first claim-check action is **none**, not revise. The correct reply is therefore never requested; the transaction-state obligation has been incorrectly treated as met. This is a delivery-level reproduction, not just an internal helper concern.

Cause: `completeness._states_transaction` now applies `_affirmed` to `_STATEMENT_ROLLBACK` and `_RETAINED`, but still accepts `_COMMITTED_STATE.search(sentence)` directly. Commit plus duplicate/partial wording remains a blanket positive regardless of negation. The Wave 2L handoff explicitly discloses this remaining arm, calling it outside the assigned scope. This audit treats it as an adversarial variation of the assigned denied-transaction-state follow-up: the acceptance condition asks for variations across that check, and the same negation defect still bypasses it through its commit branch. This is not a request for an unrestricted Oracle semantics engine.

Required action: make commit/partial-state matches distinguish affirmative statements from local denial or dismissal, just as the other state arms now do. Add these two exact negative controls, directly and through the service. Preserve these positive meanings:

- only the failing DELETE rolls back; successful INSERT rows remain in the caller's transaction;
- earlier rows are not rolled back and stay pending;
- the caller may commit partial work or leave both copies;
- “not only”/“not just”/“not merely” affirmative constructions must not be mistaken for denial.

Retain the original omission, concurrency, single-DML and propagating-handler controls. Do not remove transaction completeness or accept a keyword hit as proof that the state was explained.

## Accepted integration and scope

The 18 original service probes all pass without changing their assertions. They include exact retained EXPLAIN-06 and TUNE-03 bounded revisions; source/log injection notices across wall-time, provider, empty, tool and refused-revision paths; near-request-bound settlement; clean fallback without an invented notice; genuine pre-diagnosis wall exhaustion retaining partial/wall_time; and exact retained INJECTION-01 correction plus the formerly failing remaining-comment service case.

Inspection confirms that DDL negation excludes not-only/not-just/not-merely from the denial pattern; plan hedging is checked within the attribution subclause; and comment-removal acknowledgment no longer globally exempts false preservation claims. Additional controls confirm partial removal can preserve genuinely remaining comments without forcing a correction. These three corrections need no redesign based on this review.

The checks remain wording-based, and deterministic correction can remove a whole sentence containing both true and false details. Accepted scope is the assigned controls and tested variations; no claim of universal factual validation or live-model success is made.

## Release decision

**Q5 Trial 25 remains held pending the one transaction-state follow-up.** The original 39/39 and 18/18 targets pass, but the requested additional adversarial verification does not. Releasing the diagnostic as fully verified would conceal a reproduced gap in the same correction.

Return the narrow remaining fix and controls for independent verification. No new provider diagnostic, unchanged-candidate resampling, candidate freeze or Q6 A/B is authorized by this review. The next eventual diagnostic must still use unchanged local model/cases/rubrics/budgets, retain untouched pre-review output and served identity/fingerprints/evidence/revisions, compile actual proposals on D1 with cleanup, and freshly review all 18 cases. K-7 remains NOT QUALIFIED, Kiwi off by default, K8 gated. All historical task sections are preserved.
