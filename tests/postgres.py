"""The PostgreSQL metadata store, against a real disposable database.

PostgreSQL is the pilot store; SQLite is what the rest of the suite runs on because it
needs no external service. That difference is not cosmetic, and these are the checks that
cannot be moved onto SQLite without ceasing to mean anything:

* **Isolation.** SQLite serializes writers. PostgreSQL's default READ COMMITTED lets two
  transactions miss each other's uncommitted rows, which is the race the store-ownership
  claim is built to survive.
* **Constraints.** The version 1 to 2 migration swaps a table constraint. SQLite cannot do
  that in place at all, so it refuses the upgrade and the swap itself is never exercised.
* **Types.** ``timestamptz`` comes back timezone-aware and JSON columns are queried with
  PostgreSQL's own operators. Both are used by reconciliation, and SQLite takes a different
  path through each.

Point ``HARNESS_TEST_POSTGRES_URL`` at an empty, disposable database. Every fixture here
drops the harness tables before and after it runs, so the database must hold nothing worth
keeping.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine

from harness_api.config import Settings
from harness_api.db import build_session_factory, initialize_schema
from harness_api.models import Base

URL = os.environ.get("HARNESS_TEST_POSTGRES_URL", "")

SKIP_REASON = (
    "Set HARNESS_TEST_POSTGRES_URL to an empty, disposable PostgreSQL database. "
    "CI runs these against a service container; see .github/workflows/ci.yml."
)

if not URL and os.environ.get("HARNESS_REQUIRE_POSTGRES"):
    # A skipped store check is indistinguishable from a passing one in a summary line, and
    # these are precisely the checks that exist because SQLite cannot stand in for the
    # pilot store. Somewhere that has undertaken to run them -- CI sets this -- a missing
    # database is a broken job, not a quiet skip.
    raise RuntimeError(
        "HARNESS_REQUIRE_POSTGRES is set but HARNESS_TEST_POSTGRES_URL is empty, so the "
        "PostgreSQL checks would silently skip. Start the store, or unset "
        "HARNESS_REQUIRE_POSTGRES to allow skipping."
    )

requires_postgres = pytest.mark.skipif(not URL, reason=SKIP_REASON)


def engine() -> Engine:
    if not URL:  # pragma: no cover - the marker above prevents this
        raise RuntimeError(SKIP_REASON)
    return create_engine(URL)


@contextmanager
def empty_store() -> Iterator[Engine]:
    """An engine over a database with no harness tables, dropped again afterwards.

    Dropping on the way in as well as out matters: a previous failed run leaves its tables
    behind, and a test that then "passed" against a store it did not create would be
    worthless.
    """

    store = engine()
    try:
        Base.metadata.drop_all(store)
        yield store
    finally:
        Base.metadata.drop_all(store)
        store.dispose()


@contextmanager
def initialized_store() -> Iterator[Engine]:
    """An engine over a store already brought to the current schema version."""

    with empty_store() as store:
        initialize_schema(store)
        yield store


def settings_for(store_dir: Path, **overrides: Any) -> Settings:
    """Application settings pointing at the PostgreSQL store and the stand-in Oracle.

    The Oracle backend stays the local stand-in. What is under test here is the metadata
    store, and pulling a real Oracle into it would make the check impossible to run in CI
    and would not make it a better check of PostgreSQL.
    """

    (store_dir / "secrets").mkdir(exist_ok=True)
    (store_dir / "fake").mkdir(exist_ok=True)
    fields: dict[str, Any] = {
        "env": "development",
        "metadata_url": URL,
        "secret_dir": str(store_dir / "secrets"),
        "auth_mode": "dev",
        "dev_token_secret": "test-secret",
        "oracle_backend": "fake",
        "oracle_fake_data_dir": str(store_dir / "fake"),
        "copilot_enabled": True,
        "copilot_provider": "fake",
        "worksheet_idle_seconds": 300.0,
        "max_rows": 1000,
        "statement_timeout_seconds": 30.0,
    }
    fields.update(overrides)
    return Settings(**fields)


def session_factory(store: Engine) -> Any:
    return build_session_factory(store)
