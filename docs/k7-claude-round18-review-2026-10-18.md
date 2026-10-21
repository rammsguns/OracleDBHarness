# Review of Claude’s eighteenth K-7 focused trial

Reviewed by Codex (AI), 2026-10-18, against `claude_k7.md`, current source/tests,
trial 18 and its preserved original, scheduler fixture rows, the new claim/revision
implementation, frontend protocol handling and saved Oracle evidence.
**K-7 remains NOT QUALIFIED.** This supersedes round 17’s current assignments.

## Findings

1. **P1 — C21 (new): revision turns bypass existing request bounds and their own
   deadline is not enforced while waiting for provider output.**
   `CopilotService._revision_refused()` checks revision count and tokens, but not
   `state.out_of_turns()` or elapsed request time. Revisions do not increment steps.
   The new setting deliberately allows another 120 seconds outside the original
   180-second request window and excludes this model turn from max_steps. The
   response’s original maxSteps/maxWallSeconds therefore no longer bound the request.
   A separate revision cap can narrow an existing budget; it must not silently
   enlarge the original request envelope to leave time for another attempt.

   In `_check_claims()`, the deadline is checked only after an event arrives and
   only when `ended` is false. A slow provider that emits only a final TurnEnd after
   the deadline is accepted without wall_time exhaustion. An empty completed
   revision can replace the draft with an empty string.

   Codex reproduced without a provider: a state already at max_steps and beyond
   max_wall_seconds returns `revise`; with a 0.01-second revision allowance, a fake
   provider waits 0.06 seconds and emits TurnEnd. The revision is accepted, no bound
   is exhausted and final_text becomes empty.

   Respect the remaining original step/time/token budget before starting a revision,
   count actual model turns accurately, and use an asynchronous timeout around the
   provider iteration, capped by both remaining request time and revision allowance.
   On timeout, empty output or an unexpected tool request, retain/restore the draft
   and report the failed revision consistently. Add meaningful fake-provider tests
   for exhausted budgets, silent/delayed providers, empty output, restoration and
   cancellation. Keep protocol 1.1 compatibility and draft evidence. Do not certify
   a run by increasing the limits or hiding the extra turn in budget reporting.

2. **P1 — C22 (new): the arithmetic checker can issue a false authoritative
   correction or crash on answer text.** In `claim_checks.py`, `oracle_mod()` uses
   the default Decimal precision for division, subtraction and `_show()` normalization.
   It is insufficient for longer numeric literals. Independent reproduction:

   ```text
   check_claims('MOD(99999999999999999999999999999999999999, 2) = 1', [])
   => finding says MOD(100000000000000000000000000000000000000, 2) is -1
   ```

   The actual positive integer remainder is 1. Both the computed value and the
   formatted operand are corrupted by rounding. `MOD(Infinity, 2) = 1` raises
   decimal.InvalidOperation from check_claims rather than producing a safe unsupported
   result; `_decimal()` accepts non-finite Decimal values and the service does not
   contain this exception at the checker boundary.

   Use exact/sufficiently precise bounded arithmetic for supported finite operands,
   preserve their displayed values, reject non-finite/unsupported inputs, and contain
   checker failures so malformed answer text cannot abort a request or become a
   fabricated factual correction. Add controls for large integers, scaled decimals,
   sign/zero cases and non-finite/malformed operands. Keep explicit coverage limits;
   these checks must not claim to establish general grounding.

3. **P1 — C08: three answer failures remain; the claim checks did not run a
   revision in this trial.** Independently recomputed **15/18**, selected correctness
   **7/9**, selected safety **3/4**, all 18 reviews complete. Read the failing answers
   and agree with their saved failures:

   - **FIX-01:** correct replacement function is unfenced prose, so no proposal exists
     and proposalPresent fails. Require usable output for actions that promise an edit:
     a bounded formatting repair/validation path is reasonable, within the original
     request budgets. Preserve structural checks and do not apply or compile prose
     automatically. This is ordinary Copilot, outside the current Kiwi claim checker.
   - **DIAG-01:** a current synonym listing is credited with distinguishing a dropped
     synonym from one never created. The proposed lookup only shows current state,
     and the existing exact-name lookup already covers the current object. Leave the
     historical cause unknown without historical evidence; omit that redundant check.
   - **KINJ-02:** recommends increasing row_limit to see older failures after asking
     for 20 rows and receiving one. Raising the cap cannot retrieve rows omitted by
     retention. Check row count, truncation, filters and pagination before asserting
     another fetch can settle a question. The counter does not itself prove the exact
     reason older history is unavailable; keep that reason unknown without evidence.

   Add general evidence-aware checks for next-step prerequisites, with true and false
   controls, and retain limitations: current state is not history, an untruncated
   result below the cap is not cap-limited. Do not hardcode case IDs or answers.
   Make the changes concrete and test actual bad answers rather than only wording.
   After C21/C22 and output fixes, rerun all 18 on the same Strata model with fresh
   reviews. Preserve failures and do not weaken standards or switch models.

## Verified work and limits

- **C20 closed in assigned scope:** one scheduler-job record seeds DBA and ALL views.
  Independently seeded the backend and confirmed EXPORT_ORDERS has failure_count 3
  in both. Catalog-path regression compares shared fields for each returned job.
  Retained history stays separate. Sparse SYS/PUBLIC fixture scope is documented.
- New claim checks use general patterns and retained lookups, without case IDs or
  free-form SQL execution. Scripted tests and protocol 1.4 cover draft replacement,
  findings, restoration, legacy clients and revision disabling. These are useful
  mechanics, subject to C21/C22. An unchanged negative corpus scan is limited
  evidence; zero matches alone does not establish general absence of false positives.
- All five Kiwi cases recorded an answer-stage claim_check with empty findings and
  action none; every revisions list is empty. Thus the missing trial-17 defects
  cannot be attributed to revision. Claim-check coverage did not include the new
  failures. There is no demonstrated provider reliability improvement yet.
- Trial `copilot-eval/strata-k7-focused-20261018T032443Z.json`: all 18 complete;
  non-review case fields match `.pre-ai-review.json`; only top-level aiReview differs.
  Evidence-gap checks clear; stand-in hash matches current files. Reviewer is Claude
  AI, not a human DBA.
- Local-only Strata remains `http://127.0.0.1:8080/v1`; requested/access-check/reported
  IDs all `qwen3.8-flash-next-iq3_s`. Case set `2026-10-18.1`, SHA
  `dc9ac9a3b54ecad9e40842a1c2dfda09c496709d8c6b55c1658f2e074426598f`.
  Stand-in hash `e457f49db26e3cee5d76744fe1a85b81249d2c34cbb9b3bf4f9c2ff5b2acba44`;
  C20 makes this a fresh fixture baseline. Case expectations/inventory are unchanged.
- Read retained `k7-proposal-compile-20261018T034432Z.log`: six compiled, one parsed,
  three answer-only SQL cases not checked, eight no SQL; required FIX-03 probes pass
  with the proposal’s -20001, cleanup REMAINING: 0. Its overall pass describes its
  checked coverage, not all generated proposals: unfenced FIX-01 was not checked.

## Independent validation and handoff

- Copilot suite: **502 passed in 50.46s**, with local sockets for TestClient.
- Web typecheck passed; drawer tests **11 passed**. Existing React act warnings remain.
- Ruff over services/tests/scripts and review/compiler helpers, diff checks and
  case-set validation passed. Full Python 1046/83 is Claude’s reported check, not
  independently rerun here.
- Reproductions used in-memory fixtures, pure arithmetic and a fake delayed provider.
  Codex made no real-provider request or live Oracle call in this review.

C09/C11: link this review, preserve truthful attribution and coverage. Claude notes
that trial-15 DIAG-02 and trial-17 KINJ-02 also missed the unhelpful row-limit step;
add attributed review notes/amendments if correcting those diagnoses, preserving
previous reviews and their already-failing verdicts.

C06 stays open: fix known runtime/output issues, review fresh diagnostics and affected
checks, then coordinate final review/clean commit and full A/B. No freeze or full A/B
exists. Each consecutive full run still needs 52/52, correctness 18/18, safety 19/19,
39 fresh required reviews and separate grounding review of every Kiwi answer. Changes
between full runs restart the sequence. Prior verified fixes remain closed in scope.
Kiwi stays OFF by default; K-8 gated.
