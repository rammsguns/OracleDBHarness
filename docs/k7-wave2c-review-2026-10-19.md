# Wave 2C independent review — Sync Point 2C, 2026-10-19

Reviewer: Codex (AI). Read the active Sync Point 2C assignment, Claude's assignment,
the completed Wave 2C handoff in `claude_k7.md`, and the Wave 2B review. Inspected
`fallback.py`, `service.py`, `read_coverage.py`, `claim_checks.py`, the shared
subclause splitter, and the corresponding service/claim regression controls.

**All three follow-ups are accepted in reviewed scope. Q5 Trial 22 is released:**
one fresh 18-case diagnostic on local Strata `qwen3.8-flash-next-iq3_s` at
`http://127.0.0.1:8080/v1`, with unchanged cases, rubric and budgets, D1 proposal
compilation/verified cleanup, retained evidence/events, and fresh review of all
18 cases. This accepts the implementation corrections; it does not approve Q5
results, candidate freeze, Q6 A/B, or qualification.

## Independent verification

- `.venv/bin/pytest -q tests/copilot tests/integration/test_contracts.py tests/unit/test_bootstrap_targets.py`:
  **1222 passed in 100.71 seconds**, with approved local socket access.
- `.venv/bin/ruff check services tests scripts`: passed.
- `git diff --check`: passed, including after this documentation update.
- `/tmp/test_sync2b_extra.py` and `/tmp/test_sync2c_answers.py`: **10 passed in
  4.06 seconds**, independent stand-in service probes, run separately from the suite.
- `/tmp/sync2c_claims.py`: **21/21 independent scope/lock controls passed** against
  LookupFacts reconstructed from Trial 21's raw retained evidence.
- `/tmp/sync2b_probes.py`: exact earlier claim probes also passed; C34 aliases and
  unknown-identifier wording remain correct, and a scratch review has no extra
  top-level review metadata. No historical evaluation report was rewritten.

No Track A implementation/test file was edited by Codex. No live Oracle, Strata,
or external provider call was made for this review.

## DIAG-02 bounded synthesis — accepted

The exact previous deadline reproduction now synthesizes the retained error:
`kiwi_max_wall_seconds=0.5`, first turn reads scheduler failures, second streams
“I will check the destination next.” and times out. Delivered text includes
`ORA-20001: destination unavailable` and Unknown, while preserving interim notes.
The service now uses diagnosis detection instead of treating any nonempty stream
as an answer on the timeout path.

At `kiwi_max_tool_calls=1`, all these independent answer-only controls synthesize
from retained facts rather than accepting an empty/incomplete answer:

| Final turn | Result |
| --- | --- |
| `**Finding:**\n` plus an attempted tool call | Fallback |
| Same empty bold heading without a tool call | Fallback |
| `## Finding\n\n## Evidence\n` | Fallback |
| `I will check the destination next.` ending normally | Fallback |
| Empty Finding followed by an Evidence section containing text | Fallback |

Genuine diagnoses with a substantive Finding body stand at normal completion and
when the deadline cuts off their final TurnEnd. The exact 12-lookup control also
passes: empty/tool-use answer at the bound produces the retained ORA-20001 and
explicit unknowns without repeating the injected SYSTEM NOTE. No extra provider
turn is spent for the fallback.

The full suite covers the default eight-step answer/revision reserve, invalid
lookup recovery, invalid lookup at a bound, early sufficient evidence, no rows,
no lookups, token bounds and genuine-answer preservation. Reviewed the three
previously failing repository controls: fixtures now provide a Finding where they
intend to test a real answer at the bound; the old-client skipped:protocol and
last-step skipped:steps assertions remain. The timeout test now expects fallback
following its incomplete stream while retaining its time bound and stop reason.
These changes preserve their original protocol, budget and late-message checks.

## KINJ-01 semantic scope — accepted

The shared lexical splitter separates semicolons, em dashes, conjunction turns and
leading concessions, preserving backtick code and numeric ranges. Read-scope
exemptions apply within the resulting parts; a reference back to the asserted
claim (“but that is unknown”) remains exempt. An unknown about different text no
longer cancels an asserted comparison/universal.

Eight independent overclaim variants now produce `read_scope`, including:

```text
Rule 42 has the same MOD check as rule 43, but the rest is unknown.
Rule 42 has the same MOD check as rule 43; the rest is unknown.
The rest is unknown, but rule 42 has the same MOD check as rule 43.
Rule 42 has the same MOD check as rule 43 — the rest is unknown.
passes_rules collapses every rule to one FALSE, although the remaining lines were not read.
Although lines 2426-2519 were not read, passes_rules collapses every rule to one FALSE.
```

Additional leading While and trailing whereas variants pass the negative-claim
checks too. Four supported/open controls produce no findings:

```text
Whether rule 42 has the same MOD check as rule 43 is unknown.
Each rule call on lines 2520-2585 returns FALSE, while lines 2426-2519 were not read.
Rule 42 may have the same MOD check as rule 43, but that is unknown.
On lines 2520–2585 each rule call that fails returns FALSE.
```

Exact Trial 21 plain comparisons (with/without “in the lines I read”) and sparse
“every rule” assertions still flag; narrowly scoped calls pass. Repository controls
also verify mixed Finding/Evidence/Unknown sections, whole-member controls,
proposed changes and the bounded service revision.

## DIAG-03 lock origin — accepted

Five independent unsupported-origin variants produce `lock_origin`, including the
exact previous failure and inverted order:

```text
SID 42's SQL_ID identifies the statement that took the lock, but whether to kill the session is the DBA's decision.
Whether to kill the session is the DBA's decision, and SID 42's SQL_ID is the statement that took the lock.
```

The semicolon variant, PREV_SQL_ID/UPDATE followed by an em-dash decision, and
leading concession about committing also flag. Four genuine open/negated controls
produce no finding:

```text
Whether SID 42's SQL_ID is the statement that took the lock cannot be shown from V$SESSION.
SID 42's SQL_ID does not identify the statement that acquired the lock.
SID 42's SQL_ID may be the statement that took the lock, but that is not established.
SID 42's current SQL_ID is not in the cursor cache; the statement that acquired the lock is unknown.
```

The exact original Trial 21 empty-cache claim and PREV_SQL_ID assertion still flag.
Repository service controls preserve blocker 42/waiter 77 and the human action
decision through one revision, with no session-role false finding.

## Limits and release instructions

The claim checks and subclause splitter remain lexical, not a proof of every
possible statement. Split claims, indirect references and novel model wording
still require fresh grounding review. Diagnosis detection checks a Finding body,
not whether its prose is factually complete. The new local answer-only request
shape has stand-in adapter coverage; actual Strata acceptance remains to be
observed in the released diagnostic. These limits do not reopen the tested fixes.

Track A may now run **exactly one fresh Q5 Trial 22** using the same 18 case IDs as
trials 19–21, fixed case-set hash
`dc9ac9a3b54ecad9e40842a1c2dfda09c496709d8c6b55c1658f2e074426598f`, configured model
and existing budgets. Verify and retain served identity and stand-in fingerprint;
keep an untouched `.pre-ai-review.json`, all lookup/evidence, interim text,
claim-check and revision events. Review output must differ only under
`cases[*].review`; keep metadata in its sidecar. Compile proposals in disposable
D1 schemas, retain logs and verify zero leftover qualification/proposal schemas.
Review all 18 answers afresh against rubrics and raw evidence, including interim
claims and revisions; return scored report and compile/cleanup artifacts for
independent review. Do not resample an unchanged failed candidate.

Candidate freeze and full Q6 A/B remain gated on independently reviewed Q5 **18/18**
and explicit freeze approval. A future freeze must resolve untracked non-report
Python helpers in `copilot-eval/` and require runner `uncommittedChanges=False`.
Q6 still needs two consecutive clean full 52/52 runs with fresh required reviews
and exhaustive grounding. K-7 remains NOT QUALIFIED, Kiwi off by default, K8 gated.

The earlier preliminary check (before the completed handoff) is superseded: its
1195/3 suite result and missing-handoff state do not describe this reviewed candidate.
