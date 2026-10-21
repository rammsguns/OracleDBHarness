# Sync Point 2I independent review — 2026-10-19

**Accepted in assigned scope. Q5 Trial 24 is released for one fresh local-Strata diagnostic.** Both Wave 2I follow-ups pass: the 38 retained-evidence claim controls, 20 additional adversarial variations and eight independent service probes. This supersedes the Wave 2H diagnostic hold; it does not qualify K-7 or release freeze/Q6.

Claude's completed handoff is `claude_k7.md`, “Wave 2I: Sync Point 2H follow-ups” (beginning at line 3072). It was present before verification. Codex did not edit copilot implementation or test files, contracts or review scripts. Executable probes and temporary outputs stayed under `/tmp`. No live Strata/Oracle call, report rewrite, commit or freeze was performed.

## Verification

- `.venv/bin/pytest -q tests/copilot tests/integration/test_contracts.py tests/unit/test_bootstrap_targets.py`: **1375 passed in 97.52 seconds**.
- `.venv/bin/ruff check services tests scripts copilot-eval/apply_trial_reviews.py`: passed.
- `git diff --check`: passed before and after review/task documentation changes.
- `/tmp/probe_wave2h.py`: **33/33** retained-evidence claim controls passed; anchor and explanation-remedy assertions also passed.
- `/tmp/probe_wave2h_boundaries.py`: **5/5** boundary claim controls passed. Combined original suite: **38/38**.
- `/tmp/probe_wave2i_variations.py`: **20/20** additional claim controls passed, plus assertion of the restored shared source-operation set.
- `PYTHONPATH=services/api:. .venv/bin/pytest -q -p tests.conftest /tmp/test_wave2h_independent.py`: **8 passed in 2.13 seconds**.

The original scripts were rerun with their existing expectations. They reconstruct `LookupFacts` from Trial 23 raw lookup operations, status, columns, rows, parameters and lookup numbers, then invoke production `_kiwi_review`. Synthetic source-read extensions are explicitly additional adversarial inputs, not claimed historical Trial 23 lookups. Full-suite and independent probes use scripted providers and the stand-in, not a sampled live model.

## Follow-up 1: open band questions

All three genuine unknowns now produce **zero findings** against the retained KINJ-01 evidence:

1. `Whether rule 42's band check can fire is unknown because the intervening dispatcher lines were not read.`
2. `I do not know whether rule 42's band check can fire; the path between the calls was not read.`
3. `Whether rule 42's band check ever fires is unknown.`

All three adversarial negatives still produce `band_reachability`:

1. `Rule 42's only rejection test that can ever fire on a value reaching it is its band check at lines 852 and 855.`
2. `Whether negative data exists decides whether the band tests at lines 852/855 ever fire.`
3. `Rule 42's band check can fire, but the rest of the dispatcher is unknown.`

The mixed negated assertion plus unknown about unread intervening logic still passes. Additional positive variations “I cannot tell whether the band check at lines 852 and 855 can reject anything” and “... can fire remains unknown” give zero findings. A closed unrelated question followed by a firing assertion still flags: `Whether rule 1 runs first is unknown, and rule 42's band check can fire.` A “determines whether ... ever fire” variation also flags.

Inspection confirms `_asserts_firing` distinguishes an open firing question from an affirmative firing expression and a claim that data settles firing. It does not simply exempt an entire sentence containing “unknown.” Arithmetic remains based on the retained nested bands and leaves the unread intervening path unresolved.

## Follow-up 2: job intervention and source-read exemptions

The header-only bypass is closed. With the eight retained DIAG-02 lookups plus the exact one-line ORDER_FEED PACKAGE BODY header, both `The job itself needs no change.` and `What the evidence does establish is that no intervention on the job itself is needed.` produce `job_intervention`.

The additional matrix repeats both negative statements and both positives with each extension:

| Source extension | Two categorical no-change claims | Observed state / genuine unknown |
| --- | --- | --- |
| One-line ORDER_FEED package-body header | Flagged | Zero findings |
| Two unrelated implementation lines | Flagged | Zero findings |
| EXPORT_DAY implementation lines | Flagged | Zero findings |
| Package-subprogram member metadata | Flagged | Zero findings |

The positives are `The job is enabled and not disabled in this result.` and `Whether the job definition or action needs changing is unknown.` Reading source alone does not establish that operational configuration needs no intervention. Inspection confirms `_source_read` and its exemption are removed; recorded job failures retain the guard regardless of a source read. The exact original Trial 23 Remedy still flags.

The handoff also identifies and removes Wave 2H's duplicate `_SOURCE_LOOKUPS` definition. Inspection and an independent assertion confirm the shared set again contains exactly `schema.object_source`, `schema.object_source_range` and `schema.object_source_search`. Package-subprogram metadata is excluded. The full suite passes with that correction; this review does not claim exhaustive behavioral coverage of all access-check combinations.

## Previously accepted behavior remains verified

The original 38 controls still cover DIAG-01 object-type scope, sparse-MOD universal scope, DIAG-02 intervention wording, DIAG-03 lock-origin denial versus causal overclaim, and exact retained first drafts. Anchor assertions still flag the malformed DRAFT-03 owner.column references and accept owner.table.column, table.column and local record anchors. EXPLAIN-06's exact first draft, log-then-re-raise prose and text-fenced RAISE pass, while description-only/no-remedy negatives remain flagged.

The eight independent service controls again verify:

- the exact retained DRAFT-03 proposal triggers one bounded anchor repair and delivers the corrected proposal;
- revision wall-time, provider, empty-response and unexpected-tool failures remove flagged/interim/partial text and deliver the evidence-backed bounded answer;
- refused revision and step bounds also withhold the unsupported answer;
- a successful revision replaces the entire interim-plus-final answer.

Historical Trial 23 reviewed/pre-review/sidecar hashes match the Sync Point 2G audit. No historical score or report has been changed. Its independent acceptance remains 14/18.

These are wording-based checks, not a proof that every paraphrase will be classified correctly. The job guard is deliberately conservative even after source is read. Anchor validation is structural and does not compile a corrected unit; Trial 24's actual proposals still require D1 compilation. The previous review's modern-protocol and bounded-revision scope remains applicable.

## Release and required handoff

**Track A may run one fresh Q5 Trial 24 18-case diagnostic** on local Strata `qwen3.8-flash-next-iq3_s` at `http://127.0.0.1:8080/v1`, with unchanged cases, rubrics and budgets. Retain untouched pre-review output, served model identity, case-set hash `dc9ac9a3b54ecad9e40842a1c2dfda09c496709d8c6b55c1658f2e074426598f`, stand-in hash `e457f49db26e3cee5d76744fe1a85b81249d2c34cbb9b3bf4f9c2ff5b2acba44`, raw lookup evidence, claim checks and revision records. Compile actual delivered proposals on D1 in disposable schemas; retain compile/cleanup and Oracle pattern-check logs, with zero leftover K7Q_/K7P_ schemas. Review all 18 cases afresh against fixed rubrics and raw evidence, then return the reports/logs and handoff for independent audit.

Report modifications must remain literally inside `cases[*].review` values; reviewer metadata belongs in `.ai-review.json`. Do not rewrite historical reports or resample an unchanged candidate.

This releases the diagnostic only. Candidate freeze and Q6 A/B remain gated on independently accepted Q5 18/18 and explicit clean-freeze approval, including resolving untracked helper `.py` files before requiring `uncommittedChanges=False`. K-7 remains NOT QUALIFIED, Kiwi off by default, K8 gated. All historical task sections are preserved.
