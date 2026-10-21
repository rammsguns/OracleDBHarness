# K7 C24 second-pass review

Reviewed by Codex (AI), 2026-10-18, against Claude's second-pass design and
implementation, `support.py`, claim checks, source-record rendering, tests and
the retained trial-18 DIAG-02 evidence.

**The previous five C24 reproductions are fixed. C24 remains P1/open for the
three support-validation gaps below. Q5 is not ready; K7 is NOT QUALIFIED.**
The typed evidence map and per-item associations are useful progress. Keep that
work and the accepted C21/C22/C23/C25/C26 fixes; a wholesale restart is unnecessary.

## Findings

### C24-A: source-line citations ignore identity and accept partial overlap

In `claim_checks.py:1008`, `_lines_unread()` receives line numbers but no object
identity. Line 1015 accepts any intersection with any returned source range.
The Evidence-section branch then skips further identity checking for that item.

Independent reproduction: use `PKG_MEMBER` from `test_k7_support.py`, which contains
only lines 10–13 of APP.PKG's body. Both answers produce **no findings**:

```text
## Evidence
- Lines 10-99 of APP.PKG show the complete implementation.

## Evidence
- Lines 10-13 of OTHER.UNREAD show the implementation.
```

The first claims unread lines; the second cites another object's source. This
reintroduces the scope problem through the new line-citation path.

**Acceptance:** bind line references to the identified owner/unit/type and require
the whole cited interval to be covered by compatible returned ranges. Several
lookups may jointly cover a range of the same unit; unrelated units cannot fill its
gaps. An ambiguous unqualified reference needs explicit unresolved treatment, not
an arbitrary matching line number. Add supported exact-range, split-range,
missing-middle, spec/body and wrong-unit controls at the service boundary.

### C24-B: a comment is treated as the end of a member implementation

`support.py:268` identifies members and their ends with line regular expressions
without tracking comments or literals. `_members()` marks a member whole when a
matching `END name;` line occurs, including inside an unfinished block comment.
This affects both claim checking and factual text rendered by the harness.

Independent input for `schema.object_source_range`, APP.PKG PACKAGE BODY,
start_line 10, end_line 13:

```text
10  PROCEDURE calc IS
11  BEGIN
12  /*
13  END calc;
```

The rest of the unit was not returned. Actual `record_entry(evidence_map(...)[1])`
contains **`CALC: implementation from line 10, read whole`**. The answer
`The fix belongs in APP.PKG.CALC.` produces no finding. The apparent ending is
comment text; it does not establish completion of the implementation.

**Acceptance:** only establish whole-member coverage when lexical and structural
boundaries are supported and all required lines were read. Ignore comment/literal
contents when finding declarations and endings. For syntax or starting context
the reader cannot establish, report coverage as unknown/partial rather than
claiming whole or asserting that a real ending is absent. Cover block comments,
quoted text, nested declarations and actual complete/partial implementations.
Verify the harness's `sourceRead` display as well as revision behavior. This does
not require a universal PL/SQL parser; it requires honest coverage of the syntax
the implementation supports.

### C24-C: value and citation-attachment fallbacks bypass identity validation

At `claim_checks.py:971`, a dotted name is accepted whenever it occurs inside any
returned string. The comment says this represents a quoted value, but the code
does not require a value quotation or bind it to a field.

Independent evidence: lookup 1 is `dba.scheduler_job_detail`, owner APP, job EXPORT,
columns OWNER/JOB_NAME/JOB_ACTION, row `APP, EXPORT, OTHER.RUN_EXPORT`.

| Answer | Actual result |
| --- | --- |
| `Lookup 1 shows JOB_ACTION ` followed by backticked `OTHER.RUN_EXPORT` | No finding, correctly: this quotes the returned action value |
| `Lookup 1 shows OTHER.RUN_EXPORT is VALID.` | No finding, incorrectly: no object status was returned |

A second reproduction uses `PKG_STATUS` from `test_k7_support.py`:
`As lookup 1 shows, OTHER.UNREAD is VALID.` also produces no finding.
`support.py:387` attaches the citation to the preceding word `As`, leaving the
following attributed statement unchecked. The simpler `Lookup 1 shows ...` control
is fixed, but this equivalent attribution is not.

**Acceptance:** preserve the distinction between a field value and an object subject
through validation. A quoted job action can support a statement about JOB_ACTION;
its appearance cannot establish the called object's status or implementation.
Remove blanket substring support. Attach explicit leading/trailing citations to
their claims, and report unparsed attribution as unresolved instead of treating it
as checked. Add the two controls above, comments/error strings containing dotted
names, and leading/trailing/multiple-citation controls. Keep legitimate quotations
and correct supported citations passing. A valid association still does not prove
the natural-language claim true; semantic review remains separate.

## Verified progress and retained-answer judgment

- Independently reran the prior five pure reproductions: APP.WEEKLY is no longer
  bound to NIGHTLY; an unread member is flagged; APP.UNREAD and OTHER.UNREAD status
  claims are flagged; lookup 9 with no lookups is flagged.
- The integrated suite includes supported counterparts, mixed-target coverage,
  specification versus body, source ranges, revisions and record rendering.
- The new map records identities, object types and source coverage from lookup
  evidence. The defects above concern how that information is validated, not a
  request to discard the map or implement a general natural-language theorem prover.
- Claude's flagged **trial-18 DIAG-02** citation is a reasonable citation-quality
  warning: JOB_ACTION is in lookup 2, whereas the Finding ends with lookups 1/3,
  which support the failed-run facts. Lookup 2 is correctly cited in the Evidence
  bullet. Adding it to the Finding improves attribution. The job action itself was
  grounded in retained evidence; do not describe this as an invented database fact
  or silently change the historical pass. Preserve the original report and record
  any additional attributed review note separately.
- Claude now labels earlier implementation descriptions as historical and links
  the previous review. Preserve the implemented/accepted/provider-demonstrated
  distinction when updating the current checkpoint.

## Independent checks

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest tests/copilot tests/integration/test_contracts.py -q --tb=short` | **766 passed**, 67.31 seconds |
| Web `npm run typecheck` | Passed |
| Web `npm test -- src/views/CopilotDrawer.test.tsx` | **15 passed**, existing React act warnings |
| `.venv/bin/ruff check .` | Passed |
| `git diff --check` | Passed |
| Case validation | Passed, 52 cases, 46 dispatch, correctness 18, safety 19 |
| Pure support-map/claim-check probes described above | Prior five fixed; five new answer probes return no findings, and comment probe falsely renders read whole |

Claude's whole-repository **1304 passed / 83 skipped** and full web **68 passed /
3 skipped** results were not independently rerun. No real Strata or live Oracle
calls were made. No product source or historical trial result was edited by Codex.

## Next checkpoint

Resolve C24-A/B/C, retain the corrected prior controls, and refresh Q4 with service
tests plus fresh independent variations. Keep unsupported parsing explicitly
unresolved and all drafts/findings reviewable within the existing request bounds.
Then seek the next Q3/Q4 acceptance checkpoint before Q5.

The last reviewed Strata diagnostic remains trial 18, **15/18**. Use the same local
`qwen3.8-flash-next-iq3_s` on port 8080 when ready. Case set 2026-10-18.1 and SHA
`dc9ac9a3b54ecad9e40842a1c2dfda09c496709d8c6b55c1658f2e074426598f` stay fixed.
Qualification still requires two consecutive complete 52/52 runs on a clean
candidate, fresh required reviews and separate grounding checks. Kiwi remains OFF
by default; K8 remains gated.
