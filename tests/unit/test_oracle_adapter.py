"""The python-oracledb adapter, driven against a stub driver connection.

Everything else in the suite runs on the local stand-in, which means the adapter's
own logic -- when a transaction is considered open, what a break request reports, and
which driver error becomes which harness error -- is otherwise never exercised. These
tests cover that logic without a database. They are still not evidence about Oracle
itself: the ORA codes and the driver's behaviour around them have to be confirmed on
a real 19c instance, which is tracked in docs/compatibility.md.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness_worker.backend import oracle as oracle_backend
from harness_worker.backend.base import ConnectionSpec
from harness_worker.backend.oracle import OracleDbBackend, OracleDbConnection
from harness_worker.errors import (
    CancelledError_,
    CapabilityError,
    ConfigurationError,
    ConnectionFailedError,
    HarnessError,
    OracleError,
    OutcomeUnknownError,
)
from harness_worker.types import ExecutionLimits, StatementKind

SPEC = ConnectionSpec(profile_id="prf_1", host="h", port=1521, service_name="s", username="u")
LIMITS = ExecutionLimits(deadlineSeconds=30.0)


class _DriverErrorInfo:
    """Stands in for oracledb's error object, which carries the full ORA/DPY code."""

    def __init__(self, full_code: str, message: str) -> None:
        self.full_code = full_code
        self.message = message


class DriverError(Exception):
    def __init__(self, full_code: str, message: str) -> None:
        super().__init__(_DriverErrorInfo(full_code, message))


class StubVar:
    """A bind variable, remembering whether it is a collection or a scalar.

    That distinction is the whole point of the DBMS_OUTPUT tests: ``arrayvar``
    produces a PL/SQL index-by table and ``var`` produces a single value, and
    GET_LINES only accepts the first.
    """

    def __init__(self, *, is_array: bool, size: int = 1) -> None:
        self.is_array = is_array
        self.size = size
        self._values: list[Any] = [None] * (size if is_array else 1)

    def setvalue(self, pos: int, value: Any) -> None:
        self._values[pos] = value

    def getvalue(self, pos: int = 0) -> Any:
        return self._values if self.is_array else self._values[pos]

    def setarray(self, values: list[Any]) -> None:
        self._values = list(values)


class StubCursor:
    def __init__(self, owner: StubConnection) -> None:
        self._owner = owner
        self.description: Any = None
        self.rowcount = 0
        self.arraysize = 100
        self.prefetchrows = 101
        self.closed = False

    def __enter__(self) -> StubCursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def var(self, typ: Any, *args: Any, **kwargs: Any) -> StubVar:
        return StubVar(is_array=False)

    def arrayvar(self, typ: Any, size: int) -> StubVar:
        return StubVar(is_array=True, size=size)

    def callproc(self, name: str, args: list[Any]) -> None:
        self._owner.calls.append(name)
        if name == "dbms_output.get_lines":
            if self._owner.get_lines_error is not None:
                raise self._owner.get_lines_error
            chunk, count = args
            if not getattr(chunk, "is_array", False):
                # What the real driver does with a scalar where a
                # DBMSOUTPUT_LINESARRAY is expected.
                raise DriverError("ORA-06550", "PLS-00306: wrong number or types of arguments")
            wanted = count.getvalue()
            taken = self._owner.output_lines[:wanted]
            del self._owner.output_lines[:wanted]
            chunk.setarray(taken)
            count.setvalue(0, len(taken))

    def execute(self, statement: str, binds: dict[str, Any] | None = None) -> None:
        self._owner.executed.append(statement)
        self._owner.binds.append(dict(binds or {}))
        if self._owner.execute_error is not None:
            raise self._owner.execute_error
        self.rowcount = self._owner.rowcount
        if self._owner.fetch_error is not None:
            self.description = [("X", "NUMBER", None, None, None, None, True)]

    def fetchmany(self, count: int) -> list:
        if self._owner.fetch_error is not None:
            raise self._owner.fetch_error
        return []

    def fetchall(self) -> list:
        """Only the ALL_ERRORS lookup fetches this way."""

        return list(self._owner.error_rows)

    def close(self) -> None:
        self.closed = True
        if self._owner.cursor_close_error is not None:
            raise self._owner.cursor_close_error


class StubConnection:
    """The surface of an oracledb connection that the adapter actually touches."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.binds: list[dict[str, Any]] = []
        self.error_rows: list[tuple] = []
        self.calls: list[str] = []
        self.output_lines: list[str] = []
        self.get_lines_error: Exception | None = None
        self.execute_error: Exception | None = None
        self.fetch_error: Exception | None = None
        self.cursor_close_error: Exception | None = None
        self.cursors: list[StubCursor] = []
        self.commit_error: Exception | None = None
        self.rollback_error: Exception | None = None
        self.cancel_error: Exception | None = None
        self.rowcount = 1
        self.commits = 0
        self.rollbacks = 0
        self.cancels = 0
        self.closes = 0
        self.call_timeout = 0
        self.autocommit = True
        self.module = ""

    def cursor(self) -> StubCursor:
        cursor = StubCursor(self)
        self.cursors.append(cursor)
        return cursor

    def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    def cancel(self) -> None:
        self.cancels += 1
        if self.cancel_error is not None:
            raise self.cancel_error

    def close(self) -> None:
        self.closes += 1


@pytest.fixture
def pair() -> tuple[StubConnection, OracleDbConnection]:
    driver = StubConnection()
    return driver, OracleDbConnection(SPEC, driver)


def run(connection: OracleDbConnection, statement: str, kind: StatementKind) -> Any:
    return connection.execute(statement, {}, kind, LIMITS)


# -- compilation ------------------------------------------------------------------


def test_compiler_errors_are_read_for_a_unit_behind_a_leading_comment(pair) -> None:
    """A comment above CREATE does not make an invalid unit look valid.

    The classifier ignores a leading comment when it decides the statement is PL/SQL
    source, so the lookup that follows has to ignore it too. Matching the raw text
    used to find no object, read nothing, and report the unit as compiled cleanly.
    """

    driver, connection = pair
    driver.error_rows = [(3, 7, "PLS-00103: Encountered the symbol END", "ERROR", 103)]

    result = run(
        connection,
        "-- fix for INC-4471\nCREATE OR REPLACE PROCEDURE archive_orders AS BEGIN NULL END;",
        StatementKind.PLSQL_SOURCE,
    )

    assert [e.text for e in result.compiler_errors] == ["PLS-00103: Encountered the symbol END"]
    assert any("Compiled with 1 error(s)." in w for w in result.warnings)
    lookup = next(b for b in driver.binds if "name" in b)
    assert lookup == {"name": "ARCHIVE_ORDERS", "type": "PROCEDURE", "owner": None}


def test_a_quoted_schema_qualified_name_is_looked_up_as_the_dictionary_stores_it(
    pair,
) -> None:
    """Oracle folds an unquoted name to upper case and keeps a quoted one verbatim."""

    driver, connection = pair

    run(
        connection,
        'CREATE OR REPLACE PACKAGE BODY hr."Archive Orders" AS END;',
        StatementKind.PLSQL_SOURCE,
    )

    lookup = next(b for b in driver.binds if "name" in b)
    assert lookup == {"name": "Archive Orders", "type": "PACKAGE BODY", "owner": "HR"}


def test_a_unit_whose_object_cannot_be_identified_is_not_called_clean(pair) -> None:
    """Nothing was read, so "no errors" would be a guess -- and the costly one."""

    driver, connection = pair

    result = run(
        connection,
        "CREATE OR REPLACE PROCEDURE archivierung_für_aufträge AS BEGIN NULL; END;",
        StatementKind.PLSQL_SOURCE,
    )

    assert result.compiler_errors == []
    assert any("was not checked" in w for w in result.warnings)
    assert not any("all_errors" in sql.lower() for sql in driver.executed)


# -- transaction state -------------------------------------------------------------


def test_dml_opens_a_transaction_and_commit_closes_it(pair) -> None:
    driver, connection = pair
    assert connection.transaction_open is False

    result = run(connection, "UPDATE employees SET salary = 1", StatementKind.DML)
    assert result.rows_affected == 1
    assert connection.transaction_open is True

    connection.commit()
    assert driver.commits == 1
    assert connection.transaction_open is False


def test_rollback_closes_the_transaction(pair) -> None:
    driver, connection = pair
    run(connection, "UPDATE employees SET salary = 1", StatementKind.DML)
    connection.rollback()
    assert driver.rollbacks == 1
    assert connection.transaction_open is False


def test_ddl_leaves_nothing_pending_because_oracle_commits_it(pair) -> None:
    _driver, connection = pair
    run(connection, "UPDATE employees SET salary = 1", StatementKind.DML)
    assert connection.transaction_open is True

    run(connection, "CREATE TABLE t (id NUMBER)", StatementKind.DDL)
    assert connection.transaction_open is False


@pytest.mark.parametrize("statement", ["COMMIT", "commit work", "/* done */ COMMIT"])
def test_a_commit_entered_as_sql_closes_the_transaction(pair, statement: str) -> None:
    """The worksheet accepts COMMIT as text; it has to resolve the same state."""

    _driver, connection = pair
    run(connection, "UPDATE employees SET salary = 1", StatementKind.DML)

    run(connection, statement, StatementKind.TRANSACTION_CONTROL)
    assert connection.transaction_open is False


def test_a_rollback_entered_as_sql_closes_the_transaction(pair) -> None:
    _driver, connection = pair
    run(connection, "UPDATE employees SET salary = 1", StatementKind.DML)

    run(connection, "ROLLBACK", StatementKind.TRANSACTION_CONTROL)
    assert connection.transaction_open is False


@pytest.mark.parametrize(
    "statement",
    ["ROLLBACK TO SAVEPOINT before_load", "ROLLBACK WORK TO before_load", "SAVEPOINT before_load"],
)
def test_a_partial_rollback_leaves_the_transaction_open(pair, statement: str) -> None:
    """Rewinding to a savepoint is not the end of the transaction."""

    _driver, connection = pair
    run(connection, "UPDATE employees SET salary = 1", StatementKind.DML)

    run(connection, statement, StatementKind.TRANSACTION_CONTROL)
    assert connection.transaction_open is True


def test_a_query_never_opens_a_transaction(pair) -> None:
    _driver, connection = pair
    run(connection, "SELECT 1 FROM dual", StatementKind.QUERY)
    assert connection.transaction_open is False


def test_closing_rolls_back_pending_work_but_not_on_a_broken_session(pair) -> None:
    driver, connection = pair
    run(connection, "UPDATE employees SET salary = 1", StatementKind.DML)
    connection.close()
    assert (driver.rollbacks, driver.closes) == (1, 1)

    driver2 = StubConnection()
    broken = OracleDbConnection(SPEC, driver2)
    driver2.execute_error = DriverError("ORA-03113", "end-of-file on communication channel")
    with pytest.raises(OutcomeUnknownError):
        run(broken, "UPDATE employees SET salary = 1", StatementKind.DML)
    broken.close()
    # A session Oracle has already dropped cannot be rolled back; asking would only
    # raise a second error on the way out.
    assert driver2.rollbacks == 0
    assert driver2.closes == 1


# -- cleanup after a statement -----------------------------------------------------


def test_a_cursor_that_will_not_close_does_not_turn_an_applied_write_into_a_failure(pair) -> None:
    """The UPDATE reached Oracle and was applied; only the tidying up failed.

    Reporting that as a failed execution invites the caller to run the statement
    again, which applies the work twice. The gap in the tidying up is a warning on a
    result that still says what the statement did.
    """

    driver, connection = pair
    driver.cursor_close_error = DriverError("ORA-01000", "maximum open cursors exceeded")

    result = run(connection, "UPDATE employees SET salary = 1", StatementKind.DML)

    assert result.rows_affected == 1
    assert connection.transaction_open is True
    assert any("could not close the cursor" in warning for warning in result.warnings)
    assert any("maximum open cursors" in warning for warning in result.warnings)
    # A cursor that would not close is not by itself a session Oracle has dropped.
    assert connection.is_broken is False


def test_a_cleanup_failure_that_means_the_session_is_gone_condemns_the_connection(pair) -> None:
    """The write still stands. The connection it ran on does not.

    Marking it broken is what makes the session registry retire the session instead of
    handing the same dead connection to the next statement.
    """

    driver, connection = pair
    driver.cursor_close_error = DriverError("ORA-03113", "end-of-file on communication channel")

    result = run(connection, "UPDATE employees SET salary = 1", StatementKind.DML)

    assert result.rows_affected == 1
    assert connection.is_broken is True
    assert any("end-of-file" in warning for warning in result.warnings)


def test_a_cleanup_failure_never_masks_the_error_the_statement_itself_raised(pair) -> None:
    """A statement that failed has its own error; the cleanup does not replace it."""

    driver, connection = pair
    driver.execute_error = DriverError("ORA-00942", "table or view does not exist")
    driver.cursor_close_error = DriverError("ORA-01000", "maximum open cursors exceeded")

    with pytest.raises(CapabilityError) as raised:
        run(connection, "SELECT * FROM nope", StatementKind.QUERY)
    assert raised.value.detail["oracleCode"] == "ORA-00942"


# -- cancellation ------------------------------------------------------------------


def test_cancel_reports_delivery_not_whether_the_statement_stopped(pair) -> None:
    driver, connection = pair
    assert connection.cancel() is True
    assert driver.cancels == 1


def test_a_break_the_driver_refuses_is_reported_as_not_delivered(pair) -> None:
    driver, connection = pair
    driver.cancel_error = DriverError("DPY-1001", "not connected")
    assert connection.cancel() is False
    # A failed break is not by itself a reason to condemn the session; the engine
    # decides that from whether the statement actually stopped.
    assert connection.is_broken is False


@pytest.mark.parametrize(
    "kind",
    [StatementKind.QUERY, StatementKind.DML, StatementKind.PLSQL_BLOCK, StatementKind.DDL],
)
def test_a_broken_statement_is_reported_as_cancelled_rather_than_failed(
    pair, kind: StatementKind
) -> None:
    """ORA-01013 is Oracle acknowledging our own break, not refusing the statement.

    Classified as a plain Oracle error it reaches the console as ``failed``, which
    sends the user looking for a bug in SQL that was only stopped -- and for a write,
    hides that Oracle rolled the statement back rather than leaving it half applied.
    """

    driver, connection = pair
    driver.execute_error = DriverError("ORA-01013", "user requested cancel of current operation")
    with pytest.raises(CancelledError_) as excinfo:
        run(connection, "UPDATE employees SET salary = salary * 2", kind)
    assert excinfo.value.detail["oracleCode"] == "ORA-01013"
    # The statement reached the database, so the engine must not report it as never
    # sent; and a broken call leaves the session perfectly usable.
    assert excinfo.value.detail["statementStarted"] is True
    assert connection.is_broken is False


def test_a_cancelled_statement_leaves_the_rest_of_the_transaction_pending(pair) -> None:
    """ORA-01013 rolls back the statement, not the transaction before it."""

    driver, connection = pair
    run(connection, "UPDATE employees SET salary = 1", StatementKind.DML)
    assert connection.transaction_open is True

    driver.execute_error = DriverError("ORA-01013", "user requested cancel of current operation")
    with pytest.raises(CancelledError_):
        run(connection, "UPDATE employees SET salary = 2", StatementKind.DML)
    assert connection.transaction_open is True


# -- error classification ----------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "kind", "expected"),
    [
        # A lost connection with a write in flight: Oracle may have applied it.
        ("ORA-03113", StatementKind.DML, OutcomeUnknownError),
        ("ORA-03135", StatementKind.PLSQL_BLOCK, OutcomeUnknownError),
        ("DPY-4011", StatementKind.DDL, OutcomeUnknownError),
        ("ORA-03113", StatementKind.PLSQL_SOURCE, OutcomeUnknownError),
        # The same loss on a read changes nothing in the database.
        ("ORA-03113", StatementKind.QUERY, ConnectionFailedError),
        # A missing object or privilege is a capability answer, not a bug report.
        ("ORA-00942", StatementKind.QUERY, CapabilityError),
        ("ORA-01031", StatementKind.QUERY, CapabilityError),
        # A break we asked for is a cancellation, not a failure, whatever it stopped.
        ("ORA-01013", StatementKind.QUERY, CancelledError_),
        ("ORA-01013", StatementKind.DML, CancelledError_),
        # Anything else is Oracle saying no, and keeps its code.
        ("ORA-00001", StatementKind.DML, OracleError),
    ],
)
def test_driver_errors_are_classified_by_code_and_statement_kind(
    pair, code: str, kind: StatementKind, expected: type[Exception]
) -> None:
    driver, connection = pair
    driver.execute_error = DriverError(code, "driver said no")
    with pytest.raises(expected) as excinfo:
        run(connection, "SELECT 1 FROM dual", kind)
    assert code in str(excinfo.value.detail) or getattr(excinfo.value, "oracle_code", "") == code


@pytest.mark.parametrize("code", ["ORA-03113", "ORA-00028", "DPY-1001"])
def test_a_fatal_code_marks_the_session_unusable(pair, code: str) -> None:
    driver, connection = pair
    driver.execute_error = DriverError(code, "gone")
    with pytest.raises(HarnessError):
        run(connection, "SELECT 1 FROM dual", StatementKind.QUERY)
    assert connection.is_broken is True


def test_execute_failure_closes_cursor_and_resets_timeout(pair) -> None:
    driver, connection = pair
    driver.execute_error = DriverError("ORA-00942", "missing table")
    with pytest.raises(CapabilityError):
        run(connection, "SELECT * FROM missing", StatementKind.QUERY)
    assert driver.cursors[-1].closed is True
    assert driver.call_timeout == 0
    assert connection.is_broken is False


def test_fetch_disconnect_marks_session_broken_and_cleans_up(pair) -> None:
    driver, connection = pair
    driver.fetch_error = DriverError("ORA-03113", "gone during fetch")
    with pytest.raises(ConnectionFailedError) as excinfo:
        run(connection, "SELECT 1 FROM dual", StatementKind.QUERY)
    assert excinfo.value.detail["oracleCode"] == "ORA-03113"
    assert connection.is_broken is True
    assert driver.cursors[-1].closed is True
    assert driver.call_timeout == 0


def test_cleanup_failure_does_not_mask_unknown_write_outcome(pair, monkeypatch) -> None:
    driver, connection = pair
    driver.execute_error = DriverError("ORA-03113", "gone during compilation")

    def disconnected_close(self) -> None:
        raise DriverError("DPY-1001", "already disconnected")

    monkeypatch.setattr(StubCursor, "close", disconnected_close)
    with pytest.raises(OutcomeUnknownError) as excinfo:
        run(connection, "CREATE PROCEDURE p AS BEGIN NULL; END;", StatementKind.PLSQL_SOURCE)
    assert excinfo.value.detail["oracleCode"] == "ORA-03113"
    assert driver.call_timeout == 0


def test_a_commit_that_loses_its_connection_is_outcome_unknown(pair) -> None:
    """The case a failure code would misreport: Oracle may have committed."""

    driver, connection = pair
    run(connection, "UPDATE employees SET salary = 1", StatementKind.DML)
    driver.commit_error = DriverError("ORA-03113", "end-of-file on communication channel")

    with pytest.raises(OutcomeUnknownError) as excinfo:
        connection.commit()
    assert excinfo.value.retryable is False
    assert excinfo.value.detail["oracleCode"] == "ORA-03113"
    assert "verify" in excinfo.value.message.lower()
    # The commit did not report success, so the transaction is not marked resolved.
    assert connection.transaction_open is True
    assert connection.is_broken is True


def test_a_commit_entered_as_sql_that_loses_its_connection_is_outcome_unknown(pair) -> None:
    """The same protection as the toolbar: Oracle may still have committed."""

    driver, connection = pair
    run(connection, "UPDATE employees SET salary = 1", StatementKind.DML)
    driver.execute_error = DriverError("ORA-03113", "end-of-file on communication channel")

    with pytest.raises(OutcomeUnknownError) as excinfo:
        run(connection, "COMMIT", StatementKind.TRANSACTION_CONTROL)
    assert excinfo.value.detail["oracleCode"] == "ORA-03113"
    assert "verify" in excinfo.value.message.lower()
    assert connection.transaction_open is True
    assert connection.is_broken is True


def test_a_rollback_entered_as_sql_that_loses_its_connection_is_a_connection_failure(
    pair,
) -> None:
    """A session Oracle has dropped has already discarded the work; nothing is unknown."""

    driver, connection = pair
    run(connection, "UPDATE employees SET salary = 1", StatementKind.DML)
    driver.execute_error = DriverError("ORA-03113", "end-of-file on communication channel")

    with pytest.raises(ConnectionFailedError):
        run(connection, "ROLLBACK", StatementKind.TRANSACTION_CONTROL)
    assert connection.is_broken is True


def test_a_commit_oracle_refuses_outright_is_a_plain_error(pair) -> None:
    driver, connection = pair
    run(connection, "UPDATE employees SET salary = 1", StatementKind.DML)
    driver.commit_error = DriverError("ORA-02290", "check constraint violated")

    with pytest.raises(OracleError) as excinfo:
        connection.commit()
    assert not isinstance(excinfo.value, OutcomeUnknownError)
    assert excinfo.value.oracle_code == "ORA-02290"
    assert connection.is_broken is False
    assert connection.transaction_open is True


# -- DBMS_OUTPUT -------------------------------------------------------------------


def test_dbms_output_is_read_back_through_an_array_bind(pair) -> None:
    """GET_LINES takes a PL/SQL index-by table, so the bind has to be a collection."""

    driver, connection = pair
    driver.output_lines = [f"line {n}" for n in range(1, 251)]

    result = connection.execute(
        "BEGIN report.run; END;", {}, StatementKind.PLSQL_BLOCK, LIMITS, collect_dbms_output=True
    )

    assert result.dbms_output == [f"line {n}" for n in range(1, 251)]
    assert result.dbms_output_truncated is False
    assert result.warnings == []
    assert driver.calls[0] == "dbms_output.enable"
    # 100 lines a round trip; the third comes back short and ends the loop.
    assert driver.calls.count("dbms_output.get_lines") == 3


def test_output_that_cannot_be_read_back_does_not_fail_a_block_that_ran(pair) -> None:
    """The block was applied. A gap in what we can report is not a failed execution."""

    driver, connection = pair
    driver.get_lines_error = DriverError("ORA-06502", "numeric or value error")

    result = connection.execute(
        "BEGIN report.run; END;", {}, StatementKind.PLSQL_BLOCK, LIMITS, collect_dbms_output=True
    )

    assert result.dbms_output == []
    assert len(result.warnings) == 1
    assert "could not be read back" in result.warnings[0]
    assert connection.transaction_open is True


# -- connection setup --------------------------------------------------------------


class StubDriver:
    """The oracledb module surface OracleDbBackend.connect touches."""

    def __init__(self, connection: StubConnection) -> None:
        self.connection = connection
        self.params: dict[str, Any] = {}

    def connect(self, **params: Any) -> StubConnection:
        self.params = params
        return self.connection


@pytest.fixture
def driver(monkeypatch) -> StubDriver:
    stub = StubDriver(StubConnection())
    monkeypatch.setattr(oracle_backend, "oracledb", stub)
    monkeypatch.setattr(oracle_backend, "_initialized_mode", "thin")
    return stub


def _spec(**overrides: Any) -> ConnectionSpec:
    fields = {
        "profile_id": "prf_1",
        "host": "h",
        "port": 1521,
        "service_name": "s",
        "username": "app_reader",
    }
    fields.update(overrides)
    return ConnectionSpec(**fields)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("HARNESS_APP", 'ALTER SESSION SET CURRENT_SCHEMA = "HARNESS_APP"'),
        # Oracle folds an unquoted name to upper case, so a lower case profile value
        # means the same schema and must not become a name that does not exist.
        ("hr", 'ALTER SESSION SET CURRENT_SCHEMA = "HR"'),
        ("Mixed Case", 'ALTER SESSION SET CURRENT_SCHEMA = "Mixed Case"'),
    ],
)
def test_the_configured_default_schema_is_applied_to_the_session(
    driver: StubDriver, configured: str, expected: str
) -> None:
    """Unqualified names must resolve in the profile's schema, not the login user's."""

    OracleDbBackend().connect(_spec(default_schema=configured))
    assert driver.connection.executed == [expected]


def test_no_default_schema_leaves_the_session_where_it_logged_in(driver: StubDriver) -> None:
    OracleDbBackend().connect(_spec())
    assert driver.connection.executed == []


def test_a_default_schema_that_cannot_be_set_fails_the_connection(driver: StubDriver) -> None:
    """Handing back the session would run the user's statements in the wrong schema."""

    driver.connection.execute_error = DriverError("ORA-01435", "user does not exist")

    with pytest.raises(ConnectionFailedError) as excinfo:
        OracleDbBackend().connect(_spec(default_schema="MISSING"))
    assert excinfo.value.detail["oracleCode"] == "ORA-01435"
    assert excinfo.value.detail["defaultSchema"] == "MISSING"
    assert driver.connection.closes == 1


def test_an_unusable_schema_name_is_refused_before_a_session_is_opened(
    driver: StubDriver,
) -> None:
    """A name that cannot be quoted is a configuration fault, not a database round trip."""

    with pytest.raises(ConfigurationError):
        OracleDbBackend().connect(_spec(default_schema='HR" OR 1=1--'))
    assert driver.params == {}
