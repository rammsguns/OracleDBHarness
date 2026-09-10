"""Statement inspection: one statement in, one classified statement out.

MVP_PLAN.md is explicit that scripts are not split on every semicolon. The harness
accepts exactly one SQL statement or one complete PL/SQL unit, and rejects anything
else with a message that says what it found. Classification here is used for policy
decisions and for the audit record; it is deliberately *not* treated as a security
boundary, because a SELECT can still call a function that writes.
"""

from __future__ import annotations

import hashlib
import re

from harness_worker.errors import ValidationError
from harness_worker.types import StatementKind

# Leading keywords that identify a PL/SQL unit whose body legitimately contains
# semicolons. Everything else is a single SQL statement.
_PLSQL_BLOCK_START = re.compile(r"^(DECLARE|BEGIN)\b", re.IGNORECASE)
_PLSQL_SOURCE_START = re.compile(
    r"^CREATE\s+(OR\s+REPLACE\s+)?(EDITIONABLE\s+|NONEDITIONABLE\s+)?"
    r"(PACKAGE\s+BODY|PACKAGE|PROCEDURE|FUNCTION|TRIGGER|TYPE\s+BODY|TYPE|LIBRARY)\b",
    re.IGNORECASE,
)
_DDL_START = re.compile(
    r"^(CREATE|ALTER|DROP|TRUNCATE|RENAME|COMMENT|GRANT|REVOKE|ANALYZE|AUDIT|NOAUDIT|"
    r"ASSOCIATE|DISASSOCIATE|FLASHBACK|PURGE)\b",
    re.IGNORECASE,
)
_DML_START = re.compile(r"^(INSERT|UPDATE|DELETE|MERGE)\b", re.IGNORECASE)
_QUERY_START = re.compile(r"^(SELECT|WITH)\b", re.IGNORECASE)
_TRANSACTION_START = re.compile(r"^(COMMIT|ROLLBACK|SAVEPOINT|SET\s+TRANSACTION)\b", re.IGNORECASE)
_SESSION_START = re.compile(r"^(ALTER\s+SESSION|SET\s+ROLE)\b", re.IGNORECASE)

# SQL*Plus / SQLcl client commands. These are not database statements and are out of
# scope for the MVP; silently ignoring them would change what the user thinks ran.
_CLIENT_COMMAND = re.compile(
    r"^(@@?|/\s*$|SET\s+(SERVEROUTPUT|LINESIZE|PAGESIZE|FEEDBACK|ECHO|HEADING|DEFINE|TIMING|"
    r"SQLBLANKLINES|VERIFY|TRIMSPOOL|COLSEP|LONG|AUTOTRACE)\b|SPOOL\b|PROMPT\b|ACCEPT\b|"
    r"DEFINE\b|UNDEFINE\b|COLUMN\b|CONNECT\b|DISCONNECT\b|EXIT\b|QUIT\b|START\b|EDIT\b|"
    r"DESCRIBE\b|DESC\b|SHOW\b|VARIABLE\b|EXECUTE\b|EXEC\b)",
    re.IGNORECASE,
)

_SUBSTITUTION_VARIABLE = re.compile(r"(?<![&\w])&{1,2}[A-Za-z_][A-Za-z0-9_$#]*")

# Statement kinds Oracle commits the current transaction for, whether or not the user
# asked. A CREATE OR REPLACE program unit is DDL as much as CREATE TABLE is. Shared
# with the execution engine, which has to make the same judgement under the session
# lock that the API makes when it admits the request.
IMPLICITLY_COMMITTING_KINDS = frozenset({StatementKind.DDL, StatementKind.PLSQL_SOURCE})

_UNQUOTED_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")

_BIND_REFERENCE = re.compile(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_$#]*|\d+)")

# Oracle's alternative quoting mechanism, q'<delimiter>...<delimiter>'. A single
# quote inside one of these is ordinary text, so masking it as if it ended at that
# quote would leave the rest of the literal -- semicolons included -- looking like
# SQL. A bracketing delimiter closes with its mirror image; any other closes with
# itself. The national-character form nq'...' is the same literal with a prefix.
_ALTERNATIVE_QUOTE_MIRRORS = {"[": "]", "{": "}", "<": ">", "(": ")"}
_IDENTIFIER_CHAR = re.compile(r"[A-Za-z0-9_$#]")


def _alternative_quote_end(sql: str, i: int) -> int | None:
    """End offset of the ``q'...'`` literal starting at ``i``, or None if there is none.

    ``i`` is the ``q``, or the ``n`` of the national-character form. An unterminated
    literal runs to the end of the text, as it does for ordinary quoting.
    """

    j = i + 1 if sql[i] in "nN" else i
    if sql[j : j + 2].lower() != "q'":
        return None
    delimiter = sql[j + 2 : j + 3]
    # Oracle refuses a space, tab, newline or single quote as the delimiter.
    if not delimiter or delimiter.isspace() or delimiter == "'":
        return None
    closer = _ALTERNATIVE_QUOTE_MIRRORS.get(delimiter, delimiter) + "'"
    end = sql.find(closer, j + 3)
    return len(sql) if end == -1 else end + len(closer)


def strip_literals_and_comments(sql: str) -> str:
    """Replace string literals and comments with equal-length blanks.

    Both ordinary literals and Oracle's alternative quoting (``q'[...]'``) are
    masked. Positions are preserved so callers can report offsets against the
    original text. Quoted identifiers are kept, because identifier text matters for
    classification.
    """

    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif ch == "/" and nxt == "*":
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(" " * (j - i))
            i = j
        elif ch in "qQnN" and (i == 0 or not _IDENTIFIER_CHAR.match(sql[i - 1])):
            end = _alternative_quote_end(sql, i)
            if end is None:
                out.append(ch)
                i += 1
            else:
                out.append(" " * (end - i))
                i = end
        elif ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(" " * (j - i))
            i = j
        elif ch == '"':
            j = sql.find('"', i + 1)
            j = n if j == -1 else j + 1
            out.append(sql[i:j])
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def normalize(sql: str) -> str:
    """Trim whitespace and a trailing SQL*Plus run terminator.

    Only a real terminator is removed: one that stands alone on its own line in the
    masked text. A ``/`` that closes a block comment, or one inside a string literal
    or an expression, belongs to the statement and stays.
    """

    text = sql.strip()
    while True:
        masked = strip_literals_and_comments(text).rstrip()
        if not masked.endswith("/"):
            return text
        index = len(masked) - 1
        line_start = masked.rfind("\n", 0, index) + 1
        if masked[line_start:index].strip():
            return text
        text = (text[:index] + text[index + 1 :]).rstrip()


def classify(sql: str) -> StatementKind:
    masked = strip_literals_and_comments(sql).strip()
    if not masked:
        return StatementKind.UNKNOWN
    if _PLSQL_BLOCK_START.match(masked):
        return StatementKind.PLSQL_BLOCK
    if _PLSQL_SOURCE_START.match(masked):
        return StatementKind.PLSQL_SOURCE
    if _SESSION_START.match(masked):
        return StatementKind.SESSION_CONTROL
    if _TRANSACTION_START.match(masked):
        return StatementKind.TRANSACTION_CONTROL
    if _QUERY_START.match(masked):
        return StatementKind.QUERY
    if _DML_START.match(masked):
        return StatementKind.DML
    if _DDL_START.match(masked):
        return StatementKind.DDL
    return StatementKind.UNKNOWN


def is_plsql(kind: StatementKind) -> bool:
    return kind in (StatementKind.PLSQL_BLOCK, StatementKind.PLSQL_SOURCE)


def prepare(sql: str) -> tuple[str, StatementKind]:
    """Validate one statement and return it ready for the driver.

    Raises ValidationError when the input is empty, is a client-side command, uses
    substitution variables, or contains more than one statement.
    """

    text = normalize(sql)
    if not text:
        raise ValidationError("No statement was supplied.")

    masked = strip_literals_and_comments(text)
    stripped_masked = masked.strip()
    if not stripped_masked:
        raise ValidationError("The selection contains only comments.")

    if _CLIENT_COMMAND.match(stripped_masked):
        raise ValidationError(
            "SQL*Plus and SQLcl client commands are not supported in the MVP worksheet. "
            "Select a single SQL statement or a complete PL/SQL block.",
            detail={"reason": "client_command"},
        )

    substitution = _SUBSTITUTION_VARIABLE.search(masked)
    if substitution:
        raise ValidationError(
            "Substitution variables are not supported. Use a bind parameter instead of "
            f"{substitution.group(0)!r}.",
            detail={"reason": "substitution_variable", "token": substitution.group(0)},
        )

    kind = classify(text)

    if is_plsql(kind):
        # A PL/SQL unit owns its internal semicolons; the driver takes the whole body.
        return text, kind

    # The terminator is located in the masked text, where a semicolon inside a literal
    # or a comment has already been blanked out, and then removed from the original at
    # that same offset. Masking preserves positions precisely so this is possible.
    # Trimming a character off the end of the raw text instead would cut into whatever
    # trailing comment follows the terminator.
    body = masked.rstrip()
    if body.endswith(";"):
        terminator = len(body) - 1
        body = body[:terminator]
        text = (text[:terminator] + text[terminator + 1 :]).rstrip()
    extra = body.find(";")
    if extra != -1:
        raise ValidationError(
            "More than one statement was selected. The worksheet runs one SQL statement "
            "or one complete PL/SQL block at a time.",
            detail={"reason": "multiple_statements", "offset": extra},
        )
    return text, kind


def bind_names(sql: str) -> list[str]:
    """Bind placeholders referenced by a statement, in first-appearance order."""

    masked = strip_literals_and_comments(sql)
    seen: list[str] = []
    for match in _BIND_REFERENCE.finditer(masked):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def quote_identifier(name: str) -> str:
    """Quote a database identifier for safe interpolation.

    Identifiers cannot be bound, so anything interpolated into generated DDL or
    dictionary predicates passes through here. A NUL or embedded double quote is
    rejected rather than escaped, because neither can appear in a legitimate name
    the harness needs to address.
    """

    if not name:
        raise ValidationError("An empty database identifier was supplied.")
    if len(name) > 128:
        raise ValidationError("Database identifiers are limited to 128 characters.")
    if '"' in name or "\x00" in name:
        raise ValidationError(
            "Database identifiers containing a double quote or NUL are refused.",
            detail={"identifier": name[:64]},
        )
    return '"' + name + '"'


def is_simple_identifier(name: str) -> bool:
    """True when the name needs no quoting and no case folding surprises."""

    return bool(_UNQUOTED_IDENTIFIER.match(name))


def fingerprint(sql: str) -> str:
    """Stable hash of a statement with literals removed.

    Audit records keep the fingerprint by default. Raw SQL may contain secrets, so it
    is retained only where the deployment has enabled statement retention.
    """

    masked = strip_literals_and_comments(sql)
    collapsed = re.sub(r"\s+", " ", masked).strip().upper()
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()
