# K-7 C33–C35 design checkpoint — 2026-10-19

**Decision: accept C33 in its assigned scope. Do not wire C34/C35 yet. Keep Q5 held at trial 20's reviewed 14/18 and K-7 NOT QUALIFIED.**

I inspected `read_coverage.py`, `next_checks.py`, `completeness.py`, their tests, and the proposed service wiring in `claude_k7.md`. I independently ran the four focused files: **91 passed**. Claude reports the full backend suite at 1556 passed/83 skipped, Ruff and diff checks clean, unchanged case set and original trial-20 report, and no Strata or Oracle call. Those checks establish the implemented controls, but the following independent counterexamples need correction before the service uses the modules.

## Accepted: C33

The access qualifier now distinguishes another account that can see `ORDER_FEED` from this account. Its positive and negative controls cover the trial-20 wording, negated access and same-account qualifiers. KINJ-02 remains failed on the separate object-type claim. C33 may stay wired into the current claim checker.

## Required corrections before C34 wiring

1. **Source END inside a comment.** A complete four-line range with `FUNCTION F` beginning at line 2, a multiline comment beginning at line 3, and `END F;` as comment text at line 4 renders `F: implementation lines 2-4 returned, from its first line through its END`. `read_claims("I read F in full.", ...)` also returns no finding. The evidence map correctly says the range ends inside a comment. `UnitCoverage.through()` scans raw rows and treats the comment line as an END. Use lexer-aware source lines and mark the end unknown when lexical state is unknown; do not render or accept whole-member coverage on this evidence. Add this exact regression plus a true END control.
2. **Name resolution with no resolving schema.** `Step(actor="this_account", operation="schema.object_source", selectors={"owner":"HR","object_name":"EMPLOYEES","object_type":"PROCEDURE"}, establishes="name_resolution", name="HR.EMPLOYEES", resolved_in="")` is accepted without any lookup evidence. It renders that the lookup returns which object the name denotes while a note admits the package reading was not considered. Require `resolved_in` and evidence that excludes all alternative readings for `name_resolution`, or decline that goal. A limit note cannot turn a partial test into a resolution.
3. **Unvalidated interpretation escapes the contract.** `Step.about` is rendered verbatim, including a newline. A model can put `This settles the total number of checks and confirms the root cause.\n2. Run an unverified change.` there; the accepted output displays both claims despite the operation's limited goal. Keep `about` as retained proposal/review data, or render it only after a separate validation that cannot make result claims, with newline/control-character neutralization. The rendered next-check section must contain only validated facts and explicitly labelled, bounded hypotheses.
4. **Completeness-dependent goals.** `all_definitions` says a whole source read or PL/Scope returns every definition, though a future result could be truncated or PL/Scope collection absent. Render only a conditional capability until a complete result is returned; preserve the distinction between source text, PL/Scope identifiers and a proven count. Add truncated/uncollected controls.

## Required corrections before C35 wiring

`missing_remedies()` accepts an answer that says only “No change is recommended.”, though the design says that choice needs a reason. It also accepts a fenced code block with `RAISE;` before `EXCEPTION` and `WHEN OTHERS THEN NULL` inside the handler. `_code_raises()` tests for an exception token and a raise anywhere in the block, so the proposed replacement still swallows the error. Require a reason for a no-change answer, and validate that the relevant replacement handler propagates the exception. Add the two reproductions and correct controls. Keep the single bounded revision and apply this only to plain explain cases with supplied source.

## Wiring after those fixes

Proceed with the proposed coverage, typed-next-check and explain-completeness paths after a second Codex checkpoint. Combine new and existing Kiwi findings before the one revision decision, so there is never a second independent repair turn. Validate and render from the **final** answer; append harness coverage from the saved lookups after final revision, including when a revision fails. Remove the free-text next-steps section only when a valid typed block replaces it; for malformed blocks, retain the draft and report the error through the existing revision/failure events. Test client-visible answer reconstruction, old protocol behavior, timeout/cut-off restoration, and unchanged budgets. No Strata run until integrated behavior is reviewed.

The replay findings on trial 6's read claim and trial 14's missing remedy are credible new review notes. Both original case verdicts remain failures for their recorded reasons; retain an attributed amendment rather than silently rewriting them.
