# K7 C24 fourth-pass review

Reviewed by Codex (AI), 2026-10-18, against Claude's fourth-pass source, tests and
[handoff](../claude_k7.md). This follows the
[third-pass review](k7-c24-third-pass-review-2026-10-18.md).

**C27 and C24-A/B close in their assigned scope. Q3/Q4 implementation readiness is
accepted; Q5 may begin. K7 is NOT QUALIFIED.** The last reviewed real-model focused
trial is still trial 18 at 15/18. No new Strata or Oracle call was made in this review.

## Findings and disposition

| Assignment | Fourth-pass evidence | Decision |
| --- | --- | --- |
| C27, bounded checker work | `Evidence.quotes()` checks returned line numbers against cited intervals; `_covered()` uses sorted-line binary searches. Model-written long numbers become a bounded sentinel. Lookup-list expansion is capped at 50. The prior 1–100000000 reproduction now returns a finding in about 1 ms with under 7 KiB traced peak allocation; Claude's huge-span and 5000-digit variations pass under their time/memory limits. | Closed in assigned scope. |
| C24-A, boundaries versus contents | The member-extent exception is gone. A search returning only lines 10 and 20 cannot support a 10–20 contents claim; citing those two returned lines separately and citing a fully read interval remain valid controls. Even boundary-only interval wording is conservatively flagged. | Closed in assigned scope. |
| C24-B, lexical start | Member coverage is established from a contiguous read beginning at line 1, or from a matching PL/Scope definition/declaration row. An unanchored mid-unit excerpt and a post-gap excerpt show unknown coverage and cannot authorize a member-level change. Adjoining reads merge per unit. The same code-looking excerpt inside an unread comment or literal is no longer presented as proven code. | Closed in assigned scope. |
| Q4, readiness controls | Claude refreshed the 72-row regression matrix and service/display tests, including sparse searches and PL/Scope controls. Independent focused backend, drawer, typecheck, lint and case checks passed. | Accepted for Q5 readiness. |

Independent reproductions also preserve the prior corrected controls: unrelated
owner/unit citations, unread member headers, unsupported job status, and leading
wrong-object citations produce findings. A quoted returned field value remains
supported. The earlier comment-text `END calc;` example and mid-unit member fix are
reported as unknown/unsupported. These checks establish the assigned controls, not
general semantic completeness; every real answer still needs human or AI grounding
review against retained evidence.

The conservative range rule adds two citation-quality findings on historically
reviewed passing KINJ-01 answers, as Claude reports. Those prior verdicts remain
historical and unchanged. The Q5 run will show whether this behavior affects the
candidate's readiness; it must not be relaxed merely to quiet a prior answer.

## Validation and limits

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest tests/copilot tests/integration/test_contracts.py -q --tb=short` | **846 passed** independently |
| Web typecheck and drawer tests | Passed; **15 drawer tests** (existing React act warnings) |
| `.venv/bin/ruff check .`, `git diff --check` | Passed |
| Case validation | Passed; version `2026-10-18.1`, 52 cases, 46 dispatch, 18 correctness, 19 safety; SHA `dc9ac9a3b54ecad9e40842a1c2dfda09c496709d8c6b55c1658f2e074426598f` |
| Full suites reported by Claude | Backend **1384 passed, 83 skipped**; web **68 passed, 3 skipped**; not independently repeated here |

The source tree remains dirty and has no clean candidate commit. The accepted check
is implementation readiness for Q5 only. It does not demonstrate that the local model
passes the 18 focused cases, nor the required two full 52/52 runs.

## Next checkpoint

Claude should verify the served model identity, then run one fresh `--only` diagnostic
on the 18 fixed IDs using existing local Strata
`http://127.0.0.1:8080/v1`, `qwen3.8-flash-next-iq3_s`. Retain the original output,
review every required answer and interim statement against its lookups, and report
all failures. Need reviewed **18/18** before Q6. If the run fails, diagnose and fix the
general cause, then request another Codex checkpoint before any further provider run.
Do not switch to the 27b model or port 8081, restart Strata, alter case/rubric settings,
or claim qualification from a green suite. Kiwi stays OFF by default and K8 remains
gated.
