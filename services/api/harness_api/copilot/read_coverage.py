"""What a request's source lookups read, rendered from the lookups, and read claims (C34).

Trials 19 and 20 failed KINJ-01 on the answer's own account of what it read: "the rules
I read are 39-44" when the lookups returned only the last two lines of rule 39.
The evidence map (``support.evidence_map``) already knows which lines of which unit
came back and which members those lines show; the model's prose restated it, and got it
wrong. Here the harness writes that account itself:

- ``unit_coverage`` merges a request's source lookups per unit: the line runs returned,
  each member whose first line was read (as the evidence map lists it), and each member
  whose ``END name;`` was read without its first line (a *tail*: its start, declarations
  and constants were not read). A tail found in lines read without the lines before
  them may lie inside a comment opened earlier; it is listed as an apparent end.
- A member is shown read through its END only where that END is code: found with
  comments and literals blanked, from a known lexical state (line 1 of the unit, or a
  definition PL/Scope recorded). An ``END name;`` inside a comment or literal, or in
  lines read without the lines before them, is not an end (C34-A); the latter is
  rendered as an apparent END, with coverage unknown.
- ``render_coverage`` turns that into the "Lines read" section the harness appends to a
  Kiwi diagnosis, so the displayed coverage never comes from generated prose.
- ``read_claims`` flags a first-person read claim ("I read rules 39-44", "the checks I
  have read are ...", "I read check_rule_039") that names a member whose first line no
  lookup returned, or that says a member was read whole when it was not. A claim about
  lines ("I read lines 2520-2575, which show checks 032 to 050") is about lines, which
  the C24 line checks judge, and what those lines show is not a read claim.

Members are named in prose by name, or by number where the unit's members carry a
numeric suffix ("rules 39-44" for CHECK_RULE_039 ... CHECK_RULE_044). That mapping uses
only the names the lookups returned and the noun the answer used; a number that maps to
no returned name, or to more than one, is left unchecked rather than guessed.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from harness_api.copilot.claim_checks import ClaimFinding
from harness_api.copilot.fences import fenced_blocks
from harness_api.copilot.support import (
    IDENTIFIER,
    Evidence,
    Member,
    Subject,
    clauses,
    code_lines,
    covers,
    line_ranges,
    number,
    prose_lines,
    subclauses,
)

# Clauses examined and numbers expanded per answer, so an adversarial answer costs
# bounded work (C27).
_MAX_CLAUSES = 400
_MAX_RANGE = 200


@dataclass(frozen=True)
class Tail:
    """A member whose ``END name;`` was read without its first line."""

    name: str
    first_line: int  # the first line returned of it
    end_line: int
    apparent: bool  # read without the lines before it: may lie inside a comment

    def describe(self) -> str:
        span = (
            f"line {self.end_line}"
            if self.first_line == self.end_line
            else f"lines {self.first_line}-{self.end_line}"
        )
        shown = (
            f"{self.name}: only its end, {span}; its first line is before line "
            f"{self.first_line} and was not read"
        )
        if self.apparent:
            return f"{shown} (an apparent END: the lines before {self.first_line} were not read)"
        return shown


@dataclass(frozen=True)
class UnitCoverage:
    unit: Subject
    lines: tuple[int, ...]  # sorted, distinct
    members: tuple[Member, ...]
    tails: tuple[Tail, ...]
    lookups: tuple[int, ...]
    rows: tuple[tuple[int, str], ...] = ()

    def started(self, name: str) -> Member | None:
        """The member called ``name`` whose implementation's first line was read."""

        return next((m for m in self.members if m.name == name and m.implementation), None)

    def names(self) -> set[str]:
        return {m.name for m in self.members} | {t.name for t in self.tails}

    def through(self, member: Member) -> int | None:
        """The line of ``member``'s ``END name;``, when the lines from its first to that
        END were all returned and that END is code (C34-A).

        The END must be found with comments and literals blanked, which is possible only
        where the lexical state is known: from line 1 of the unit, or from a definition
        PL/Scope recorded (``member.unknown`` is empty). An ``END name;`` inside a comment
        or literal, or after lines read without the lines before them, is not an end.
        """

        if member.unknown or not member.implementation:
            return None
        return self._end_in_code(member, self._lexed_from(member))

    def apparent_end(self, member: Member) -> int | None:
        """Where the lexical state before ``member`` is unknown, the line of an ``END
        name;`` that is code if line ``member.first_line`` starts in code. This is a
        condition, not coverage: it is rendered as such and never accepted as a read."""

        if not member.implementation:
            return None
        return self._end_in_code(member, member.first_line)

    def _lexed_from(self, member: Member) -> int:
        """The first line of the run holding ``member`` whose lexical state is known: line
        1, or else the first member of that run with a known state (PL/Scope's)."""

        text = dict(self.rows)
        start = member.first_line
        while start - 1 in text:
            start -= 1
        if start == 1:
            return 1
        known = [
            m.first_line
            for m in self.members
            if not m.unknown and start <= m.first_line <= member.first_line
        ]
        return min(known, default=member.first_line)

    def _end_in_code(self, member: Member, start: int) -> int | None:
        text = dict(self.rows)
        numbers = []
        line = start
        while line in text:
            numbers.append(line)
            line += 1
        code, _ = code_lines([text[n] for n in numbers])
        end = re.compile(rf"^\s*END\s+{re.escape(member.name)}\s*;", re.IGNORECASE)
        for n, line_code in zip(numbers, code, strict=True):
            if n > member.first_line and end.match(line_code):
                return n
        return None

    def describe(self, member: Member) -> str:
        if not member.implementation:
            return member.describe()
        end = self.through(member)
        if end is not None:
            return (
                f"{member.name}: implementation lines {member.first_line}-{end} returned, "
                "from its first line through its END"
            )
        last = member.first_line
        while last + 1 in dict(self.rows):
            last += 1
        apparent = self.apparent_end(member)
        if apparent is not None:
            # Only where the lines before were not read: a known state would have found it.
            shown = (
                f"{member.name}: implementation from line {member.first_line}, lines "
                f"{member.first_line}-{apparent} returned up to an apparent END; whether "
                "it was read whole is unknown"
            )
        else:
            shown = (
                f"{member.name}: implementation from line {member.first_line}, read in part "
                f"(lines {member.first_line}-{last}; its END was not returned)"
            )
        return f"{shown} ({member.unknown})" if member.unknown else shown


def unit_coverage(evidence: dict[int, Evidence]) -> list[UnitCoverage]:
    """Each unit a source lookup returned lines of, with what those lines read."""

    units: dict[Subject, dict[str, object]] = {}
    for number_, entry in sorted(evidence.items()):
        for read in entry.source:
            slot = units.setdefault(read.unit, {"rows": {}, "members": {}, "lookups": []})
            slot["rows"].update(read.numbered)  # type: ignore[union-attr]
            for member in read.members:
                slot["members"].setdefault((member.name, member.first_line), member)  # type: ignore[union-attr]
            slot["lookups"].append(number_)  # type: ignore[union-attr]
    coverage = []
    for unit, slot in units.items():
        rows: dict[int, str] = slot["rows"]  # type: ignore[assignment]
        members: dict[tuple[str, int], Member] = slot["members"]  # type: ignore[assignment]
        ordered = tuple(sorted(members.values(), key=lambda m: m.first_line))
        coverage.append(
            UnitCoverage(
                unit,
                tuple(sorted(rows)),
                ordered,
                _tails(unit, sorted(rows.items()), ordered),
                tuple(dict.fromkeys(slot["lookups"])),  # type: ignore[arg-type]
                tuple(sorted(rows.items())),
            )
        )
    return coverage


_END = re.compile(rf"^\s*END\s+(?!(?:IF|LOOP|CASE)\s*;)({IDENTIFIER})\s*;", re.IGNORECASE)


def _tails(
    unit: Subject, rows: Sequence[tuple[int, str]], members: Sequence[Member]
) -> tuple[Tail, ...]:
    """Members whose END is in a run of lines that does not contain their first line."""

    tails: list[Tail] = []
    started = {(m.name, m.first_line) for m in members}
    for run in _runs(rows):
        first = run[0][0]
        # Comments and literals can be blanked only where the run starts in known code.
        code = code_lines([t for _, t in run])[0] if first == 1 else [t for _, t in run]
        since = first  # the line after the last END: where an unstarted tail may begin
        for (n, _), text in zip(run, code, strict=True):
            found = _END.match(text)
            if found is None:
                continue
            name = found[1].upper()
            if name != unit.identity.name and not any(
                m == name and first <= at <= n for m, at in started
            ):  # not the unit's own END, and its start is not in this run
                tails.append(Tail(name, since, n, apparent=first != 1))
            since = n + 1
    return tuple(tails)


def _runs(rows: Sequence[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    runs: list[list[tuple[int, str]]] = []
    for n, text in rows:
        if runs and n == runs[-1][-1][0] + 1:
            runs[-1].append((n, text))
        else:
            runs.append([(n, text)])
    return runs


def render_coverage(evidence: dict[int, Evidence], limit: int = 30) -> str:
    """The "Lines read" section, written from the lookups alone."""

    blocks: list[str] = []
    for unit in unit_coverage(evidence):
        if not unit.lines:
            continue
        cited = ", ".join(str(n) for n in unit.lookups)
        noun = "lookups" if len(unit.lookups) > 1 else "lookup"
        out = [f"- `{unit.unit}`: lines {line_ranges(unit.lines, 12)} ({noun} {cited})."]
        described = [unit.describe(m) for m in unit.members] + [t.describe() for t in unit.tails]
        for text in described[:limit]:
            out.append(f"  - {text}")
        if len(described) > limit:
            out.append(f"  - and {len(described) - limit} more members")
        blocks.append("\n".join(out))
    if not blocks:
        return ""
    return (
        "## Lines read (from the lookups)\n\n"
        "Written by the harness from what the source lookups returned, not by the model.\n\n"
        + "\n".join(blocks)
    )


# -- read claims ----------------------------------------------------------------------

# "I read", "I have read", "we only read", "I've read".
_I_READ = re.compile(r"\b(?:I|we)(?:\s+have|'ve|\s+had)?(?:\s+only)?\s+read\b", re.IGNORECASE)
# "the rules I read are 39-44": the noun before the reader.
_NOUN_BEFORE = re.compile(r"\bthe\s+(\w+?)s?\s+(?:that\s+|which\s+)?$", re.IGNORECASE)
# Where the read's object ends: what the lines then show or call is not what was read.
_OBJECT_END = re.compile(
    r",\s*(?:which|that|so|but|and\s+so|where|showing)\b"
    r"|\s(?:which|that)\s+(?:shows?|lists?|calls?)\b|\s+but\b"
    r"|\s(?:shows?|showing|calls?|calling)\s|[;:—–]\s|\.\s|\(",
    re.IGNORECASE,
)
# A read of lines, calls or matches: what lines came back, which the C24 checks judge.
_LINES_OBJECT = re.compile(
    r"^\s*(?:only\s+)?(?:the\s+)?(?:source\s+)?(?:lines?|calls?|call\s+sites?|matches|references)\b",
    re.IGNORECASE,
)
_NOT_READ_BEFORE = re.compile(
    r"\b(?:lines?|calls?|call\s+sites?|matches|references)\s+(?:that\s+|which\s+)?$",
    re.IGNORECASE,
)
# "rules 002-040 not at all": a mention the claim says was not read.
_NEGATED = re.compile(r"^\s*,?\s*(?:not\b|none\b|unread\b|were\s+not\b)", re.IGNORECASE)
_WHOLE = re.compile(
    r"\b(?:whole|entire(?:ly)?|in\s+full|fully|all\s+of|complete(?:ly)?|every\s+line)\b",
    re.IGNORECASE,
)
_COPULA = re.compile(r"^\s*(?:are|were|was|is|:)\s*", re.IGNORECASE)
_NOUN_NUMBERS = re.compile(
    r"\b([A-Za-z]+?)s?\s+(\d{1,4}(?:\s*(?:-|–|—|to|through|,|and|&)\s*\d{1,4})*)",
    re.IGNORECASE,
)
_NAME = re.compile(rf"`?({IDENTIFIER})`?")
_SUFFIX = re.compile(r"^(.*?)_?0*(\d+)$")
_NOT_MEMBERS = frozenset({"LINE", "LOOKUP", "ROW", "STEP", "POSITION", "COLUMN"})


def read_claims(answer: str, evidence: dict[int, Evidence]) -> list[ClaimFinding]:
    """First-person read claims naming members the lookups did not read as claimed."""

    units = [u for u in unit_coverage(evidence) if u.lines]
    if not units:
        return []
    known = _known_names(units, evidence)
    findings: list[ClaimFinding] = []
    examined = 0
    for line in prose_lines(answer):
        for clause in clauses(line):
            examined += 1
            if examined > _MAX_CLAUSES:
                return findings
            for match in _I_READ.finditer(clause):
                obj = clause[match.end() :]
                cut = _OBJECT_END.search(obj)
                obj = obj[: cut.start()] if cut else obj
                before = _NOUN_BEFORE.search(clause[: match.start()])
                if _NOT_READ_BEFORE.search(clause[: match.start()]):
                    continue  # "the calls I read run from ...": lines, not members
                if before:  # "the rules I read are 39-44": the numbers follow the verb
                    obj = f"{before[1]}s {_COPULA.sub('', obj)}"
                if _LINES_OBJECT.match(obj):
                    continue
                whole = bool(_WHOLE.search(obj))
                for name in _named_members(obj, known):
                    finding = _check(name, whole, units, clause)
                    if finding:
                        findings.append(finding)
    return findings


def _known_names(units: Sequence[UnitCoverage], evidence: dict[int, Evidence]) -> set[str]:
    """Member names the lookups returned: members, tails, and names called in source."""

    names = {name for unit in units for name in unit.names()}
    called = re.compile(rf"\b({IDENTIFIER})\s*\(")
    for entry in evidence.values():
        for read in entry.source:
            for _, text in read.numbered:
                names.update(m.upper() for m in called.findall(text))
    return names


def _named_members(obj: str, known: set[str]) -> list[str]:
    """Members the read's object names: by name, or by number with a noun."""

    found = [
        m[1].upper()
        for m in _NAME.finditer(obj)
        if m[1].upper() in known and "_" in m[1] and not _NEGATED.match(obj[m.end() :])
    ]
    for match in _NOUN_NUMBERS.finditer(obj):
        noun = match[1].upper()
        if noun in _NOT_MEMBERS or _NEGATED.match(obj[match.end() :]):
            continue
        for value in _expand(match[2]):
            candidates = [
                name
                for name in known
                if (s := _SUFFIX.match(name)) and int(s[2]) == value and noun in s[1]
            ]
            if len(candidates) == 1:
                found.append(candidates[0])
    return list(dict.fromkeys(found))


def _expand(text: str) -> list[int]:
    values: list[int] = []
    for part in re.split(r"\s*(?:,|and|&)\s*", text):
        bounds = re.split(r"\s*(?:-|–|—|to|through)\s*", part.strip())
        if not bounds[0].isdigit():
            continue
        low = number(bounds[0])
        high = number(bounds[-1]) if bounds[-1].isdigit() else low
        if high < low or high - low > _MAX_RANGE:
            values.append(low)
            continue
        values.extend(range(low, high + 1))
    return values


def _check(
    name: str, whole: bool, units: Sequence[UnitCoverage], clause: str
) -> ClaimFinding | None:
    started = [(u, m) for u in units if (m := u.started(name))]
    # Read whole: every line from its first through its END came back.
    if started and (not whole or any(u.through(m) for u, m in started)):
        return None
    tails = [t for u in units for t in u.tails if t.name == name]
    apparent = [m for u, m in started if m.unknown and u.apparent_end(m)]
    if apparent:
        member = apparent[0]
        problem = (
            f"{name}'s lines from {member.first_line} were returned, but {member.unknown}, "
            "so an END in them may be comment or literal text and the lookups do not show "
            "it read whole. Say which lines were read instead."
        )
    elif started:
        problem = (
            f"{name}'s first line was read, but no lookup returned it through its END, so "
            "it was read in part, not whole."
        )
    elif tails:
        problem = (
            f"of {name} the lookups returned {tails[0].describe().split(': ', 1)[1]}. Its start, "
            "declarations and constants were not returned, so it was not read; say which "
            "lines were read instead."
        )
    else:
        problem = (
            f"no lookup returned the first line of {name}'s implementation, so it was not "
            "read. Name only the members whose lines were returned."
        )
    return ClaimFinding("read_coverage", clause[:300], problem)


# -- read scope: comparisons and universal claims (Wave 2B, trial 21 KINJ-01) ------------

# "match", "the same", "identical", "mirror": a claim that two members agree.
_COMPARISON = re.compile(
    r"\b(?:the\s+same|identical(?:ly)?|match(?:es|ed|ing)?|mirror(?:s|ed|ing)?|"
    r"equivalent|alike|not\s+(?:\w+\s+)?different|no\s+different|"
    r"structural(?:ly)?\s+(?:like|similar|the\s+same)|similar(?:ly)?\s+to|resembl\w*)\b",
    re.IGNORECASE,
)
# Members compared without being named: "its neighbours", "the neighbouring rules",
# "adjacent checks", "the surrounding functions" (Sync Point 2D, trial 22 KINJ-01).
_NEIGHBOURS = re.compile(
    r"\b(?:its|their|the|his|her)\s+(?:(?:two|immediate|nearest|adjacent)\s+)?"
    r"neighbou?rs\b|\b(?:neighbou?ring|adjacent|surrounding|nearby)\s+(\w+)",
    re.IGNORECASE,
)
# Likeness said with the neighbours: "is like its neighbours", "consistent with".
_LIKENESS = re.compile(
    r"\b(?:like|similar\w*|structural(?:ly)?|resembl\w*|consistent\s+with|in\s+line\s+with|"
    r"follows?\s+the\s+same|same\s+(?:shape|pattern|structure|form))\b",
    re.IGNORECASE,
)
# "every rule", "all the checks", "each of the calls"; "always", "never".
_UNIVERSAL = re.compile(
    r"\b(?:every|all|each)\s+(?:(?:of\s+)?(?:the|its|these|those)\s+)?(\w+)\b"
    r"|\b(always|never)\b",
    re.IGNORECASE,
)
# What narrows a universal to what was read: "every rule I read", "each check on lines
# 2520-2585", "all the calls returned".
_SCOPED = re.compile(
    r"\b(?:I|we)\s+(?:have\s+|had\s+)?(?:only\s+)?read\b|\bread\b|\breturned\b|\bshown\b"
    r"|\bvisible\b|\blisted\b|\b(?:on|in|at|from)\s+lines?\b|\bof\s+those\b",
    re.IGNORECASE,
)
# Before the quantifier, only a reader narrows it: "the rules I read all ...".
_SCOPED_BEFORE = re.compile(
    r"\b(?:I|we)\s+(?:have\s+|had\s+)?(?:only\s+)?read\b|\breturned\b|\bshown\b",
    re.IGNORECASE,
)
# A range of lines that opens the clause: "On lines 2520-2526 each call ...". A single
# line ("at line 2957") names where, not what was read.
_LINES_BEFORE = re.compile(r"^\W*(?:on|in|across)\s+lines\s+\d+\s*[-\u2013]\s*\d+\b", re.IGNORECASE)
# A change being proposed: what the code would do, not what the code read does.
_PROPOSED = re.compile(r"\b(?:would|should|could)\b", re.IGNORECASE)
# A question or a hedge: not a claim.
_OPEN = re.compile(
    r"\bwhether\b|\bunknown\b|\bnot\s+(?:been\s+)?(?:read|returned|shown)\b|\bunread\b"
    r"|\bhypothesis\b|\bif\s+so\b",
    re.IGNORECASE,
)
# Nouns that name members of a unit even where no member name carries them.
_MEMBER_NOUNS = frozenset(
    {"SUBPROGRAM", "FUNCTION", "PROCEDURE", "MEMBER", "PATH", "BRANCH", "CALLER", "CALL", "CHECK"}
)
_SCOPE_WINDOW = 60


def read_scope_claims(answer: str, evidence: dict[int, Evidence]) -> list[ClaimFinding]:
    """Comparisons and universal claims that go past the lines the lookups returned.

    Two shapes, both general (trial 21, KINJ-01):

    - A comparison ("matches", "the same", "identical", "mirrors") naming a member that
      was read in part (its END not returned), not read at all, or whose returned lines
      do not contain a construct the sentence compares (a function called in the
      returned source, such as MOD). Saying "in the lines I read" does not cover lines
      no lookup returned.
    - A universal ("every rule", "all checks", "always", "collapses every ...") about the
      members of a unit read in part, or naming a member read in part, unless narrowed
      at the quantifier to what was read ("every rule I read", "each check on lines
      2520-2585").

    - A comparison with members not named: "its neighbours", "the neighbouring rules",
      "adjacent checks", including likeness ("is structurally like its neighbours").
      The neighbours are the members either side of the one compared, by numeric suffix
      (CHECK_RULE_041 and _043 for rule 42) or by position in the lines returned; each
      must have been returned from its first line through its END, and one on a side
      no lookup returned is unknown (Sync Point 2D, trial 22 KINJ-01).

    Questions and hedges (whether, unknown, unread, hypothesis) are not claims. Every
    streamed line is read, the notes written between lookups too.
    """

    units = [u for u in unit_coverage(evidence) if u.lines]
    if not units:
        return []
    known = _known_names(units, evidence)
    members = known_members(units)
    constructs = _constructs(units) - members
    nouns = _member_nouns(known)
    partial = [u for u in units if not _contiguous_from_one(u.lines)]
    findings: list[ClaimFinding] = []
    examined = 0
    for line in prose_lines(answer):
        # "unread" labels the subject, not uncertainty about an asserted comparison.
        for sentence in clauses(line):
            for part in subclauses(sentence):
                if (
                    partial
                    and _COMPARISON.search(part)
                    and re.search(
                        r"\bunread\b[^.;]{0,40}\b(?:bodies|implementations)\b", part, re.I
                    )
                    and re.search(r"\b(?:have|are|contain|use)\b", part, re.I)
                    and not re.search(r"\b(?:whether|unknown|unclear|not|never)\b", part, re.I)
                ):
                    findings.append(
                        ClaimFinding(
                            "read_scope",
                            part[:300],
                            "The comparison asserts contents of unread implementations. "
                            "Call-site similarity does not establish body contents; "
                            "leave those unknown.",
                        )
                    )
        for clause, lead in (claim for sentence in clauses(line) for claim in _claims(sentence)):
            examined += 1
            if examined > _MAX_CLAUSES:
                return findings
            # Members only: a name the source calls (a table in an INSERT) is not compared.
            named = [n for n in _named_members(clause, known) if n in members]
            call_site_only = (
                bool(re.search(r"\b(?:at|in|among)\s+(?:the\s+)?call[ -]sites?\b", clause, re.I))
                or _call_site_range(clause, units)
            ) and not re.search(
                r"\b(?:bodies|body|implementations?|MOD|floor|ceiling)\b", clause, re.I
            )
            if call_site_only:
                named = []
            neighbours = (
                [] if call_site_only else _neighbour_problems(clause, lead, units, known, nouns)
            )
            if _COMPARISON.search(clause) or neighbours:
                # One finding per sentence, however many members it names.
                problems = neighbours + [
                    p for name in named if (p := _compared(name, clause, units, constructs))
                ]
                if problems:
                    problem = " ".join(problems[:2])
                    if len(problems) > 2:
                        problem += f" The same holds for {len(problems) - 2} more members named."
                    findings.append(ClaimFinding("read_scope", clause[:300], problem))
            problem = _universal(clause, lead, named, units, partial, nouns)
            if problem:
                findings.append(ClaimFinding("read_scope", clause[:300], problem))
    return findings


# A later part that leaves the claim before it open: "..., but that is unknown".
_LEFT_OPEN = re.compile(
    r"^(?:but|although|though|yet|however)?,?\s*(?:that|this|which|it|either|both)\s+"
    r"(?:is|are|was|were|remains?|stays?)\s+(?:still\s+)?(?:unknown|unproven|unverified|"
    r"not\s+(?:known|established|shown|proven|verified))\b",
    re.IGNORECASE,
)


def _call_site_range(clause: str, units: Sequence[UnitCoverage]) -> bool:
    """A stated, fully returned range consisting of caller checks, not member bodies."""
    for match in re.finditer(r"\blines?\s+(\d+)\s*[-–]\s*(\d+)", clause, re.I):
        first, last = map(int, match.groups())
        if last < first or last - first > _MAX_RANGE:
            continue
        for unit in units:
            rows = dict(unit.rows)
            if not covers(unit.lines, first, last):
                continue
            code = [
                r.strip()
                for r in code_lines([rows[n] for n in range(first, last + 1)])[0]
                if r.strip()
            ]
            if (
                code
                and any(_REJECTING_CALL.search(r) for r in code)
                and all(
                    re.fullmatch(
                        rf"(?:IF\s+NOT\s+{IDENTIFIER}\([^;]*\)\s+THEN|RETURN\s+FALSE;|END\s+IF;)",
                        r,
                        re.I,
                    )
                    for r in code
                )
            ):
                return True
    return False


def _claims(sentence: str) -> list[tuple[str, str]]:
    """The parts of ``sentence`` that assert something, each with the sentence up to it.

    A question or hedge (whether, unknown, unread, hypothesis) exempts only the part it
    is in, or the part before when it refers back to it ("but that is unknown"). An
    unknown said of something else ("..., but the rest is unknown") leaves a comparison
    or universal elsewhere in the sentence asserted (Sync Point 2B). A change proposed
    earlier in the sentence ("would have to return ... - for example ...") carries into
    the parts after it, so each part comes with the sentence up to its end.
    """

    parts = subclauses(sentence)
    asserted = []
    for index, part in enumerate(parts):
        following = parts[index + 1] if index + 1 < len(parts) else ""
        if _OPEN.search(part) or _LEFT_OPEN.search(following):
            continue
        asserted.append((part, " ".join(parts[: index + 1])))
    return asserted


def known_members(units: Sequence[UnitCoverage]) -> set[str]:
    return {name for unit in units for name in unit.names()}


def _constructs(units: Sequence[UnitCoverage]) -> set[str]:
    """Names called in the returned source: MOD, NVL, a helper. What a sentence compares."""

    called = re.compile(rf"\b({IDENTIFIER})\s*\(")
    found: set[str] = set()
    for unit in units:
        code, _ = code_lines([text for _, text in unit.rows])
        for text in code:
            found.update(m.upper() for m in called.findall(text))
    return found - _KEYWORDS


# Words a "(" follows in PL/SQL that are not calls.
_KEYWORDS = frozenset(
    {
        "IN",
        "AND",
        "OR",
        "NOT",
        "EXISTS",
        "VALUES",
        "INTO",
        "IF",
        "ELSIF",
        "WHEN",
        "THEN",
        "RETURN",
        "SELECT",
        "FROM",
        "WHERE",
        "CURSOR",
        "TABLE",
        "VARCHAR2",
        "NUMBER",
        "CHAR",
        "RAW",
        "OVER",
        "BY",
        "AS",
        "IS",
        "OUT",
        "ON",
        "USING",
        "LOOP",
        "WHILE",
        "FOR",
        "CASE",
        "ELSE",
        "ANY",
        "ALL",
        "SOME",
        "SET",
        "OF",
        "TYPE",
        "RECORD",
        "VARRAY",
        "OBJECT",
    }
)


def _member_nouns(names: set[str]) -> set[str]:
    """The nouns members are counted by: RULE for CHECK_RULE_041, and the generic ones.

    From every name returned, called ones included: a dispatcher read without the rules
    it calls still counts its calls to ``check_rule_032`` as rules.
    """

    nouns = set(_MEMBER_NOUNS)
    for name in names:
        suffixed = _SUFFIX.match(name)
        if suffixed and suffixed[1]:
            nouns.update(part for part in suffixed[1].split("_") if len(part) > 2)
    return nouns


def _singular(word: str, nouns: set[str]) -> str:
    """RULE for RULES, BRANCH for BRANCHES; the word itself when no member noun fits."""

    for candidate in (word, word[:-1], word[:-2]):
        if candidate in nouns and word in (candidate, candidate + "S", candidate + "ES"):
            return candidate
    return word


def _contiguous_from_one(lines: Sequence[int]) -> bool:
    return bool(lines) and lines[0] == 1 and lines[-1] - lines[0] + 1 == len(lines)


def _returned_span(unit: UnitCoverage, member: Member) -> tuple[int, int]:
    """The lines of ``member`` returned without a gap from its first, up to its END."""

    rows = dict(unit.rows)
    last = member.first_line
    while last + 1 in rows:
        last += 1
    end = unit.through(member) or unit.apparent_end(member)
    return member.first_line, min(last, end) if end else last


def _compared(name: str, clause: str, units: Sequence[UnitCoverage], constructs: set[str]) -> str:
    started = [(u, m) for u in units if (m := u.started(name))]
    if not started:
        tails = [t for u in units for t in u.tails if t.name == name]
        shown = f"only its end ({tails[0].describe().split(': ', 1)[1]})" if tails else "none"
        return (
            f"{name} is compared, but of its lines the lookups returned {shown}: what it "
            "holds is unknown, so it cannot be called the same as another member. Compare "
            "only what the returned lines of each show, or leave the comparison out."
        )
    unit, member = started[0]
    first, last = _returned_span(unit, member)
    if unit.through(member) is None and unit.apparent_end(member) is None:
        return (
            f"{name} was read in part (lines {first}-{last}; not every line from there to "
            "its END was returned), so what it holds after those lines is unknown and it "
            "cannot be called the same "
            '"in the lines I read" or otherwise. Say what the returned lines of each show, '
            "and leave the rest of it unknown."
        )
    text = " ".join(t for n, t in unit.rows if first <= n <= last)
    for construct in sorted(constructs):
        # Named as code: in capitals or in backticks, not an English word ("in").
        named_here = re.search(rf"\b{re.escape(construct)}\b", clause) or re.search(
            rf"`{re.escape(construct)}\b", clause, re.IGNORECASE
        )
        if named_here and not re.search(rf"\b{re.escape(construct)}\s*\(", text, re.IGNORECASE):
            return (
                f"the returned lines of {name} ({first}-{last}) contain no {construct}, so "
                f"its {construct} cannot be compared with another member's. Say only what "
                "its returned lines show."
            )
    return ""


def _universal(
    clause: str,
    lead: str,
    named: Sequence[str],
    units: Sequence[UnitCoverage],
    partial: Sequence[UnitCoverage],
    nouns: set[str],
) -> str:
    in_part = [
        (u, m)
        for name in named
        for u in units
        if (m := u.started(name)) and u.through(m) is None and u.apparent_end(m) is None
    ]
    if _PROPOSED.search(lead):
        return ""
    for match in _UNIVERSAL.finditer(clause):
        window = clause[match.end() : match.end() + _SCOPE_WINDOW]
        before = clause[max(0, match.start() - 20) : match.start()]
        if (
            _SCOPED.search(window)
            or _SCOPED_BEFORE.search(before)
            or _LINES_BEFORE.search(clause[: match.start()])
        ):
            continue
        noun = _singular((match[1] or "").upper(), nouns)
        if noun not in nouns and not match[2]:
            continue  # "every rejected row": not about the members of a unit
        # "every rule" is about the rules, not about one rule the sentence also names.
        about = [(u, m) for u, m in in_part if not noun or noun not in m.name.split("_")]
        if about:
            unit, member = about[0]
            first, last = _returned_span(unit, member)
            return (
                f'"{match[0]}" is said of {member.name}, which was read in part (lines '
                f"{first}-{last}; not every line from there to its END was returned): what "
                "its unread lines do is unknown. State it of the lines returned (for example "
                '"each check on '
                'lines N-M"), and leave the rest unknown.'
            )
        if noun in nouns and partial:
            unit = partial[0]
            return (
                f'"{match[0]}" is said of every {noun.lower()}, but {unit.unit} was read in '
                f"part (lines {line_ranges(unit.lines, 6)}): the {noun.lower()}s in its "
                "unread lines are unknown. Narrow it to the lines returned (for example "
                f'"each {noun.lower()} on lines N-M"), and leave the rest unknown.'
            )
    return ""


# -- neighbours (Sync Point 2D, trial 22 KINJ-01) ----------------------------------------

_DIGITS = re.compile(r"^(.*?)(\d+)$")


def _neighbour_problems(
    clause: str,
    lead: str,
    units: Sequence[UnitCoverage],
    known: set[str],
    nouns: set[str],
) -> list[str]:
    """Why a comparison of a member with its unnamed neighbours goes past what was read."""

    mention = _NEIGHBOURS.search(clause)
    if mention is None or not (_COMPARISON.search(clause) or _LIKENESS.search(clause)):
        return []
    if mention[1] and _singular(mention[1].upper(), nouns) not in nouns:
        return []  # "adjacent lines", "the surrounding text": not members
    # The member compared is named before its neighbours are ("Rule 42 is like its
    # neighbours"), in this part or earlier in the sentence. Members named after them
    # ("the adjacent rules: rule 39, 40, ...") are the neighbours themselves, and the
    # comparison check above reads those by name.
    members = known_members(units)
    earlier = lead[: max(0, lead.rfind(clause))] if clause in lead else ""
    subjects = [n for n in _named_members(clause[: mention.start()], known) if n in members] or [
        n for n in _named_members(earlier, known) if n in members
    ][-1:]
    problems: list[str] = []
    for subject in subjects[:2]:
        for neighbour in _neighbours_of(subject, units, known):
            problem = _neighbour_read(neighbour, subject, units)
            if problem:
                problems.append(problem)
    return problems


def _neighbours_of(name: str, units: Sequence[UnitCoverage], known: set[str]) -> list[str]:
    """The members either side of ``name``: by numeric suffix where its name has one,
    returned or not (CHECK_RULE_041 and _043 for CHECK_RULE_042), else by position."""

    numbered = _DIGITS.match(name)
    if numbered:
        stem, digits = numbered[1], numbered[2]
        value = int(digits)
        found = []
        for other in (value - 1, value + 1):
            if other < 0:
                continue
            same = [
                k for k in known if (m := _DIGITS.match(k)) and m[1] == stem and int(m[2]) == other
            ]
            found.append(same[0] if same else f"{stem}{str(other).zfill(len(digits))}")
        return found
    for unit in units:
        ordered = sorted(
            [(m.first_line, m.name) for m in unit.members if m.implementation]
            + [(t.first_line, t.name) for t in unit.tails]
        )
        names = [n for _, n in ordered]
        if name in names:
            index = names.index(name)
            before = names[index - 1] if index > 0 else "the member before it"
            after = names[index + 1] if index + 1 < len(names) else "the member after it"
            return [before, after]
    return []


def _neighbour_read(neighbour: str, subject: str, units: Sequence[UnitCoverage]) -> str:
    """Why ``neighbour`` cannot be compared, or "" when its lines were returned from its
    first through its END (an apparent END too, as a named comparison accepts)."""

    for unit in units:
        member = unit.started(neighbour)
        if member is not None and (unit.through(member) or unit.apparent_end(member)):
            return ""
    started = [(u, m) for u in units if (m := u.started(neighbour))]
    tails = [t for u in units for t in u.tails if t.name == neighbour]
    if started:
        unit, member = started[0]
        first, last = _returned_span(unit, member)
        shown = f"only lines {first}-{last}, not through its END"
    elif tails:
        shown = f"only its end ({tails[0].describe().split(': ', 1)[1]})"
    else:
        shown = "none of its lines"
    return (
        f"{subject} is compared with its neighbours, and of its neighbour {neighbour} the "
        f"lookups returned {shown}: what that neighbour holds is unknown, so {subject} "
        "cannot be called like it, structurally or otherwise. Say what the returned lines "
        "of each show, or leave the comparison out."
    )


# -- required changes (Sync Point 2D, trial 22 KINJ-01) ----------------------------------

# "edits across every check_rule_0NN call site", "touches all callers".
_EVERY_CALL_SITE = re.compile(
    r"\b(?:every|all|each)\s+(?:of\s+)?(?:the\s+|its\s+)?(?:`?[\w$#]+`?\s+)?"
    r"(?:call\s*sites?|callers?|call\s+points?|invocations?)\b",
    re.IGNORECASE,
)
_CHANGE_WORDS = re.compile(
    r"\b(?:edit\w*|chang\w*|updat\w*|touch\w*|modif\w*|means?|requires?|needs?|must|"
    r"rewrit\w*)\b",
    re.IGNORECASE,
)


def required_change_claims(answer: str, evidence: dict[int, Evidence]) -> list[ClaimFinding]:
    """A change said to need edits at every call site or caller, past what was read.

    Where the source lookups returned a unit only in part (not from its line 1 without
    a gap), or returned no source at all, its callers and call sites beyond the lines
    returned are unknown, so "edits across ... every check_rule_0NN call site" is not
    established (trial 22, KINJ-01). Narrowed to what was read ("every call site on
    lines N-M", "each caller I read"), proposed ("would"), or left open, it is not
    flagged.
    """

    units = [u for u in unit_coverage(evidence) if u.lines]
    partial = [u for u in units if not _contiguous_from_one(u.lines)]
    if units and not partial:
        return []
    findings: list[ClaimFinding] = []
    examined = 0
    for line in prose_lines(answer):
        for clause, _lead in (claim for sentence in clauses(line) for claim in _claims(sentence)):
            examined += 1
            if examined > _MAX_CLAUSES:
                return findings
            match = _EVERY_CALL_SITE.search(clause)
            if not match or not _CHANGE_WORDS.search(clause):
                continue
            window = clause[match.end() : match.end() + _SCOPE_WINDOW]
            before = clause[max(0, match.start() - 20) : match.start()]
            if _SCOPED.search(window) or _SCOPED_BEFORE.search(before):
                continue
            shown = (
                f"{partial[0].unit} was read only at lines {line_ranges(partial[0].lines, 6)}"
                if partial
                else "no lookup returned the source"
            )
            findings.append(
                ClaimFinding(
                    "required_change",
                    clause[:300],
                    f'"{match[0]}" sets the scope of a change, but {shown}: its other '
                    "callers and call sites are unknown. Name the call sites the lines "
                    "returned show, and leave the rest of the change's scope unknown.",
                )
            )
    return findings


# -- nested bands (Sync Point 2G, trial 23 KINJ-01) --------------------------------------

# "l_limit CONSTANT NUMBER := 1042 * 1000;": a constant of plain arithmetic.
_CONSTANT = re.compile(
    rf"^\s*({IDENTIFIER})\s+CONSTANT\s+{IDENTIFIER}(?:\s*\([\d\s,]*\))?\s*:=\s*"
    r"([-+*()\d\s.]+?)\s*;",
    re.IGNORECASE,
)
# "IF p_amount < l_floor THEN RETURN FALSE": a band bound that rejects.
_BOUND = re.compile(
    rf"^\s*IF\s+({IDENTIFIER})\s*([<>])\s*(-?\s*\d+(?:\.\d+)?|{IDENTIFIER})\s+THEN\b",
    re.IGNORECASE,
)
_RETURN_FALSE = re.compile(r"\bRETURN\s+FALSE\s*;", re.IGNORECASE)
# "IF NOT check_rule_001(p_amount) THEN RETURN FALSE": a call that rejects on failure.
_REJECTING_CALL = re.compile(
    rf"\bIF\s+NOT\s+({IDENTIFIER})\s*\(\s*({IDENTIFIER})\s*\)\s*THEN\b", re.IGNORECASE
)
# A band check said to fire: "the only test that can ever fire", "can reject", "decides
# whether the band tests ever fire". "cannot fire" and "never fires" are not matched.
_FIRES = re.compile(
    r"\b(?:can|could|may|might|will|would|does|do)\s+(?:ever\s+|still\s+|also\s+|only\s+)?"
    r"(?:fire|reject|trigger)s?\b"
    r"|\bonly\s+(?:\w+\s+){0,2}test\s+that\s+(?:can|could|will|does)\b"
    r"|\b(?:decides?|determines?|settles?)\s+whether\b.{0,80}?\bfires?\b"
    r"|(?<!never\s)(?<!not\s)\bever\s+fires?\b",
    re.IGNORECASE,
)
# Data said to settle the firing: asserted, however the sentence opens.
_SETTLES_FIRING = re.compile(
    r"\b(?:decides?|determines?|settles?)\s+whether\b.{0,80}?\bfires?\b", re.IGNORECASE
)
# A firing question left open (Sync Point 2H): "whether ... can fire is unknown",
# "I do not know whether ...", "cannot tell whether ...".
_WHETHER = re.compile(r"\bwhether\b", re.IGNORECASE)
_NOT_KNOWN_BEFORE = re.compile(
    r"\b(?:(?:do|does|did)\s+not|don't|doesn't|cannot|can't|could\s+not|couldn't)\s+"
    r"(?:yet\s+)?(?:know|tell|say|establish|determine|confirm|show)\s*$"
    r"|\b(?:unknown|unclear|unsure|not\s+sure|not\s+known|unverified|open\s+question)"
    r"(?:\s+is)?\s*:?\s*$",
    re.IGNORECASE,
)
_NOT_KNOWN_AFTER = re.compile(
    r"\b(?:is|are|was|remains?|stays?)\s+(?:still\s+)?(?:unknown|unclear|unverified|open|"
    r"not\s+(?:known|shown|established|settled|proven|verified))\b",
    re.IGNORECASE,
)
# A firing said of no rule (Sync Point 2N, trial 25 KINJ-01): "I read no single rule
# that would reject a value all others pass", "none of the checks that could fire". The
# subject the firing expression belongs to is denied, so nothing is said to fire.
_DENIED_SUBJECT = re.compile(
    r"\b(?:no|none\s+of\s+the|not\s+(?:a|one|any)|neither)\s+(?:[\w'`]+\s+){0,3}"
    r"(?:that|which)\s*$",
    re.IGNORECASE,
)
_BAND_WORDS = re.compile(
    r"\b(?:band|floor|ceiling|l_floor|l_limit|limit|lower\s+bound|upper\s+bound)s?\b",
    re.IGNORECASE,
)
_NUMBERS = re.compile(r"(?<![\w.])\d{1,9}(?![\w.])")
_MAX_MEMBER_LINES = 80


@dataclass(frozen=True)
class Band:
    """A member's rejecting band: below ``floor`` or above ``ceiling`` returns FALSE."""

    name: str
    floor: Fraction
    ceiling: Fraction
    lines: tuple[int, ...]  # the lines of its two bound checks


@dataclass(frozen=True)
class _Call:
    name: str
    line: int
    argument: str


def _arithmetic(text: str) -> Fraction | None:
    """The value of a constant written as plain arithmetic, or None."""

    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError:
        return None

    def value(node: ast.AST) -> Fraction:
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return Fraction(str(node.value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
            inner = value(node.operand)
            return -inner if isinstance(node.op, ast.USub) else inner
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Sub | ast.Mult):
            left, right = value(node.left), value(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            return left - right if isinstance(node.op, ast.Sub) else left * right
        raise ValueError(node)

    try:
        return value(tree.body)
    except (ValueError, RecursionError):
        return None


def _member_rows(unit: UnitCoverage, member: Member) -> list[tuple[int, str]]:
    """``member``'s returned lines from its first, without a gap, up to its END."""

    first, last = _returned_span(unit, member)
    rows = dict(unit.rows)
    return [(n, rows[n]) for n in range(first, min(last, first + _MAX_MEMBER_LINES) + 1)]


def _band(unit: UnitCoverage, member: Member) -> Band | None:
    rows = _member_rows(unit, member)
    code, _ = code_lines([text for _, text in rows])
    constants: dict[str, Fraction] = {}
    floor = ceiling = None
    lines: list[int] = []
    for index, (n, line) in enumerate(zip([n for n, _ in rows], code, strict=True)):
        constant = _CONSTANT.match(line)
        if constant and (worth := _arithmetic(constant[2])) is not None:
            constants[constant[1].upper()] = worth
            continue
        bound = _BOUND.match(line)
        if not bound:
            continue
        rest = line[bound.end() :] + " " + " ".join(code[index + 1 : index + 2])
        if not _RETURN_FALSE.search(rest):
            continue
        limit = constants.get(bound[3].upper())
        if limit is None:
            limit = _arithmetic(bound[3].replace(" ", ""))
        if limit is None:
            continue
        if bound[2] == "<" and floor is None:
            floor = limit
            lines.append(n)
        elif bound[2] == ">" and ceiling is None:
            ceiling = limit
            lines.append(n)
    if floor is None or ceiling is None or floor > ceiling:
        return None
    return Band(member.name, floor, ceiling, tuple(lines))


def _rejecting_calls(unit: UnitCoverage) -> dict[str, _Call]:
    """Each member's first call in the returned lines that returns FALSE when it fails."""

    numbered = [n for n, _ in unit.rows]
    code, _ = code_lines([text for _, text in unit.rows])
    calls: dict[str, _Call] = {}
    for index, (n, line) in enumerate(zip(numbered, code, strict=True)):
        for call in _REJECTING_CALL.finditer(line):
            following = code[index + 1] if index + 1 < len(code) else ""
            nxt = numbered[index + 1] if index + 1 < len(numbered) else None
            rest = line[call.end() :] + (" " + following if nxt == n + 1 else "")
            if _RETURN_FALSE.search(rest):
                calls.setdefault(call[1].upper(), _Call(call[1].upper(), n, call[2].upper()))
    return calls


def _asserts_firing(part: str) -> bool:
    """Whether ``part`` says a band check fires, rather than asking whether it does.

    A firing expression that is the object of an open question - "whether rule 42's
    band check can fire is unknown", "I do not know whether it ever fires" - asserts
    nothing, and is what the finding asks for when the lines between the calls were not
    read. Nor does one whose subject is denied: "no single rule that would reject a
    value". Data said to decide it ("decides whether the band tests ever fire") is a
    claim, and so is any firing expression outside such a question, whatever the rest
    of the sentence leaves unknown.
    """

    if _SETTLES_FIRING.search(part):
        return True
    for fires in _FIRES.finditer(part):
        before = part[: fires.start()]
        if _DENIED_SUBJECT.search(before):
            continue
        asked = list(_WHETHER.finditer(before))
        if not asked:
            return True
        opened = asked[-1]
        if _NOT_KNOWN_BEFORE.search(before[: opened.start()]):
            continue
        if _NOT_KNOWN_AFTER.search(part, fires.end()):
            continue
        return True
    return False


def _shown(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else str(float(value))


def _mentions(sentence: str, band: Band) -> bool:
    """Whether ``sentence`` names the member, as itself or as "rule 42"."""

    if re.search(rf"\b{re.escape(band.name)}\b", sentence, re.IGNORECASE):
        return True
    suffixed = _SUFFIX.match(band.name)
    if not suffixed or not suffixed[1]:
        return False
    nouns = "|".join(re.escape(p) for p in suffixed[1].split("_") if len(p) > 2)
    return bool(
        nouns and re.search(rf"\b(?:{nouns})s?\s+0*{int(suffixed[2])}\b", sentence, re.IGNORECASE)
    )


def nested_band_claims(answer: str, evidence: dict[int, Evidence]) -> list[ClaimFinding]:
    """A band check said to fire on values an earlier rule's band already rejects.

    Where the source lookups return two members with numeric bands (``IF p < l_floor
    THEN RETURN FALSE``, ``IF p > l_limit THEN RETURN FALSE``) and a caller that runs
    the first, returning FALSE when it fails, before the second on the same argument,
    a value that passed the first unchanged lies in its band. When that band lies
    inside the second's, the second's band check cannot reject it (trial 23, KINJ-01:
    rule 001's [-1, 1001000] inside rule 042's [-42, 1042000]). A claim that the later
    band check "can fire", or "is the only test that can fire on a value reaching it",
    is contradicted. When the caller's lines between the two calls were not all read,
    whether a value reaches the later rule unchanged is unknown, and the finding says
    so rather than settling it.
    """

    findings: list[ClaimFinding] = []
    pairs = []
    for unit in unit_coverage(evidence):
        if not unit.lines:
            continue
        bands = {
            m.name.upper(): band
            for m in unit.members
            if m.implementation and (band := _band(unit, m)) is not None
        }
        calls = _rejecting_calls(unit)
        for earlier in bands.values():
            for later in bands.values():
                first, then = calls.get(earlier.name.upper()), calls.get(later.name.upper())
                if (
                    earlier is later
                    or first is None
                    or then is None
                    or first.line >= then.line
                    or first.argument != then.argument
                    or not (later.floor <= earlier.floor and earlier.ceiling <= later.ceiling)
                ):
                    continue
                read = covers(unit.lines, first.line, then.line)
                pairs.append((earlier, later, first, then, read))
    if not pairs:
        return findings
    examined = 0
    for line in prose_lines(answer):
        for sentence in clauses(line):
            examined += 1
            if examined > _MAX_CLAUSES:
                return findings
            cited = {number(digits) for digits in _NUMBERS.findall(sentence)}
            for earlier, later, first, then, read in pairs:
                at_lines = bool(cited & set(later.lines))
                if not (_mentions(sentence, later) or at_lines):
                    continue
                fires = [
                    part
                    for part in re.split(r"[—;]|,?\s+but\s+", sentence, flags=re.I)
                    if _asserts_firing(part)
                    and (
                        _BAND_WORDS.search(part)
                        or (not re.search(r"\bMOD\b", part, re.I) and cited & set(later.lines))
                    )
                ]
                if not fires:
                    continue
                problem = (
                    f"{later.name}'s band check rejects values below {_shown(later.floor)} or "
                    f"above {_shown(later.ceiling)}; {earlier.name}, called first (line "
                    f"{first.line}, before line {then.line}) with the same argument, returns "
                    f"FALSE outside [{_shown(earlier.floor)}, {_shown(earlier.ceiling)}], "
                    f"which lies inside [{_shown(later.floor)}, {_shown(later.ceiling)}]. A "
                    f"value that passed {earlier.name} and reaches {later.name} unchanged "
                    "cannot fail that band check, so it cannot be the test that fires."
                )
                if not read:
                    problem += (
                        f" Lines {first.line + 1}-{then.line - 1} between the two calls were "
                        f"not all read, so whether every value reaches {later.name} unchanged "
                        "is unknown: leave that unknown."
                    )
                problem += (
                    " State that the band check cannot reject a value that passed "
                    f"{earlier.name}, or leave whether it fires unknown; do not say it can fire."
                )
                findings.append(ClaimFinding("band_reachability", sentence[:300], problem))
                break
    return findings


# -- a member's own first line (Sync Point 2T) ---------------------------------------------

_START_WORD = (
    r"(?:(?:procedure|function|subprogram|member|routine)(?:'s)?\s+)?"
    r"(?:header|signature|declaration|definition|starts?|begins?|opens?|starting|beginning"
    r"|(?:is\s+)?(?:declared|defined))"
)
_LINE_N = r"(?:(?:is|at|on|in|from|with)\s+)*\(?\s*line\s+(\d+)"
_GAP = r"(?:[\s`'(),:—–-]|\b(?:which|whose|its|the|is|was)\b){0,8}"
# A start line given for a named member: "In `transform` (procedure header at line 2937)",
# "transform starts at line 2949", "line 2949 is the header of transform".
_STARTS_AT = re.compile(
    rf"`?(?P<name>{IDENTIFIER})`?(?:'s)?{_GAP}{_START_WORD}\s+{_LINE_N}"
    rf"|\bline\s+(?P<line>\d+)\)?,?\s+(?:is|holds|has|shows|opens|starts|begins|declares)\s+"
    rf"(?:with\s+)?(?:the\s+)?(?:header|start|declaration|signature|definition|first\s+line)"
    rf"\s+(?:of|for)\s+(?:(?:the\s+)?(?:procedure|function)\s+)?`?(?P<of>{IDENTIFIER})`?"
    rf"|\bline\s+(?P<code>\d+)\W{{0,4}}(?:PROCEDURE|FUNCTION)\s+`?(?P<coded>{IDENTIFIER})`?",
    re.IGNORECASE,
)
# A line given for a named member without saying it is the first: "procedure `transform`
# at line 2955" holds when the line is inside it.
_MEMBER_AT = re.compile(
    rf"\b(?:procedure|function)\s+`?(?P<name>{IDENTIFIER})`?\s*\(?\s*{_LINE_N}", re.IGNORECASE
)


def member_line_claims(answer: str, evidence: dict[int, Evidence]) -> list[ClaimFinding]:
    """A line given as a named member's first line, or as one of its lines, that the
    returned source rows place elsewhere (trial 27, KINJ-01: "In `transform` (procedure
    header at line 2937)", where lookup 10 returned ``PROCEDURE load_stage`` at 2937 and
    ``PROCEDURE transform`` at 2949).

    Only a member whose implementation (or declaration) the lookups show is checked, and
    only a line those lookups returned for a unit holding it: a line never returned is
    for the line checks (C24). "header", "starts" and the like claim the member's first
    line; "procedure X at line N" claims a line inside it.
    """

    units = [u for u in unit_coverage(evidence) if u.members]
    if not units:
        return []
    findings: list[ClaimFinding] = []
    examined = 0
    for line in prose_lines(answer):
        for sentence in clauses(line):
            examined += 1
            if examined > _MAX_CLAUSES:
                return findings
            claims: list[tuple[str, int, bool]] = []
            starts = []
            for found in _STARTS_AT.finditer(sentence):
                name = found["name"] or found["of"] or found["coded"]
                digits = found[2] or found["line"] or found["code"]
                claims.append((name.upper(), number(digits), True))
                starts.append(found.span())
            for found in _MEMBER_AT.finditer(sentence):
                if not any(a <= found.start() < b for a, b in starts):
                    claims.append((found["name"].upper(), number(found[2]), False))
            for name, cited, first in claims:
                problem = _member_line_problem(name, cited, first, units, evidence)
                if problem:
                    findings.append(ClaimFinding("evidence_reference", sentence[:300], problem))
                    break
    return findings


def _member_line_problem(
    name: str, cited: int, first: bool, units: Sequence[UnitCoverage], evidence: dict[int, Evidence]
) -> str:
    held = [(u, m) for u in units for m in u.members if m.name == name and cited in u.lines]
    if not held:
        return ""
    for unit, member in held:
        low, high = _member_span(unit, member)
        if cited == member.first_line or (not first and low <= cited <= high):
            return ""
    unit, member = held[0]
    rows = dict(unit.rows)
    lookup = next(
        (n for n in unit.lookups if any(cited in r.lines for r in evidence[n].source)),
        unit.lookups[0],
    )
    owner = next((m for m in reversed(unit.members) if m.first_line <= cited), None)
    if owner is not None and owner.first_line == cited:
        there = f"is the first line of {owner.name}"
    elif owner is not None and owner.name != name:
        there = f"lies in {owner.name} (from line {owner.first_line})"
    else:
        there = f"is not where {name} starts"
    shown = rows.get(member.first_line, "").strip()
    starts = f"{name} starts at line {member.first_line}" + (f" ({shown[:80]})" if shown else "")
    return (
        f"lookup {lookup} returned line {cited} of {unit.unit}: it {there}"
        + (f" ({rows[cited].strip()[:80]})" if rows.get(cited, "").strip() else "")
        + f"; {starts}. Give {name}'s own line from the returned rows, or leave the line out."
    )


def _member_span(unit: UnitCoverage, member: Member) -> tuple[int, int]:
    """The lines of ``member`` as the returned rows show them: from its first line to its
    END, or to the line before the next member, or to the last line returned in a run."""

    end = unit.through(member) or unit.apparent_end(member)
    if end is not None:
        return member.first_line, end
    later = [m.first_line for m in unit.members if m.first_line > member.first_line]
    if later:
        return member.first_line, min(later) - 1
    last = member.first_line
    while last + 1 in unit.lines:
        last += 1
    return member.first_line, last


def correct_member_lines(text: str, evidence: dict[int, Evidence]) -> tuple[str, list[str]]:
    """``text`` with each member start line ``member_line_claims`` flags replaced by the
    member's first line in the returned rows, and a note per correction.

    Only a first-line claim ("header", "starts") is corrected, and only where the unit
    that returned the cited line shows one first line for the member; any other finding
    is left for the caller to settle. No model call is made.
    """

    units = [u for u in unit_coverage(evidence) if u.members]
    if not units:
        return text, []
    blocks = fenced_blocks(text)
    pieces: list[str] = []
    notes: list[str] = []
    done = 0
    for found in _STARTS_AT.finditer(text):
        if any(b.start <= found.start() < b.end for b in blocks):
            continue
        name = (found["name"] or found["of"] or found["coded"]).upper()
        group = 2 if found[2] else ("line" if found["line"] else "code")
        cited = number(found[group])
        if not _member_line_problem(name, cited, True, units, evidence):
            continue
        firsts = {
            m.first_line for u in units if cited in u.lines for m in u.members if m.name == name
        }
        if len(firsts) != 1:
            continue
        [first] = firsts
        start, end = found.span(group)
        pieces += [text[done:start], str(first)]
        done = end
        notes.append(f"{name} starts at line {first} of the returned rows, not line {cited}")
    return "".join(pieces) + text[done:], notes


# Statement attribution uses returned text, not an assumed offset from a member's start.
# Capture an explicit SQL/PLSQL token sequence, including quoted fragments, then compare
# lexical tokens (not substrings, comments or literals) at the asserted line.
_STATEMENT_WORDS = (
    r"RETURN\s+(?:TRUE|FALSE|NULL|[A-Za-z][\w$#]*)|END\s+IF|END\s+LOOP|"
    r"INSERT\s+INTO|DELETE\s+FROM|RAISE(?:_APPLICATION_ERROR)?|COMMIT|ROLLBACK|"
    r"UPDATE|SELECT|MERGE\s+INTO|FORALL|EXIT\s+WHEN"
)
_STATEMENT_AT = re.compile(
    rf"(?P<token>`[^`\n]+`|\b(?:{_STATEMENT_WORDS})\b)\s*"
    r"(?:\([^\n)]{0,80}\)\s*)?(?:statement\s+)?(?:at|on)\s+(?:line\s+)?"
    r"(?P<line>\d[\d,]*)(?![\d,]|\s*[-–]\s*\d)",
    re.I,
)
_LINE_HAS = re.compile(
    rf"\bline\s+(?P<line>\d[\d,]*)\s+(?:has|contains|is|shows)\s+"
    rf"(?P<token>`[^`\n]+`|\b(?:{_STATEMENT_WORDS})\b)",
    re.I,
)
_SOURCE_CITATION = re.compile(r"\blookups?\s+([\d, /and]+)", re.I)


def statement_line_claims(answer: str, evidence: dict[int, Evidence]) -> list[ClaimFinding]:
    """Check explicit statement locations only in source records cited by the clause.

    An unread line is left to existing coverage checks. Several cited units or
    conflicting snapshots are ambiguous, so no location is guessed. In particular a
    member header isn't a statement attribution and stays with member_line_claims.
    """
    findings = []
    for line in prose_lines(answer):
        for clause in clauses(line):
            cited = {
                int(n)
                for citation in _SOURCE_CITATION.finditer(line)
                for n in re.findall(r"\d+", citation[1])
            }
            reads = [
                r
                for n in cited
                if n in evidence and evidence[n].succeeded
                for r in evidence[n].source
            ]
            if not reads:
                continue
            for match in list(_STATEMENT_AT.finditer(clause)) + list(_LINE_HAS.finditer(clause)):
                fragment = match["token"].strip("`")
                if not (
                    re.match(rf"^(?:{_STATEMENT_WORDS})\b", fragment, re.I)
                    or match["token"].startswith("`")
                ):
                    continue
                if fragment.upper() in {m.name.upper() for r in reads for m in r.members}:
                    # "in member X at line N" may name a line inside that member.
                    # Member-start/range claims have their own semantic checker.
                    continue
                before = clause[max(0, match.start() - 80) : match.start()]
                if re.search(r"\b(?:whether|unknown|unclear|not|never)\b[^.;]*$", before, re.I):
                    continue
                if re.match(
                    r"\s+(?:is|remains?|stays?)\s+(?:unknown|unclear|not\s+known)",
                    clause[match.end() :],
                    re.I,
                ):
                    continue
                n = number(match["line"].replace(",", ""))
                rows = [(r.unit, text) for r in reads for at, text in r.numbered if at == n]
                if (
                    not rows
                    or len({unit for unit, _ in rows}) != 1
                    or len({text for _, text in rows}) != 1
                ):
                    continue
                # Blank comments/literals with the worker's SQL lexer. Token equality
                # prevents RETURN TRUE from matching RETURN TRUE_VALUE.
                from harness_worker.statement import strip_literals_and_comments

                wanted = re.findall(
                    r"[A-Za-z_][\w$#]*|[^\s]", strip_literals_and_comments(fragment).upper()
                )
                by_line = {
                    at: text for r in reads for at, text in r.numbered if r.unit == rows[0][0]
                }
                first_tokens = re.findall(
                    r"[A-Za-z_][\w$#]*|[^\s]", strip_literals_and_comments(by_line[n]).upper()
                )
                following = []
                for offset in range(1, 9):
                    if n + offset not in by_line:
                        break
                    following.extend(
                        re.findall(
                            r"[A-Za-z_][\w$#]*|[^\s]",
                            strip_literals_and_comments(by_line[n + offset]).upper(),
                        )
                    )
                # A quoted identifier may name a literal label (e.g. a log step),
                # not an executable statement. Check such one-token labels against
                # raw row text; SQL/PLSQL statement tokens still use the code lexer.
                if re.fullmatch(IDENTIFIER, fragment) and not re.match(
                    rf"^(?:{_STATEMENT_WORDS})\b", fragment, re.I
                ):
                    first_tokens = re.findall(r"[A-Za-z_][\w$#]*|[^\s]", by_line[n].upper())
                tokens = first_tokens + following
                # A statement can span rows; its first token must occur on N, not
                # merely on a later line. This accepts an IF ... THEN RETURN FALSE
                # summarized on one prose line, without accepting RETURN TRUE on
                # the preceding END IF's line.
                present = any(
                    tokens[i : i + len(wanted)] == wanted for i in range(len(first_tokens))
                )
                if not present and not re.match(rf"^(?:{_STATEMENT_WORDS})\b", fragment, re.I):
                    # Abbreviated declarations can omit type/CONSTANT qualifiers.
                    remaining = iter(first_tokens)
                    present = all(any(token == value for token in remaining) for value in wanted)
                if present:
                    continue
                findings.append(
                    ClaimFinding(
                        "evidence_reference",
                        match[0],
                        f"The cited retained source at line {n} does not contain {fragment!r}. "
                        "Verify the statement against the returned line text; do not infer an "
                        "offset from a nearby member or statement. Leave unread locations unknown.",
                    )
                )
    return findings
