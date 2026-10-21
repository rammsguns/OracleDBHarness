# Sync Point 2H independent review — 2026-10-19

**Changes requested. Q5 Trial 24 is not released.** The exact Trial 23 failures now trigger the intended checks, the requested anchor repair and bounded fallback work, and the two retained detector false positives are fixed. Independent variations expose two follow-ups: the new nested-band check rejects genuine unknowns, and a package-header-only read disables the job-intervention check despite leaving the action unread.

Claude's completed Wave 2H handoff in `claude_k7.md` (section beginning at line 2889) was present before verification. Codex did not edit `services/api/harness_api/copilot/*`, `tests/copilot/*`, contracts or review scripts. All new executable probes ran under `/tmp`. No live Strata or Oracle call, report rewrite, commit or freeze was performed.

## Verification

- `.venv/bin/pytest -q tests/copilot tests/integration/test_contracts.py tests/unit/test_bootstrap_targets.py`: **1351 passed in 97.06 seconds**.
- `.venv/bin/ruff check services tests scripts copilot-eval/apply_trial_reviews.py`: passed.
- `git diff --check`: passed before and after documentation updates.
- `/tmp/probe_wave2h.py`: **31/33** independent retained-evidence claim controls passed, plus passing anchor and explanation-remedy assertions. Two genuine band-unknown controls failed.
- `/tmp/probe_wave2h_boundaries.py`: **3/5** boundary controls passed; one more genuine band unknown was flagged and one header-only job-action exemption bypassed the check. Combined claim controls: **34/38**, four failures across two items.
- `PYTHONPATH=services/api:. .venv/bin/pytest -q -p tests.conftest /tmp/test_wave2h_independent.py`: **8 passed in 1.98 seconds**. These exercise the exact retained DRAFT-03 proposal and six unsuccessful/refused revision paths plus successful full-answer replacement.

The retained-evidence scripts independently reconstruct `LookupFacts` from Trial 23's raw operation, status, columns, rows, parameters and lookup number, then call production `_kiwi_review`. The header-only control explicitly adds a synthetic lookup to those retained facts; it is not represented as a lookup Trial 23 actually made. Explanation checks use the supplied selected-source lines and exact retained first draft. A scratch invocation initially supplied the source as one string instead of a sequence of lines; that was corrected before reporting its results.

Trial 23's reviewed, pre-review and sidecar hashes remain identical to those recorded in the Sync Point 2G audit. Historical reports are unchanged. The independent Trial 23 score remains 14/18.

## Results by assigned item

| Item | Independent finding |
| --- | --- |
| DRAFT-03 anchors | Accepted in assigned scope. The exact retained proposal flags precisely the malformed HARNESS_APP.STATUS%TYPE and HARNESS_APP.TOTAL%TYPE anchors. Correct three-part table-column anchors, ORDERS.STATUS%TYPE and local l_rec.status%TYPE pass. The service requests one bounded repair and delivers the corrected complete proposal. |
| KINJ-01 nested bands | Exact Finding and negative-data assertion are flagged as band_reachability against retained rule-001/rule-042 bounds. Negated contradictions and the unrelated MOD test pass. **Changes needed:** genuine unknowns about the unread intervening path are also flagged. |
| KINJ-01 sparse MOD universal | Accepted. “Every rule function ... same shape ... THEN RETURN FALSE” yields read_scope; the statement limited to 120 returned matching lines passes. |
| KINJ-01 bounded revision fallback | Accepted for modern revisable protocol in tested scope. Wall-time failure, provider failure after partial text, empty reply, unexpected tool reply, revisions refused and steps refused all remove flagged/interim/partial text and deliver the retained-fact fallback, Unknown and lines-read record. Successful revision replaces the entire interim-plus-final answer. |
| DIAG-01 type-filtered object scope | Accepted in tested scope. Exact retained Finding and only-object/no-other-object variants yield schema_scope; table-local, TABLE/SYNONYM result statements and genuine unknowns pass. A categorical only-object claim with a separate unknown caveat is still flagged. |
| DIAG-02 job-intervention overclaim | Exact retained Remedy and needs-no-change variants yield job_intervention; observed-state and genuine unknown controls pass. **Changes needed:** a returned package header alone exempts the same unsupported claim. |
| EXPLAIN-06 false positive | Accepted. Exact retained initial answer, log-then-re-raise recommendation and RAISE in a text fence give no missing_remedy. A mere “does not re-raise” description and a fence ending NULL still yield missing_remedy. |
| DIAG-03 false positive | Accepted. Exact retained initial answer and explicit “says/shows nothing about the statement that took the lock” statements no longer yield lock_origin. Cursor absence used as the cause of unknown lock origin still yields lock_origin, including when a denial is appended. |

## Follow-up 1: genuine band unknowns are flagged

Against the actual Trial 23 KINJ-01 evidence, each of these returns `band_reachability` although it asserts no reachable rejection:

1. `Whether rule 42's band check can fire is unknown because the intervening dispatcher lines were not read.`
2. `I do not know whether rule 42's band check can fire; the path between the calls was not read.`
3. `Whether rule 42's band check ever fires is unknown.`

The retained dispatcher reads are sparse (2425–2436 and 2530–2570); the new finding itself tells the model to leave reaching rule 42 unchanged unknown. These statements follow that instruction. Flagging them can cause an unnecessary revision and then the new withheld fallback, replacing a properly cautious answer.

In `read_coverage.nested_band_claims`, `_FIRES` matches “can fire” or “ever fires” without determining whether the expression is the object of a genuine unknown/open question. It treats an unresolved question like the original affirmative “only test that can ever fire.” The issue is separate from arithmetic extraction.

Required action: distinguish genuinely open/negated firing questions from affirmative reachability claims or assertions that data settles firing. Add the three controls above. Preserve these adversarial negatives:

- `Rule 42's only rejection test that can ever fire on a value reaching it is its band check at lines 852 and 855.`
- `Whether negative data exists decides whether the band tests at lines 852/855 ever fire.`
- `Rule 42's band check can fire, but the rest of the dispatcher is unknown.`

All three currently flag correctly. Do not exempt an affirmative assertion just because another subclause contains “unknown.” The mixed positive `Rule 42's band check cannot reject an unchanged value that passed rule 001, but whether it can fire after unread intervening logic is unknown.` already passes and should remain a control.

## Follow-up 2: header-only source read bypasses job-intervention grounding

Start with the actual eight DIAG-02 lookups: failed run, job detail naming `ORDER_FEED.EXPORT_DAY`, unresolved ORDER_FEED and source searches with no rows. Add one source-range result:

```text
operation: schema.object_source_range
parameters: owner=HARNESS_APP, object_name=ORDER_FEED,
            object_type=PACKAGE BODY, start_line=1, end_line=1, row_limit=200
columns: LINE, TEXT
rows: [1, "PACKAGE BODY order_feed AS"]
```

Now `The job itself needs no change.` produces **no findings**. Without that header-only result it correctly produces `job_intervention`.

`claim_checks._source_read` returns true on any nonempty source lookup whose `object_name` matches a JOB_ACTION name component. It does not require the action's implementation or relevant configuration to be returned. A package declaration establishes neither EXPORT_DAY's contents nor that the job/action/destination configuration needs no change. This is the same unsupported intervention conclusion with one irrelevant line added, not a resolved diagnosis. The submitted regression test `test_with_the_action_source_read_the_claim_is_not_judged` explicitly uses this header-only shape as its positive; that assumption needs correction.

Required action: do not treat a header, spec/member metadata or sparse unrelated lines as sufficient evidence to exempt a categorical no-change claim. At minimum retain the guard when the named action implementation remains unread, and keep unresolved schema-versus-package interpretation explicit. Even reading an implementation does not by itself prove operational configuration is correct; exemption must reflect evidence relevant to the conclusion, or leave intervention necessity unknown. Add this exact retained-facts-plus-header negative, a partial/irrelevant-source negative, and genuine observed-state/open-question positives. The independent package_subprograms-only extension already continues to flag the overclaim and should stay that way.

The original sentence `What the evidence does establish is that no intervention on the job itself is needed.`, `The job itself needs no change.`, named-job/no-change and does-not-need-intervention variants all correctly flag with the original retained facts. `The job is enabled and not disabled in this result.` and `Whether the job definition or action needs changing is unknown.` both pass. The correction should preserve those results.

## Bounded repair and fallback limits

The anchor check is metadata-based structural validation, not compilation. Codex did not compile the repaired DRAFT-03 unit on D1 during this pure-Python review. A future released diagnostic must compile the actual delivered proposal. The handoff records that a failed anchor repair keeps the first proposal and exposes `failed:anchor`; no guarantee that every delivered proposal compiles is established here.

The eight independent service probes verify:

- the exact Trial 23 proposal gets two identified anchor problems, one repair call and a corrected proposal;
- `revision_failed:wall_time`, `revision_failed:provider_failure`, `revision_failed:empty` and `revision_failed:tool_request` withhold the unsupported diagnosis;
- `skipped:revisions` and `skipped:steps` also withhold it;
- the successful path clears interim notes and delivers the corrected standalone answer.

These checks use the real service with the stand-in and scripted provider; no live model was sampled. Earlier protocol clients still retain drafts under `skipped:protocol`, as disclosed in the handoff. Rendering errors and a successful revision that still has findings are not covered by a blanket “flagged drafts can never appear” guarantee. The accepted change is the assigned unsuccessful/refused-revision path, not universal claim correctness.

## Release decision

**Q5 Trial 24 remains held.** Return the two follow-ups and their positive/negative controls for independent verification. Accepted anchor repair, withholding paths, scoped claims and retained false-positive corrections need not be redesigned.

No new provider diagnostic, unchanged-candidate resampling, candidate freeze or Q6 A/B is authorized by this review. Once the follow-ups pass independent checks, Track B can release one fresh local-Strata 18-case diagnostic with unchanged model/cases/rubrics/budgets, untouched pre-review output, served identity/fingerprints, raw evidence and revision records, D1 proposal compilation/cleanup and fresh review of every case. Prior Trial 23 acceptance remains 14/18. K-7 remains NOT QUALIFIED, Kiwi off by default, K8 gated. All historical task sections are retained.
