"""Metadata store wiring.

PostgreSQL is the pilot store. SQLite is supported so the application can be run and
tested without external services; the schema is identical, and anything that would
only work on one of them belongs in a migration rather than here.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import (
    Connection,
    Engine,
    Inspector,
    Table,
    create_engine,
    insert,
    inspect,
    select,
    update,
)
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from harness_api.config import Settings
from harness_api.models import Base, SchemaVersion, StoreOwner, utcnow
from harness_worker.errors import ConfigurationError

log = logging.getLogger("harness.db")

# 4: executions and worksheet sessions record their owning runtime, and the point at
#    which work was dispatched, so interrupted work can be reconciled after a restart.
SCHEMA_VERSION = "4"


def resolve_metadata_url(settings: Settings) -> URL:
    """The connection URL with the mounted password filled in, if there is one."""

    url = make_url(settings.metadata_url)
    if settings.metadata_password_file:
        url = url.set(password=_read_password_file(settings.metadata_password_file))
    return url


def build_engine(settings: Settings) -> Engine:
    url = resolve_metadata_url(settings)
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.drivername.startswith("sqlite"):
        # The execution service touches the metadata store from worker threads.
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def _read_password_file(path: str) -> str:
    """Read the metadata password from a mounted file.

    The deployment mounts the same secret the store itself reads, so there is one
    password rather than a file for PostgreSQL and a literal in the URL for the API.
    A missing file is a configuration error, not a silent fallback to whatever the
    URL happened to carry: that would fail later as an authentication error and read
    like a credential problem.
    """

    candidate = Path(path)
    if not candidate.is_file():
        raise ConfigurationError(
            "HARNESS_METADATA_PASSWORD_FILE points at a file that does not exist. "
            "Mount the secret, or put the password in HARNESS_METADATA_URL.",
            detail={"path": path},
        )
    # A trailing newline from `echo` or a Kubernetes secret is not part of the
    # password; anything else, including inner whitespace, is preserved.
    try:
        return candidate.read_text(encoding="utf-8").rstrip("\r\n")
    except PermissionError as exc:
        # The file is there and this process may not read it. Almost always a mounted
        # secret whose mode only its creator can read, met by a container that runs as
        # an unprivileged user: the API is uid 10001, which is not the operator who
        # wrote the file. Worth naming, because the bare PermissionError points at a
        # path and leaves the reader to work out that Compose preserved the host's mode
        # -- and because the database container in the same deployment is unaffected,
        # its entrypoint reading the same file as root before dropping privileges.
        raise ConfigurationError(
            "The metadata password file exists but this process cannot read it. A "
            "mounted secret keeps the mode it has on the host, and the API does not run "
            "as root: make the file readable by others and restrict its directory "
            "instead. See deploy/secrets/README.md.",
            detail={"path": path, "uid": getattr(os, "getuid", lambda: None)()},
        ) from exc


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@dataclass(frozen=True)
class Migration:
    """One step from the version it is registered under to ``to_version``.

    ``apply`` runs inside the upgrade transaction. It must leave the store matching the
    models for ``to_version``; tables that are new in that version can be left to
    ``create_all``, which runs after the last step.
    """

    to_version: str
    description: str
    apply: Callable[[Connection], None]


def _scope_dedup_key_to_actor(connection: Connection) -> None:
    if connection.dialect.name == "sqlite":
        # SQLite cannot drop a table constraint without rebuilding the table, and it
        # is the local development store only.
        raise ConfigurationError(
            "A SQLite metadata store at schema version 1 cannot be upgraded in place: "
            "the idempotency-key constraint is part of the table definition. Remove "
            "the development store and let the API create a new one.",
            detail={"storeVersion": "1"},
        )
    connection.exec_driver_sql(
        "ALTER TABLE executions DROP CONSTRAINT IF EXISTS uq_executions_dedup_key"
    )
    connection.exec_driver_sql(
        "ALTER TABLE executions ADD CONSTRAINT uq_executions_actor_dedup_key "
        "UNIQUE (user_id, dedup_key)"
    )


def _add_request_digest(connection: Connection) -> None:
    connection.exec_driver_sql("ALTER TABLE executions ADD COLUMN request_digest VARCHAR(64)")


def _add_restart_reconciliation(connection: Connection) -> None:
    """Add the ownership and dispatch columns restart reconciliation reads.

    Existing rows keep an empty ``owner_id`` and a NULL ``dispatched_at``. That is not
    read as "this never reached Oracle": a store written by an earlier build has no
    dispatch marker at all, so reconciliation treats its non-terminal writes as
    uncertain rather than assuming the safe answer. See harness_api.recovery.
    """

    timestamp = "TIMESTAMP WITH TIME ZONE" if connection.dialect.name != "sqlite" else "TIMESTAMP"
    for table in ("executions", "worksheet_sessions"):
        connection.exec_driver_sql(
            f"ALTER TABLE {table} ADD COLUMN owner_id VARCHAR(40) DEFAULT '' NOT NULL"
        )
    connection.exec_driver_sql(f"ALTER TABLE executions ADD COLUMN dispatched_at {timestamp}")
    connection.exec_driver_sql(
        f"ALTER TABLE worksheet_sessions ADD COLUMN commit_requested_at {timestamp}"
    )
    # store_owner and execution_runtimes are new tables, so create_all makes them after
    # this step. The singleton row is not a table, and claiming the store locks it, so it
    # has to exist before two processes can race for it.
    Base.metadata.tables[StoreOwner.__tablename__].create(connection, checkfirst=True)
    connection.execute(insert(StoreOwner).values(id=1, runtime_id=""))


# Keyed by the version a step upgrades *from*. Add a step here in the same change that
# bumps SCHEMA_VERSION, and test it from a store the previous release would leave.
MIGRATIONS: dict[str, Migration] = {
    "1": Migration(
        "2",
        "executions.dedup_key is unique per actor, not globally",
        _scope_dedup_key_to_actor,
    ),
    "2": Migration(
        "3",
        "executions.request_digest for exact idempotency checks",
        _add_request_digest,
    ),
    "3": Migration(
        "4",
        "runtime ownership and dispatch markers for restart reconciliation",
        _add_restart_reconciliation,
    ),
}


def initialize_schema(engine: Engine) -> str:
    """Create a new store, or upgrade an existing one, to SCHEMA_VERSION.

    Refuses to start rather than serve requests against a store it does not match: a
    version newer than this build, an older one with no registered migration path, an
    unversioned store that already holds harness tables, or tables missing columns the
    models expect. Any of those would otherwise fail later, mid-request, as an opaque
    database error.
    """

    recorded = _recorded_version(engine)
    if recorded is None:
        present = sorted(set(inspect(engine).get_table_names()) & set(Base.metadata.tables))
        if present:
            raise ConfigurationError(
                "The metadata store holds harness tables but no schema version, so "
                "there is no way to tell which build created them. Restore the "
                "schema_version row, or point HARNESS_METADATA_URL at an empty database.",
                detail={"tables": present},
            )
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(
                SchemaVersion(id=1, version=SCHEMA_VERSION, note="Created by initialize_schema.")
            )
            # The row claiming runs against. Unclaimed until an execution service starts.
            session.add(StoreOwner(id=1, runtime_id=""))
            session.commit()
        return SCHEMA_VERSION

    if recorded != SCHEMA_VERSION:
        _upgrade(engine, recorded)
    # A table introduced without a schema bump, or by a migration that left it to
    # create_all. Existing tables are never altered by this.
    Base.metadata.create_all(engine)
    _refuse_missing_columns(engine)
    return SCHEMA_VERSION


def _recorded_version(engine: Engine) -> str | None:
    inspector = inspect(engine)
    if not inspector.has_table(SchemaVersion.__tablename__):
        return None
    # Reading the row selects every mapped column, so check this table before the
    # others: a missing one would otherwise surface as a raw database error.
    missing = _missing_columns(inspector, [Base.metadata.tables[SchemaVersion.__tablename__]])
    if missing:
        raise ConfigurationError(
            "The metadata store's schema_version table is missing columns, so its "
            "version cannot be read. It was altered by hand or by an unreleased build.",
            detail={"missingColumns": missing},
        )
    with Session(engine) as session:
        row = session.scalars(select(SchemaVersion).limit(1)).first()
        return None if row is None else row.version


def _upgrade(engine: Engine, recorded: str) -> None:
    try:
        newer = int(recorded) > int(SCHEMA_VERSION)
    except ValueError:
        raise ConfigurationError(
            f"The metadata store reports schema version {recorded!r}, which this build "
            "does not recognise.",
            detail={"storeVersion": recorded, "expectedVersion": SCHEMA_VERSION},
        ) from None
    if newer:
        raise ConfigurationError(
            f"The metadata store is at schema version {recorded}, newer than this build "
            f"(version {SCHEMA_VERSION}). Run the build that upgraded it; a store is "
            "never downgraded.",
            detail={"storeVersion": recorded, "expectedVersion": SCHEMA_VERSION},
        )

    steps: list[tuple[str, Migration]] = []
    version = recorded
    while version != SCHEMA_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise ConfigurationError(
                f"The metadata store is at schema version {recorded} and this build "
                f"expects {SCHEMA_VERSION}, but there is no migration from version "
                f"{version}. Nothing was changed. Restore a backup taken by a matching "
                "build, or start from an empty store.",
                detail={
                    "storeVersion": recorded,
                    "expectedVersion": SCHEMA_VERSION,
                    "missingStepFrom": version,
                },
            )
        steps.append((version, step))
        version = step.to_version

    # One transaction for the whole path: PostgreSQL and SQLite both roll DDL back,
    # so a step that fails leaves the store exactly at the version it reported.
    with engine.begin() as connection:
        if connection.dialect.name == "sqlite":
            # pysqlite only opens a transaction before DML, so DDL would autocommit
            # step by step. An explicit BEGIN makes the driver's commit and rollback
            # cover it.
            connection.exec_driver_sql("BEGIN")
        for from_version, step in steps:
            log.warning(
                "metadata: upgrading schema %s -> %s: %s",
                from_version,
                step.to_version,
                step.description,
            )
            step.apply(connection)
        connection.execute(
            update(SchemaVersion)
            .where(SchemaVersion.id == 1)
            .values(
                version=SCHEMA_VERSION,
                applied_at=utcnow(),
                note=f"Upgraded from version {recorded} by initialize_schema.",
            )
        )


def _missing_columns(inspector: Inspector, tables: Iterable[Table]) -> list[str]:
    missing: list[str] = []
    for table in tables:
        present = {column["name"] for column in inspector.get_columns(table.name)}
        missing.extend(
            f"{table.name}.{column.name}" for column in table.columns if column.name not in present
        )
    return missing


def _refuse_missing_columns(engine: Engine) -> None:
    missing = _missing_columns(inspect(engine), Base.metadata.sorted_tables)
    if missing:
        raise ConfigurationError(
            f"The metadata store claims schema version {SCHEMA_VERSION} but is missing "
            "columns this build writes to. It was altered by hand or by an unreleased "
            "build; requests against it would fail.",
            detail={"missingColumns": missing},
        )


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
