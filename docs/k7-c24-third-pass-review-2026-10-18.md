# K7 C24 third-pass review

Reviewed by Codex (AI), 2026-10-18, against Claude's third-pass implementation,
source, tests and independent probes. This follows the
[second-pass review](k7-c24-second-pass-review-2026-10-18.md).

**The prior exact A/B/C probes are corrected. C24-C closes in its assigned scope;
C24-A/B remain open for coverage issues below. New C27 is P1/open. Q5 remains
blocked on implementation readiness; K7 is NOT QUALIFIED.**

## C27 — P1: model-written line ranges cause unbounded allocation

At `support.py:178`, `Evidence.quotes()` expands every cited interval into a set:

```python
cited = {k for low, high in line_references(text) for k in range(low, high + 1)}
```

`line_references()` does not cap the interval. This allocation happens even for
a job lookup with no source rows. The 100,000-line guard in `_covered()` does not
protect this separate path. Checker execution is synchronous, so a provider-stream
deadline does not bound the allocation or CPU work.

Independent reproduction: lookup 1 is `dba.scheduler_job_detail`, APP.EXPORT,
columns OWNER/JOB_NAME/JOB_ACTION, row `APP, EXPORT, OTHER.RUN_EXPORT`. Pass this
short answer to `review_claims()`:

```text
Lookup 1 shows OTHER.RUN_EXPORT is VALID at lines 1-100000000.
```

In an isolated Python process limited to 32 MiB of additional virtual memory, the
call raised **MemoryError**. A three-second alarm also bounded the probe. No large
allocation was attempted in a running API or Strata server. Without such a limit,
this path can consume substantial memory before exception containment helps.

**Assignment:** keep ranges as intervals; test returned line numbers for membership
without enumerating the model's interval. Bound all checker work derived from
untrusted numeric ranges and handle unsupported bounds explicitly. Audit adjacent
range-expansion paths, including number parsing and missing-line diagnostics.
Add large-span controls that finish promptly with bounded memory, alongside valid
single-line/range quotations. A catch for MemoryError alone is not a fix.

## C24-A — P1: member boundaries are accepted as contents coverage

`claim_checks.py:1148` accepts `(low, high) in extents` in the same `_covered()`
function that validates claims about returned source contents. This exception
does not inspect whether the statement describes only boundaries.

Independent lookup: `schema.object_source_search` for APP, returning exactly:

```text
NAME  TYPE          LINE  TEXT
PKG   PACKAGE BODY  10    PROCEDURE calc IS
PKG   PACKAGE BODY  20    END calc;
```

The source record correctly says lines **10, 20**, member **read in part**. But
this answer produces no findings:

```text
## Evidence
- Lines 10-20 of APP.PKG show every statement in CALC (lookup 1).
```

The nine intervening lines were not returned. The new
`test_a_members_extent_is_supported_by_its_first_line_and_its_end` also explicitly
expects an analogous content claim to pass, so green tests encode this gap.

**Assignment:** separate evidence of a boundary from evidence of contents. An
established first/end pair can support an appropriately limited extent statement;
it cannot satisfy source-content coverage. Keep the normal full-interval requirement
for content references, and expose unresolved statement interpretation rather than
silently granting full support. Replace the incorrect content-pass expectation
with a failing control, while retaining supported boundary-only and fully read
content controls. Respect lexical-context uncertainty from C24-B for boundaries too.

## C24-B — P1: arbitrary excerpts assume a trusted starting lexical state

At `support.py:301`, every scan starts with `state="code"` and
`started_in_code=True`. A later unmatched closing delimiter can invalidate that
assumption, but an excerpt wholly inside a comment has no delimiter to reveal it.

Independent range input for APP.PKG PACKAGE BODY, lines 10–13:

```text
10  PROCEDURE calc IS
11  BEGIN
12  NULL;
13  END calc;
```

These exact lines can be comment text, with `/*` at line 9 and `*/` at line 14,
neither returned. The current record nevertheless states **CALC read whole**, and
`The fix belongs in APP.PKG.CALC.` yields no finding. The same excerpt could also
be genuine code; the request's evidence alone cannot distinguish the two.

Claude documents this assumption, but the rendered record presents it as an
established fact. That conflicts with the assigned requirement to leave unknown
starting context unresolved. This is not a request for a universal PL/SQL parser.

**Assignment:** distinguish known lexical start from assumed start. Establish it
from an adequate contiguous prefix or trustworthy boundary evidence, or report the
member/boundary as unverified and do not use it to authorize a whole-member claim.
Gaps in source-search results must not implicitly carry lexical state across unread
lines. Add paired controls for identical excerpts inside and outside comments or
literals, with and without preceding context. Keep the existing open-comment and
literal regressions; update tests that assume arbitrary range starts are proven code.

## Accepted progress and limits

- Prior line-range overreach and wrong-unit probes now produce findings.
- The prior visible open-comment example now renders **coverage unknown**, not
  read whole, and produces a change-location finding.
- **C24-C closes in assigned scope:** a job action alone no longer supports the
  original VALID claim, the field quotation still passes, and `As lookup 1 shows`
  now associates the following wrong-object claim with the citation. Leading,
  trailing and multiple-citation regressions are in the passing suite. C27 is a
  separate resource-safety defect in the new quotation path.
- This closure is not general semantic certification. Claude explicitly discloses
  that naming a field can still allow a mixed value/object assertion. Every actual
  answer, including interim text, still needs the separate grounding review.
- C21/C22/C23/C25/C26 and earlier accepted fixes remain closed in assigned scope.
  The typed evidence map should be retained; no wholesale restart is requested.
- Retained-answer rescans are useful compatibility checks, not held-out proof.
  Do not generalize a boundary exception merely to suppress a warning on an old
  pass. Preserve old verdicts and distinguish citation quality from fabricated facts.

## Independent validation

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest tests/copilot tests/integration/test_contracts.py -q --tb=short` | **823 passed**, 70.78 seconds |
| Web `npm run typecheck` | Passed |
| Web `npm test -- src/views/CopilotDrawer.test.tsx` | **15 passed**, existing React act warnings |
| `.venv/bin/ruff check .` | Passed |
| `git diff --check` | Passed |
| Case validation | Passed; version 2026-10-18.1, 52 cases, 46 dispatch, 18 correctness, 19 safety |
| Prior exact A/B/C pure probes | Corrected, including the valid quotation control |
| New sparse-contents and unknown-start probes | Both expose the coverage gaps above |
| Isolated large-range probe | MemoryError under the stated memory cap |

Claude's full backend **1361 passed / 83 skipped** and full web **68 passed /
3 skipped** counts were not independently rerun. No real provider/live Oracle call,
server restart, credential change, product-source edit or historical-result edit
was made by this review.

## Next checkpoint

Fix C27 first, then complete C24-A/B with honest coverage states and service/display
controls. Refresh Q4 and seek acceptance before Q5. Keep runtime limits, case hash,
rubric and model fixed. The last reviewed diagnostic remains trial 18, **15/18**.

When ready, Q5 uses existing local Strata `qwen3.8-flash-next-iq3_s` at port 8080.
Qualification still requires two consecutive full 52/52 runs on a clean candidate,
all required fresh reviews and separate grounding checks. Kiwi remains OFF by
default; K8 remains gated.
