"""The API starts only against a metadata store whose schema it matches.

Accepting any recorded version means an old store starts cleanly and then fails
mid-request on a column that is not there. Startup has to either bring the store to
the expected version through a registered migration or refuse, and say why.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, create_engine, inspect, select, text, update
from sqlalchemy.orm import Session

from harness_api import db as metadata
from harness_api.db import SCHEMA_VERSION, Migration, initialize_schema
from harness_api.models import Base, SchemaVersion, StoreOwner
from harness_worker.errors import ConfigurationError


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return create_engine(f"sqlite+pysqlite:///{(tmp_path / 'store.sqlite3').as_posix()}")


def _stamp(engine: Engine, version: str) -> None:
    with engine.begin() as connection:
        connection.execute(update(SchemaVersion).values(version=version))


def _version(engine: Engine) -> str:
    with Session(engine) as session:
        return session.scalars(select(SchemaVersion.version)).one()


# What each schema version added, so a store "as version N left it" can be built from
# the current schema by stripping everything above N back out. Keeping this table here,
# rather than hand-written per test, is what stops these fixtures quietly becoming
# current-schema stores with an old number stamped on them -- which would test nothing.
_ADDED_BY_VERSION: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "4": (
        (
            "executions.owner_id",
            "executions.dispatched_at",
            "worksheet_sessions.owner_id",
            "worksheet_sessions.commit_requested_at",
        ),
        ("execution_runtimes", "store_owner"),
    ),
    "3": (("executions.request_digest",), ()),
}


def _store_as_of(engine: Engine, version: str) -> None:
    """Build the store the named release would have left behind."""

    initialize_schema(engine)
    with engine.begin() as connection:
        for above, (columns, tables) in sorted(_ADDED_BY_VERSION.items(), reverse=True):
            if int(above) <= int(version):
                continue
            for qualified in columns:
                table, column = qualified.split(".")
                connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
            for table in tables:
                connection.execute(text(f"DROP TABLE {table}"))
    _stamp(engine, version)


def _columns(engine: Engine, table: str) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table)}


def test_an_empty_store_is_created_at_the_current_version(engine: Engine) -> None:
    assert initialize_schema(engine) == SCHEMA_VERSION
    assert _version(engine) == SCHEMA_VERSION
    assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())


def test_a_store_at_the_current_version_starts_unchanged(engine: Engine) -> None:
    initialize_schema(engine)
    assert initialize_schema(engine) == SCHEMA_VERSION
    assert _version(engine) == SCHEMA_VERSION


def test_an_older_version_with_no_migration_is_refused(engine: Engine) -> None:
    initialize_schema(engine)
    _stamp(engine, "0")

    with pytest.raises(ConfigurationError, match="no migration from version 0") as refused:
        initialize_schema(engine)
    assert refused.value.detail["storeVersion"] == "0"
    assert refused.value.detail["expectedVersion"] == SCHEMA_VERSION
    # Refused before touching anything.
    assert _version(engine) == "0"


def test_a_newer_version_is_refused_rather_than_downgraded(engine: Engine) -> None:
    initialize_schema(engine)
    newer = str(int(SCHEMA_VERSION) + 1)
    _stamp(engine, newer)

    with pytest.raises(ConfigurationError, match="newer than this build"):
        initialize_schema(engine)
    assert _version(engine) == newer


def test_an_unrecognised_version_is_refused(engine: Engine) -> None:
    initialize_schema(engine)
    _stamp(engine, "3-beta")
    with pytest.raises(ConfigurationError, match="does not recognise"):
        initialize_schema(engine)


def test_an_unversioned_store_with_harness_tables_is_refused(engine: Engine) -> None:
    initialize_schema(engine)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM schema_version"))

    with pytest.raises(ConfigurationError, match="no schema version") as refused:
        initialize_schema(engine)
    assert "executions" in refused.value.detail["tables"]


def test_a_current_version_missing_a_column_is_refused(engine: Engine) -> None:
    initialize_schema(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE executions DROP COLUMN request_digest"))

    with pytest.raises(ConfigurationError, match="missing columns") as refused:
        initialize_schema(engine)
    assert refused.value.detail["missingColumns"] == ["executions.request_digest"]


def test_a_schema_version_table_missing_a_column_is_refused_by_name(engine: Engine) -> None:
    # The version is read before the general column check, so this table is the one
    # place a missing column would otherwise escape as a raw database error.
    initialize_schema(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE schema_version DROP COLUMN note"))

    with pytest.raises(ConfigurationError, match="version cannot be read") as refused:
        initialize_schema(engine)
    assert refused.value.detail["missingColumns"] == ["schema_version.note"]


def test_a_version_2_store_is_upgraded_in_place(engine: Engine) -> None:
    _store_as_of(engine, "2")

    assert initialize_schema(engine) == SCHEMA_VERSION
    assert _version(engine) == SCHEMA_VERSION
    assert "request_digest" in _columns(engine, "executions")


def test_a_version_3_store_gains_the_reconciliation_columns(engine: Engine) -> None:
    """The 3 -> 4 step, which restart reconciliation cannot work without.

    An upgraded store starts with no ownership stamps and no dispatch markers on the
    records it already holds. That is the case ``resolve_execution`` treats as unknown
    rather than safe, and ``test_restart_recovery`` covers the consequence.
    """

    _store_as_of(engine, "3")

    assert initialize_schema(engine) == SCHEMA_VERSION
    assert {"owner_id", "dispatched_at"} <= _columns(engine, "executions")
    assert {"owner_id", "commit_requested_at"} <= _columns(engine, "worksheet_sessions")
    tables = inspect(engine).get_table_names()
    assert "execution_runtimes" in tables
    # The owner row has to exist before two processes can contend for it, so the migration
    # creates the table and the row rather than leaving the table to create_all.
    assert "store_owner" in tables
    with Session(engine) as session:
        assert session.scalars(select(StoreOwner)).one().runtime_id == ""


def test_a_version_1_sqlite_store_is_refused_with_what_to_do(engine: Engine) -> None:
    # The 1 -> 2 step swaps a table constraint, which SQLite cannot do in place.
    _store_as_of(engine, "1")

    with pytest.raises(ConfigurationError, match="cannot be upgraded in place"):
        initialize_schema(engine)
    assert _version(engine) == "1"
    assert "request_digest" not in _columns(engine, "executions")


def test_a_failing_migration_leaves_the_store_at_its_old_version(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store_as_of(engine, "2")

    def half_done(connection: Connection) -> None:
        connection.execute(text("ALTER TABLE executions ADD COLUMN request_digest VARCHAR(64)"))
        raise RuntimeError("the second half of this step failed")

    monkeypatch.setitem(
        metadata.MIGRATIONS, "2", Migration(SCHEMA_VERSION, "fails halfway", half_done)
    )

    with pytest.raises(RuntimeError):
        initialize_schema(engine)
    assert _version(engine) == "2"
    assert "request_digest" not in _columns(engine, "executions")


def test_a_gap_in_the_migration_chain_is_refused_before_any_step_runs(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_schema(engine)
    _stamp(engine, "1")
    ran: list[str] = []
    # 1 -> 2 exists, 2 -> 3 does not.
    monkeypatch.setitem(
        metadata.MIGRATIONS, "1", Migration("2", "first step", lambda _: ran.append("1"))
    )
    monkeypatch.delitem(metadata.MIGRATIONS, "2")

    with pytest.raises(ConfigurationError, match="no migration from version 2"):
        initialize_schema(engine)
    assert ran == []
    assert _version(engine) == "1"


# -- PostgreSQL, the pilot store ------------------------------------------------------

POSTGRES_URL = os.environ.get("HARNESS_TEST_POSTGRES_URL", "")


@pytest.mark.skipif(
    not POSTGRES_URL,
    reason="Set HARNESS_TEST_POSTGRES_URL to an empty, disposable PostgreSQL database.",
)
def test_a_version_1_postgresql_store_is_upgraded_in_place() -> None:
    pytest.importorskip("psycopg")
    engine = create_engine(POSTGRES_URL)
    Base.metadata.drop_all(engine)
    try:
        # The store as version 1 left it: a global dedup_key constraint, no digest.
        _store_as_of(engine, "2")
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE executions DROP CONSTRAINT uq_executions_actor_dedup_key")
            )
            connection.execute(
                text(
                    "ALTER TABLE executions ADD CONSTRAINT uq_executions_dedup_key "
                    "UNIQUE (dedup_key)"
                )
            )
        _stamp(engine, "1")

        assert initialize_schema(engine) == SCHEMA_VERSION
        assert _version(engine) == SCHEMA_VERSION
        assert "request_digest" in _columns(engine, "executions")
        assert {"owner_id", "dispatched_at"} <= _columns(engine, "executions")
        assert {"owner_id", "commit_requested_at"} <= _columns(engine, "worksheet_sessions")
        constraints = {
            item["name"]: item["column_names"]
            for item in inspect(engine).get_unique_constraints("executions")
        }
        assert constraints.get("uq_executions_actor_dedup_key") == ["user_id", "dedup_key"]
        assert "uq_executions_dedup_key" not in constraints
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
