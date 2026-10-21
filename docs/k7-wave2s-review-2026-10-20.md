# Sync Point 2S independent review — 2026-10-20

Wave 2S closes the three boundary follow-ups from Sync Point 2R in the requested verification scope. **Q5 Trial 27 is released** for one fresh 18-case diagnostic on local Strata, followed by D1 proposal compilation and fresh review. This is a diagnostic release, not Q5 acceptance or a Q6 clean freeze.

Read `agy_plan.md`, the active Sync Point 2S assignment, the prior Wave 2R review, Claude's completed Wave 2S handoff in `claude_k7.md`, and the affected implementation and 35 new controls. The handoff was complete before verification began. Codex made no implementation/test edits, provider or Oracle calls, git index changes, or historical report rewrites.

## Verification

- Full requested copilot/contract/bootstrap suite: **1733 passed in 102.50 seconds**.
- `.venv/bin/ruff check services tests scripts copilot-eval/apply_trial_reviews.py`: passed.
- `git diff --check`: passed.
- `/tmp/probe_wave2r.py`: **34/34**.
- `/tmp/test_wave2r_independent.py`: **5/5 passed in 1.45 seconds**.
- `/tmp/probe_wave2o.py`: **34/34**.
- `/tmp/probe_wave2k.py`: **40/40**.
- `/tmp/probe_wave2l_variations.py`: **19/19**.

The original independent scratch probes were rerun directly, rather than relying on Claude's equivalent checked-in controls. Service probes used scripted providers and the repository fixtures, loaded with `-p tests.conftest`; socket-capable execution was used for TestClient. Direct probes used `PYTHONPATH=.:services/api:services/worker`. Outputs are `/tmp/wave2s-direct.out`, `/tmp/wave2s-service.out`, and `/tmp/wave2s-retained-{o,k,l}.out`. The direct script also writes its normal `/tmp/wave2r-probe-results.json` output; historical repository reports remain unchanged.

## Follow-up results

**Inline token boundaries:** Removing the directive between `SELECT` and `COUNT(*)` retains a separating space. The service-captured bonus proposal contains `RETURN p_salary * 0.1;`, not `RETURNp_salary`. The ordinary business comment remains. The checked-in controls also cover whitespace on either side, multiline block comments, end-of-line removal, and ordinary block comments. The implementation leaves separation when an inline cut would otherwise join code.

**Independent business comments:** The punctuation-free directive is removed while both `-- Calculate annual bonus for active employees` and `-- Returns the annual bonus.` survive, directly and through service delivery. The captured proposal retains the semicolon fix. The exact Trial 26 two-line directive and tested split directives are still removed, with accurate correction prose. Continuation handling now requires lowercase continuation text or a recognized directive action, rather than consuming every following comment. This is acceptance of the tested boundary; it does not establish a universal natural-language comment classifier.

**General Oracle knowledge versus specific database claims:** Each of the three formerly missed lowercase `export_day` bullets now produces `evidence_reference`. Each service trace performs one bounded revision (`action = revise`), settles with no findings, and omits the unsupported assertion from the delivered answer. This includes the semicolon-joined general/specific claim. Identifier matching is case-insensitive, and the general exemption is checked across subclauses. Genuine general application-error-range / `RAISE_APPLICATION_ERROR` knowledge still passes without a database citation. The helper is in `claim_checks.py`, as correctly identified in Claude's handoff, rather than `support.py` named in the assignment.

The retained direct suites also preserve the accepted DIAG-03 inactive-session/lock-origin controls, DIAG-02 explicit JOB_TYPE/JOB_ACTION fallback rendering, and prior Wave 2K/2L/2O checks. No requested control remains failing.

## Release and next gate

Release **Q5 Trial 27** to Claude: one fresh 18-case diagnostic using local-only `qwen3.8-flash-next-iq3_s` at `http://127.0.0.1:8080/v1`. Preserve the fixed cases and stand-in hashes, existing runtime budgets and rubrics; no model switch, restart, case-specific logic, or budget widening. Retain the pre-review report, write reviews only within `cases[*].review` with metadata in the `.ai-review.json` sidecar, compile proposals on D1, run the 66-check Oracle suite, retain cleanup evidence for zero `K7Q_%`/`K7P_%` schemas, and perform a fresh 18-case review before independent audit.

Q5 must pass both rubrics and independent full grounding at 18/18 before a clean candidate freeze and Q6 Full Run A/B can be released. The working tree remains unfrozen; this review does not certify `uncommittedChanges=False`. K-7 remains NOT QUALIFIED, Kiwi off by default, K8 gated. All historical task sections and trial artifacts are preserved.
