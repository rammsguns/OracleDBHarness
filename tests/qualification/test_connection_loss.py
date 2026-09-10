"""Losing a connection for real, from outside the session.

These are the checks the compatibility gap table cannot answer without a database.
The harness classifies a lost connection during a write as ``outcome_unknown`` rather
than a failure, because a failure implies the write did not happen and nobody knows
that. Whether that classification is reached depends on which ORA code python-oracledb
raises, and the only way to find out is to end a session while a statement is in it.

Every test here needs the privileged connection (``HARNESS_QUAL_ADMIN_DSN``): a
session cannot honestly lose itself.
"""

from __future__ import annotations

import threading
import time

import pytest

from harness_worker.backend import OracleConnection
from harness_worker.errors import HarnessError, OutcomeUnknownError
from harness_worker.types import ExecutionLimits, StatementKind
from tests.qualification.evidence import Evidence

pytestmark = pytest.mark.needs_admin

_BURN_SECONDS = 30
_KILL_AFTER_SECONDS = 3.0
# A bulk update is interrupted sooner than a burn loop, so the kill goes in earlier.
_WRITE_KILL_AFTER_SECONDS = 1.0
# Kept in step with the CONNECT BY level in oracle/qualification/01_fixtures.sql.
_FIXTURE_ROWS = 400_000


def _kill(
    admin: OracleConnection,
    limits: ExecutionLimits,
    sid: int,
    serial: int,
    *,
    immediate: bool,
) -> None:
    clause = " IMMEDIATE" if immediate else ""
    admin.execute(
        f"ALTER SYSTEM KILL SESSION '{sid},{serial}'{clause}",
        {},
        StatementKind.DDL,
        limits,
    )


def _kill_after(
    admin: OracleConnection,
    limits: ExecutionLimits,
    sid: int,
    serial: int,
    delay: float,
    *,
    immediate: bool = True,
) -> threading.Thread:
    def worker() -> None:
        time.sleep(delay)
        try:
            _kill(admin, limits, sid, serial, immediate=immediate)
        except HarnessError:
            # The session may already be gone. The test asserts on what the victim
            # saw, not on what the killer managed to do.
            pass

    thread = threading.Thread(target=worker, name="qualification-kill", daemon=True)
    thread.start()
    return thread


def test_a_killed_session_surfaces_as_a_fatal_code(
    connection: OracleConnection,
    admin_connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """Which ORA code reaches the adapter when the session ends mid-statement.

    ``_FATAL_CODES`` in the adapter lists ORA-03113, ORA-03135 and DPY-4011. Those
    were taken from documentation. Anything else here means a dead connection would
    be classified as an ordinary failure and left leased.
    """

    identity = connection.identity()
    assert identity.session_id is not None and identity.serial_number is not None

    generous = limits.model_copy(update={"deadline_seconds": 120.0})
    thread = _kill_after(
        admin_connection,
        limits,
        identity.session_id,
        identity.serial_number,
        _KILL_AFTER_SECONDS,
    )

    with pytest.raises(HarnessError) as raised:
        connection.execute(
            "BEGIN harness_burn(:seconds); END;",
            {"seconds": _BURN_SECONDS},
            StatementKind.PLSQL_BLOCK,
            generous,
        )
    thread.join(timeout=15)

    error = raised.value
    evidence.note(
        "What does a killed session raise mid-statement?",
        f"`{type(error).__name__}` / harness code `{error.code}`"
        + (f", Oracle code `{error.oracle_code}`" if getattr(error, "oracle_code", "") else ""),
    )
    assert connection.is_broken, (
        "The adapter did not mark the connection broken after its session was killed. "
        "A dead connection would stay leased to the worksheet session."
    )


def test_a_lost_connection_during_a_write_is_outcome_unknown(
    connection: OracleConnection,
    admin_connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """A write whose answer is lost must never be reported as a plain failure.

    The user has to be told the outcome is unknown, because retrying a write that
    may have succeeded is how duplicates are created.
    """

    identity = connection.identity()
    assert identity.session_id is not None and identity.serial_number is not None

    generous = limits.model_copy(update={"deadline_seconds": 120.0})
    thread = _kill_after(
        admin_connection,
        limits,
        identity.session_id,
        identity.serial_number,
        _WRITE_KILL_AFTER_SECONDS,
    )

    outcome: Exception | None = None
    try:
        # Every row of the slow-query fixture, so the write is still in flight when
        # the session is killed. It generates real undo and redo rather than being
        # optimised away.
        connection.execute(
            "UPDATE harness_order_lines SET unit_price = unit_price + 0.01",
            {},
            StatementKind.DML,
            generous,
        )
    except Exception as exc:  # noqa: BLE001 - the classification is the assertion
        outcome = exc
    thread.join(timeout=15)

    if outcome is None:
        connection.rollback()
        evidence.note(
            "Lost connection during a write",
            f"Inconclusive - the update of {_FIXTURE_ROWS:,} rows finished before the "
            "session was killed. Raise the row count in oracle/qualification and re-run.",
        )
        pytest.skip("The write completed before the kill landed on this run.")

    evidence.note(
        "Is a lost connection during a write reported as outcome_unknown?",
        f"Raised `{type(outcome).__name__}`"
        + (f" with code `{outcome.code}`" if isinstance(outcome, HarnessError) else "")
        + (
            "."
            if isinstance(outcome, OutcomeUnknownError)
            else " - **not** outcome_unknown. The user would be told the write failed "
            "when nobody knows whether it did."
        ),
    )
    assert isinstance(outcome, OutcomeUnknownError)


def test_a_lost_connection_during_commit_is_outcome_unknown(
    connection: OracleConnection,
    admin_connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """The narrowest and worst window: the session ends during COMMIT itself.

    Nothing can tell the caller whether the commit landed. The only correct answer is
    that the outcome is unknown and the session is retired.
    """

    identity = connection.identity()
    assert identity.session_id is not None and identity.serial_number is not None

    connection.execute(
        "INSERT INTO harness_departments (department_id, department_name, location_id)"
        " VALUES (920, 'Committed or not', 9999)",
        {},
        StatementKind.DML,
        limits,
    )

    # Kill with no delay: the race is the point, so this may or may not land inside
    # the commit. A run where it did not is not a failure, it is an inconclusive
    # attempt, and it is recorded as one.
    outcome: Exception | None = None
    _kill_after(admin_connection, limits, identity.session_id, identity.serial_number, 0.0)
    try:
        connection.commit()
    except Exception as exc:  # noqa: BLE001 - the classification is the assertion
        outcome = exc

    if outcome is None:
        evidence.note(
            "Lost connection during COMMIT",
            "Inconclusive - the commit completed before the session was killed. "
            "Re-run to try to hit the window.",
        )
        pytest.skip("The kill did not land inside the commit window on this run.")

    evidence.note(
        "Lost connection during COMMIT",
        f"Raised `{type(outcome).__name__}`"
        + (f" with code `{outcome.code}`" if isinstance(outcome, HarnessError) else "")
        + ("" if isinstance(outcome, OutcomeUnknownError) else " - **not** outcome_unknown."),
    )
    assert isinstance(outcome, OutcomeUnknownError)
    assert connection.is_broken


def test_a_broken_connection_is_never_reported_healthy(
    connection: OracleConnection,
    admin_connection: OracleConnection,
    limits: ExecutionLimits,
) -> None:
    """``is_healthy`` guards connection reuse, so a false positive leaks a dead lease."""

    identity = connection.identity()
    assert identity.session_id is not None and identity.serial_number is not None

    _kill(admin_connection, limits, identity.session_id, identity.serial_number, immediate=True)
    # Give the instance a moment to actually tear the session down.
    time.sleep(2)

    assert not connection.is_healthy()
