"""Transaction behaviour that only Oracle can settle.

The harness tracks whether a transaction is open and refuses DDL while DML is
pending. Those rules were written from Oracle's documented behaviour and tested
against a stand-in that implements them by construction, which proves the harness
agrees with itself. These tests check it against the database.
"""

from __future__ import annotations

import pytest

from harness_worker.backend import OracleConnection
from harness_worker.types import ExecutionLimits, StatementKind
from tests.qualification.evidence import Evidence


def _count(connection: OracleConnection, limits: ExecutionLimits, where: str = "") -> int:
    sql = "SELECT COUNT(*) FROM harness_departments"
    if where:
        sql = f"{sql} WHERE {where}"
    result = connection.execute(sql, {}, StatementKind.QUERY, limits)
    assert result.result_set is not None
    return int(result.result_set.rows[0][0])


def _insert(connection: OracleConnection, limits: ExecutionLimits, department_id: int) -> None:
    connection.execute(
        "INSERT INTO harness_departments (department_id, department_name, location_id)"
        " VALUES (:id, :name, :loc)",
        {"id": department_id, "name": f"Qualification {department_id}", "loc": 9999},
        StatementKind.DML,
        limits,
    )


def test_uncommitted_dml_is_invisible_to_another_session(
    connection: OracleConnection,
    second_connection: OracleConnection,
    limits: ExecutionLimits,
) -> None:
    """The isolation the whole worksheet model rests on."""

    _insert(connection, limits, 900)
    assert connection.transaction_open

    assert _count(second_connection, limits, "department_id = 900") == 0
    assert _count(connection, limits, "department_id = 900") == 1

    connection.rollback()
    assert _count(connection, limits, "department_id = 900") == 0


def test_commit_makes_the_change_visible_and_closes_the_transaction(
    connection: OracleConnection,
    second_connection: OracleConnection,
    limits: ExecutionLimits,
) -> None:
    _insert(connection, limits, 901)
    connection.commit()
    assert not connection.transaction_open

    try:
        assert _count(second_connection, limits, "department_id = 901") == 1
    finally:
        connection.execute(
            "DELETE FROM harness_departments WHERE department_id = 901",
            {},
            StatementKind.DML,
            limits,
        )
        connection.commit()


def test_rollback_undoes_dml(connection: OracleConnection, limits: ExecutionLimits) -> None:
    before = _count(connection, limits)
    _insert(connection, limits, 902)
    assert _count(connection, limits) == before + 1
    connection.rollback()
    assert _count(connection, limits) == before
    assert not connection.transaction_open


def test_ddl_commits_the_open_transaction(
    connection: OracleConnection,
    second_connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """The behaviour the worksheet's DDL guard exists to protect users from.

    Oracle commits an open transaction when DDL runs. The harness blocks DDL in a
    worksheet with pending DML precisely because of this, and the adapter clears its
    pending-transaction flag afterwards. Both claims are checked here: the pending
    insert really does become permanent, and the adapter really does stop reporting
    an open transaction.
    """

    _insert(connection, limits, 903)
    assert connection.transaction_open

    connection.execute("CREATE TABLE harness_ddl_probe (id NUMBER)", {}, StatementKind.DDL, limits)
    try:
        assert not connection.transaction_open, (
            "The adapter still reports an open transaction after DDL. Oracle has "
            "already committed it, so the harness would offer a rollback that does "
            "nothing."
        )
        committed = _count(second_connection, limits, "department_id = 903")
        evidence.note(
            "Does DDL commit pending DML?",
            "Yes - the row inserted before the CREATE was visible to another session "
            "without an explicit commit."
            if committed == 1
            else f"**No** - another session saw {committed} rows. This contradicts the "
            "assumption the worksheet DDL guard is built on. Investigate before release.",
        )
        assert committed == 1
    finally:
        connection.execute("DROP TABLE harness_ddl_probe PURGE", {}, StatementKind.DDL, limits)
        connection.execute(
            "DELETE FROM harness_departments WHERE department_id = 903",
            {},
            StatementKind.DML,
            limits,
        )
        connection.commit()


def test_a_plsql_block_leaves_its_transaction_open(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """A block that writes but does not commit must leave work pending.

    The adapter records a PL/SQL block as leaving a transaction open. If Oracle
    disagreed, the harness would release a connection with uncommitted work on it.
    """

    connection.execute(
        "BEGIN INSERT INTO harness_departments (department_id, department_name, location_id)"
        " VALUES (904, 'From a block', 9999); END;",
        {},
        StatementKind.PLSQL_BLOCK,
        limits,
    )
    try:
        assert connection.transaction_open
        assert _count(connection, limits, "department_id = 904") == 1
        evidence.note(
            "Does a PL/SQL block leave a transaction open?",
            "Yes - the adapter's assumption holds.",
        )
    finally:
        connection.rollback()


def test_rollback_to_savepoint_leaves_the_transaction_open(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """The open question in the compatibility gap table.

    The adapter treats ``ROLLBACK TO SAVEPOINT`` as leaving the transaction open,
    unlike a bare ``ROLLBACK``. Getting this wrong in either direction loses a user's
    work or reports a lease as free while it still holds one.
    """

    _insert(connection, limits, 905)
    connection.execute("SAVEPOINT harness_sp", {}, StatementKind.TRANSACTION_CONTROL, limits)
    _insert(connection, limits, 906)

    connection.execute(
        "ROLLBACK TO SAVEPOINT harness_sp", {}, StatementKind.TRANSACTION_CONTROL, limits
    )
    try:
        still_open = connection.transaction_open
        survived = _count(connection, limits, "department_id = 905")
        rolled_back = _count(connection, limits, "department_id = 906")
        evidence.note(
            "Does ROLLBACK TO SAVEPOINT leave the transaction open?",
            f"Adapter reports open={still_open}; the row before the savepoint "
            f"survived ({survived} == 1) and the row after it did not "
            f"({rolled_back} == 0).",
        )
        assert survived == 1
        assert rolled_back == 0
        assert still_open, (
            "The adapter reports no open transaction after ROLLBACK TO SAVEPOINT, "
            "but the earlier row is still pending. The lease would be released with "
            "uncommitted work on it."
        )
    finally:
        connection.rollback()


def test_a_worksheet_commit_resolves_the_same_state_as_the_toolbar(
    connection: OracleConnection,
    limits: ExecutionLimits,
) -> None:
    """COMMIT typed into the worksheet, rather than pressed as a button."""

    _insert(connection, limits, 907)
    connection.execute("COMMIT", {}, StatementKind.TRANSACTION_CONTROL, limits)
    try:
        assert not connection.transaction_open
    finally:
        connection.execute(
            "DELETE FROM harness_departments WHERE department_id = 907",
            {},
            StatementKind.DML,
            limits,
        )
        connection.commit()


@pytest.mark.parametrize("statement", ["CREATE OR REPLACE PROCEDURE"])
def test_create_or_replace_of_a_program_unit_clears_the_transaction(
    connection: OracleConnection,
    limits: ExecutionLimits,
    statement: str,
) -> None:
    """``CREATE OR REPLACE`` is DDL, and so commits, like any other DDL."""

    _insert(connection, limits, 908)
    connection.execute(
        f"{statement} harness_noop AS BEGIN NULL; END;",
        {},
        StatementKind.PLSQL_SOURCE,
        limits,
    )
    try:
        assert not connection.transaction_open
    finally:
        connection.execute("DROP PROCEDURE harness_noop", {}, StatementKind.DDL, limits)
        connection.execute(
            "DELETE FROM harness_departments WHERE department_id = 908",
            {},
            StatementKind.DML,
            limits,
        )
        connection.commit()
