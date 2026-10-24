"""Unit tests for deploy/upgrade_drill_helper.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text

# Add deploy directory to path so helper can be imported
deploy_dir = Path(__file__).resolve().parents[2] / "deploy"
if str(deploy_dir) not in sys.path:
    sys.path.insert(0, str(deploy_dir))

import upgrade_drill_helper as helper  # noqa: E402

from harness_api.db import initialize_schema  # noqa: E402


@pytest.fixture
def sqlite_engine(tmp_path: Path) -> Engine:
    db_file = tmp_path / "test_upgrade.sqlite3"
    return create_engine(f"sqlite+pysqlite:///{db_file.as_posix()}", future=True)


def _setup_store_at_v3(engine: Engine) -> None:
    """Build a store shaped as version 3 left it."""
    initialize_schema(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE connection_profiles DROP COLUMN wallet_secret_reference_id"))
        conn.execute(text("ALTER TABLE executions DROP COLUMN owner_id"))
        conn.execute(text("ALTER TABLE executions DROP COLUMN dispatched_at"))
        conn.execute(text("ALTER TABLE worksheet_sessions DROP COLUMN owner_id"))
        conn.execute(text("ALTER TABLE worksheet_sessions DROP COLUMN commit_requested_at"))
        conn.execute(text("DROP TABLE IF EXISTS store_owner"))
        conn.execute(text("DROP TABLE IF EXISTS execution_runtimes"))
        conn.execute(text("DROP TABLE IF EXISTS copilot_tool_calls"))
        conn.execute(text("DROP TABLE IF EXISTS proposed_edit_parts"))
        conn.execute(text("UPDATE schema_version SET version = '3' WHERE id = 1"))


def test_helper_seed_and_snapshot(sqlite_engine: Engine) -> None:
    initialize_schema(sqlite_engine)
    helper.seed_representative_data(sqlite_engine, run_demo_seed=False)

    snapshot = helper.take_snapshot(sqlite_engine)
    assert snapshot["schema_version"] == "5"
    assert "users" in snapshot["table_counts"]
    assert "executions" in snapshot["table_counts"]
    assert len(snapshot["records"]["users"]) >= 1
    assert len(snapshot["records"]["executions"]) >= 1

    # Verify that verify_upgrade passes on this snapshot
    helper.verify_upgrade(sqlite_engine, snapshot, expected_version="5")


def test_helper_verify_upgrade_catches_missing_row(sqlite_engine: Engine) -> None:
    initialize_schema(sqlite_engine)
    helper.seed_representative_data(sqlite_engine, run_demo_seed=False)
    snapshot = helper.take_snapshot(sqlite_engine)

    # Delete the execution record
    with sqlite_engine.begin() as conn:
        conn.execute(text("DELETE FROM executions WHERE id = 'exe_drill_1'"))

    with pytest.raises(AssertionError, match="Row count mismatch in 'executions'"):
        helper.verify_upgrade(sqlite_engine, snapshot, expected_version="5")


def test_helper_verify_upgrade_catches_corrupted_field(sqlite_engine: Engine) -> None:
    initialize_schema(sqlite_engine)
    helper.seed_representative_data(sqlite_engine, run_demo_seed=False)
    snapshot = helper.take_snapshot(sqlite_engine)

    # Mutate a pre-existing record field
    with sqlite_engine.begin() as conn:
        conn.execute(text("UPDATE executions SET state = 'corrupted' WHERE id = 'exe_drill_1'"))

    with pytest.raises(AssertionError, match="changed"):
        helper.verify_upgrade(sqlite_engine, snapshot, expected_version="5")


def test_helper_verify_upgrade_catches_version_mismatch(sqlite_engine: Engine) -> None:
    initialize_schema(sqlite_engine)
    helper.seed_representative_data(sqlite_engine, run_demo_seed=False)
    snapshot = helper.take_snapshot(sqlite_engine)

    with pytest.raises(AssertionError, match="Schema version mismatch"):
        helper.verify_upgrade(sqlite_engine, snapshot, expected_version="4")


def test_helper_idempotency_check(sqlite_engine: Engine) -> None:
    initialize_schema(sqlite_engine)
    helper.seed_representative_data(sqlite_engine, run_demo_seed=False)
    snapshot = helper.take_snapshot(sqlite_engine)

    # Calling verify_idempotency runs initialize_schema again and checks that snapshot is identical
    helper.verify_idempotency(sqlite_engine, snapshot)


def test_helper_simulated_v3_to_v5_upgrade(sqlite_engine: Engine) -> None:
    # 1. Start with store shaped as version 3
    _setup_store_at_v3(sqlite_engine)
    helper.seed_representative_data(sqlite_engine, run_demo_seed=False)

    # 2. Snapshot store at version 3
    snapshot_v3 = helper.take_snapshot(sqlite_engine)
    assert snapshot_v3["schema_version"] == "3"
    assert "store_owner" not in snapshot_v3["table_counts"]

    # 3. Upgrade to current version (5)
    result_version = helper.upgrade_schema(sqlite_engine)
    assert result_version == "5"

    # 4. Verify upgrade data retention and schema extensions
    helper.verify_upgrade(sqlite_engine, snapshot_v3, expected_version="5")

    # 5. Verify idempotency
    snapshot_v5 = helper.take_snapshot(sqlite_engine)
    helper.verify_idempotency(sqlite_engine, snapshot_v5)
