"""Cancellation against a statement that is genuinely running.

``Connection.cancel()`` is called on a second thread while the first is blocked in a
driver round trip. That is how the harness cancels: the execution service holds the
connection and a cancel request arrives on another request thread. A stub cannot show
whether the break is delivered, what ORA code comes back, or what the statement
leaves behind.
"""

from __future__ import annotations

import threading
import time

import pytest

from harness_worker.backend import OracleConnection
from harness_worker.errors import CancelledError_, HarnessError, TimeoutError_
from harness_worker.types import ExecutionLimits, StatementKind
from tests.qualification.evidence import Evidence

# Long enough that the break lands mid-statement on any plausible machine, short
# enough that a failed cancellation does not stall the suite for minutes.
_BURN_SECONDS = 30
_CANCEL_AFTER_SECONDS = 3.0


def _cancel_after(connection: OracleConnection, delay: float) -> threading.Thread:
    def worker() -> None:
        time.sleep(delay)
        connection.cancel()

    thread = threading.Thread(target=worker, name="qualification-cancel", daemon=True)
    thread.start()
    return thread


def test_a_running_statement_can_be_broken(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """The break is delivered, and the harness reports cancelled rather than failed.

    Reporting a cancellation as a failure is the specific defect this guards: a user
    who cancelled would be told their statement broke.
    """

    generous = limits.model_copy(update={"deadline_seconds": 120.0})
    thread = _cancel_after(connection, _CANCEL_AFTER_SECONDS)
    started = time.monotonic()

    with pytest.raises(HarnessError) as raised:
        connection.execute(
            "BEGIN harness_burn(:seconds); END;",
            {"seconds": _BURN_SECONDS},
            StatementKind.PLSQL_BLOCK,
            generous,
        )
    elapsed = time.monotonic() - started
    thread.join(timeout=5)

    error = raised.value
    evidence.note(
        "What does a broken statement raise?",
        f"`{type(error).__name__}` with code `{error.code}`"
        + (f", Oracle code `{error.oracle_code}`" if getattr(error, "oracle_code", "") else "")
        + f". Returned after {elapsed:.1f}s of a {_BURN_SECONDS}s statement.",
    )

    assert isinstance(error, CancelledError_), (
        f"A broken statement surfaced as {type(error).__name__}, not a cancellation. "
        "docs/compatibility.md assumes ORA-01013 reaches the adapter's cancel path."
    )
    assert elapsed < _BURN_SECONDS, "The statement ran to completion; the break was not honoured."


def test_the_connection_is_reusable_after_a_cancellation(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """A cancelled statement must not cost the session.

    If it did, every cancellation would silently retire a lease and the worksheet
    would lose its transaction.
    """

    generous = limits.model_copy(update={"deadline_seconds": 120.0})
    thread = _cancel_after(connection, _CANCEL_AFTER_SECONDS)
    with pytest.raises(HarnessError):
        connection.execute(
            "BEGIN harness_burn(:seconds); END;",
            {"seconds": _BURN_SECONDS},
            StatementKind.PLSQL_BLOCK,
            generous,
        )
    thread.join(timeout=5)

    assert not connection.is_broken
    assert connection.is_healthy()
    result = connection.execute("SELECT 1 FROM dual", {}, StatementKind.QUERY, limits)
    assert result.result_set is not None
    assert result.result_set.rows[0][0] == 1
    evidence.note(
        "Is a session reusable after a cancellation?",
        "Yes - the connection stayed healthy and answered a query afterwards.",
    )


def test_cancelling_one_statement_leaves_earlier_work_in_the_transaction(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """The open question from the compatibility gap table.

    Oracle rolls back the *statement* that was broken, not the transaction. Work done
    earlier in the same transaction should still be pending afterwards. If it is not,
    a cancellation silently discards a user's uncommitted work, and the harness must
    say so rather than offering a commit.
    """

    connection.execute(
        "INSERT INTO departments (department_id, department_name, location_id)"
        " VALUES (910, 'Before the cancel', 9999)",
        {},
        StatementKind.DML,
        limits,
    )

    generous = limits.model_copy(update={"deadline_seconds": 120.0})
    thread = _cancel_after(connection, _CANCEL_AFTER_SECONDS)
    with pytest.raises(HarnessError):
        connection.execute(
            "BEGIN harness_burn(:seconds); END;",
            {"seconds": _BURN_SECONDS},
            StatementKind.PLSQL_BLOCK,
            generous,
        )
    thread.join(timeout=5)

    try:
        result = connection.execute(
            "SELECT COUNT(*) FROM departments WHERE department_id = 910",
            {},
            StatementKind.QUERY,
            limits,
        )
        assert result.result_set is not None
        survived = int(result.result_set.rows[0][0])
        evidence.note(
            "Does a cancellation roll back earlier work in the same transaction?",
            "No - the earlier insert was still pending, as assumed."
            if survived == 1
            else "**Yes** - the earlier insert was gone. A cancellation discards "
            "uncommitted work on this database, which the harness does not currently "
            "tell the user.",
        )
        assert survived == 1
    finally:
        connection.rollback()


def test_the_deadline_stops_a_statement_that_is_never_cancelled(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """The total operation deadline, enforced without anyone pressing cancel."""

    tight = limits.model_copy(update={"deadline_seconds": 5.0})
    started = time.monotonic()
    with pytest.raises(HarnessError) as raised:
        connection.execute(
            "BEGIN harness_burn(:seconds); END;",
            {"seconds": _BURN_SECONDS},
            StatementKind.PLSQL_BLOCK,
            tight,
        )
    elapsed = time.monotonic() - started

    evidence.note(
        "What enforces the execution deadline?",
        f"A {tight.deadline_seconds:.0f}s budget stopped a {_BURN_SECONDS}s statement "
        f"after {elapsed:.1f}s, raising `{type(raised.value).__name__}`.",
    )
    assert isinstance(raised.value, TimeoutError_ | CancelledError_)
    assert elapsed < _BURN_SECONDS
