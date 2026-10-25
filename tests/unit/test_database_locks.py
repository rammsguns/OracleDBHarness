"""Database lock identity/order and contention need no live database."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.database_locks import database_locks, lock_names


def test_lock_names_deduplicate_aliases_and_sort():
    first = "localhost:1522/ORCL"
    second = "localhost:1523/PRODCDB"
    assert lock_names([second, first, first, ""]) == sorted(lock_names([first, second]))
    assert len(lock_names([first, second])) == 2
    assert lock_names([first]) == lock_names(["//127.0.0.1:1522/orcl"])
    assert lock_names(["localhost/FREEPDB1"]) == lock_names(["localhost:1521/freepdb1"])
    assert lock_names([first, second]) == lock_names([second, first])


def test_lock_blocks_other_process_and_releases(tmp_path: Path):
    script = """
import sys
from pathlib import Path
from tests.database_locks import database_locks
with database_locks(['localhost:1522/ORCL'], 'contender', repository=Path(sys.argv[1]), timeout=0):
    pass
"""
    with database_locks(["localhost:1522/ORCL"], "test holder", repository=tmp_path):
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True
        )
        assert result.returncode != 0
        assert f"waiting for lock held by test holder; PID={os.getpid()}" in result.stderr
        assert "Timed out" in result.stderr
    assert subprocess.run([sys.executable, "-c", script, str(tmp_path)]).returncode == 0


def test_negative_timeout_rejected(tmp_path: Path):
    with pytest.raises(ValueError), database_locks([], "invalid", repository=tmp_path, timeout=-1):
        pass
