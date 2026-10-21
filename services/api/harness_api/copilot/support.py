"""Which lookup can support which statement: a typed evidence map and the answer's items.

The C24 review (docs/k7-c23-c26-review-2026-10-18.md) found the claim checks matching by
overlapping words: a shared owner made another job's history count, a package name let
an unread member pass, and any string in a returned cell counted as an object the lookup
was about. This module replaces those overlaps with identities (the design is in
``claude_k7.md``, "C24 design").

The **evidence map** is built from the request's lookups only, never from the model's
text: per lookup number, the objects it is about (from its parameters and from typed
owner/name columns, not from other cells), its filters and page, and for source lookups
the lines returned per unit and the members those lines show, each a declaration or an
implementation read whole or in part.

The **answer items** are the answer's bullets and sentences outside code, each with the
diagnosis section it is under (when the answer uses the form the prompt asks for), the
lookups it cites and the qualified names it uses. Checks in ``claim_checks`` compare the
two. A match does not make a statement true; it rules out support the evidence cannot
give. Whether a cited row says what the prose says stays a reviewer's judgment.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from harness_api.copilot.fences import fenced_blocks

IDENTIFIER = r"[A-Za-z_][\w$#]*"

# Parameters that name the object a lookup is about, with ``owner``.
NAME_PARAMETERS = (
    "object_name",
    "table_name",
    "job_name",
    "package_name",
    "program_name",
    "chain_name",
)
# Parameters that page or bound a lookup rather than say what it is about.
PAGE_PARAMETERS = frozenset({"row_limit", "row_offset", "start_line", "end_line"})
_NOT_SCOPE = frozenset({"why", "owner", "object_type", *NAME_PARAMETERS, *PAGE_PARAMETERS})
# Row columns that name an object, in order of preference, and its type.
_NAME_COLUMNS = ("OBJECT_NAME", "NAME", "JOB_NAME", "TABLE_NAME", "PROGRAM_NAME")
_TYPE_COLUMNS = ("OBJECT_TYPE", "TYPE")
# Lookups that return a unit's own lines. A search returns only the lines that matched.
SOURCE_LOOKUPS = frozenset(
    {"schema.object_source", "schema.object_source_range", "schema.object_source_search"}
)
# Line and lookup numbers the model writes are parsed only this far (C27): a longer one is
# beyond any unit's lines, and is kept as this bound rather than converted digit for digit.
NUMBER_LIMIT = 10**9


def number(digits: str) -> int:
    """A model-written number, at most ``NUMBER_LIMIT``; never more than ten digits read."""

    digits = digits.lstrip("0") or "0"
    return NUMBER_LIMIT if len(digits) > 9 else int(digits)


def covers(lines: Sequence[int], low: int, high: int) -> bool:
    """Every line from ``low`` to ``high`` is among ``lines`` (sorted, distinct).

    Counted with two binary searches, so a model's interval is never enumerated.
    """

    if high < low:
        return False
    return bisect_right(lines, high) - bisect_left(lines, low) == high - low + 1


# Types whose source declares members without implementing them.
_SPECIFICATIONS = frozenset({"PACKAGE", "TYPE"})


class Lookup(Protocol):
    @property
    def operation_id(self) -> str: ...
    @property
    def succeeded(self) -> bool: ...
    @property
    def columns(self) -> Sequence[str]: ...
    @property
    def rows(self) -> Sequence[Sequence[Any]]: ...
    @property
    def parameters(self) -> dict[str, Any]: ...
    @property
    def sequence(self) -> int: ...


# -- identities ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    """A schema object's name. ``owner`` is "" when it was not said."""

    owner: str
    name: str

    def matches(self, other: Identity) -> bool:
        """Same name, and the same owner unless one side did not say it."""

        if self.name != other.name:
            return False
        return not self.owner or not other.owner or self.owner == other.owner

    def __str__(self) -> str:
        return f"{self.owner}.{self.name}" if self.owner else self.name


@dataclass(frozen=True)
class Subject:
    identity: Identity
    type: str = ""

    def __str__(self) -> str:
        return f"{self.identity} {self.type}".strip()


@dataclass(frozen=True)
class Member:
    """A ``PROCEDURE`` or ``FUNCTION`` that a source lookup's lines show."""

    name: str
    first_line: int
    implementation: bool  # False: a declaration (a specification, or a forward one)
    whole: bool  # an implementation read from its first line through its END
    unknown: str = ""  # why coverage cannot be established from the lines read

    def describe(self) -> str:
        if not self.implementation:
            shown = f"{self.name}: declared at line {self.first_line}, not implemented here"
            return f"{shown} (unverified: {self.unknown})" if self.unknown else shown
        if self.unknown:
            return (
                f"{self.name}: implementation from line {self.first_line}, coverage "
                f"unknown ({self.unknown})"
            )
        state = "read whole" if self.whole else "read in part"
        return f"{self.name}: implementation from line {self.first_line}, {state}"


@dataclass(frozen=True)
class SourceRead:
    unit: Subject
    lines: frozenset[int]
    members: tuple[Member, ...]
    texts: tuple[str, ...]
    numbered: tuple[tuple[int, str], ...] = ()  # each returned line, by number

    def describe_lines(self) -> str:
        if not self.lines:
            return f"{len(self.texts)} line(s)"
        return "lines " + _ranges(sorted(self.lines))


@dataclass(frozen=True)
class Evidence:
    """One lookup, as the support checks need it."""

    lookup: int
    operation_id: str
    succeeded: bool
    subjects: tuple[Subject, ...]
    filters: dict[str, str]
    page: dict[str, Any]
    source: tuple[SourceRead, ...]
    # String cells returned, upper case, with the column each came from: a value is
    # evidence about its field, not about an object it names (C24-C).
    cells: frozenset[tuple[str, str]] = frozenset()

    def identities(self) -> list[Identity]:
        return [s.identity for s in self.subjects] + [r.unit.identity for r in self.source]

    def supports(self, identity: Identity) -> bool:
        return any(identity.matches(known) for known in self.identities())

    def names(self) -> set[str]:
        return {identity.name for identity in self.identities()}

    def member_names(self) -> set[str]:
        return {m.name for read in self.source for m in read.members}

    def owners(self) -> set[str]:
        return {identity.owner for identity in self.identities() if identity.owner}

    def describe(self) -> str:
        what = ", ".join(str(s) for s in self.subjects[:3]) or self.operation_id
        return f"lookup {self.lookup} ({what})"

    def quotes(self, written: str, text: str) -> bool:
        """``text`` quotes ``written`` as a value this lookup returned in a field it names.

        The field is named by its column ("JOB_ACTION", "job action") or, for source,
        by a cited line whose text holds the name. A value elsewhere in the row, or a
        claim about the named object itself, is not a quotation of it.
        """

        word = re.compile(rf"(?<![\w$#.]){re.escape(written)}(?![\w$#])")
        for column, value in self.cells:
            if word.search(value) and _names_field(column, text):
                return True
        cited = line_references(text)
        return any(
            any(low <= at <= high for low, high in cited) and word.search(line.upper())
            for read in self.source
            for at, line in read.numbered
        )

    def returned(self, written: str) -> bool:
        """Some cell this lookup returned (a source line among them) holds ``written``."""

        word = re.compile(rf"(?<![\w$#.]){re.escape(written)}(?![\w$#])")
        return any(word.search(value) for _, value in self.cells)

    def fields_holding(self, written: str) -> list[str]:
        word = re.compile(rf"(?<![\w$#.]){re.escape(written)}(?![\w$#])")
        return sorted({column for column, value in self.cells if word.search(value)})


def _names_field(column: str, text: str) -> bool:
    """The text names the column: "JOB_ACTION", "job action", "the job's action", or by
    its last word when that is a noun of four letters or more ("whose action is")."""

    words = column.split("_")
    spoken = r"(?:[_ ]|['’]s\s+)".join(re.escape(word) for word in words)
    if len(words) > 1 and len(words[-1]) >= 4:
        spoken = rf"(?:{spoken}|{re.escape(words[-1])})"
    return re.search(rf"(?<![\w$#]){spoken}(?![\w$#])", text, re.IGNORECASE) is not None


def evidence_map(lookups: Iterable[Lookup]) -> dict[int, Evidence]:
    """Each numbered lookup's identities, filters, page and source coverage.

    Members are found over all of the request's lines of a unit together, so lookups
    that read adjoining ranges cover a member between them; each lookup's record lists
    the members whose first line it returned.
    """

    numbered = [lookup for lookup in lookups if lookup.sequence]
    entries = {lookup.sequence: _evidence(lookup) for lookup in numbered}
    confirmed = _confirmed_definitions(numbered)
    lines: dict[Subject, dict[int, str]] = {}
    for entry in entries.values():
        for read in entry.source:
            lines.setdefault(read.unit, {}).update(read.numbered)
    found = {
        unit: unit_members(unit, sorted(rows.items()), confirmed.get(unit, set()))
        for unit, rows in lines.items()
    }
    for number_, entry in entries.items():
        if entry.source:
            entries[number_] = replace(
                entry,
                source=tuple(
                    replace(
                        read,
                        members=tuple(
                            m for m in found.get(read.unit, ()) if m.first_line in read.lines
                        )
                        if read.numbered
                        else unit_members(read.unit, [(0, t) for t in read.texts], set()),
                    )
                    for read in entry.source
                ),
            )
    return entries


def _confirmed_definitions(lookups: Sequence[Lookup]) -> dict[Subject, set[tuple[int, str]]]:
    """Definition lines PL/Scope recorded, per unit: compiled code, not comment text."""

    confirmed: dict[Subject, set[tuple[int, str]]] = {}
    for lookup in lookups:
        if lookup.operation_id != "schema.plscope_identifiers" or not lookup.succeeded:
            continue
        upper = [str(column).upper() for column in lookup.columns]
        if not {"NAME", "IDENTIFIER_TYPE", "USAGE", "LINE"} <= set(upper):
            continue
        at = {column: upper.index(column) for column in upper}
        owner = str(lookup.parameters.get("owner") or "").upper()
        name = str(lookup.parameters.get("object_name") or "").upper()
        for row in lookup.rows:
            kind = (
                row[at["OBJECT_TYPE"]]
                if "OBJECT_TYPE" in at
                else lookup.parameters.get("object_type")
            )
            if (
                str(row[at["USAGE"]] or "").upper() in ("DEFINITION", "DECLARATION")
                and str(row[at["IDENTIFIER_TYPE"]] or "").upper() in ("PROCEDURE", "FUNCTION")
                and isinstance(row[at["LINE"]], int)
            ):
                unit = Subject(Identity(owner, name), str(kind or "").upper())
                confirmed.setdefault(unit, set()).add(
                    (row[at["LINE"]], str(row[at["NAME"]] or "").upper())
                )
    return confirmed


def _evidence(lookup: Lookup) -> Evidence:
    parameters = {
        name: value
        for name, value in lookup.parameters.items()
        if value is not None and value != ""
    }
    owner = str(parameters.get("owner") or "").upper()
    kind = str(parameters.get("object_type") or "").upper()
    subjects: list[Subject] = []
    for name in NAME_PARAMETERS:
        if isinstance(parameters.get(name), str):
            subjects.append(Subject(Identity(owner, parameters[name].upper()), kind))
    source: tuple[SourceRead, ...] = ()
    if lookup.succeeded:
        subjects += _row_subjects(lookup, owner)
        if lookup.operation_id in SOURCE_LOOKUPS:
            source = _source_reads(lookup, owner, kind)
    return Evidence(
        lookup=lookup.sequence,
        operation_id=lookup.operation_id,
        succeeded=lookup.succeeded,
        subjects=tuple(dict.fromkeys(subjects)),
        filters={
            name: str(value).upper()
            for name, value in parameters.items()
            if name not in _NOT_SCOPE and isinstance(value, str)
        },
        page={name: value for name, value in parameters.items() if name in PAGE_PARAMETERS},
        source=source,
        cells=frozenset(
            (str(column).upper(), cell.upper())
            for row in (lookup.rows if lookup.succeeded else ())
            for column, cell in zip(lookup.columns, row, strict=False)
            if isinstance(cell, str) and cell
        ),
    )


def _row_subjects(lookup: Lookup, owner: str) -> list[Subject]:
    """Objects named by typed owner/name column pairs, such as REFERENCED_OWNER/_NAME."""

    upper = [str(column).upper() for column in lookup.columns]
    pairs: list[tuple[str, int | None, int, int | None]] = []
    for prefix in sorted({c[: -len("OWNER")] for c in upper if c.endswith("OWNER")} | {""}):
        name_at = next(
            (upper.index(prefix + c) for c in _NAME_COLUMNS if prefix + c in upper), None
        )
        if name_at is None:
            continue
        owner_at = upper.index(prefix + "OWNER") if prefix + "OWNER" in upper else None
        type_at = next(
            (upper.index(prefix + c) for c in _TYPE_COLUMNS if prefix + c in upper), None
        )
        pairs.append((prefix, owner_at, name_at, type_at))
    subjects: list[Subject] = []
    for row in lookup.rows:
        for prefix, owner_at, name_at, type_at in pairs:
            name = row[name_at]
            if not isinstance(name, str) or not name:
                continue
            # The lookup's own owner applies to its own objects, not to referenced ones.
            row_owner = row[owner_at] if owner_at is not None else ("" if prefix else owner)
            kind = row[type_at] if type_at is not None else ""
            subjects.append(
                Subject(Identity(str(row_owner or "").upper(), name.upper()), str(kind).upper())
            )
    return subjects


_DEFINITION = re.compile(
    rf"^\s*(?:(?:OVERRIDING|FINAL|INSTANTIABLE|NOT)\s+)*"
    rf"(?:(?:MEMBER|STATIC|CONSTRUCTOR|MAP|ORDER)\s+)?(?:FUNCTION|PROCEDURE)\s+({IDENTIFIER})",
    re.IGNORECASE,
)


def code_lines(texts: Sequence[str]) -> tuple[list[str], str]:
    """Each line with comments and literals blanked, as PL/SQL reads it.

    Returns the code of each line and the state the scan ended in ("code", "comment"
    or "literal"). The lines must start in code: only lines whose start is known
    (``unit_members``) are scanned. This is a lexical scan, not a parser: it keeps
    comment and literal text from reading as a definition or an END.
    """

    code: list[str] = []
    state, closing = "code", ""
    for text in texts:
        out: list[str] = []
        i = 0
        while i < len(text):
            if state == "comment":
                end = text.find("*/", i)
                if end < 0:
                    out.append(" " * (len(text) - i))
                    break
                out.append(" " * (end + 2 - i))
                i, state = end + 2, "code"
                continue
            if state == "literal":
                end = text.find(closing, i)
                while end >= 0 and closing == "'" and text.startswith("''", end):
                    end = text.find(closing, end + 2)
                if end < 0:
                    out.append(" " * (len(text) - i))
                    break
                out.append(" " * (end + len(closing) - i))
                i, state = end + len(closing), "code"
                continue
            rest = text[i:]
            if rest.startswith("--"):
                out.append(" " * len(rest))
                break
            if rest.startswith("/*"):
                state = "comment"
                out.append("  ")
                i += 2
                continue
            quoted = re.match(r"[nN]?[qQ]'(.)", rest)
            if quoted and not (i and re.match(r"[\w$#]", text[i - 1])):
                opening = quoted[1]
                closing = {"[": "]", "{": "}", "<": ">", "(": ")"}.get(opening, opening) + "'"
                state = "literal"
                out.append(" " * quoted.end())
                i += quoted.end()
                continue
            if rest[0] in "'\"":
                closing = rest[0]
                state = "literal"
                out.append(" ")
                i += 1
                continue
            out.append(rest[0])
            i += 1
        code.append("".join(out))
    return code, state


def _source_reads(lookup: Lookup, owner: str, kind: str) -> tuple[SourceRead, ...]:
    upper = [str(column).upper() for column in lookup.columns]
    if "TEXT" not in upper:
        return ()
    text_at = upper.index("TEXT")
    line_at = upper.index("LINE") if "LINE" in upper else None
    name_at = upper.index("NAME") if "NAME" in upper else None
    type_at = upper.index("TYPE") if "TYPE" in upper else None
    owner_at = upper.index("OWNER") if "OWNER" in upper else None
    default = str(lookup.parameters.get("object_name") or "").upper()
    units: dict[Subject, list[tuple[int | None, str]]] = {}
    for row in lookup.rows:
        name = str(row[name_at]).upper() if name_at is not None else default
        if not name:
            continue
        unit = Subject(
            Identity(str(row[owner_at]).upper() if owner_at is not None else owner, name),
            str(row[type_at]).upper() if type_at is not None else kind,
        )
        line = row[line_at] if line_at is not None else None
        units.setdefault(unit, []).append(
            (
                int(line) if isinstance(line, int | float) and 0 < line < NUMBER_LIMIT else None,
                str(row[text_at] or ""),
            )
        )
    reads = []
    for unit, rows in units.items():
        numbered = sorted(rows, key=lambda r: (r[0] is None, r[0] or 0))
        lines = frozenset(n for n, _ in numbered if n is not None)
        reads.append(
            SourceRead(
                unit,
                lines,
                (),  # found over the request's lines of the unit, in evidence_map()
                tuple(t for _, t in numbered),
                tuple((n, t) for n, t in numbered if n is not None),
            )
        )
    return tuple(reads)


def unit_members(
    unit: Subject, rows: Sequence[tuple[int, str]], confirmed: set[tuple[int, str]]
) -> tuple[Member, ...]:
    """The members ``rows`` (line, text; sorted) define, and how far each was read.

    Lexical state is known only where it can be established (C24-B): from line 1 of the
    unit, or from a definition PL/Scope recorded at that line. Lines read without the
    lines before them may lie inside a comment or literal opened earlier, so a
    definition there is listed with coverage unknown, and nothing carries across lines
    not read. Where the state is known, comments and literals are blanked first, and an
    implementation is whole when an ``END name;`` in code follows it in the same
    unbroken lines. Rows numbered 0 have no line numbers: their start is never known.
    """

    members: list[Member] = []
    for run in _runs(rows):
        first = run[0][0]
        known = (
            0
            if first == 1
            else next(
                (
                    i
                    for i, (n, text) in enumerate(run)
                    if (found := _DEFINITION.match(text)) and (n, found[1].upper()) in confirmed
                ),
                None,
            )
        )
        unread_start = run if known is None else run[:known]
        why = (
            f"the lines from {first} were read without the lines before them, so whether "
            "it is code or inside a comment or literal opened earlier is not established"
            if first
            else "the lines have no line numbers, so their context is not established"
        )
        for n, text in unread_start:
            found = _DEFINITION.match(text)
            if found:
                head = text.split("--", 1)[0]
                declared = unit.type in _SPECIFICATIONS or (
                    head.rstrip().endswith(";") and not re.search(r"\b(?:IS|AS)\b", head, re.I)
                )
                members.append(Member(found[1].upper(), n, not declared, False, why))
        if known is not None:
            members += _known_members(unit, run[known:])
    return tuple(members)


def _runs(rows: Sequence[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """``rows`` split where a line number is skipped (unnumbered rows are one run)."""

    runs: list[list[tuple[int, str]]] = []
    for n, text in rows:
        if runs and (n == 0 or n == runs[-1][-1][0] + 1):
            runs[-1].append((n, text))
        else:
            runs.append([(n, text)])
    return runs


def _known_members(unit: Subject, run: Sequence[tuple[int, str]]) -> list[Member]:
    """Members in unbroken lines whose first line is known to start in code."""

    code, final = code_lines([text for _, text in run])
    members = []
    for index, (number_, _) in enumerate(run):
        found = _DEFINITION.match(code[index])
        if found is None:
            continue
        name = found[1].upper()
        # The header ends at the first ";" (a declaration) or IS/AS (an implementation).
        header = " ".join(code[index:])[found.end() :]
        ending = re.search(r";|\b(?:IS|AS)\b", header, re.IGNORECASE)
        declared = unit.type in _SPECIFICATIONS or (ending is not None and ending[0] == ";")
        whole, unknown = False, ""
        if ending is None and not declared:
            unknown = "its header does not end in the lines read"
        if not declared and not unknown:
            end = re.compile(rf"^\s*END\s+{re.escape(name)}\s*;", re.IGNORECASE)
            for line_code in code[index + 1 :]:
                if end.match(line_code):
                    whole = True
                    break
            else:
                if final != "code":
                    unknown = f"the lines read end inside a {final}"
        members.append(Member(name, number_, not declared, whole, unknown))
    return members


# -- answer items -------------------------------------------------------------------------

SECTIONS = ("finding", "evidence", "unknown", "remedy", "next")
_HEADING = re.compile(
    r"^(?P<hash>#{1,6}\s*)?(?P<bold>\*\*|__)?\s*(?P<name>findings?|evidence|unknowns?|"
    r"remedy|remedies|next\s+steps?|next\s+checks?)\s*(?:\([^)\n]*\))?\s*(?:\*\*|__)?\s*"
    r"(?P<colon>:)?"
    r"\s*(?:\*\*|__)?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
# Any other heading: "## Fix", "**Suggested change**", "**Not a cause:**" on its own line.
_OTHER_HEADING = re.compile(
    r"^(?:#{1,6}\s+.+|(?:\*\*|__)[^*_]{1,60}(?:\*\*|__)\s*:?|[A-Z][\w ()/-]{0,40}:)\s*$"
)
# A number after the first is another lookup unless a unit follows it: "(lookup 6, 19
# rows)" cites lookup 6 and gives its row count (Sync Point 2N, trial 25 KINJ-02).
_COUNTED = (
    r"(?!\d)(?!\s*(?:rows?|lines?|characters?|chars?|bytes?|ms|s|secs?|seconds?|minutes?|"
    r"runs?|failures?|columns?|objects?|executions?|calls?|times?|matches?|hits?|%)(?!\w))"
)
CITATION = re.compile(
    rf"\blookups?\s+(?P<list>\d+(?:\s*(?:,|–|-|/|to|and|&)\s*\d+{_COUNTED})*)", re.IGNORECASE
)
QUALIFIED = re.compile(
    rf"(?<![\w$#.])({IDENTIFIER})\.({IDENTIFIER})(?:\.({IDENTIFIER}))?(?![\w$#])"
)
# A dotted name that is not a schema object: a lookup's own id or a dictionary view.
# Oracle-supplied packages (DBMS_SCHEDULER.DISABLE) are not objects a lookup read either.
_NOT_AN_OBJECT = re.compile(
    r"^(?:DBA|SCHEMA|TUNING|SYS|PUBLIC|STANDARD)$|__|^(?:ALL|DBA|USER|CDB)_|^G?V\$|"
    r"^(?:DBMS|UTL|OWA|HTP|HTF|CTX|ORD|APEX)_",
    re.IGNORECASE,
)
# A backticked span that is a name, not code: `APP.PKG`, `APP.PKG.run()`.
_NAME_SPAN = re.compile(rf"^\s*{IDENTIFIER}(?:\.{IDENTIFIER}){{1,2}}\s*(?:\(\s*\))?\s*$")


@dataclass(frozen=True)
class Segment:
    """Text a citation is attached to, and the dotted names in it."""

    citations: frozenset[int]
    text: str
    qualified: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Item:
    section: str  # one of SECTIONS, or "other"
    text: str
    citations: frozenset[int]
    qualified: tuple[tuple[str, ...], ...]  # upper-case parts of each dotted name
    segments: tuple[Segment, ...] = ()
    bullet: bool = False  # a list item, as the diagnosis form's Evidence bullets are


def heading_name(line: str) -> str | None:
    """The diagnosis section ``line`` heads (one of ``SECTIONS``), or None."""

    heading = _HEADING.match(line)
    if heading and (not heading["rest"] or heading["colon"] or heading["hash"]):
        return _section(heading["name"])
    return None


def other_heading(line: str) -> bool:
    """``line`` is a heading the diagnosis form does not have."""

    return heading_name(line) is None and _OTHER_HEADING.match(line) is not None


def answer_items(answer: str) -> list[Item]:
    """The answer's bullets and sentences outside code, by diagnosis section."""

    items: list[Item] = []
    section = "other"
    for line in prose_lines(answer):
        heading = _HEADING.match(line)
        # "Evidence shows ..." is a sentence; "Evidence:", "**Evidence**" or "## Evidence"
        # is the section.
        if heading and (not heading["rest"] or heading["colon"] or heading["hash"]):
            section = _section(heading["name"])
            line = heading["rest"]
            # "Next steps (max three)": a note on the heading, not an item.
            if not line.strip() or re.fullmatch(r"\(.*\)\s*:?", line.strip()):
                continue
        elif _OTHER_HEADING.match(line):
            section = "other"  # a heading the form does not have ends the last section
            continue
        if _BULLET.match(line):
            items.append(item(section, _BULLET.sub("", line, count=1), bullet=True))
        else:
            items.extend(item(section, part) for part in clauses(line))
    return items


def item(section: str, text: str, bullet: bool = False) -> Item:
    return Item(
        section,
        text,
        frozenset(cited(text)),
        tuple(qualified_names(text)),
        tuple(cited_segments(text)),
        bullet,
    )


# A citation that labels what follows: at the start ("Lookup 2 shows", "**Lookup 2** —",
# "As lookup 2 shows,", "According to lookup 2,") or followed by a colon or dash
# ("Job definition (lookup 1): ...").
_LEADING = re.compile(
    r"^[\s*_>(\[-]*(?:(?:in|per|from|by|and|as|see|according\s+to|based\s+on|as\s+"
    r"(?:shown|seen|returned)\s+(?:in|by))\s+)?(?:the\s+(?:output|result|rows?)\s+of\s+)?$",
    re.IGNORECASE,
)
_LABEL_AFTER = re.compile(r"^[\s)*_\]]*(?::|—|–|\s-\s)")
# What a leading citation's verb and comma leave: "shows, X", "shows that X".
_WORDS = re.compile(r"[A-Za-z0-9_$#]+")


def cited_segments(text: str) -> list[Segment]:
    """Each citation with the text it is attached to, sentence by sentence.

    A citation that labels what follows ("Lookup 1:", "(lookup 1): ...") or opens its
    sentence or clause after text naming nothing ("Lookup 2 shows ...", "As lookup 1
    shows, ...") covers the text after it, up to the next citation. A parenthetical one,
    or one with nothing after it, covers the text before it, back to the previous
    citation. Any other placement ("X is VALID per lookup 1, and Y ...") is not
    resolved to one side, so both sides are checked against it (C24-C). Text no
    citation is attached to is not checked against one: a sentence can name an object
    it does not claim the lookup showed.
    """

    segments: list[Segment] = []
    for sentence in clauses(text):
        matches = list(CITATION.finditer(sentence))
        for index, match in enumerate(matches):
            numbers = frozenset(cited(match.group(0)))
            previous = matches[index - 1].end() if index else 0
            following = matches[index + 1].start() if index + 1 < len(matches) else None
            before = sentence[previous : match.start()]
            after = sentence[match.end() : following]
            # A clause opened by the citation: "The spec is fine: lookup 1 shows ...".
            clause_start = max((before.rfind(mark) for mark in ":;,—"), default=-1) + 1
            # Inside parentheses ("(lines 1-18, lookup 3)") it is about what precedes them.
            parenthetical = before.rfind("(") > before.rfind(")")
            opens = not parenthetical and _LEADING.match(before[clause_start:]) is not None
            if _LABEL_AFTER.match(after):
                span = after
            elif parenthetical or not _WORDS.search(after):
                span = before
            elif opens and not qualified_names(before):
                span = after
            elif opens or qualified_names(after):
                span = f"{before} {after}"  # which side it supports is unresolved: check both
            else:
                span = before
            segments.append(Segment(numbers, span, tuple(qualified_names(span))))
    return segments


def _section(name: str) -> str:
    lowered = name.lower()
    for known in SECTIONS:
        if lowered.startswith(known):
            return known
    return "remedy" if lowered.startswith("remed") else "other"


def cited(text: str) -> set[int]:
    numbers: set[int] = set()
    for match in CITATION.finditer(text):
        listed = match["list"]
        for low, high in re.findall(r"(\d+)\s*(?:–|-|to)\s*(\d+)", listed):
            if 0 <= number(high) - number(low) <= 50:
                numbers.update(range(number(low), number(high) + 1))
        numbers.update(number(n) for n in re.findall(r"\d+", listed))
    return numbers


def qualified_names(text: str) -> list[tuple[str, ...]]:
    """Dotted names written as names: backticked, or upper case in prose.

    Mixed-case dotted words outside backticks are usually two sentences run together
    ("what.Reading"), so they are left out.
    """

    found: list[tuple[str, ...]] = []
    # A span of code (`VALUES (r.order_id, ...)`) is not a name: only whole-name spans.
    spans: list[str] = [s for s in re.findall(r"`([^`\n]+)`", text) if _NAME_SPAN.match(s)]
    for span in spans:
        found += [_parts(m) for m in QUALIFIED.finditer(span)]
    bare = re.sub(r"`[^`\n]*`", " ", text)
    found += [_parts(m) for m in QUALIFIED.finditer(bare) if m.group(0).upper() == m.group(0)]
    return [parts for parts in dict.fromkeys(found) if not _NOT_AN_OBJECT.search(parts[0])]


def _parts(match: re.Match[str]) -> tuple[str, ...]:
    return tuple(part.upper() for part in match.groups() if part)


def clauses(line: str) -> list[str]:
    """``line`` cut at sentence ends, keeping dotted names such as ``APP.PKG`` whole."""

    return [part for part in re.split(r"(?<=[.!?])\s+", line) if part.strip()]


# Where a sentence turns to another point: a semicolon, a dash, or a contrasting or
# conceding conjunction ("..., but the rest is unknown", "..., although ...").
_TURN = re.compile(
    # A dash between numbers is a range ("lines 2520 - 2585"), not a turn.
    r"\s*;\s*|\s*\u2014\s*|(?<!\d)\s+(?:\u2013|-{1,2})\s+(?!\d)"
    r"|,\s*(?=(?:but|although|though|whereas|while|yet|however|except)\b)"
    r"|\s+(?=but\s+(?:the|that|this|it|its|their|what|whether|which)\b)",
    re.IGNORECASE,
)
_CODE_SPAN = re.compile(r"`[^`\n]*`")
# A part that opens with a concession ends at its first comma: "Although X, Y".
_CONCEDING = re.compile(r"^(?:although|though|whereas|while|even\s+(?:though|if))\b", re.I)


def subclauses(clause: str) -> list[str]:
    """``clause`` cut where it turns to another point, so that a qualification in one
    part ("but the rest is unknown") is not read as qualifying a claim in another
    (Sync Point 2B). Each part keeps its conjunction. Code in backticks is not cut:
    ``RETURN FALSE; END IF;`` is one span."""

    spans = [(m.start(), m.end()) for m in _CODE_SPAN.finditer(clause)]
    cuts = [
        m
        for m in _TURN.finditer(clause)
        if not any(start <= m.start() < end for start, end in spans)
    ]
    pieces = []
    start = 0
    for cut in cuts:
        pieces.append(clause[start : cut.start()])
        start = cut.end()
    pieces.append(clause[start:])
    parts: list[str] = []
    for part in pieces:
        part = part.strip()
        if not part:
            continue
        if _CONCEDING.match(part) and ", " in part:
            head, tail = part.split(", ", 1)
            parts += [head, tail]
        else:
            parts.append(part)
    return parts


def prose_lines(answer: str) -> list[str]:
    """The answer's non-blank lines outside fenced blocks, stripped."""

    blocks = fenced_blocks(answer)
    lines: list[str] = []
    offset = 0
    for line in answer.splitlines(keepends=True):
        if not any(block.start <= offset < block.end for block in blocks):
            if line.strip():
                lines.append(line.strip())
        offset += len(line)
    return lines


# -- line references ---------------------------------------------------------------------

# "line 851", "lines 845–861": source lines a statement says it comes from.
_LINE_REFERENCE = re.compile(r"\blines?\s+(\d+)(?:\s*(?:–|—|-|to|through)\s*(\d+))?", re.IGNORECASE)
_NAME = rf"`?({IDENTIFIER}(?:\.{IDENTIFIER}){{0,2}})`?"
_KIND_WORDS = (
    r"(?:(?:package|type|procedure|function|trigger)\s+)?(?:(?:spec(?:ification)?|body)\s+)?"
)
# The unit a line reference is of: "lines 10-13 of the APP.PKG body", "APP.PKG lines 10-13".
_OF_UNIT = re.compile(rf"^\s*(?:of|in|from)\s+(?:the\s+)?{_KIND_WORDS}(?:of\s+)?{_NAME}", re.I)
_UNIT_BEFORE = re.compile(rf"{_NAME}(?:'s)?\s*(?:spec(?:ification)?|body)?\s*[,(]?\s*$", re.I)
_SPEC_WORD = re.compile(r"\b(?:spec|specification)\b|\bheader\b", re.IGNORECASE)
# A member's header ("procedure header at line 2949") is the first line of its
# implementation, which a body holds as well; only a header of no member names a
# specification (Sync Point 2T, trial 27 KINJ-01).
_MEMBER_HEADER = re.compile(
    r"\b(?:procedure|function|subprogram|member|routine|method|cursor)(?:'s)?[\s`]*$",
    re.IGNORECASE,
)
_BODY_WORD = re.compile(r"\bbody\b", re.IGNORECASE)


def line_references(text: str) -> list[tuple[int, int]]:
    """Each source-line interval the text cites, low to high."""

    found = []
    for match in _LINE_REFERENCE.finditer(text):
        low = number(match[1])
        high = number(match[2]) if match[2] else low
        found.append((low, high) if high >= low else (low, low))
    return found


@dataclass(frozen=True)
class LineUnits:
    """The units a line reference can be of. ``unread`` names one the text gives that no
    read returned."""

    subjects: tuple[Subject, ...]
    unread: str = ""


def line_units(text: str, reads: Sequence[SourceRead]) -> LineUnits:
    """Which of the units read a statement's line references are about (C24-A).

    A unit named next to the reference ("lines 10-13 of APP.PKG", "APP.PKG lines 10-13")
    binds it; otherwise a unit the text names that was read; otherwise every unit read,
    which leaves the reference unresolved when there are several. "spec" or "body"
    narrows the type. A name elsewhere in the text that was not read (a callee) does
    not bind the lines.
    """

    units = list(dict.fromkeys(read.unit for read in reads))
    spec, body = _names_specification(text), bool(_BODY_WORD.search(text))
    kinds = None if spec == body else (_SPECIFICATIONS if spec else {"PACKAGE BODY", "TYPE BODY"})
    bound: list[Identity] = []
    for match in _LINE_REFERENCE.finditer(text):
        after = _OF_UNIT.match(text[match.end() :])
        before = _UNIT_BEFORE.search(text[: match.start()])
        for found in (after, before):
            if found is None or _NOT_AN_OBJECT.search(found[1].split(".")[0]):
                continue
            parts = found[1].upper().split(".")
            # A bare word binds only as a unit that was read; a member, or a word such
            # as "source" or "at", leaves the reference to the units read.
            if len(parts) > 1 or any(u.identity.name == parts[0] for u in units):
                bound.append(_unit_identity(parts, units))
    if not bound:
        named = [_unit_identity(list(parts), units) for parts in qualified_names(text)]
        bare = re.findall(rf"`({IDENTIFIER})`|\b([A-Z][A-Z0-9_$#]+)\b", text)
        named += [Identity("", (a or b).upper()) for a, b in bare]
        bound = [n for n in named if any(u.identity.matches(n) for u in units)]
    chosen = [u for u in units if not bound or any(u.identity.matches(n) for n in bound)]
    if bound and not chosen:
        return LineUnits((), str(bound[0]))
    typed = [u for u in chosen if kinds is None or u.type in kinds]
    if not typed:
        word = "specification" if spec else "body"
        return LineUnits((), f"{chosen[0].identity} {word}")
    return LineUnits(tuple(typed))


def _names_specification(text: str) -> bool:
    """Whether ``text`` names a specification: "spec", "specification", or a header
    that is not a member's."""

    return any(
        word[0].lower() != "header" or not _MEMBER_HEADER.search(text[: word.start()])
        for word in _SPEC_WORD.finditer(text)
    )


def _unit_identity(parts: list[str], units: Sequence[Subject]) -> Identity:
    """The unit a dotted name refers to: OWNER.UNIT, UNIT.MEMBER or OWNER.UNIT.MEMBER."""

    if len(parts) >= 3:
        return Identity(parts[0], parts[1])
    if len(parts) == 2:
        whole = Identity(parts[0], parts[1])
        if any(u.identity.matches(whole) for u in units):
            return whole
        if any(u.identity.name == parts[0] for u in units):
            return Identity("", parts[0])
        return whole
    return Identity("", parts[0])


def line_ranges(numbers: Sequence[int], limit: int | None = None) -> str:
    """``numbers`` as runs; with ``limit``, the first runs and how many more there are."""

    runs = _ranges(numbers).split(", ")
    if limit is None or len(runs) <= limit:
        return ", ".join(runs)
    return f"{', '.join(runs[:limit])} and {len(runs) - limit} more runs"


# -- the record --------------------------------------------------------------------------


def record_entry(evidence: Evidence) -> dict[str, Any]:
    """What the lookup record shows of the map: subjects and source read, per lookup."""

    return {
        "subjects": [str(subject) for subject in evidence.subjects[:10]],
        "sourceRead": [
            {
                "unit": str(read.unit),
                "lines": read.describe_lines(),
                "members": [member.describe() for member in read.members],
            }
            for read in evidence.source
        ],
    }


def _ranges(numbers: Sequence[int]) -> str:
    runs: list[str] = []
    start = previous = numbers[0]
    for number in [*numbers[1:], None]:
        if number is not None and number == previous + 1:
            previous = number
            continue
        runs.append(f"{start}" if start == previous else f"{start}-{previous}")
        if number is not None:
            start = previous = number
    return ", ".join(runs)
