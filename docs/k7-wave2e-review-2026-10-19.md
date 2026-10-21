# Sync Point 2E independent review — 2026-10-19

**Changes requested. Q5 Trial 23 is not released.** The exact Trial 22 defects now trigger the intended checks, but independent variations expose a remaining KINJ-01 mandatory-DDL bypass. Review serialization meets the runner-format requirement demonstrated below, but does not guarantee literal outside-review byte preservation for other valid JSON encodings. All five items are therefore not verified as requested.

This reviews Claude's completed Wave 2E handoff in `claude_k7.md` (section beginning at line 2526), against the active Sync Point 2E assignment and the Trial 22 independent audit. No copilot implementation/test files, historical report, live provider or database were modified/called by Codex. All new executable probes and report copies were under `/tmp`.

## Validation

`.venv/bin/pytest -q tests/copilot tests/integration/test_contracts.py tests/unit/test_bootstrap_targets.py`: **1275 passed in 97.50 seconds**.

- Ruff: `.venv/bin/ruff check services tests scripts copilot-eval/apply_trial_reviews.py` passed.
- `git diff --check` passed before documentation changes; repeated after them.
- Three independent service controls passed (`/tmp/test_wave2e_independent.py`, using `tests.conftest`): bounded minimal-repair delivery, and full-answer replacement for two indirect-neighbour interim notes.
- Twenty-four independent claim controls against retained Trial 22 raw evidence: **21 passed, 3 failed** (`/tmp/probe_wave2e.py`). Facts were reconstructed from each evidence record's operation, status, columns, rows, parameters and lookup number, and evaluated through production `_kiwi_review`.
- Additional full-answer replays and call-site controls: `/tmp/probe_wave2e_more.py`.

These scripted tests do not measure the probability that a live model produces an acceptable new trial. Existing Trial 22 scores remain unchanged.

## Results by item

| Item | Independent result |
| --- | --- |
| FIX-04 | Accepted in assigned scope. The two-variable rewrite produces three minimal-repair problems (deleted scalar, rewritten SELECT INTO, rewritten RETURN). A scripted service reply triggers exactly one repair and delivers the widened original variable with the original statements. The minimal widened proposal passes the structural check. |
| DIAG-01 | Accepted in assigned scope. The original full answer now yields `schema_scope`; only/unique/no-other-table variants are flagged against the actual prefix-filtered listing and single EMPLOYEES column lookup. Positive table-local and inspected-object-scoped claims pass. |
| DIAG-03 | Accepted in assigned scope. The original full answer yields `lock_origin`. Because/since/as and inverted so/therefore/thus variants are flagged. Separate Finding/Unknown statements preserving earlier-lock uncertainty pass. |
| KINJ-01 | Partially accepted; changes required. Original indirect comparisons and original mandatory-change remedy are flagged. Service revisions remove all superseded interim text. Mandatory-DDL variants mentioning REASON incorrectly pass, detailed below. |
| Review serialization | Actual Trial 22 pre-review scratch copy and fresh runner-format non-ASCII report preserve outside-review bytes and keep metadata in a sidecar. Escaped-Unicode and compact-JSON variants fail literal preservation; see scope and required action below. |

## Remaining KINJ-01 failure

The actual retained table-column lookup includes `REASON VARCHAR2(200)`. Each of these unsupported mandatory changes returns **no findings** through `_kiwi_review` with the actual KINJ-01 lookup facts:

1. `Recording the rule label requires a new column despite REASON VARCHAR2(200) already existing.`
2. `REASON currently stores generic messages, so recording the rule label requires a table change.`
3. `Recording the rule label requires a new column; the existing REASON field can store text.`

Expected: `required_change`. Mentioning the existing column does not establish that a new column is mandatory. In `claim_checks._required_changes`, `if any(c[2] in named for c in held): continue` exempts the claim whenever a suitable existing column is named in the sentence. This bypass is independent of table names and reproduces with retained evidence rather than a fabricated schema.

Required action: distinguish an explicit proposal to use/weigh the existing field from an assertion that DDL is necessary despite that field. Do not use mere identifier presence as the exemption. Add these three negative controls and retain positive controls for `Store the rule label in REASON; a dedicated column is optional.` and `Recording the rule label could use a new column.` Both positives pass currently. The straightforward mandatory-change assertion without REASON also correctly produces `required_change`.

Other KINJ-01 checks passed: both original indirect-neighbour sentences, a `check_rule_042`/surrounding-procedures variation, and a comparison with an unrelated unknown caveat produce `read_scope`; genuinely open and line-local statements pass. The original full answer produces findings for both interim comparisons and required-change assertions. An every-call-site change beyond the read caller is flagged, while a change explicitly confined to lines 2530–2565 passes.

Two independent service probes use unsupported interim notes followed by an already-scoped final diagnosis, then a corrected revision. In both, the revision draft equals the complete interim-plus-final text and the reconstructed delivered answer starts with the revision and contains no surviving interim overclaim.

## Serialization scope and remaining limitation

`apply()` still parses and reserializes the entire report with `json.dumps(indent=2, ensure_ascii=False)`. This fixes Trial 22's observed runner-format Unicode escaping defect: a scratch copy of the actual pre-review report passes outside-review masking, as does a newly constructed non-ASCII report in that format. No historical file was rewritten.

Independent variants use the same object with answer `héllo — ✓`, case answer `café`, and an empty review. The mask independently locates each review value via `JSONDecoder.raw_decode` and replaces only that value span:

- UTF-8, indent 2, runner newline: **pass**.
- Indent 2 with escaped Unicode (`ensure_ascii=True`): **fail**; outside-review Unicode escapes are rewritten.
- Compact UTF-8 JSON: **fail**; outside-review whitespace changes.

Thus the current implementation is acceptable for the runner's current canonical encoding, but its unconditional byte-identity claim is too broad. The active assignment requests byte identity outside review spans. Required action for that guarantee: replace only the parsed `cases[*].review` value spans in the original text, preserving all other bytes; alternatively explicitly validate the required canonical input format and fail before writing unsupported input. Keep sidecar metadata and untouched pre-review backups. Add escaped-Unicode and compact input controls, including non-ASCII text. Do not normalize or rewrite retained Trials 1–22 to make comparisons pass.

## Release decision

Return the mandatory-change correction and serialization guarantee/explicit input validation for independent follow-up. **No Q5 Trial 23 release, unchanged-candidate resampling, candidate freeze or Q6 A/B is authorized by this review.** Accepted portions need not be redesigned. Once the remaining controls and full checks pass, Track B can release one fresh 18-case local-Strata diagnostic with unchanged cases/rubric/budgets, retained raw evidence and served identity, D1 proposal compilation/cleanup, and fresh review of all 18 cases. Q6 still requires independently accepted Q5 18/18 and a separate clean freeze. K-7 remains NOT QUALIFIED, Kiwi off by default, K8 gated.
