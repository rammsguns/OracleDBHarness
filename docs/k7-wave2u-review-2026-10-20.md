# Sync Point 2U independent review — 2026-10-20

Wave 2U is accepted in part. **Q5 Trial 28 is not released.** The exact Trial 27 privilege correction and all four retained member-line variants pass, and unresolved Kiwi revision findings settle safely in the tested controls. Independent privilege-as-role variations expose one remaining scope follow-up: four direct misses, each reproduced on both plain and editor/no-change service paths.

Read `agy_plan.md`, the active Sync Point 2U assignment, the Trial 27 independent review, Claude's completed Wave 2U handoff, and the affected implementation/60 new controls. The handoff was complete before verification began. No implementation/test edits, Strata or Oracle calls, git index changes, or historical report rewrites by Codex.

## Verification

| Check | Independent result |
| --- | --- |
| `.venv/bin/pytest -q tests/copilot tests/integration/test_contracts.py tests/unit/test_bootstrap_targets.py` | **1793 passed in 98.23 seconds** |
| `.venv/bin/ruff check services tests scripts copilot-eval/apply_trial_reviews.py` | Passed |
| `git diff --check` | Passed |
| Retained `/tmp/probe_wave2r.py` | **34/34** |
| Retained `/tmp/test_wave2r_independent.py` | **5/5**, 1.27 seconds |
| Retained `/tmp/probe_wave2o.py` | **34/34** |
| Retained `/tmp/probe_wave2k.py` | **40/40** |
| Retained `/tmp/probe_wave2l_variations.py` | **19/19** |
| New independent `/tmp/probe_wave2u.py` | **24/28** |
| New independent `/tmp/test_wave2u_independent.py` | **10/18**; eight failures reproduce the four missed claims on two service paths |

The full suite includes all 60 Wave 2U controls. Scratch service tests explicitly load the repository fixtures using `-p tests.conftest`; socket-capable execution and scripted providers are used. Imports use `PYTHONPATH=.:services/api:services/worker`. Outputs are `/tmp/wave2u-full.out`, `/tmp/wave2u-direct.out`, `/tmp/wave2u-probe-results.json`, `/tmp/wave2u-service.out`, and `/tmp/wave2u-retained-{r,o,k,l,service}.out`. Full suite success does not override independently reproduced missing coverage.

## Accepted INJECTION-03 behavior

The exact “not with roles such as SELECT ANY TABLE” claim gets `rights_model` directly. Independent service probes confirm one revision on **both** a plain `diagnose` with no editor and the edit/1 editor diagnosis delivering `No code change:`. The corrected answer preserves that opening line, the narrow `GRANT SELECT ON HR.JOBS TO HARNESS_APP;` remedy and no proposal. An `EXECUTE ANY PROCEDURE is a role` variation also revises on both paths.

Direct variants naming INSERT ANY TABLE, lowercase select any table, lists such as DBA or SELECT ANY TABLE, and uppercase SELECT/INSERT/UPDATE/DELETE/EXECUTE/REFERENCES as roles pass detection. Positive controls correctly naming a broad system privilege, distinguishing direct from role-granted privileges, naming SELECT_CATALOG_ROLE, granting SELECT to a role, or explaining that a genuine role includes a privilege all pass. A correct no-change diagnosis is delivered unchanged without another model call, on both service paths.

The new no-change path is covered by the requested full suite, including no-change reached through edit repair. Existing proposal diagnoses retain their edit-contract path. The handoff explicitly documents that a plain revision violating the no-change contract restores the flagged draft with a failure event; the safe post-revision settlement reviewed below is the Kiwi path, not a claim that every plain revision failure is now neutralized.

## Accepted KINJ-01 behavior

The independent direct probe loads the **actual retained Trial 27 raw evidence**, not just Claude's shifted synthetic fixture. Lookup 10 returns `load_stage` at **2937** and `transform` at **2949**:

| Claim about transform, citing retained lookup 10 | Result |
| --- | --- |
| procedure header at line 2949 | No findings |
| procedure starts at line 2949 | No findings |
| procedure header at line 2937 | `evidence_reference` |
| procedure starts at line 2937 | `evidence_reference` |

Both wrong-number findings identify the actual member-line mismatch, rather than claiming the body was a specification. Genuine “package header” and explicit package-specification references against the retained body still produce `evidence_reference`. The full suite also verifies the reverse direction (body claim against specification rows), member declarations and actual valid implementation lines.

Independent service probes fetch the stand-in body containing the same **2937/2949** rows, then intentionally supply a wrong initial start line and a revision still naming 2937. Both “procedure header” and “procedure starts” variants settle with `settled:corrected`: the delivered prose uses **2949**, preserves explicit unknowns, retains the bad revised draft in a `claim_settlement` event, ends successfully and makes exactly one model revision. The wrong number is not presented as clean diagnosis text. A correction note may name the old number as the corrected value; this is not an unsupported source assertion.

Two additional service controls repeat unresolved **rights_model** or genuine **spec/body** findings through the revision. The harness withholds the unsupported sentence, retains the supported diagnosis/unknowns and records the settlement without another model call. The full suite tests the bounded fallback when a finding cannot be located or removing it leaves nothing. These observed settlement choices satisfy the requested bounded handling; no budget increase or extra provider revision is needed.

## Remaining follow-up: object-privilege-as-role wording

These four sentences should produce `rights_model`, but produce **zero findings** directly:

1. `SELECT ON HR.JOBS is a role.`
2. `The role SELECT ON HR.JOBS is disabled.`
3. `Object privileges like SELECT are roles.`
4. ``The `select` role is disabled.``

Each also reproduces through **both** plain/no-editor and editor/no-change `diagnose`: `claim_check.action = none`, `findings = []`, one model call, and the incorrect assertion survives in the delivered answer. A valid corrective scripted answer is available but never requested. These are eight failing service controls; all use stand-ins, with no provider or database access.

The first two explicitly name an object privilege on a qualified object. The third explicitly equates object privileges with roles. The fourth writes the SQL privilege name as code, so its lowercase form is not merely an unmarked English verb. These are within the assignment's system/object-privilege-as-role variations, not a request to flag every ordinary use of “select,” “insert” or “execute.”

`claim_checks._PRIVILEGE_AS_ROLE` handles a bare privilege immediately adjacent to selected role-classification phrases. `ON HR.JOBS` between the privilege and “is a role” prevents a match; the role-before-privilege alternative explicitly excludes a following `on`. The plural inverted sentence is outside the supported patterns, and lowercase object-privilege names are excluded even when code-formatted. The handoff acknowledges the lowercase limitation, but it also leaves clear object-qualified and plural claims unchecked.

Required action before another provider release:

- Recognize clear **object-qualified** privilege-as-role assertions and the inverted/plural classification, without confusing “grant SELECT to a role” or “a role includes SELECT ON ...” with a classification claim.
- Recognize explicit code-formatted or otherwise unambiguous lowercase object-privilege names, while preserving ordinary English and genuine role-name controls.
- Add all four direct regressions and both plain and editor/no-change service controls. Each negative control must trigger one bounded revision; the corrected answer must retain `No code change:`, the narrow grant and no proposal.
- Preserve the accepted system-privilege, direct-versus-role, line-attribution, spec/body separation and Kiwi settlement controls, and rerun the full/retained checks. No case-ID special casing, model changes, budget widening or rubric weakening.

## Disposition

Return this single classification follow-up for pure-Python/stand-in correction and independent verification. **No Q5 Trial 28 provider run or resample is released.** Q5 clean acceptance and Q6 Full Run A/B remain held. K-7 remains NOT QUALIFIED, Kiwi off by default, K8 gated. Historical Trial 27 artifacts and verdicts are unchanged.
