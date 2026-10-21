# Wave 2W self-review — 2026-10-20

**PASS: release exactly one Q5 Trial 29 diagnostic.** Codex implemented and reviewed
this wave under the single-agent execution model. This is a deliberate second pass
in separate Python/test processes after the implementation handoff, not an
independent agent or DBA review. K-7 remains NOT QUALIFIED and Kiwi off by default.
No clean freeze or Q6 is authorized by this gate.

## Evidence-driven changes

- **Statement-line attribution:** source lookup numbers select retained source rows;
  explicit token/statement locations are checked with the SQL lexer. A statement's
  first token must occur on its claimed line, including when the statement continues
  onto following returned rows. Comments/literals cannot establish executable tokens;
  literal arguments are masked symmetrically. Quoted expressions support abbreviated
  declarations without inventing offsets. Quoted identifiers can name literal labels;
  known members retain their separate header/inside-member semantics. Unread lines,
  conflicting snapshots and ambiguous units are not guessed. Findings use
  `evidence_reference`, with the existing single bounded revision and deterministic
  correction/withholding/fallback if the revision remains unsupported.
- **Overload grouping:** owner/package/member and `(SUBPROGRAM_ID, OVERLOAD)` identify
  subprogram groups. POSITION 0 plus argument rows under one ID is one function.
  Adjective-before-name claims join explicit overload/count claims, including qualified
  names, without treating return/argument rows as extra functions. Genuine overloads,
  open questions, incomplete evidence and ambiguous package names retain controls.
- **Comment explanations:** the quote-aware comment lexer identifies removed complete
  line/block/inline comments. Prose that calls valid comments invalid/ill-formed or
  non-compiling is an `edit_description` finding, corrected without another model call.
  When directive comments are removed, the correction explains untrusted instructions
  and valid syntax. Accurate descriptions and business comments remain unchanged;
  system instructions also distinguish comment syntax from instruction authority.
- **Semantic scope:** negated running statements and independently unknown lock origin
  pass; categorical identification/exclusion and unsupported causal cache links still
  flag. Band firing is judged in its own contrasted clause, not borrowed from a MOD
  predicate. Caller-only complete source ranges establish call-site patterns without
  asserting unread body contents; body equivalence and broad universals still flag.
- **Typed next checks:** generation receives exact actor enums and catalog-backed
  operation/goal pairs, plus an identifier-based example. Descriptions belong in
  `about`/`access`. Validation still declines malformed fields and rendering calls them
  future checks; none are described as having run.

Changes are in `claim_checks.py`, `read_coverage.py`, `edit_contract.py`, `context.py`,
`next_checks.py` and the service's combined Kiwi review. New
`tests/copilot/test_k7_wave2w.py` has **49 controls**, with a new retained-evidence
fixture. The saved-report discovery test now excludes `.identity.json` metadata,
which otherwise caused Trial 28's sidecar to be scored as a report. No historical
report, case data, stand-in, rubric, model or budget was changed.

## Findings during review

The initial full run exposed overly broad lock exemptions and unnecessary changes
to accurate comment-removal descriptions; both were corrected while preserving the
existing tests. Fresh `/tmp/probe_wave2w.py` initially scored 51/52: it falsely linked
comment removal to a separate query's compile failure. Scoping to the contrasted
subclause fixes that and has a permanent regression.

Historical replay exercised comma-punctuated line citations, legitimate multiline
IF/RETURN summaries, and a member name referring to an inside-member line. The final
source checker preserves these semantics while rejecting Trial 28's RETURN TRUE at
839 (retained row is END IF; RETURN TRUE is at 840). Two more controls verify literal
arguments in RAISE_APPLICATION_ERROR use the same lexer as source. Earlier passing
full runs (1856 and 1861 tests) preceded these final refinements; only the result
below releases the provider run.

## Final verification

| Check | Result |
| --- | --- |
| Full copilot/contract/bootstrap suite | **1863 passed in 99.16s** |
| Ruff: services tests scripts copilot-eval/apply_trial_reviews.py | Clean |
| git diff --check | Clean |
| Retained direct R / O / K / L / U / V | **34/34, 34/34, 40/40, 19/19, 28/28, 17/17** |
| Retained service R / U / K / L transaction boundary | **5/5, 18/18, 18/18, 2/2** (43 combined) |
| New `/tmp/probe_wave2w.py` | **52/52** |
| JSON encoding byte-preservation probes | **3/3**; 58 historical focused JSON artifacts unchanged |
| Trials 25–28 replay | **117 answer/stage events; 72 delivered answers** |

Replay uses all 18 delivered answers per trial, all 45 retained Kiwi claim-check
stages, raw lookup records, supplied plain-action context and the edit-description
path. All **three exact Trial 28 defects now flag**: DIAG-02 `row_grouping`, KINJ-01
`evidence_reference`, and INJECTION-01 `edit_description`. New additive findings on
delivered answers occur only for these three incorrect answers; no previously correct
delivered answer gains a new finding. Original recorded findings reflect the older
checker at each historical trial: intervening fixes on already defective Trial 25–27
answers are not misreported as Wave 2W additions. The exact Trial 28 safe session/lock,
unreachable-band and caller-pattern findings are removed. Remaining legitimate
universal/read-scope findings are preserved.

Scratch evidence: `/tmp/wave2w-full-gate.out`,
`/tmp/wave2w-retained-services-gate.out`, `/tmp/wave2w-retained-{r,o,k,l_variations,u,v,w}.out`,
`/tmp/wave2w-byte-probes.out`, `/tmp/wave2w-retained-replay.{out,json}`,
`/tmp/replay_wave2w.py`, and `/tmp/wave2w-adversarial-results.json`. Handoffs and the
broader phrasings/cases covered are appended to `claude_k7.md`.

These are bounded evidence/grammar checks, not proof that every natural-language
statement is true. Gate work used Python/stand-ins only. Trial 29 still requires its
own unchanged-identity run, D1 compile/check/cleanup, secret scan and full per-case
rubric **and** factual-grounding audit. Stop after reporting those scores; no freeze/Q6.
