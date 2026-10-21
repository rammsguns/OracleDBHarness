# Wave 1D independent review — Sync Point 1D, 2026-10-19

Reviewer: Codex (AI). Read the task handoffs, the Sync Point 1C review, and
Claude's Wave 1D section in `claude_k7.md`. Inspected `completeness.py`,
`test_completeness.py`, and the corresponding service regressions.

**AGY-1 / AGY-2 accepted in reviewed scope. Wave 2 / AGY-3 Q5 is released.**
The labelled-loop finding from Sync Point 1C is closed. This is implementation
acceptance, not provider-demonstrated qualification.

## Independent probes

Called `handlers()` and `missing_remedies("explain", swallowing_source,
fenced_replacement)` separately from pytest, without a prose remedy.

| Replacement before handler-level `RAISE;` | Result |
| --- | --- |
| Inner loop: `EXIT outer_loop WHEN l_ignore;` | One handler, `propagates=False`, `missing_remedy` |
| Inner loop: `CONTINUE outer_loop WHEN l_ignore;` | One handler, `propagates=False`, `missing_remedy` |
| Handler level: `EXIT outer_loop WHEN l_ignore;` | One handler, `propagates=False`, `missing_remedy`; conditional WHEN is not a handler |
| Handler level: `CONTINUE outer_loop WHEN l_ignore;` | One handler, `propagates=False`, `missing_remedy` |
| Inner loop: unlabelled `EXIT WHEN` / `CONTINUE WHEN` | `propagates=True`, zero findings |
| Labelled inner loop: `EXIT inner_loop WHEN` / `CONTINUE inner_loop WHEN` | `propagates=True`, zero findings |
| Conditional RETURN/GOTO, directly or inside an inner loop | `propagates=False`, `missing_remedy` |
| Dead/conditional-only RAISE | `propagates=False`, `missing_remedy` |
| `IF l_debug THEN NULL; END IF; RAISE;` | `propagates=True`, zero findings |

All **15 independent handler probes** passed. Separately reran all nine multiline
transfer fixtures, including a labelled WHILE loop targeted from a deeper loop;
all passed. The scanner resolves targets against loops opened inside the handler
and conservatively withholds propagation for unknown/outer labels. Its prior-token
check keeps a transfer's WHEN from opening a handler.

Prior Sync Point 1B/1C controls remain green: five independent no-change reason
checks passed; both mixed PL/Scope test functions passed separately, including
collected-spec narrowing. The full suite includes the revised explain answer
retaining `missing_remedy`, bounded revision controls, and labelled-loop service
checks. Independently parsed all three actual source SQL catalog entries: **21
C28 checks** passed (LIBRARY/MLE MODULE and padded lowercase accepted;
VIEW/TABLE/ASSEMBLY refused). Earlier findings 1, 2 and 4 remain closed.

## Verification

- `.venv/bin/pytest -q tests/copilot tests/integration/test_contracts.py`:
  **1134 passed in 93.93 seconds**, with local socket access.
- `git diff --check`: passed.
- No Track A source/tests were edited by Codex. No Strata or live Oracle call.

The first restricted suite attempt stalled early; the successful run above used
approved local socket access. A transient automatic-review capacity failure on
the first access retry cleared on retry and left no verification blocker.

## Release and remaining gates

Claude may now run **one fresh 18-case Q5 diagnostic** on the existing local Strata
`qwen3.8-flash-next-iq3_s` at `http://127.0.0.1:8080/v1`, with unchanged case set,
rubric and budgets. Preserve outputs, model/settings, evidence, claim-check and
revision events; compile proposals on D1 in disposable `K7Q_*` schemas under the
existing cleanup rules. Review all 18 cases afresh and hand off the report and
compile log to Codex. Do not resample an unchanged failed candidate.

Candidate freeze and full Q6 A/B runs remain gated on reviewed Q5 **18/18** and
Codex freeze approval. Q6 still requires two consecutive full **52/52** runs with
fresh reviews and grounding. K-7 remains **NOT QUALIFIED**, Kiwi off by default,
and K8 gated. Earlier task-file hold language is checkpoint history superseded
only to the extent of this explicit Q5 release.
