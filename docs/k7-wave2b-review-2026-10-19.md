# Wave 2B independent review — Sync Point 2B, 2026-10-19

Reviewer: Codex (AI). Read the completed Wave 2B handoff in `claude_k7.md`, both
active task assignments, and the Trial 21 review. Independently inspected the five
corrections and ran the requested checks and additional probes against the completed
checkout. **Changes requested; Q5 Trial 22 is not released.** The earlier readiness
finding is superseded: Claude's handoff now exists and the implementation has changed.

The requested suite passes **1198 tests**, Ruff and diff checks pass. Exact Trial 21
negative claim probes now trigger findings, and the next-check and reviewer-metadata
corrections pass. However, bounded synthesis still loses an answer on two edges, and
the new scope/lock checks suppress real overclaims when an unrelated qualification
occurs in the same sentence. These are reproducible gaps in three assigned corrections.
No provider, Strata or live Oracle call was made; API probes use the stand-in.
No Track A implementation/tests or retained evaluation reports were edited.

## Requested verification

- `.venv/bin/pytest -q tests/copilot tests/integration/test_contracts.py tests/unit/test_bootstrap_targets.py`:
  **1198 passed in 84.46 seconds**, with approved local socket access.
- `.venv/bin/ruff check services tests scripts`: passed.
- `git diff --check`: passed; rerun after documentation updates passed.
- Additional API probes in `/tmp/test_sync2b_extra.py`: **two failing assertions**
  demonstrating incomplete final answers, and **one passing** exact 12-lookup control.
  These are independent probes, separate from the 1198 repository tests.
- Pure evidence/claim/next-check probes: `/tmp/sync2b_probes.py`. Reconstructed
  LookupFacts from Trial 21's raw evidence (columns, rows, operation, parameters and
  lookup number), not copied reviewer verdicts. Scope checks use
  `read_scope_claims(answer, evidence_map(facts))`; lock checks use `check_claims`.

## 1. DIAG-02 bounded synthesis — changes requested

Accepted improvements: LocalConversation omits tools/tool_choice on answer-only
turns and sends the answer-now instruction as its own user message. The service
reserves revision headroom within eight steps. The new service controls exercise
step exhaustion with a tool-only last turn, lookup exhaustion with empty text,
invalid lookup recovery and invalid lookup at a bound, token exhaustion before a
lookup, early sufficient evidence, no rows/no lookups, and timeout with empty text.
Model diagnoses that already answer are preserved. The requested suite includes
all 16 `test_k7_bounded_answer.py` controls and adapter transcript checks.

An independent control at **max_tool_calls=12** also passes: one turn requests
scheduler failures/history plus ten further failure lookups, then an answer-only
turn emits another tool call and no text. Delivered text contains ORA-20001,
destination unavailable and Unknown, without the appended SYSTEM NOTE instruction.
The default eight-step service control passes in the full suite.

Two additional controls fail:

**A. Deadline after interim text.** Configure `kiwi_max_wall_seconds=0.5`. First
turn returns `investigating('f1', failures())`; second is
`ScriptedTurn(text='I will check the destination next.', delay=5.0)`. It streams the
notes, then times out before TurnEnd. Delivered answer is exactly:

```text
Next I will look at f1. I will check the destination next.
```

The retained first lookup already contains ORA-20001: destination unavailable, but
neither that finding nor explicit Unknown reaches the user. At
`service.py:958`, the timeout path passes `answered=bool(state.final_text.strip())`:
any partial text is treated as an answer. Required correction: assess substantive
diagnosis completeness before preserving timed-out text; keep genuine answers but
synthesize from retained facts when the partial stream is only notes.

**B. Empty Finding heading accepted.** Configure `kiwi_max_tool_calls=1`. First
turn reads scheduler failures; answer-only turn returns
`ScriptedTurn(text='**Finding:**\n', tool_calls=NO_ANSWER.tool_calls)`. Delivered answer:

```text
Next I will look at f1. **Finding:**
```

There is no finding or Unknown. `service.py:1972` uses `is_diagnosis(text)` for a
forbidden tool-use turn, while `fallback.py:is_diagnosis` only searches for a
Finding heading. Required correction: a heading alone must not count as a diagnosis.
At exhausted bounds, consistently distinguish interim/empty sections from an answer,
including nonempty notes ending with a normal stop as well as tool_use and timeout.
Add regressions for both failures and preserve the existing substantive-answer,
no-evidence, deadline and budget controls. Do not add provider turns beyond the budget.

## 2. KINJ-01 semantic scope — changes requested

The exact requested negative probes now produce `read_scope` against Trial 21:

- “Its band and MOD check match rules 41 and 43.”
- The same sentence with “in the lines I read”.
- “Rules 41 and 43 use the identical shape.”
- “passes_rules collapses every rule to one FALSE.”
- “Every rule collapses to the same single FALSE.”

The positive control “Each rule call on lines 2520–2585 returns FALSE.” produces
no finding. Repository controls cover both whole members, partial neighbours,
sparse functions, scoped quantifiers, proposed changes and service revision.

However, these independent variants both produce **zero findings**:

```text
Rule 42 has the same MOD check as rule 43, but the rest is unknown.
passes_rules collapses every rule to one FALSE, although the remaining lines were not read.
```

Rule 43's MOD is still beyond the returned line 875, and the sparse function's
unread gaps still cannot support “every rule”. These sentences assert the defect
before acknowledging a different unknown; the qualification does not retract it.
`read_coverage.py:506` skips the entire clause whenever `_OPEN` matches unknown,
unread or not read anywhere. Required correction: bind the exemption to the claim
being left unproven, rather than to unrelated wording elsewhere in the sentence.
Keep genuinely open comparisons and appropriately line-scoped claims passing.
Test mixed Finding/Evidence/Unknown prose so an unknown cannot cancel an asserted
comparison or universal, including a single sentence containing both.

## 3. DIAG-03 cursor versus lock origin — changes requested

The exact Trial 21 sentence and the PREV_SQL_ID/acquired-UPDATE variant now produce
`lock_origin`. The positive control reporting current SQL_ID absent from cache and
the lock-taking statement unknown passes. Repository service controls preserve
correct blocker 42/waiter 77 and human decision ownership through one revision.

This independent assertion produces **zero findings**:

```text
SID 42's SQL_ID identifies the statement that took the lock, but whether to kill the session is the DBA's decision.
```

The final human-decision qualification does not weaken the unsupported lock-origin
assertion. `claim_checks.py:354` exempts the whole clause because
`_LOCK_ORIGIN_KEPT_APART` matches the unrelated word “whether”. Required correction:
bind questions/negation/unknown exemptions to the SQL_ID–lock-origin relationship.
Keep unrelated DBA decisions from suppressing this finding, and keep supported
blocker identification and genuinely unknown origin statements passing.

## 4. C34 next-check aliases and decline wording — accepted in scope

Independently supplied valid sql_id/row_limit selectors and cursor_text goal with
both `tuning.cursor_search` and `tuning__cursor_search`. Both render accepted
`tuning.cursor_search` steps; the alias has an accurate normalization note.
“read the passes_rules subprogram in full” without a valid selector identifier is
declined as “its operation is not a catalog operation id”, not forbidden access.
The suite also covers aliases supplied in selectors.tool/selectors.operation,
canonical names, unsupported goals, and existing access/coverage declines.

## 5. apply_trial_reviews.py metadata — accepted in scope

Imported the helper with runpy (main loop not executed) and redirected HERE to a
temporary directory. A synthetic report gains no top-level aiReview. More strongly,
replayed **all 18 Trial 21 reviews on a scratch copy of its pre-review report**:
returns (18 reviewed, 1 failed); after removing only `cases[*].review`, before and
after documents are exactly equal. A separate `.ai-review.json` sidecar exists.
No saved report was rewritten. This closes the literal review-fields-only issue
for new outputs; historical Trial 21 metadata remains retained as evidence.

## Handoff and gate

Track A: close the three findings above with meaningful positive/negative controls,
retain the two accepted corrections, rerun the requested suite/lint/diff checks,
and record a follow-up handoff for independent verification. This is review work,
not authorization to edit Track A files from Track B. No new Strata diagnostic,
unchanged-candidate resampling, candidate freeze or Q6 A/B is released here.

After correction acceptance, release exactly one fresh Q5 18-case diagnostic on
local Strata `qwen3.8-flash-next-iq3_s`, unchanged cases/rubric/budgets, with D1
proposal compilation/verified cleanup, retained evidence/events, and fresh review
of every case. That release remains pending. Q6 still requires reviewed Q5 18/18,
a clean committed candidate (including resolving untracked helper .py files), and
explicit freeze approval. K-7 remains NOT QUALIFIED, Kiwi off by default, K8 gated.

## Earlier readiness check (superseded)

Before Claude completed Wave 2B, an earlier check found no handoff and reproduced
the original defects. Its restricted suite attempt stalled after 31 dots and was
interrupted. The completed handoff and successful 1198-test run above supersede
that readiness assessment; current blockers are the concrete follow-up findings
in this review, not missing work or a missing handoff.
