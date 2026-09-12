"""The API starts only against a metadata store whose schema it matches.

Accepting any recorded version means an old store starts cleanly and then fails
mid-request on a column that is not there. Startup has to either bring the store to
the expected version through a registered migration or refuse, and say why.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, create_engine, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from harness_api import db as metadata
from harness_api.db import SCHEMA_VERSION, Migration, initialize_schema
from harness_api.models import (
    AuditEvent,
    Base,
    ConnectionProfile,
    Execution,
    SchemaVersion,
    SecretReference,
    StoreOwner,
    User,
    UserTargetGrant,
)
from harness_worker.errors import ConfigurationError
from tests import postgres
from tests.postgres import requires_postgres


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
#
# The checks above run on SQLite and prove the migration machinery. They cannot prove the
# migrations themselves: the version 1 to 2 step swaps a table constraint, which SQLite
# refuses outright, so on SQLite that step is only ever tested by its refusal. These run
# against a real disposable database. See tests/postgres.py.


@requires_postgres
def test_a_version_1_postgresql_store_is_upgraded_in_place() -> None:
    with postgres.empty_store() as engine:
        _version_1_store(engine)

        assert initialize_schema(engine) == SCHEMA_VERSION
        assert _version(engine) == SCHEMA_VERSION
        assert "request_digest" in _columns(engine, "executions")
        assert {"owner_id", "dispatched_at"} <= _columns(engine, "executions")
        assert {"owner_id", "commit_requested_at"} <= _columns(engine, "worksheet_sessions")
        assert {"execution_runtimes", "store_owner"} <= set(inspect(engine).get_table_names())
        constraints = {
            item["name"]: item["column_names"]
            for item in inspect(engine).get_unique_constraints("executions")
        }
        assert constraints.get("uq_executions_actor_dedup_key") == ["user_id", "dedup_key"]
        assert "uq_executions_dedup_key" not in constraints
        with Session(engine) as session:
            assert session.scalars(select(StoreOwner)).one().runtime_id == ""


@requires_postgres
def test_an_upgrade_keeps_the_records_the_store_already_held() -> None:
    """A migration that loses history is not an upgrade.

    The steps are ALTER TABLE, so nothing should touch a row -- which is exactly the kind
    of assumption worth asserting once against the real engine, because a step that
    rebuilt a table to change a constraint would pass every other check in this file.
    """

    with postgres.empty_store() as engine:
        _version_1_store(engine)
        _seed_representative_records(engine)

        initialize_schema(engine)

        with Session(engine) as session:
            profile = session.scalars(select(ConnectionProfile)).one()
            assert profile.name == "development"
            assert profile.worksheets_enabled is True
            # Two users were seeded, so name the one whose roles are being checked.
            user = session.get(User, "usr_1")
            assert user is not None and user.roles == ["developer"]
            grant = session.scalars(select(UserTargetGrant)).one()
            assert grant.permissions == ["read", "worksheet"]
            executions = session.scalars(select(Execution).order_by(Execution.id)).all()
            assert [row.id for row in executions] == ["exe_one", "exe_two"]
            assert [row.state for row in executions] == ["succeeded", "failed"]
            # Carried over untouched, and the columns the upgrade added are empty rather
            # than invented. Reconciliation reads that as "cannot be established".
            assert [row.owner_id for row in executions] == ["", ""]
            assert [row.dispatched_at for row in executions] == [None, None]
            assert session.scalars(select(AuditEvent)).one().operation_id == "worksheet.execute"


@requires_postgres
def test_an_idempotency_key_is_scoped_to_the_actor_after_the_upgrade() -> None:
    """The point of the version 1 to 2 step, checked by what the constraint now permits.

    Under version 1 the key was globally unique, so one user's choice of key locked every
    other user out of it -- and a lookup by key alone could hand one user another's
    execution record. The upgrade makes it unique per actor. Asserting the constraint's
    name is not the same as asserting its behaviour, so this inserts the rows that each
    case turns on.
    """

    with postgres.empty_store() as engine:
        _version_1_store(engine)
        initialize_schema(engine)
        # After the upgrade, so these go in through the current models: what is under test
        # is the constraint the migration installed, not how old rows were written.
        _seed_current_users_and_target(engine)

        # The same key, two different actors: allowed now, refused before the upgrade.
        with Session(engine) as session:
            session.add(_execution("exe_a", user_id="usr_1", dedup_key="nightly-refresh"))
            session.add(_execution("exe_b", user_id="usr_2", dedup_key="nightly-refresh"))
            session.commit()

        # The same key twice for one actor is still refused: that is what deduplication is.
        with Session(engine) as session, pytest.raises(IntegrityError):
            session.add(_execution("exe_c", user_id="usr_1", dedup_key="nightly-refresh"))
            session.commit()

        # A NULL key is not a value, so any number of executions may have none.
        with Session(engine) as session:
            session.add(_execution("exe_d", user_id="usr_1", dedup_key=None))
            session.add(_execution("exe_e", user_id="usr_1", dedup_key=None))
            session.commit()
        with Session(engine) as session:
            assert len(session.scalars(select(Execution)).all()) == 4


@requires_postgres
def test_a_failing_migration_leaves_a_postgresql_store_at_its_old_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole upgrade path is one transaction, and PostgreSQL rolls DDL back.

    The SQLite version of this check needs an explicit BEGIN to stop the driver
    autocommitting each statement, so it proves the harness works around pysqlite rather
    than that the guarantee holds on the pilot store.
    """

    with postgres.empty_store() as engine:
        _store_as_of(engine, "3")
        _seed_representative_records(engine)

        def half_done(connection: Connection) -> None:
            connection.exec_driver_sql(
                "ALTER TABLE executions ADD COLUMN owner_id VARCHAR(40) DEFAULT '' NOT NULL"
            )
            raise RuntimeError("the second half of this step failed")

        monkeypatch.setitem(
            metadata.MIGRATIONS, "3", Migration(SCHEMA_VERSION, "fails halfway", half_done)
        )

        with pytest.raises(RuntimeError):
            initialize_schema(engine)

        assert _version(engine) == "3"
        assert "owner_id" not in _columns(engine, "executions")
        with Session(engine) as session:
            assert len(session.scalars(select(Execution)).all()) == 2


@requires_postgres
def test_a_newer_postgresql_store_is_refused_rather_than_downgraded() -> None:
    with postgres.initialized_store() as engine:
        newer = str(int(SCHEMA_VERSION) + 1)
        _stamp(engine, newer)

        with pytest.raises(ConfigurationError, match="newer than this build"):
            initialize_schema(engine)
        assert _version(engine) == newer


@requires_postgres
def test_a_postgresql_store_missing_a_column_is_refused() -> None:
    """The guard that stops a hand-edited store serving requests and failing mid-flight."""

    with postgres.initialized_store() as engine:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE executions DROP COLUMN dispatched_at")

        with pytest.raises(ConfigurationError, match="missing columns") as refused:
            initialize_schema(engine)
        assert refused.value.detail["missingColumns"] == ["executions.dispatched_at"]


# -- shared fixtures for the PostgreSQL checks ----------------------------------------


def _version_1_store(engine: Engine) -> None:
    """The store as the first release left it: a global dedup_key constraint, no digest."""

    _store_as_of(engine, "2")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE executions DROP CONSTRAINT uq_executions_actor_dedup_key"
        )
        connection.exec_driver_sql(
            "ALTER TABLE executions ADD CONSTRAINT uq_executions_dedup_key UNIQUE (dedup_key)"
        )
    _stamp(engine, "1")


def _execution(execution_id: str, *, user_id: str, dedup_key: str | None) -> Execution:
    """An execution row for the *current* schema, inserted through the ORM."""

    return Execution(
        id=execution_id,
        user_id=user_id,
        profile_id="tgt_1",
        operation_id="worksheet.execute",
        statement_kind="query",
        risk_class="read",
        statement_fingerprint="f" * 64,
        state="succeeded",
        dedup_key=dedup_key,
    )


def _seed_current_users_and_target(engine: Engine) -> None:
    """The users and target an execution's foreign keys need, at the current schema."""

    with Session(engine) as session:
        session.add(SecretReference(id="sec_1", name="oracle-app", locator="oracle_app.password"))
        session.add(
            ConnectionProfile(
                id="tgt_1",
                name="development",
                host="oracle.internal",
                port=1521,
                service_name="DEV",
                username="harness_app",
                secret_reference_id="sec_1",
                worksheets_enabled=True,
            )
        )
        session.add(User(id="usr_1", subject="dev@example.internal", roles=["developer"]))
        session.add(User(id="usr_2", subject="dba@example.internal", roles=["dba"]))
        session.commit()


def _seed_representative_records(engine: Engine) -> None:
    """One of each record an upgrade has to carry across, with its relationships intact.

    Written with explicit SQL, naming only the columns that existed at schema version 1,
    because that is the whole point: these are rows an *older* build left behind. Inserting
    them through today's ORM would name today's columns -- ``request_digest``, ``owner_id``,
    ``dispatched_at`` -- and fail against the very store shape under test. A model mapped
    over a table it does not match is exactly the situation the upgrade exists to resolve.
    """

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO secret_references (id, name, provider, locator, description,"
                " created_at) VALUES"
                " ('sec_1', 'oracle-app', 'file', 'oracle_app.password', '', now())"
            )
        )
        connection.execute(
            text(
                "INSERT INTO connection_profiles (id, name, environment, host, port,"
                " service_name, username, default_schema, protocol, wallet_dir,"
                " secret_reference_id, worksheets_enabled, mutating_runbooks_enabled, notes,"
                " created_at) VALUES"
                " ('tgt_1', 'development', 'development', 'oracle.internal', 1521, 'DEV',"
                " 'harness_app', 'HARNESS_APP', 'tcp', '', 'sec_1', true, false, '', now())"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users (id, subject, display_name, email, roles, disabled,"
                " created_at) VALUES"
                " ('usr_1', 'dev@example.internal', 'Dev', '', '[\"developer\"]', false, now()),"
                " ('usr_2', 'dba@example.internal', 'Dba', '', '[\"dba\"]', false, now())"
            )
        )
        connection.execute(
            text(
                "INSERT INTO user_target_grants (id, user_id, profile_id, permissions,"
                " granted_by, created_at) VALUES"
                " ('grt_1', 'usr_1', 'tgt_1', '[\"read\", \"worksheet\"]', '', now())"
            )
        )
        # The version 1 execution columns, and only those.
        for execution_id, user_id, state, dedup_key, error_code in (
            ("exe_one", "usr_1", "succeeded", "'nightly-refresh'", ""),
            ("exe_two", "usr_2", "failed", "NULL", "oracle_error"),
        ):
            connection.execute(
                text(
                    "INSERT INTO executions (id, user_id, profile_id, operation_id,"
                    " statement_kind, risk_class, statement_fingerprint, bind_names,"
                    " limits_json, policy_decision, policy_reason, state, truncated,"
                    " error_code, error_message, verification_json, dedup_key, started_at)"
                    f" VALUES ('{execution_id}', '{user_id}', 'tgt_1', 'worksheet.execute',"
                    " 'query', 'read', repeat('f', 64), '[]', '{}', 'allowed', '',"
                    f" '{state}', false, '{error_code}', '', '{{}}', {dedup_key}, now())"
                )
            )
        connection.execute(
            text(
                "INSERT INTO audit_events (id, created_at, actor_id, actor_subject,"
                " profile_id, operation_id, execution_id, risk_class, policy_decision,"
                " outcome, statement_fingerprint, affected_counts, detail) VALUES"
                " ('aud_1', now(), 'usr_1', 'dev@example.internal', 'tgt_1',"
                " 'worksheet.execute', 'exe_one', 'read', 'allowed', 'succeeded',"
                " repeat('f', 64), '{}', '{}')"
            )
        )
