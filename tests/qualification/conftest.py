"""Fixtures for the Oracle 19c qualification suite.

Nothing here runs unless the configuration in ``config.py`` is present. When it is,
the schema objects in ``oracle/qualification/`` are rebuilt once for the session and
dropped afterwards, so a run is repeatable regardless of how the previous one ended.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from harness_worker.backend import OracleBackend, OracleConnection, create_backend
from harness_worker.types import ExecutionLimits
from tests.qualification import config as qual_config
from tests.qualification.evidence import Evidence
from tests.qualification.fixtures import apply_fixtures, drop_fixtures


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "needs_admin: requires the privileged qualification connection "
        "(HARNESS_QUAL_ADMIN_DSN) because it has to end a session from outside it.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip the whole suite when it is not configured, before anything connects."""

    if qual_config.is_configured():
        return
    skip = pytest.mark.skip(reason=qual_config.SKIP_REASON)
    for item in items:
        if "qualification" in item.nodeid.split("/"):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def oracle_config() -> qual_config.OracleTestConfig:
    return qual_config.load()


@pytest.fixture(scope="session")
def backend(oracle_config: qual_config.OracleTestConfig) -> Iterator[OracleBackend]:
    instance = create_backend(
        "oracledb",
        driver_mode=oracle_config.driver_mode,
        lib_dir=oracle_config.client_lib_dir or None,
    )
    try:
        yield instance
    finally:
        instance.shutdown()


@pytest.fixture(scope="session")
def evidence(oracle_config: qual_config.OracleTestConfig) -> Iterator[Evidence]:
    record = Evidence(config=oracle_config)
    try:
        yield record
    finally:
        record.write()


@pytest.fixture(scope="session")
def _schema(
    backend: OracleBackend,
    oracle_config: qual_config.OracleTestConfig,
) -> Iterator[None]:
    """Build the fixture schema once, and take it down again.

    Teardown runs even when the suite fails, because leaving 400,000 rows and a
    deliberately invalid package behind would change the result of the next run.
    """

    setup = backend.connect(oracle_config.connection_spec("qualification-setup"))
    try:
        apply_fixtures(setup)
    finally:
        setup.close()

    try:
        yield
    finally:
        teardown = backend.connect(oracle_config.connection_spec("qualification-teardown"))
        try:
            drop_fixtures(teardown)
        finally:
            teardown.close()


@pytest.fixture
def connection(
    backend: OracleBackend,
    oracle_config: qual_config.OracleTestConfig,
    _schema: None,
) -> Iterator[OracleConnection]:
    """A fresh session per test.

    Per test rather than per session: several of these checks deliberately break
    their connection, and a broken session must never be handed to the next test.
    """

    session = backend.connect(oracle_config.connection_spec())
    try:
        yield session
    finally:
        try:
            if not session.is_broken and session.transaction_open:
                session.rollback()
        finally:
            session.close()


@pytest.fixture
def second_connection(
    backend: OracleBackend,
    oracle_config: qual_config.OracleTestConfig,
    _schema: None,
) -> Iterator[OracleConnection]:
    """A second independent session, for isolation and blocking checks."""

    session = backend.connect(oracle_config.connection_spec("qualification-second"))
    try:
        yield session
    finally:
        try:
            if not session.is_broken and session.transaction_open:
                session.rollback()
        finally:
            session.close()


@pytest.fixture
def admin_connection(
    backend: OracleBackend,
    oracle_config: qual_config.OracleTestConfig,
) -> Iterator[OracleConnection]:
    spec = oracle_config.admin_spec()
    if spec is None:
        pytest.skip(
            f"{qual_config.ENV_ADMIN_DSN} is not set. A connection cannot be lost "
            "honestly from inside itself, so these checks need a privileged session "
            "that can run ALTER SYSTEM KILL SESSION."
        )
    session = backend.connect(spec)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def limits() -> ExecutionLimits:
    """The harness defaults, so what is qualified is what a deployment runs."""

    return ExecutionLimits()
