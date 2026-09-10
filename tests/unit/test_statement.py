"""Statement inspection.

The rule these tests protect: the worksheet takes one SQL statement or one complete
PL/SQL unit, and anything else is refused with a message that says what it found.
"""

from __future__ import annotations

import pytest

from harness_worker.errors import ValidationError
from harness_worker.statement import (
    bind_names,
    classify,
    fingerprint,
    prepare,
    quote_identifier,
    strip_literals_and_comments,
)
from harness_worker.types import StatementKind


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1 FROM dual", StatementKind.QUERY),
        ("  with x as (select 1 from dual) select * from x", StatementKind.QUERY),
        ("UPDATE employees SET salary = 1", StatementKind.DML),
        ("MERGE INTO a USING b ON (1=1)", StatementKind.DML),
        ("CREATE TABLE t (id NUMBER)", StatementKind.DDL),
        ("BEGIN NULL; END;", StatementKind.PLSQL_BLOCK),
        ("DECLARE x NUMBER; BEGIN NULL; END;", StatementKind.PLSQL_BLOCK),
        ("CREATE OR REPLACE PACKAGE BODY p AS END;", StatementKind.PLSQL_SOURCE),
        ("COMMIT", StatementKind.TRANSACTION_CONTROL),
        ("ALTER SESSION SET current_schema = HR", StatementKind.SESSION_CONTROL),
    ],
)
def test_classification(sql: str, expected: StatementKind) -> None:
    assert classify(sql) is expected


def test_single_trailing_semicolon_is_accepted() -> None:
    statement, kind = prepare("SELECT 1 FROM dual;")
    assert kind is StatementKind.QUERY
    assert statement == "SELECT 1 FROM dual"


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        # The terminator is removed from where it actually is, so nothing that follows
        # it is eaten. Trimming the last character of the raw text instead used to cut
        # into the trailing comment and leave the semicolon in place.
        ("SELECT 1 FROM dual; -- done", "SELECT 1 FROM dual -- done"),
        ("SELECT 1 FROM dual; /* done */", "SELECT 1 FROM dual /* done */"),
        ("SELECT 1 FROM dual;\n-- one\n-- two", "SELECT 1 FROM dual\n-- one\n-- two"),
        ("SELECT 1 FROM dual /* mid */;", "SELECT 1 FROM dual /* mid */"),
        ("SELECT 1 FROM dual;   \n\t ", "SELECT 1 FROM dual"),
        ("SELECT 1 FROM dual  ;", "SELECT 1 FROM dual"),
        ("  SELECT 1 FROM dual;  ", "SELECT 1 FROM dual"),
        # A semicolon that is not a terminator is left exactly where it is.
        ("SELECT 'a;b' FROM dual;", "SELECT 'a;b' FROM dual"),
        ("SELECT 1 FROM dual -- a ; b", "SELECT 1 FROM dual -- a ; b"),
    ],
)
def test_a_trailing_terminator_is_removed_without_touching_the_rest(
    sql: str, expected: str
) -> None:
    statement, kind = prepare(sql)
    assert statement == expected
    assert kind is StatementKind.QUERY


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        # A '/' on its own line is the SQL*Plus run terminator and is dropped; the
        # block's own END; is the PL/SQL terminator and is kept.
        ("BEGIN NULL; END;\n/", "BEGIN NULL; END;"),
        ("BEGIN NULL; END;\n/\n\n", "BEGIN NULL; END;"),
        ("DECLARE x NUMBER; BEGIN NULL; END;", "DECLARE x NUMBER; BEGIN NULL; END;"),
    ],
)
def test_plsql_terminators_are_preserved(sql: str, expected: str) -> None:
    statement, kind = prepare(sql)
    assert statement == expected
    assert kind is StatementKind.PLSQL_BLOCK


@pytest.mark.parametrize(
    "sql",
    [
        # A '/' that closes a block comment is not a run terminator, and removing it
        # would leave an unterminated comment for Oracle to choke on.
        "SELECT 1 FROM dual /* keep me */",
        "SELECT a/b FROM dual",
        "SELECT '/' FROM dual",
    ],
)
def test_a_slash_that_is_not_a_run_terminator_is_kept(sql: str) -> None:
    statement, _ = prepare(sql)
    assert statement == sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 FROM dual; SELECT 2 FROM dual;",
        "SELECT 1 FROM dual;\nSELECT 2 FROM dual",
        "SELECT 1 FROM dual; SELECT 2 FROM dual; -- and a comment",
    ],
)
def test_multiple_statements_are_still_refused_with_a_terminator_present(sql: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        prepare(sql)
    assert excinfo.value.detail["reason"] == "multiple_statements"


def test_two_statements_are_refused() -> None:
    with pytest.raises(ValidationError) as excinfo:
        prepare("SELECT 1 FROM dual; SELECT 2 FROM dual")
    assert excinfo.value.detail["reason"] == "multiple_statements"


def test_semicolons_inside_a_plsql_block_are_kept() -> None:
    block = "BEGIN\n  DBMS_OUTPUT.PUT_LINE('a');\n  DBMS_OUTPUT.PUT_LINE('b');\nEND;"
    statement, kind = prepare(block)
    assert kind is StatementKind.PLSQL_BLOCK
    assert statement.count(";") == 3


def test_semicolon_inside_a_string_literal_is_not_a_statement_break() -> None:
    statement, kind = prepare("SELECT 'a; b' FROM dual")
    assert kind is StatementKind.QUERY
    assert statement.endswith("FROM dual")


def test_semicolon_inside_a_comment_is_not_a_statement_break() -> None:
    statement, _ = prepare("SELECT 1 FROM dual -- trailing ; comment")
    assert "trailing" in statement


def test_client_commands_are_refused_rather_than_ignored() -> None:
    for command in ("SET SERVEROUTPUT ON", "@script.sql", "SPOOL out.txt", "EXIT"):
        with pytest.raises(ValidationError) as excinfo:
            prepare(command)
        assert excinfo.value.detail["reason"] == "client_command"


def test_substitution_variables_are_refused() -> None:
    with pytest.raises(ValidationError) as excinfo:
        prepare("SELECT * FROM employees WHERE department_id = &dept")
    assert excinfo.value.detail["reason"] == "substitution_variable"


def test_an_ampersand_inside_a_literal_is_not_a_substitution_variable() -> None:
    statement, _ = prepare("SELECT 'Tom & Jerry' FROM dual")
    assert "Jerry" in statement


def test_empty_and_comment_only_selections_are_refused() -> None:
    with pytest.raises(ValidationError):
        prepare("   ")
    with pytest.raises(ValidationError):
        prepare("-- just a note")


def test_bind_names_ignores_literals_and_type_casts() -> None:
    sql = "SELECT :a, ':b' FROM t WHERE c = :c AND d = :a"
    assert bind_names(sql) == ["a", "c"]


def test_quote_identifier_refuses_embedded_quotes() -> None:
    assert quote_identifier("employees") == '"employees"'
    with pytest.raises(ValidationError):
        quote_identifier('bad" OR 1=1 --')
    with pytest.raises(ValidationError):
        quote_identifier("")


def test_fingerprint_ignores_literals_whitespace_and_case() -> None:
    a = fingerprint("select * from t where name = 'alice'")
    b = fingerprint("SELECT   *\n  FROM t\n WHERE name = 'bob'")
    assert a == b
    assert a != fingerprint("SELECT * FROM other")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT q'[it's; fine]' FROM dual",
        "SELECT q'{a; b}' FROM dual",
        "SELECT Q'<a; b>' FROM dual",
        "SELECT q'(a; b)' FROM dual",
        "SELECT q'!a; b!' FROM dual",
        "SELECT nq'#a; b#' FROM dual",
    ],
)
def test_alternative_quoting_holds_its_own_semicolons(sql: str) -> None:
    """q'[...]' is one literal, however many quotes and semicolons are inside it.

    Masking it as an ordinary literal ends at the first embedded quote and leaves the
    rest of the string looking like SQL, which is how a single valid statement came to
    be refused as more than one.
    """

    statement, kind = prepare(sql)
    assert kind is StatementKind.QUERY
    assert statement == sql
    masked = strip_literals_and_comments(sql)
    assert len(masked) == len(sql)
    assert ";" not in masked


def test_a_q_that_is_not_a_quoting_prefix_is_left_alone() -> None:
    masked = strip_literals_and_comments("SELECT queue_id, nq_count FROM q_table")
    assert masked == "SELECT queue_id, nq_count FROM q_table"


def test_an_alternative_quoted_literal_is_excluded_from_the_fingerprint() -> None:
    assert fingerprint("SELECT q'[alice]' FROM t") == fingerprint("SELECT q'[bob]' FROM t")


def test_masking_preserves_offsets() -> None:
    sql = "SELECT 'abc' /* note */ FROM t"
    masked = strip_literals_and_comments(sql)
    assert len(masked) == len(sql)
    assert "abc" not in masked
    assert "note" not in masked
    assert masked.strip().startswith("SELECT")
