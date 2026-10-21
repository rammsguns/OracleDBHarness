# AGY-1 / AGY-2 independent review — 2026-10-19

Reviewer: Codex (AI). Scope: Claude's Wave 1 changes for C34-A/B, C35-A, the
single-turn service wiring, C28 source types, and generated contract parity.
**Verdict: changes requested. Sync Point 1 does not release Q5.** No Strata or Oracle
call was made during this review, and no Track A file was edited.

## Findings

1. **Plain explain can falsely report a failed revision as clear.** In
   `services/api/harness_api/copilot/service.py:759`, `_check_plain_claims` overwrites
   its `action` argument (`"explain"`) with the revision decision (`"revise"`). At line
   808 it passes that decision to `_plain_review`. `_plain_review("revise", source,
   answer)` runs the diagnosis checks instead of `missing_remedies`. Independent probe:
   with a supplied `WHEN OTHERS THEN NULL` source and the unchanged revised answer
   `The handler swallows errors.`, `_plain_review("revise", ...)` returns zero findings,
   while `_plain_review("explain", ...)` returns one `missing_remedy`. The final
   `claim_check` can therefore say `findings: []` although the remedy is still absent.
   Keep the request action and revision decision in separate variables, and add an
   end-to-end control where the model's second answer remains unremedied.

2. **A connective alone counts as a reason for no change.** In
   `services/api/harness_api/copilot/completeness.py:222-233`, `_REASON_WORD` is searched
   across the whole sentence. `missing_remedies("explain", source, "No change is
   recommended because.")` returns no finding for a swallowing handler. The same
   happens with `"No change is recommended because: "`. Neither gives a reason, which
   violates C35-A. Require substantive text after the connective, and pin this case.

3. **A conditional raise is counted as propagation on every path.** In
   `completeness.py:237-246`, `_code_raises` accepts a fenced replacement when each
   handler contains any raise token. For `WHEN OTHERS THEN IF 1=0 THEN RAISE; ELSE
   NULL; END IF;`, `missing_remedies` returns no finding even though the handler
   swallows the error on its actual path. Either verify that propagation is
   unconditional or leave conditional handlers unproven and require a prose remedy.

4. **Mixed PL/Scope status is accepted for `all_definitions`.** In
   `services/api/harness_api/copilot/next_checks.py:457-458`, a lookup is declined only
   if `COLLECTED` is absent from *all* rows. A same-name package lookup can include a
   `PACKAGE` row with `COLLECTED` and a `PACKAGE BODY` row with `NOT COLLECTED`, because
   `schema.plscope_identifiers` does not require an `object_type` selector. An
   independent two-row `LookupFacts` probe accepted this `all_definitions` step and
   rendered the claim. Decline or narrow the step when any relevant unit is not
   collected, and test mixed spec/body status.

## Controls and verification

- Independent source-range probes with `END check_rule_001;` in a block comment,
  line comment, ordinary string, and q-quote returned no whole-member coverage and
  flagged `I read check_rule_001 in full.` A real code `END` returned whole coverage.
  A range starting at line 10 kept the end apparent and flagged the same claim.
- Independent next-check probes declined `name_resolution` without `resolved_in` and
  with an unexcluded package reading. A newline and Markdown claim in `about` was
  withheld from the rendered text.
- The reviewed C28 catalog and SQL headers consistently list the documented source
  types through `JAVA SOURCE`; `LIBRARY` and `ASSEMBLY` remain pending live
  `ALL_SOURCE` verification in Wave 2. Contract tests verify generator output,
  published finding kinds, TypeScript union, and `revision.rendered` parity.
- `.venv/bin/pytest -q tests/copilot tests/integration/test_contracts.py`:
  **1081 passed** with local socket access. A sandboxed attempt stalled when local
  sockets were denied; it was interrupted and rerun outside that restriction.
- `npm --prefix apps/web test -- --run src/views/CopilotDrawer.test.tsx`:
  **17 passed** (existing React `act(...)` warnings). Web typecheck passed.

The four findings above are independent of the green suites. Rerun the targeted
probes after corrections and then repeat Sync Point 1 before a candidate freeze or
fresh Q5 run. Kiwi remains disabled by default; no qualification is claimed.
