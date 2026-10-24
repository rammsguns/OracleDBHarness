"""Keep fixture teardown away from connections still used by test threads."""

from __future__ import annotations

import threading

import pytest

from harness_worker.backend import OracleConnection

_CONNECTION_THREADS: dict[OracleConnection, set[threading.Thread]] = {}


def start_connection_thread(connection: OracleConnection, thread: threading.Thread) -> None:
    _CONNECTION_THREADS.setdefault(connection, set()).add(thread)
    thread.start()


def join_connection_thread(
    connection: OracleConnection, thread: threading.Thread, timeout: float = 30.0
) -> None:
    thread.join(timeout=timeout)
    if thread.is_alive():
        pytest.fail(
            f"Background connection thread {thread.name!r} did not finish within {timeout}s; "
            "connection cleanup must be skipped while it is busy."
        )
    _CONNECTION_THREADS[connection].discard(thread)
    if not _CONNECTION_THREADS[connection]:
        del _CONNECTION_THREADS[connection]


def assert_connection_idle(connection: OracleConnection) -> None:
    threads = _CONNECTION_THREADS.get(connection, set())
    busy = [thread.name for thread in threads if thread.is_alive()]
    if busy:
        pytest.fail(
            f"Skipping cleanup of busy connection: background threads still running: {busy}"
        )
    _CONNECTION_THREADS.pop(connection, None)
