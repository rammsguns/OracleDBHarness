"""C30 prototype: the ``read_range`` check, replayed against retained K-7 answers.

Not wired into the harness. C30 proposed four checks for derived claims; the C28-C30
review accepted three for hardening, and they are now ``remedy_alternative``,
``elapsed_bound`` and ``row_grouping`` in ``claim_checks.py`` (C31). This one stays here:

- ``read_range``: a numbered range said to have been read ("rules 39-44") where the
  lookups returned no member header with one of those numbers.

It flags an accepted trial-17 statement about dispatcher *call* lines as though it
claimed to read member *bodies*, so it cannot ask for a revision until it tells those two
source scopes apart (C28-C30 review). KINJ-01's unread-floor error is left to grounding
review; nothing here corrects it.

Usage: ``python copilot-eval/derived_claims_prototype.py [REPORT ...]`` prints each
flag with its case's recorded grounding verdict, over every retained Strata report by
default.
"""

from __future__ import annotations

import glob
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace

from harness_api.copilot.claim_checks import LookupFacts
from harness_api.copilot.support import evidence_map, prose_lines


@dataclass(frozen=True)
class Flag:
    kind: str
    claim: str
    problem: str


# -- a numbered range said to have been read ---------------------------------------------

_READ_RANGE = re.compile(
    r"\b(?:I|we)\s+(?:have\s+)?read\b[^.;]{0,40}?\b(\d{1,5})\s*(?:–|—|-|to|through)\s*(\d{1,5})\b"
    r"|\b(\d{1,5})\s*(?:–|—|-|to|through)\s*(\d{1,5})\b[^.;]{0,20}?\b(?:I|we)\s+(?:have\s+)?read\b"
    r"|\b(?:\w+\s+)?(?:I|we)\s+read\s+(?:are|were|is)\s+(\d{1,5})\s*(?:–|—|-|to|through)\s*"
    r"(\d{1,5})\b",
    re.IGNORECASE,
)
_NUMBERED = re.compile(r"^(.*?)(\d+)$")
_MAX_RANGE = 200


def read_ranges(answer: str, facts: Sequence[LookupFacts]) -> list[Flag]:
    # Members whose header line a source lookup returned, by name stem and number.
    read: dict[str, dict[int, str]] = {}
    for evidence in evidence_map(facts).values():
        for name in evidence.member_names():
            numbered = _NUMBERED.match(name)
            if numbered:
                read.setdefault(numbered.group(1), {})[int(numbered.group(2))] = name
    if not read:
        return []
    flags: list[Flag] = []
    for line in prose_lines(answer):
        for match in _READ_RANGE.finditer(line):
            low, high = (int(g) for g in match.groups() if g is not None)
            if not 0 < high - low <= _MAX_RANGE:
                continue
            said = set(range(low, high + 1))
            for stem, members in read.items():
                if not said & set(members):
                    continue  # not this family of members
                missing = sorted(said - set(members))
                if not missing:
                    continue
                width = max(len(_NUMBERED.match(n).group(2)) for n in members.values())
                unread = ", ".join(f"{stem}{n:0{width}d}" for n in missing[:5])
                flags.append(
                    Flag(
                        "read_range",
                        line[max(0, match.start() - 60) : match.end() + 40],
                        f"the lookups returned the header of {', '.join(sorted(members.values()))}"
                        f" but not of {unread}{' and others' if len(missing) > 5 else ''}: "
                        "a statement about the members read cannot include those.",
                    )
                )
                break
    return flags


def review(answer: str, facts: Sequence[LookupFacts]) -> list[Flag]:
    return read_ranges(answer, facts)


# -- replay -------------------------------------------------------------------------------


def facts_of(case: dict) -> list[LookupFacts]:
    facts: list[LookupFacts] = []
    for entry in case.get("evidence") or []:
        columns: list = []
        rows: list = []
        try:
            body = json.loads(entry["content"].splitlines()[1])
            columns, rows = body.get("columns", []), body.get("rows", [])
        except (IndexError, ValueError, KeyError, AttributeError):
            pass
        facts.append(
            LookupFacts(
                entry.get("operationId", ""),
                entry.get("status") == "succeeded",
                columns,
                rows,
                entry.get("parameters") or {},
                entry.get("lookup", 0),
                row_count=entry.get("rowCount"),
                truncated=bool(entry.get("lookupTruncated")),
            )
        )
    return facts


def grounding(case: dict) -> str:
    review_ = case.get("review") or {}
    value = review_.get("grounding")
    return (value.get("verdict") if isinstance(value, dict) else value) or "unreviewed"


def main(paths: Sequence[str]) -> int:
    paths = paths or sorted(
        p for p in glob.glob("copilot-eval/strata-k7-*.json") if ".pre-ai-review" not in p
    )
    answers = 0
    tally: dict[tuple[str, str], int] = {}
    for path in paths:
        report = json.loads(open(path, encoding="utf-8").read())
        for case in report.get("cases", []):
            answer = case.get("answer") or ""
            if not answer:
                continue
            answers += 1
            verdict = grounding(case)
            for flag in review(answer, facts_of(case)):
                tally[(flag.kind, verdict)] = tally.get((flag.kind, verdict), 0) + 1
                claim = " ".join(flag.claim.split())[:120]
                print(f"{path.rsplit('/', 1)[-1]} {case['id']} [{verdict}] {flag.kind}: {claim}")
    print(f"\n{answers} answers replayed")
    for (kind, verdict), count in sorted(tally.items()):
        print(f"  {kind:20} grounding {verdict:10} {count}")
    return 0


__all__ = ["Flag", "facts_of", "review", "replace"]

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
