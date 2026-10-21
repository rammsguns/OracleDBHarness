# Sync Point 2M independent review — 2026-10-19

**Wave 2M is accepted in the tested scope. Q5 Trial 25 is released to Track A.**
Both negated commit-state answers now produce `transaction_state` directly and trigger bounded revision through `CopilotService`. All requested retained controls pass, including affirmative transaction-state explanations and the previously accepted Wave 2K/2L corrections.

Codex read `agy_plan.md`, the active Sync Point 2M assignment, both task files and Claude's completed Wave 2M handoff in `claude_k7.md` (section beginning at line 3690) before testing. No implementation, copilot test, contract, review-script or historical report files were edited. Codex made no live Strata or Oracle call, commit or freeze.

## Validation

- `.venv/bin/pytest -q tests/copilot tests/integration/test_contracts.py tests/unit/test_bootstrap_targets.py`: **1540 passed in 102.25 seconds**.
- `.venv/bin/ruff check services tests scripts copilot-eval/apply_trial_reviews.py`: passed.
- `git diff --check`: passed, including after documentation updates.
- Unchanged `/tmp/probe_wave2k.py`: **40/40**.
- Unchanged `/tmp/probe_wave2l_variations.py`: **19/19**.
- `PYTHONPATH=services/api:. .venv/bin/pytest -q -p tests.conftest /tmp/test_wave2k_independent.py /tmp/test_wave2l_transaction_boundary.py --tb=short`: **20 passed in 6.18 seconds**, comprising **18/18** original service probes and **2/2** transaction-boundary probes.

An initial scratch-service invocation omitted the repository import path and shared fixture plugin and failed during collection. The corrected invocation above collected and passed all 20 without modifying assertions. Direct outputs are `/tmp/probe_wave2m-base.out` and `/tmp/probe_wave2m-variations.out`.

## Transaction-state verification

Against the retained EXPLAIN-06 source (successful INSERT, failing DELETE, swallowed exception), both exact answers yield `transaction_state`:

1. `Log the failure and re-raise it. The caller will never commit duplicates.`
2. `Log the failure and re-raise it. The caller cannot commit partial work because Oracle rolls back the entire transaction.`

The unchanged independent service probes verify initial action `revise`, the transaction-state finding and delivery of the corrected retained-INSERT/DELETE-rollback answer. The full suite also verifies exactly two provider calls and revised action `none`, plus a one-call/no-revision affirmative partial-commit control.

Inspection confirms `_COMMITTED_STATE` marks commit and partial-state terms in both orders. `_committed` checks local denial before each term instead of treating a bare regex match as an explanation. The shared denial pattern includes `cannot` and `without`; not-only/not-just/not-merely remain affirmative.

The requested positive controls pass: only the failing DELETE rolls back while successful INSERT rows remain in the caller's transaction; earlier rows are not rolled back and stay pending; the caller may commit partial work or leave both copies; and the three not-only/not-just/not-merely constructions. Original omission, concurrency, dismissal, denied-retention, single-DML and propagating-handler controls remain intact.

The retained suites also continue to verify mandatory-DDL denial scope, bind-attribution uncertainty scope, actual comment preservation and deterministic prose correction, source/log injection notices across failed revision paths, near-bound revision settlement, and genuine pre-diagnosis wall exhaustion. No expectations were removed or weakened.

## Limits and release

The guard remains a wording-based completeness check. As Claude discloses, denial recognition uses a local three-word window and does not cover distant or postposed denials. This review accepts the assigned exact controls and retained variations; it does not establish universal semantic validation or a live-model 18/18 result.

Release **one fresh Q5 Trial 25 diagnostic**, run by Claude on local Strata `qwen3.8-flash-next-iq3_s` at `http://127.0.0.1:8080/v1`, with unchanged cases, rubrics and budgets. Retain served identity, raw lookup evidence, claim-check/revision events, untouched `.pre-ai-review.json`, reviewed `.json` and metadata-only `.ai-review.json`. Reviewed output must remain literally byte-identical outside `cases[*].review`.

Use case-set hash `dc9ac9a3b54ecad9e40842a1c2dfda09c496709d8c6b55c1658f2e074426598f` and stand-in hash `e457f49db26e3cee5d76744fe1a85b81249d2c34cbb9b3bf4f9c2ff5b2acba44`. Compile actual delivered proposals on D1 in disposable schemas, run Oracle pattern checks and retain compile/cleanup logs confirming zero remaining `K7Q_%`/`K7P_%` schemas and zero secret exposure. Review all 18 cases afresh, then return the handoff/artifacts for independent audit.

This supersedes the Sync Point 2L hold. It permits no historical report rewrite or unchanged-candidate resampling. Trial 24 remains independently 13/18. Candidate freeze and Q6 Full Runs A/B remain gated on independently accepted Q5 18/18 and clean-freeze approval; untracked helper scripts must be resolved before requiring `uncommittedChanges=False`. Current HEAD is `952920f5723f93f29ee5e73247f16d82a0b4e39f` with an unfrozen dirty working tree. K-7 remains NOT QUALIFIED, Kiwi off by default, K8 gated.
