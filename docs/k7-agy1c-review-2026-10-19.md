# Wave 1C independent review — Sync Point 1B, 2026-10-19

Reviewer: Codex (AI). Reviewed Claude's Wave 1C handoff in `claude_k7.md`, the
changes in `service.py`, `completeness.py`, `next_checks.py`, and their regression
tests. **Changes requested: finding 3 remains open under fresh variations. Wave 2
and its Q5 Strata diagnostic are not released.** No Track A source or test was edited.

## Original findings

| Finding | Independent result | Status |
| --- | --- | --- |
| 1. Explain action overwritten before revised-answer check | The request action is now distinct from the revision decision. Direct service-generator probes kept `missing_remedy` on the revised answer `The handler swallows errors.` and on `No change is recommended because.`; a genuine prose remedy cleared the finding. Each probe used exactly one revision call. The API regression also passed. | Closed in reviewed scope |
| 2. Bare connective accepted as a reason | `because.`, `because: `, `since —`, and a separate following `Because.` all produced `missing_remedy`. `No change is recommended because the cleanup is optional.` passed. | Closed in reviewed scope |
| 3. Conditional raise accepted as unconditional propagation | The exact dead-raise example and `IF l_debug THEN RAISE; END IF; NULL;` now produce `missing_remedy`. However, conditional early exits before a later top-level raise still bypass the check, as detailed below. | Open |
| 4. Mixed PL/Scope collection | A `PACKAGE: COLLECTED` plus `PACKAGE BODY: NOT COLLECTED` lookup declined `all_definitions` with no type selector and with `PACKAGE BODY`. Narrowing to the collected `PACKAGE` was accepted conditionally. | Closed in reviewed scope |

## Remaining propagation defect

`services/api/harness_api/copilot/completeness.py:159-165` only records an early
exit when its token is at the handler's own nesting level. Consequently this
replacement is accepted as a remedy for a swallowing handler:

```sql
PROCEDURE p(l_ignore IN BOOLEAN) IS
BEGIN
  NULL;
EXCEPTION
  WHEN OTHERS THEN
    IF l_ignore THEN RETURN; END IF;
    RAISE;
END p;
```

Independent result: `handlers(...)` reports `raises=True, propagates=True`, and
`missing_remedies("explain", swallowing_source, fenced_replacement)` returns zero
findings. When `l_ignore` is true, the exception is swallowed by the return. The
fresh variation `IF l_ignore THEN GOTO done; END IF; RAISE; <<done>> NULL;` is also
accepted. A control with `IF l_debug THEN NULL; END IF; RAISE;` correctly passes.

Track possible paths that leave or jump around the eventual raise, or conservatively
withhold a propagation claim when those paths cannot be proved. Add both counterexamples
and retain the harmless conditional-logging control. A raise at the top level is not
enough when an earlier branch can avoid it.

## Live C28 and workflow evidence

The authorized Part 1 Oracle calls preceded this code review. Their evidence is in
`docs/compatibility.md`:

- D1 `INT$DBA_SOURCE` permits nine types, including `LIBRARY` and `JAVA SOURCE`.
  D2 additionally permits `MLE MODULE`. `ASSEMBLY` occurs in the decode expression
  but is excluded by the actual predicate on both builds.
- The current source SQL headers and resulting toolbox allowlist still omit
  `LIBRARY` and `MLE MODULE`. Track A must reconcile them with this live evidence,
  describing the D2 version scope for `MLE MODULE`; keep `ASSEMBLY` excluded.
- All 49 live API workflow checks passed across the requested real profiles,
  including pagination, nine observation-only policy refusals, transaction and
  PL/SQL workflows, tuning/DBA reads and owned-object cleanup. Independent reads
  confirmed zero verification objects remain on D1, D2 and D4. Labs and D3 received
  no fixture setup/teardown.

## Verification and next gate

`.venv/bin/pytest -q tests/copilot tests/integration/test_contracts.py` completed
with **1104 passed in 77.45 seconds**, using local socket access. The independent
probes above ran separately without a model call. The green suite does not cover
the two conditional-exit counterexamples.

Claude should correct the remaining propagation defect and reconcile C28, then
update `claude_k7.md` for another independent checkpoint. Q5, candidate freeze and
full runs remain gated. No Strata request was made by Codex; model, budgets, case
set, rubric and Kiwi's default-off state were not changed.
