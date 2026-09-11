"""The DBA overview's blocking panel, against a session that is really blocked.

The stand-in seeds a blocked session into a table. Here one session holds a row lock,
a second waits on it, and the shipped dba.blocking query - loaded from the catalog,
so exactly what the overview runs - has to name the waiter and its holder.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from harness_worker.backend import OracleBackend, OracleConnection
from harness_worker.catalog import load_catalog
from harness_worker.types import ExecutionLimits, StatementKind
from tests import oracle_config as qual_config
from tests.oracle_fixtures import qualification_dir
from tests.qualification.evidence import Evidence

_LOCK_ROW = "UPDATE departments SET location_id = location_id WHERE department_id = 10"


@pytest.fixture
def observer(
    backend: OracleBackend, oracle_config: qual_config.OracleTestConfig, _schema: None
) -> Iterator[OracleConnection]:
    session = backend.connect(oracle_config.connection_spec("qualification-observer"))
    try:
        yield session
    finally:
        session.close()


def test_a_blocked_session_is_shown_with_its_blocker(
    connection: OracleConnection,
    second_connection: OracleConnection,
    observer: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    holder_sid = connection.identity().session_id
    waiter_sid = second_connection.identity().session_id
    blocking_sql = load_catalog(qualification_dir().parent).get("dba.blocking").sql

    connection.execute(_LOCK_ROW, {}, StatementKind.DML, limits)
    waited: dict[str, Any] = {}

    def wait_for_the_lock() -> None:
        started = time.monotonic()
        try:
            second_connection.execute(_LOCK_ROW, {}, StatementKind.DML, limits)
        except Exception as exc:  # noqa: BLE001 - reported by the assertion below
            waited["error"] = exc
        waited["seconds"] = time.monotonic() - started

    waiter = threading.Thread(target=wait_for_the_lock, name="qualification-waiter", daemon=True)
    waiter.start()
    try:
        rows: list[list[Any]] = []
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = observer.execute(blocking_sql, {}, StatementKind.QUERY, limits)
            assert result.result_set is not None
            rows = [row for row in result.result_set.rows if row[0] == waiter_sid]
            if rows:
                break
            time.sleep(0.5)
    finally:
        # Releasing the lock is what lets the waiter finish, whatever was observed.
        connection.rollback()
        waiter.join(timeout=30)

    evidence.note(
        "Does the blocking panel show a real blocked session?",
        f"Session {waiter_sid} waiting on {holder_sid}: "
        + (f"shown as `{rows[0][:4]}` blocked by SID `{rows[0][5]}`" if rows else "**not shown**"),
    )
    assert rows, f"dba.blocking did not show session {waiter_sid} waiting on {holder_sid}."
    assert rows[0][5] == holder_sid
    assert "enq" in str(rows[0][2]).lower(), f"Unexpected wait event {rows[0][2]!r}"
    assert not waiter.is_alive(), "The waiter never got the row after the holder rolled back."
    assert "error" not in waited, waited.get("error")
    second_connection.rollback()
