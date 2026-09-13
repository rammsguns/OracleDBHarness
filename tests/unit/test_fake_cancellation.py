"""A break delivered to a running stand-in query is a cancellation, as it is on Oracle.

The Oracle adapter turns ORA-01013 into ``CancelledError_``, and the 2026-09-11 run against
19c confirmed that is what a broken statement raises (docs/compatibility.md). The stand-in
interrupted SQLite and then reported the interruption as a plain Oracle error, so every
cancellation of a running query on the stand-in came back ``failed`` with
``oracle_error`` - which the capacity rehearsal found, because it cancels statements
that are genuinely running rather than ones still queued.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from harness_worker.backend.base import ConnectionSpec
from harness_worker.backend.fake import FakeOracleBackend
from harness_worker.errors import CancelledError_, OracleError
from harness_worker.types import ExecutionLimits, StatementKind

# 4,000 order lines cubed: it does not finish on its own within any test's patience.
ENDLESS = "SELECT COUNT(*) FROM order_lines a, order_lines b, order_lines c"


def test_cancelling_a_running_query_raises_a_cancellation_not_an_oracle_error(
    tmp_path: Path,
) -> None:
    backend = FakeOracleBackend(tmp_path)
    connection = backend.connect(
        ConnectionSpec(
            profile_id="p", host="localhost", port=1521, service_name="CANCEL", username="u"
        )
    )
    raised: list[BaseException] = []

    def run() -> None:
        try:
            connection.execute(ENDLESS, {}, StatementKind.QUERY, ExecutionLimits())
        except BaseException as exc:  # noqa: BLE001 - the raised type is the assertion
            raised.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    time.sleep(0.3)
    assert connection.cancel() is True
    worker.join(10)

    assert not worker.is_alive(), "the interrupted query did not stop"
    assert raised, "the endless query returned instead of being cancelled"
    assert isinstance(raised[0], CancelledError_), f"raised {type(raised[0]).__name__}: {raised[0]}"
    assert not isinstance(raised[0], OracleError)
    assert raised[0].detail.get("statementStarted") is True
    # The session survives a break, as Oracle's does.
    result = connection.execute(
        "SELECT COUNT(*) FROM order_lines", {}, StatementKind.QUERY, ExecutionLimits()
    )
    assert result.result_set is not None and result.result_set.rows == [[4000]]
    connection.close()


def test_an_interruption_nobody_asked_for_is_still_an_error(tmp_path: Path) -> None:
    """Only a requested break is a cancellation; other SQLite failures keep their mapping."""

    backend = FakeOracleBackend(tmp_path)
    connection = backend.connect(
        ConnectionSpec(
            profile_id="p", host="localhost", port=1521, service_name="CANCEL2", username="u"
        )
    )
    with pytest.raises(OracleError, match="ORA-00942"):
        connection.execute(
            "SELECT * FROM no_such_table", {}, StatementKind.QUERY, ExecutionLimits()
        )
    connection.close()
