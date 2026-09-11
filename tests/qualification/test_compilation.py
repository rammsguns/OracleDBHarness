"""PL/SQL compilation, line-level errors and DBMS_OUTPUT.

This is the least qualified area in the whole harness. The stand-in implements no
PL/SQL at all, so the compile path, the error rows and every line of DBMS_OUTPUT the
suite has ever asserted on came from a stub. Nothing below has an equivalent
elsewhere in the test suite.
"""

from __future__ import annotations

from harness_worker.backend import OracleConnection
from harness_worker.types import ExecutionLimits, StatementKind
from tests.qualification.evidence import Evidence

_VALID_BODY = """CREATE OR REPLACE PACKAGE BODY employee_report AS
  FUNCTION headcount(p_department_id IN NUMBER) RETURN NUMBER IS
    l_count NUMBER;
  BEGIN
    SELECT COUNT(*) INTO l_count FROM employees WHERE department_id = p_department_id;
    RETURN l_count;
  END headcount;
  PROCEDURE report_department(p_department_id IN NUMBER) IS
  BEGIN
    DBMS_OUTPUT.PUT_LINE('headcount=' || headcount(p_department_id));
  END report_department;
  PROCEDURE emit_lines(p_count IN NUMBER, p_width IN NUMBER DEFAULT 40) IS
  BEGIN
    FOR i IN 1 .. p_count LOOP
      DBMS_OUTPUT.PUT_LINE(LPAD(TO_CHAR(i), p_width, '.'));
    END LOOP;
  END emit_lines;
END employee_report;"""

_INVALID_BODY = _VALID_BODY.replace("FROM employees", "FROM employee")


def _object_status(
    connection: OracleConnection, limits: ExecutionLimits, name: str, kind: str
) -> str:
    result = connection.execute(
        "SELECT status FROM user_objects WHERE object_name = :name AND object_type = :kind",
        {"name": name, "kind": kind},
        StatementKind.QUERY,
        limits,
    )
    assert result.result_set is not None
    assert result.result_set.rows, f"{kind} {name} does not exist"
    return str(result.result_set.rows[0][0])


def test_the_seeded_package_body_is_invalid_with_line_level_errors(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """The acceptance criterion: a seeded invalid package shows correct errors.

    The fixture body references a table that does not exist. Oracle should report it
    on the line where the reference is, not on the first line of the package.
    """

    result = connection.execute(_INVALID_BODY, {}, StatementKind.PLSQL_SOURCE, limits)

    assert result.compiler_errors is not None, (
        "The adapter could not identify the object from the statement, so it read no "
        "errors at all. A user would be shown nothing where the errors should be."
    )
    assert result.compiler_errors, "Oracle reported no errors for a body that cannot compile."

    detail = "; ".join(f"line {e.line} col {e.position}: {e.text}" for e in result.compiler_errors)
    evidence.note("Compiler errors for the seeded invalid body", detail)

    assert _object_status(connection, limits, "EMPLOYEE_REPORT", "PACKAGE BODY") == "INVALID"
    assert any("ORA-00942" in e.text for e in result.compiler_errors), (
        f"Expected a missing-table error. Got: {detail}"
    )
    # The bad reference is on line 5 of the body. An error reported on line 1 is
    # useless in an editor.
    assert any(e.line == 5 for e in result.compiler_errors), (
        f"No error on the line with the bad reference. Got: {detail}"
    )


def test_the_package_can_be_repaired_and_recompiles_clean(
    connection: OracleConnection,
    limits: ExecutionLimits,
) -> None:
    """The other half of the acceptance criterion: repair, recompile, no errors."""

    result = connection.execute(_VALID_BODY, {}, StatementKind.PLSQL_SOURCE, limits)
    assert result.compiler_errors == [], (
        f"The corrected body still reported errors: {result.compiler_errors}"
    )
    assert _object_status(connection, limits, "EMPLOYEE_REPORT", "PACKAGE BODY") == "VALID"

    # Leave the fixture as the next test expects to find it.
    connection.execute(_INVALID_BODY, {}, StatementKind.PLSQL_SOURCE, limits)


def test_a_repaired_package_produces_the_expected_output(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """DBMS_OUTPUT, produced by a real block and read back through the adapter.

    ``Cursor.arrayvar`` binding to ``DBMSOUTPUT_LINESARRAY`` has never been executed
    against a database. If it does not bind, every block in the PL/SQL workspace
    loses its output.
    """

    connection.execute(_VALID_BODY, {}, StatementKind.PLSQL_SOURCE, limits)
    try:
        result = connection.execute(
            "BEGIN employee_report.report_department(20); END;",
            {},
            StatementKind.PLSQL_BLOCK,
            limits,
            collect_dbms_output=True,
        )
        evidence.note(
            "Does DBMS_OUTPUT reach the adapter?",
            f"Yes - {len(result.dbms_output)} line(s): {result.dbms_output!r}"
            if result.dbms_output
            else "**No lines were returned.** The arrayvar bind or the GET_LINES read "
            "does not work on this database and driver.",
        )
        # Department 20 has three employees in the fixture data.
        assert result.dbms_output == ["headcount=3"], result.dbms_output
    finally:
        connection.execute(_INVALID_BODY, {}, StatementKind.PLSQL_SOURCE, limits)


def test_dbms_output_arrives_in_the_order_it_was_written(
    connection: OracleConnection,
    limits: ExecutionLimits,
) -> None:
    """More lines than one GET_LINES round trip, so the chunking is exercised."""

    connection.execute(_VALID_BODY, {}, StatementKind.PLSQL_SOURCE, limits)
    try:
        generous = limits.model_copy(update={"max_dbms_output_bytes": 1 << 20})
        result = connection.execute(
            "BEGIN employee_report.emit_lines(250, 10); END;",
            {},
            StatementKind.PLSQL_BLOCK,
            generous,
            collect_dbms_output=True,
        )
        assert not result.dbms_output_truncated
        assert len(result.dbms_output) == 250
        assert result.dbms_output[0].strip(".") == "1"
        assert result.dbms_output[-1].strip(".") == "250"
    finally:
        connection.execute(_INVALID_BODY, {}, StatementKind.PLSQL_SOURCE, limits)


def test_dbms_output_is_bounded_and_says_so(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """A block that produces more output than the budget must be truncated, not dropped.

    Silently returning a partial buffer is the failure this guards: a user reading a
    diagnostic block would draw a conclusion from output that stopped early without
    telling them.
    """

    connection.execute(_VALID_BODY, {}, StatementKind.PLSQL_SOURCE, limits)
    try:
        tight = limits.model_copy(update={"max_dbms_output_bytes": 512})
        result = connection.execute(
            "BEGIN employee_report.emit_lines(500, 60); END;",
            {},
            StatementKind.PLSQL_BLOCK,
            tight,
            collect_dbms_output=True,
        )
        assert result.dbms_output_truncated, (
            "500 lines of 60 characters fitted inside a 512-byte budget, which cannot "
            "be right. The truncation flag is not being set."
        )
        produced = sum(len(line.encode("utf-8")) for line in result.dbms_output)
        evidence.note(
            "Is DBMS_OUTPUT bounded?",
            f"Yes - a 512-byte budget returned {len(result.dbms_output)} line(s) "
            f"({produced} bytes) and set the truncated flag.",
        )
    finally:
        connection.execute(_INVALID_BODY, {}, StatementKind.PLSQL_SOURCE, limits)


def test_an_anonymous_block_reports_its_runtime_error(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """A block that raises must surface the ORA code, not a generic failure."""

    from harness_worker.errors import OracleError

    try:
        connection.execute(
            "BEGIN RAISE_APPLICATION_ERROR(-20001, 'qualification probe'); END;",
            {},
            StatementKind.PLSQL_BLOCK,
            limits,
        )
    except OracleError as exc:
        evidence.note(
            "Runtime error from a PL/SQL block",
            f"Oracle code `{exc.oracle_code}`, message `{exc}`",
        )
        assert "20001" in exc.oracle_code or "20001" in str(exc)
    else:
        raise AssertionError("A block that raises returned successfully.")
