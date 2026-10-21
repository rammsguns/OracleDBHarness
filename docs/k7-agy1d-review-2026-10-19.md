# Wave 1C follow-up review — Sync Point 1C, 2026-10-19

Reviewer: Codex (AI). Reviewed Claude's latest “Wave 1C follow-up” handoff in
`claude_k7.md`, `completeness.py`, `toolbox.py`, all three `object_source*.sql`
entries, and their completeness, service-wiring and catalog tests.

**Changes requested. The requested exact controls pass, and C28 is reconciled.
A fresh labelled-loop counterexample still bypasses remedy checking. Wave 2 / Q5
is not released.** No Track A source or tests were edited.

## Independent controls

The probes called `missing_remedies("explain", swallowing_source, fenced_code)`
directly, with a `WHEN OTHERS THEN NULL` source and no prose remedy.

| Replacement handler | Result |
| --- | --- |
| `IF l_ignore THEN RETURN; END IF; RAISE;` | `missing_remedy` |
| `IF l_ignore THEN GOTO done; END IF; RAISE; <<done>> NULL;` | `missing_remedy` |
| Inner `FOR` loop with unlabelled `EXIT WHEN`, followed by handler-level `RAISE` | Zero findings |
| Inner `FOR` loop with unlabelled `CONTINUE WHEN`, followed by handler-level `RAISE` | Zero findings |
| Conditional `RETURN` inside an inner loop before handler-level `RAISE` | `missing_remedy` |
| Conditional `GOTO done` inside an inner loop, label after handler-level `RAISE` | `missing_remedy` |
| `IF l_debug THEN NULL; END IF; RAISE;` | Zero findings |

All seven requested handler controls passed. Independently parsed the three actual
SQL catalog files and called `KiwiToolbox._check_object_type` on each: `LIBRARY`,
`MLE MODULE`, and their padded/lowercase forms were accepted; `VIEW`, `TABLE`, and
`ASSEMBLY` raised `ValidationError` (21 type checks). Headers and generated toolbox
descriptions now include the live domain and MLE MODULE's version scope. This closes
the assigned C28 discrepancy; acceptance is validation, not evidence that a matching
source object exists on every target.

## Remaining finding: labelled loop transfers can skip the raise

`services/api/harness_api/copilot/completeness.py:175` treats any loop nested inside
a handler as sufficient to make `EXIT` / `CONTINUE` harmless. It does not inspect
the statement's optional target label. The target can be a loop surrounding the
handler, so the transfer bypasses the later raise:

```sql
<<outer_loop>>
LOOP
  BEGIN
    NULL;
  EXCEPTION
    WHEN OTHERS THEN
      FOR i IN 1 .. 3 LOOP
        EXIT outer_loop WHEN l_ignore;
      END LOOP;
      RAISE;
  END;
END LOOP;
```

With `l_ignore` true, the exit leaves `outer_loop` without propagating the handled
exception. Replacing `EXIT` with `CONTINUE` similarly skips the raise and starts the
next outer iteration. Both independent probes returned zero findings and
`Handler(raises=True, propagates=True)`. The original conditional RETURN/GOTO defect
is corrected, but the broader propagation claim is still unsound.

Track A should resolve labelled loop targets relative to the handler, or
conservatively leave propagation unproven for labelled transfers that could escape
it. Add outer-target EXIT and CONTINUE regressions while retaining the passing
unlabelled inner-loop controls (and, if supported, labels targeting only inner loops).

## Verification and gate

`.venv/bin/pytest -q tests/copilot tests/integration/test_contracts.py` passed
**1116 tests in 85.97 seconds**, with local socket access. The independent probes
above ran separately from the suite without a model. The green suite lacks the two
labelled outer-loop transfer controls.

No Strata or live Oracle call was made by Codex. The previous findings 1, 2 and 4
remain closed in their reviewed scope. **Q5 remains gated on the labelled-transfer
correction and another independent checkpoint.** Candidate freeze and full Q6 runs
are also gated. K-7 remains not qualified; Kiwi remains off by default.
