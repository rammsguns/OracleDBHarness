"""A structural check of proposed code: does it look like one whole unit?

A proposal replaces the editor buffer, so it has to be the complete statement or program
unit and not a fragment of one. A bare ``FUNCTION f ... END f;`` is neither a stored
routine (that needs ``CREATE OR REPLACE``) nor a block, and only the code's surroundings
say whether it is a standalone routine or a member of a package. This module does not
guess which: it reports the fragment and what each reading would need.

This is **not** an Oracle compilation and not a PL/SQL parser. It runs the worksheet's
statement preparer and then a few conservative boundary checks on text with its comments,
literals and quoted identifiers blanked out:

* parentheses balance;
* a PL/SQL unit's ``BEGIN``/``CASE``/``IF``/``LOOP`` openers and ``END`` closers balance,
  with the ``IS``/``AS`` of a subprogram, package or ``DECLARE`` merging into the
  ``BEGIN`` that follows it, as it does in PL/SQL;
* a routine or block has a body, and the unit ends at its terminating ``;``;
* nothing follows the unit: a second statement is not part of the replacement.

``complete`` therefore means "no boundary problem was found", never "this compiles". The
result says so, and ``compiled`` is always False. Code that defeats the keyword scan
(conditional compilation, unusual layouts) may be reported incomplete; it is never
rewritten or blocked by this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from harness_worker.errors import ValidationError
from harness_worker.statement import normalize, prepare, strip_literals_and_comments
from harness_worker.types import StatementKind

# What a package member, or a routine pasted without its CREATE, starts with.
_FRAGMENT_START = re.compile(r"^(FUNCTION|PROCEDURE|PRAGMA|TYPE|SUBTYPE|CURSOR)\b", re.IGNORECASE)
_CREATE_ROUTINE = re.compile(
    r"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:NON)?EDITIONABLE\s+)?(?:FUNCTION|PROCEDURE)\b",
    re.IGNORECASE,
)
# Subprogram properties such as AUTHID come before IS/AS, never after.
_AUTHID_AFTER_IS = re.compile(r"\b(?:IS|AS)\s+(?:AUTHID|ACCESSIBLE\s+BY|DETERMINISTIC)\b", re.I)
_QUOTED = re.compile(r'"[^"]*"')
_WORD = re.compile(r"(?<![\w$#:.])([A-Za-z][\w$#]*)|([();])|(?<![\w$#])(\d[\w.]*)")
_EXTERNAL_BODY = {"LANGUAGE", "EXTERNAL"}
# Words that cannot be the unit name after a final END: they start another statement or
# clause, so ``END SELECT 1 FROM dual;`` is not a closed unit. A quoted name is blanked
# before the scan and so is never mistaken for one of these.
_NOT_A_NAME = frozenset(
    {
        "SELECT",
        "WITH",
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "CREATE",
        "ALTER",
        "DROP",
        "GRANT",
        "REVOKE",
        "TRUNCATE",
        "BEGIN",
        "DECLARE",
        "END",
        "FROM",
        "WHERE",
        "EXCEPTION",
        "COMMIT",
        "ROLLBACK",
        "SAVEPOINT",
        "NULL",
        "RETURN",
        "IS",
        "AS",
        "THEN",
        "ELSE",
        "ELSIF",
        "WHEN",
        "SET",
    }
)
# What a recognisable SQL or PL/SQL block in an answer starts with, as against error
# text or program output, which a fence often holds too.
_CODE_START = re.compile(
    r"^(SELECT|WITH|INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|GRANT|REVOKE|TRUNCATE|"
    r"BEGIN|DECLARE|SET|SPOOL|PROMPT)\b",
    re.IGNORECASE,
)

CHECK_NOTE = (
    "Structural check of statement boundaries only. Nothing was compiled or run, so a "
    "unit that passes may still fail to compile."
)


@dataclass(frozen=True)
class ProposalCheck:
    kind: str
    complete: bool
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "complete": self.complete,
            "problems": list(self.problems),
            "compiled": False,
            "note": CHECK_NOTE,
        }


def _tokens(text: str) -> list[str]:
    """Upper-cased words and the punctuation ( ) ; of ``text``, literals blanked."""

    masked = _QUOTED.sub(lambda m: " " * len(m.group(0)), strip_literals_and_comments(text))
    return [(m.group(1) or m.group(2) or m.group(3)).upper() for m in _WORD.finditer(masked)]


def _unit_problems(tokens: list[str], *, needs_body: bool) -> list[str]:
    """Boundary problems of a PL/SQL unit, from a bracket-style scan of its keywords."""

    problems: list[str] = []
    # Each scope: [kind, begun]. "decl" scopes (DECLARE, a subprogram or package IS/AS)
    # merge with the BEGIN that follows, so one END closes both.
    stack: list[list[Any]] = []
    parens = 0
    header = False  # a FUNCTION / PROCEDURE / PACKAGE / TYPE BODY header is open
    saw_begin = False
    external = False
    unit_end: int | None = None
    previous = ""
    skip_next = False

    for index, word in enumerate(tokens):
        if skip_next:
            skip_next = False
            previous = word
            continue
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if word == "(":
            parens += 1
        elif word == ")":
            parens -= 1
        elif word == ";":
            header = False
        elif parens > 0:
            pass
        elif word in {"FUNCTION", "PROCEDURE"} and previous != "END":
            header = True
        elif word == "PACKAGE" and previous != "END":
            header = True
        elif word == "BODY" and previous == "TYPE":
            header = True
        elif word == "TRIGGER" and previous == "COMPOUND":
            stack.append(["decl", True])
        elif word in _EXTERNAL_BODY and header:
            external = True
        elif word in {"IS", "AS"} and header:
            header = False
            stack.append(["decl", False])
        elif word == "DECLARE":
            stack.append(["decl", False])
        elif word == "BEGIN":
            saw_begin = True
            if stack and stack[-1][0] == "decl" and not stack[-1][1]:
                stack[-1][1] = True
            else:
                stack.append(["begin", True])
        elif word in {"CASE", "IF", "LOOP"} and previous != "END":
            stack.append([word, True])
        elif word == "END":
            if not stack:
                problems.append("An END has no matching BEGIN, CASE, IF, LOOP or IS/AS.")
                break
            stack.pop()
            if following in {"IF", "LOOP", "CASE"}:
                skip_next = True
            if not stack and unit_end is None:
                unit_end = index
        previous = word

    if parens != 0:
        problems.append("Parentheses do not balance.")
    if stack:
        problems.append(
            "The unit is not closed: a BEGIN, IS/AS, CASE, IF or LOOP has no matching END."
        )
    if needs_body and not saw_begin and not external:
        problems.append("The routine or block has no BEGIN: there is no body.")
    if unit_end is not None and not problems:
        rest = [t for t in tokens[unit_end + 1 :]]
        # After the closing END comes an optional name, then the terminating semicolon.
        if ";" not in rest:
            problems.append("The unit does not end with a semicolon after its final END.")
        elif (name := rest[: rest.index(";")]) and (
            len(name) > 1 or name[0] in _NOT_A_NAME or not name[0][0].isalpha()
        ):
            problems.append(
                "The final END is followed by more than an optional unit name before its "
                "semicolon, so the unit is not closed there."
            )
        else:
            after = rest[rest.index(";") + 1 :]
            if after:
                problems.append(
                    "Something follows the unit's final END;. A replacement is one unit; "
                    "a second statement belongs in its own request."
                )
    return problems


def check_proposal(text: str) -> ProposalCheck:
    """Classify ``text`` and say whether it looks like one whole statement or unit."""

    masked = strip_literals_and_comments(text).strip()
    fragment = _FRAGMENT_START.match(masked)
    if fragment:
        member = fragment.group(1).upper()
        return ProposalCheck(
            kind="fragment",
            complete=False,
            problems=[
                f"The text starts with a bare {member}, which is a fragment: a "
                "stored routine needs CREATE OR REPLACE in front, and a package member "
                "has to be returned inside its whole package body (and spec, when the "
                "specification changes). Return the complete unit."
            ],
        )
    try:
        _, kind = prepare(text)
    except ValidationError as exc:
        return ProposalCheck(kind="unrecognised", complete=False, problems=[exc.message])
    if kind == StatementKind.UNKNOWN:
        return ProposalCheck(
            kind=kind.value,
            complete=False,
            problems=[
                "The text does not start like a SQL statement, a PL/SQL block or a CREATE "
                "OR REPLACE program unit."
            ],
        )

    problems: list[str] = []
    body = normalize(text)
    tokens = _tokens(body)
    if tokens.count("(") != tokens.count(")"):
        problems.append("Parentheses do not balance.")
    if kind in (StatementKind.PLSQL_BLOCK, StatementKind.PLSQL_SOURCE):
        needs_body = kind == StatementKind.PLSQL_BLOCK or bool(_CREATE_ROUTINE.match(masked))
        problems += [p for p in _unit_problems(tokens, needs_body=needs_body) if p not in problems]
    if _CREATE_ROUTINE.match(masked) and _AUTHID_AFTER_IS.search(masked):
        problems.append("AUTHID and the other subprogram properties go before IS/AS, not after it.")
    return ProposalCheck(kind=kind.value, complete=not problems, problems=problems)


def fragments(blocks: list[str]) -> list[dict[str, Any]]:
    """The code blocks of an answer that are not whole units, with why."""

    found = []
    for index, text in enumerate(blocks, start=1):
        result = check_proposal(text)
        # Unlabelled blocks are often error text or a quoted line, so only code that is
        # recognisably a fragment, a PL/SQL unit with a boundary problem, or SQL the
        # preparer refused (several statements, client commands) is reported.
        if result.complete:
            continue
        unit = result.kind in ("fragment", "plsql_source", "plsql_block")
        sql = _CODE_START.match(strip_literals_and_comments(text).strip()) is not None
        if unit or sql:
            found.append({"block": index, **result.as_dict()})
    return found
