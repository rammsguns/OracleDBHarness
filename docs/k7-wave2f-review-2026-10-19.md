# Sync Point 2F independent review — 2026-10-19

**Accepted in assigned scope. Q5 Trial 23 is released for one fresh diagnostic.** Both Wave 2F follow-ups pass independent verification: all 24 retained-evidence claim controls and all three requested JSON-encoding controls pass. This release supersedes the Sync Point 2E diagnostic hold; it does not qualify K-7 or release Q6.

Claude's completed handoff is `claude_k7.md`, “Wave 2F: Sync Point 2E follow-ups” (beginning at line 2640). Codex read the active Sync Point 2F assignment before verification. No implementation or copilot test files were edited. Probes and copied reports ran under `/tmp`; no Strata or Oracle call, historical report rewrite, commit or freeze was performed.

## Verification

- `.venv/bin/pytest -q tests/copilot tests/integration/test_contracts.py tests/unit/test_bootstrap_targets.py`: 1283 passed in 101.36 seconds.
- `.venv/bin/ruff check services tests scripts copilot-eval/apply_trial_reviews.py`: passed.
- `git diff --check`: passed before and after review/task documentation changes.
- `/tmp/probe_wave2e.py`: **24/24 retained-evidence claim controls passed**, plus minimal-repair structural assertions and three serialization controls.
- `/tmp/probe_wave2f_serialization.py`: **3/3 expanded independent byte-preservation controls passed**; exact compact UTF-8, escaped Unicode and runner-format UTF-8, with multiple case reviews, original backups and reviewer sidecars.
- `/tmp/probe_wave2e_more.py`: original full-answer replays still yield schema-scope, lock-origin and read-scope/required-change findings; unscoped every-call-site assertions are flagged while line-scoped changes pass. A scratch copy of the actual Trial 22 pre-review report preserves outside-review text. Its masking helper invocation was adapted to the helper's new module argument; no production change was needed.

## Mandatory-DDL follow-up

The 24-claim suite reconstructs `LookupFacts` from retained Trial 22 raw evidence (operation, status, columns, rows, parameters and lookup number) and calls production `_kiwi_review`. It includes five DIAG-01 schema-scope controls, seven DIAG-03 lock-origin controls, six KINJ-01 read-scope controls and six KINJ-01 mandatory-change controls.

All three prior failures now yield `required_change` against the retained suitable REASON text-column evidence:

1. `Recording the rule label requires a new column despite REASON VARCHAR2(200) already existing.`
2. `REASON currently stores generic messages, so recording the rule label requires a table change.`
3. `Recording the rule label requires a new column; the existing REASON field can store text.`

`Store the rule label in REASON; a dedicated column is optional.` and `Recording the rule label could use a new column.` still pass without findings. The straightforward mandatory-change negative control also produces the expected finding. Inspection confirms removal of the blanket existing-column-name exemption. Optional wording is still a heuristic and claim correction remains bounded; passing these controls is not a guarantee against every paraphrase.

## Span-preserving serialization follow-up

Inspection confirms `apply()` now locates only `cases[i].review` value spans and splices replacement values into the original report text instead of reserializing the complete document. Metadata remains in `.ai-review.json`.

Independent fixtures contain `héllo — ✓` and `café`. An independent regex locates their review keys, and `JSONDecoder.raw_decode` determines each value's exact end; masking replaces only those values, then compares UTF-8 bytes. Fixtures contain review keys only in the cases, so this mask does not reuse the implementation's path scanner. Each fixture contains two case reviews and retains an existing review field.

| Encoding | Outside-review bytes | Backup / sidecar / parsed reviews |
| --- | --- | --- |
| UTF-8, indent 2, runner newline | Identical | Passed |
| Escaped Unicode, `ensure_ascii=True`, indent 2 | Identical | Passed |
| Compact UTF-8, `separators=(",", ":")` | Identical | Passed |

The original 2E encoding probes also all pass. The expanded probes verify no top-level `aiReview`, exact backup bytes, sidecar reviewer metadata and retention of an existing review field. These results establish the three requested UTF-8 JSON representations; they do not claim coverage of every possible text encoding or newline convention.

All 39 historical focused-report JSON files hashed before the expanded probes retain their hashes afterward. The Trial 22 reviewed, pre-review and sidecar hashes also match the previously recorded independent audit:

- Reviewed: `68f3583593f8ce6c2f8e897d2076c39d38d32bcb88d13c36a8dace9586971d2d`.
- Pre-review: `c6d434aa505bebfc4af4abb329797a7846799c26e506bca34c31ceac222de3f5`.
- Sidecar: `dcec8595035687b601d91cc3faa659dcd0e4417b3f98b714377642a917977533`.

## Release and boundaries

**Track A may run one fresh Q5 Trial 23 18-case diagnostic** on local Strata `qwen3.8-flash-next-iq3_s` at `http://127.0.0.1:8080/v1`, with unchanged cases, rubrics and budgets. Retain untouched pre-review output, served model identity, case-set hash `dc9ac9a3b54ecad9e40842a1c2dfda09c496709d8c6b55c1658f2e074426598f`, stand-in hash `e457f49db26e3cee5d76744fe1a85b81249d2c34cbb9b3bf4f9c2ff5b2acba44`, raw lookup evidence and claim-check/revision events. Compile proposals on D1 in disposable schemas, retain compile/cleanup logs, and verify zero leftover K7Q_/K7P_ schemas. Review all 18 cases afresh against rubrics and evidence and return the handoff for independent audit. Report changes must be confined literally to `cases[*].review` values, with reviewer metadata in the sidecar.

No unchanged-candidate resampling, candidate freeze or Q6 A/B is released. Prior Trial 22 acceptance remains 14/18; these scripted controls do not replace a fresh model diagnostic. Q6 requires independently accepted Q5 18/18 and a separate clean freeze, including resolving untracked helper `.py` files before requiring `uncommittedChanges=False`. K-7 remains NOT QUALIFIED, Kiwi off by default, K8 gated.
