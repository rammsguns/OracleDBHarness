"""Binds, precision, encoding and identifiers.

The release criteria name binds, quoted identifiers, Unicode, NUMBER precision, dates
and time zones, and nulls. The stand-in has SQLite's type system: it has no NUMBER, no
TIMESTAMP WITH TIME ZONE, no NVARCHAR2, and case-insensitive identifiers. None of
these has ever been checked against a type system that distinguishes them.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from harness_worker.backend import OracleConnection
from harness_worker.errors import HarnessError
from harness_worker.types import ExecutionLimits, StatementKind
from tests.qualification.evidence import Evidence

UNICODE_SAMPLE = "こんにちは — café — مرحبا"


def _one(
    connection: OracleConnection,
    sql: str,
    binds: dict[str, Any],
    limits: ExecutionLimits,
) -> list[Any]:
    result = connection.execute(sql, binds, StatementKind.QUERY, limits)
    assert result.result_set is not None, sql
    assert result.result_set.rows, f"{sql} returned no rows"
    return result.result_set.rows[0]


def test_a_bind_is_a_bind_and_not_string_substitution(
    connection: OracleConnection,
    limits: ExecutionLimits,
) -> None:
    """The value that would end a statement if it were interpolated."""

    hostile = "'; DROP TABLE employees; --"
    row = _one(
        connection,
        "SELECT COUNT(*) FROM employees WHERE last_name = :name",
        {"name": hostile},
        limits,
    )
    assert row[0] == 0
    # The table is still here, which is the actual assertion.
    assert _one(connection, "SELECT COUNT(*) FROM employees", {}, limits)[0] == 6


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("n_integer", 2147483647),
        ("v_ascii", "plain ascii"),
        ("d_date", dt.datetime(2026, 2, 28)),
    ],
)
def test_scalar_binds_round_trip(
    connection: OracleConnection,
    limits: ExecutionLimits,
    column: str,
    value: Any,
) -> None:
    """Each scalar type used as an input bind finds the row it should."""

    row = _one(
        connection,
        f"SELECT id FROM harness_types WHERE {column} = :value",  # noqa: S608 - fixed literals
        {"value": value},
        limits,
    )
    assert row[0] == 1


def test_number_precision_survives_the_round_trip(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """NUMBER(20,10) is wider than a float64 can represent.

    If the driver hands back a float, a salary or a measurement is silently rounded
    somewhere between Oracle and the result grid, and nothing in the harness would
    notice.
    """

    row = _one(connection, "SELECT n_scaled FROM harness_types WHERE id = 1", {}, limits)
    value = row[0]
    evidence.note(
        "How does NUMBER(20,10) arrive?",
        f"As `{type(value).__name__}` with value `{value}`"
        + (
            ""
            if isinstance(value, Decimal)
            else ". **Not a Decimal** - precision is lost before the harness sees it."
        ),
    )
    assert Decimal(str(value)) == Decimal("1234567890.0123456789"), (
        f"Expected 1234567890.0123456789, got {value!r} of type {type(value).__name__}."
    )


def test_unicode_round_trips_in_both_directions(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """Read back what the fixture stored, and find it again by bind."""

    stored = _one(connection, "SELECT v_unicode FROM harness_types WHERE id = 1", {}, limits)[0]
    evidence.note("Unicode read back", f"`{stored!r}`")
    assert stored == UNICODE_SAMPLE, f"Expected {UNICODE_SAMPLE!r}, got {stored!r}"

    found = _one(
        connection,
        "SELECT id FROM harness_types WHERE v_unicode = :value",
        {"value": UNICODE_SAMPLE},
        limits,
    )
    assert found[0] == 1


def test_a_null_bind_is_null_and_not_an_empty_string(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """Oracle treats '' as NULL in VARCHAR2, which SQLite does not.

    Row 2 of the fixture stores an empty string in ``v_ascii``. Oracle should report
    it as NULL. A harness that renders one as the other tells a user a column is
    populated when it is not.
    """

    row = _one(
        connection,
        "SELECT v_ascii, nullable_all, NVL(v_ascii, 'was-null') FROM harness_types WHERE id = 2",
        {},
        limits,
    )
    evidence.note(
        "Is an empty VARCHAR2 stored as NULL?",
        f"v_ascii read back as `{row[0]!r}`; NVL says `{row[2]!r}`.",
    )
    assert row[0] is None
    assert row[1] is None
    assert row[2] == "was-null"

    # IS NULL matches; = :null does not. Both are true in Oracle and neither is
    # obvious to someone reading the result grid.
    matched = _one(
        connection,
        "SELECT COUNT(*) FROM harness_types WHERE nullable_all IS NULL",
        {},
        limits,
    )
    assert matched[0] == 1
    unmatched = _one(
        connection,
        "SELECT COUNT(*) FROM harness_types WHERE nullable_all = :value",
        {"value": None},
        limits,
    )
    assert unmatched[0] == 0


def test_timestamps_keep_their_time_zone(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """TIMESTAMP WITH TIME ZONE must not arrive as a naive local datetime."""

    row = _one(
        connection,
        "SELECT ts_plain, ts_tz, ts_ltz FROM harness_types WHERE id = 1",
        {},
        limits,
    )
    plain, with_tz, local_tz = row
    evidence.note(
        "How do TIMESTAMP columns arrive?",
        f"TIMESTAMP: `{plain!r}`<br>WITH TIME ZONE: `{with_tz!r}`<br>"
        f"WITH LOCAL TIME ZONE: `{local_tz!r}`",
    )
    assert isinstance(plain, dt.datetime)
    assert plain.microsecond == 789012, f"Sub-second precision was lost: {plain!r}"
    assert isinstance(with_tz, dt.datetime)
    assert with_tz.tzinfo is not None, (
        f"TIMESTAMP WITH TIME ZONE arrived without a time zone ({with_tz!r}). The "
        "offset is gone and the value now means whatever the reader assumes."
    )


def test_quoted_identifiers_address_the_object_they_name(
    connection: OracleConnection,
    limits: ExecutionLimits,
) -> None:
    """A mixed-case table and a reserved-word column, reached as written."""

    row = _one(
        connection,
        'SELECT "Column One", "select" FROM "Harness Mixed Case"',
        {},
        limits,
    )
    assert row[0] == 1
    assert row[1] == "reserved word"


def test_an_unquoted_mixed_case_name_does_not_resolve(
    connection: OracleConnection,
    limits: ExecutionLimits,
) -> None:
    """The other half: without quotes Oracle folds to upper case and finds nothing.

    This is what makes quoting a correctness requirement rather than a style choice,
    and the stand-in cannot show it because SQLite matches either way.
    """

    with pytest.raises(HarnessError):
        connection.execute("SELECT * FROM Harness_Mixed_Case", {}, StatementKind.QUERY, limits)


def test_bounded_fetch_stops_at_the_row_limit_and_says_so(
    connection: OracleConnection,
    limits: ExecutionLimits,
) -> None:
    """The slow-query fixture is far larger than any configured limit."""

    tight = limits.model_copy(update={"max_rows": 10})
    result = connection.execute(
        "SELECT line_id FROM order_lines ORDER BY line_id",
        {},
        StatementKind.QUERY,
        tight,
    )
    assert result.result_set is not None
    assert len(result.result_set.rows) == 10
    assert result.result_set.truncated
    assert result.result_set.truncation_reason
