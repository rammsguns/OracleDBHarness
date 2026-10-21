# Sync Point 2P independent review — 2026-10-20

**Wave 2P accepted; Q5 Trial 26 released.** The three scope follow-ups from the [Wave 2O review](k7-wave2o-review-2026-10-20.md) pass verification. This releases one fresh 18-case diagnostic, with unchanged model, cases, rubrics and runtime budgets, D1 proposal compilation and fresh review. It does not release a candidate freeze or Q6.

## Verification

The fixes and 39 regression tests were already present when Codex received the implementation request. Codex inspected them and reran verification; the results are also recorded in the Wave 2P addendum in `claude_k7.md`.

- Full copilot/contract/bootstrap suite: **1647 passed in 103.62 seconds** (`.venv/bin/pytest -q tests/copilot tests/integration/test_contracts.py tests/unit/test_bootstrap_targets.py`).
- Retained `/tmp/probe_wave2o.py`: **34/34**, including all seven prior misses.
- Retained `/tmp/probe_wave2k.py`: **40/40**.
- Retained `/tmp/probe_wave2l_variations.py`: **19/19**.
- Ruff over services, tests, scripts and `copilot-eval/apply_trial_reviews.py`: passed.
- `git diff --check`: passed.

The initial sandboxed suite was interrupted when local test sockets stalled. The complete stand-in suite passed with sandbox escalation. No local Strata or live Oracle call was made during this verification.

## Accepted corrections

| Scope | Independent result |
| --- | --- |
| EXPLAIN-06 | All three exact negated-catching/open-rollback claims produce `transaction_state` and one bounded service revision. Catching requires an affirmed verb; uncertainty does not establish the unhandled-call rollback distinction. Caught-caller/pending-INSERT, unhandled-call rollback, pre-call work and logging/re-raise positives pass. |
| INACCESSIBLE-03 | All three exact IR runtime role denials produce `rights_model` and one bounded service revision. Subject-before-verb forms are recognized, and unrelated owner, compilation and enabled-role wording do not exempt the runtime claim. DR default, owner compilation, denied compilation shift and accurate IR enabled-role positives pass. |
| DIAG-03 | The mixed current-execution row claim/cumulative execution-count sentence produces `session_cursor` and one bounded service revision. Cumulative qualification applies to the same subclause. Correct cumulative counters, reported SQL_ID/cache miss and unknown history positives pass. |

The seven service controls in `tests/copilot/test_k7_wave2p.py` verify the initial `revise`, subsequent clean settlement and corrected delivered answer. Previously accepted KINJ-01 cumulative-check hypothesis and KINJ-02 counted-citation controls remain accepted. No finding kind or contract change was needed in Wave 2P.

## Limits and release conditions

These are bounded wording and subclause checks, not a proof of arbitrary prose semantics. The handoff discloses the catching-denial word window and neighbouring-sentence rights-model limits. The Strata restart actor/trigger and immutable weight continuity remain unverified; this Python verification establishes neither.

Track A may run **Q5 Trial 26** on local Strata `qwen3.8-flash-next-iq3_s`, retain untouched pre-review bytes and raw evidence, compile delivered proposals on D1 in disposable schemas, retain cleanup/66-check logs and freshly review all 18 cases. Reviewer metadata belongs in `.ai-review.json`; changes to the report must remain strictly within `cases[*].review` value spans. Return the completed handoff for Sync Point 2Q independent audit. No unchanged-candidate resampling is released.

This decision supersedes the historical Sync Point 2O hold. Historical Trial 25 verdicts and artifacts remain unchanged. Q5 must achieve independently accepted 18/18 before a clean candidate freeze and Q6 A/B. K-7 remains NOT QUALIFIED, Kiwi off by default and K8 gated.
