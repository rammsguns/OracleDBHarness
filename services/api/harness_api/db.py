"""Metadata store wiring.

PostgreSQL is the pilot store. SQLite is supported so the application can be run and
tested without external services; the schema is identical, and anything that would
only work on one of them belongs in a migration rather than here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from harness_api.config import Settings
from harness_api.models import Base, SchemaVersion
from harness_worker.errors import ConfigurationError

# 3: executions retain an exact request digest for idempotency checks.
SCHEMA_VERSION = "3"


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
    return candidate.read_text(encoding="utf-8").rstrip("\r\n")


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def initialize_schema(engine: Engine) -> str:
    """Create tables if absent and record the schema version.

    A real migration tool belongs here before the pilot upgrades anything in place;
    until then this is create-if-missing and refuses to guess about drift.
    """

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        row = session.scalars(select(SchemaVersion).limit(1)).first()
        if row is None:
            session.add(
                SchemaVersion(
                    id=1,
                    version=SCHEMA_VERSION,
                    note="Created by initialize_schema; no migration history yet.",
                )
            )
            session.commit()
            return SCHEMA_VERSION
        return row.version


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
