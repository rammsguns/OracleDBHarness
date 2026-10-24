"""Offline coverage for qualification thread cleanup, including a stuck driver call."""

import threading
from unittest.mock import Mock

import pytest

from tests.qualification.threads import (
    assert_connection_idle,
    join_connection_thread,
    start_connection_thread,
)


def test_join_timeout_prevents_connection_cleanup_until_thread_finishes() -> None:
    connection = Mock()
    release = threading.Event()
    thread = threading.Thread(target=release.wait, name="stuck-driver-call", daemon=True)
    start_connection_thread(connection, thread)
    try:
        with pytest.raises(pytest.fail.Exception, match="stuck-driver-call.*did not finish"):
            join_connection_thread(connection, thread, timeout=0.01)
        with pytest.raises(pytest.fail.Exception, match="Skipping cleanup of busy connection"):
            assert_connection_idle(connection)
    finally:
        release.set()
        join_connection_thread(connection, thread)
    assert_connection_idle(connection)


def test_completed_thread_allows_connection_cleanup() -> None:
    connection = Mock()
    thread = threading.Thread(target=lambda: None)
    start_connection_thread(connection, thread)
    join_connection_thread(connection, thread)
    assert_connection_idle(connection)
