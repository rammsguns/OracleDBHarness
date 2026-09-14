"""Fixtures for the Oracle 19c qualification suite.

Nothing here runs unless the configuration in ``tests/oracle_config.py`` is present. When it is,
the schema objects in ``oracle/qualification/`` are rebuilt once for the session and
dropped afterwards, so a run is repeatable regardless of how the previous one ended.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from harness_worker.backend import OracleBackend, OracleConnection, create_backend
from harness_worker.types import ExecutionLimits
from tests import oracle_config as qual_config
from tests.oracle_fixtures import apply_fixtures, drop_fixtures
from tests.qualification.evidence import Evidence
from tests.qualification.requirements import skip_or_fail


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
    # Written up front, so the report names what this run could never have reached even
    # when the suite stops early.
    if oracle_config.admin is None:
        record.gap(
            "Connection loss (killed sessions)",
            f"{qual_config.ENV_ADMIN_DSN} not set; no privileged account to end a session.",
        )
    if oracle_config.second is None:
        record.gap("Second target", qual_config.NO_SECOND_TARGET_REASON)
    if oracle_config.restricted is None:
        record.gap("Restricted account", qual_config.NO_RESTRICTED_ACCOUNT_REASON)
    if oracle_config.driver_mode == "thin":
        record.gap("Thick mode", "This run is thin mode; thick needs a separate process.")
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
        skip_or_fail(
            oracle_config,
            "admin",
            f"{qual_config.ENV_ADMIN_DSN} is not set. A connection cannot be lost "
            "honestly from inside itself, so these checks need a privileged session "
            "that can run ALTER SYSTEM KILL SESSION.",
        )
    assert spec is not None
    session = backend.connect(spec)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def second_target_connection(
    backend: OracleBackend,
    oracle_config: qual_config.OracleTestConfig,
) -> Iterator[OracleConnection]:
    """A session on the second configured database. Needs no fixture objects."""

    spec = oracle_config.second_spec()
    if spec is None:
        skip_or_fail(oracle_config, "second_target", qual_config.NO_SECOND_TARGET_REASON)
    assert spec is not None
    session = backend.connect(spec)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def restricted_connection(
    backend: OracleBackend,
    oracle_config: qual_config.OracleTestConfig,
) -> Iterator[OracleConnection]:
    spec = oracle_config.restricted_spec()
    if spec is None:
        skip_or_fail(oracle_config, "restricted_account", qual_config.NO_RESTRICTED_ACCOUNT_REASON)
    assert spec is not None
    session = backend.connect(spec)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def limits() -> ExecutionLimits:
    """The harness defaults, so what is qualified is what a deployment runs."""

    return ExecutionLimits()
