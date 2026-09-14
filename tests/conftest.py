"""Shared fixtures.

By default every test runs against the local stand-in backend. That exercises the
real execution, session, limit, transaction and policy code, but it is not evidence
of Oracle compatibility. See docs/compatibility.md.

Configure an Oracle target (``tests/oracle_config.py``) and these same suites run
against it instead: the ``settings`` fixture switches the backend, the demonstration
seed is pointed at the real endpoints, and the fixture schema is built from the
reviewed DDL in ``oracle/qualification/``. Tests that can only work against the
stand-in — the ones that inject a fault into it, or monkeypatch its class — are
marked ``stand_in_only`` and skip.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from harness_api.app import create_app
from harness_api.config import Settings
from harness_api.db import build_engine, build_session_factory
from harness_api.execution import ExecutionService
from harness_api.seed import TargetEndpoint, seed
from harness_worker.backend import create_backend
from tests import oracle_config
from tests.oracle_fixtures import (
    SEEDED_PACKAGE_STEPS,
    apply_fixtures,
    drop_fixtures,
    reapply_steps,
    warm_cursor_cache,
)

MARKERS = [
    (
        "stand_in_only: cannot run against Oracle. Either it injects a fault the "
        "stand-in exposes for the purpose, or it patches the stand-in's class. The "
        "Oracle equivalent lives in tests/qualification."
    ),
    ("needs_second_target: needs two genuinely separate databases (HARNESS_QUAL_SECOND_DSN)."),
    (
        "oracle_only: needs behaviour the stand-in does not have (row locks that hold a "
        "statement inside the database, a V$SESSION that describes real sessions). Skips "
        "against the stand-in rather than passing trivially there."
    ),
]


def _repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def pytest_configure(config: pytest.Config) -> None:
    for marker in MARKERS:
        config.addinivalue_line("markers", marker)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip what the configured backend cannot honestly run.

    Against the stand-in, only the two-target checks are affected, and only when no
    second target is configured -- the stand-in invents one per service name, so it
    always has two.
    """

    on_oracle = oracle_config.is_configured()
    have_second = (not on_oracle) or oracle_config.has_second_target()

    stand_in = pytest.mark.skip(
        reason="Marked stand_in_only: it injects a fault into the stand-in or patches "
        "its class, so it cannot run against Oracle. The Oracle equivalent is in "
        "tests/qualification."
    )
    no_second = pytest.mark.skip(reason=oracle_config.NO_SECOND_TARGET_REASON)
    no_oracle = pytest.mark.skip(reason=f"Marked oracle_only. {oracle_config.SKIP_REASON}")

    for item in items:
        if on_oracle and item.get_closest_marker("stand_in_only"):
            item.add_marker(stand_in)
        if not on_oracle and item.get_closest_marker("oracle_only"):
            item.add_marker(no_oracle)
        if not have_second and item.get_closest_marker("needs_second_target"):
            item.add_marker(no_second)


@pytest.fixture(scope="session")
def oracle_target() -> oracle_config.OracleTestConfig | None:
    """The configured Oracle target, or None when running against the stand-in.

    Session scoped so a misconfiguration is reported once, before any test builds a
    workspace, rather than once per test.
    """

    return oracle_config.load() if oracle_config.is_configured() else None


@pytest.fixture(scope="session")
def _oracle_schema(oracle_target: oracle_config.OracleTestConfig | None) -> Iterator[None]:
    """Build the fixture schema once per run, on every configured database.

    Session scoped deliberately. The slow-query fixture is 400,000 rows; building it
    per test would make a run take hours and would say nothing extra. The suites that
    write to it clean up after themselves, exactly as they do against the stand-in.

    Does nothing when no Oracle target is configured: the stand-in seeds itself.
    """

    if oracle_target is None:
        yield
        return

    for endpoint in (oracle_target.primary, oracle_target.second):
        if endpoint is not None and endpoint.schema.upper() != oracle_config.DEMO_SCHEMA:
            # Raised once, here, rather than left to surface as several dozen
            # ORA-00942s across the suite.
            raise oracle_config.ConfigurationProblem(
                f"{endpoint.dsn} is configured with schema {endpoint.schema!r}. "
                f"{oracle_config.WRONG_SCHEMA_REASON}"
            )

    backend = create_backend(
        "oracledb",
        driver_mode=oracle_target.driver_mode,
        lib_dir=oracle_target.client_lib_dir or None,
    )
    specs = [oracle_target.connection_spec("api-suite-setup")]
    second = oracle_target.second_spec("api-suite-setup-second")
    if second is not None:
        specs.append(second)

    try:
        for spec in specs:
            connection = backend.connect(spec)
            try:
                apply_fixtures(connection)
                # The tuning suite looks for a cached cursor over the slow-query
                # fixture. EXPLAIN PLAN does not execute anything, so without this
                # there is nothing in v$sql to find and the check would fail for a
                # reason that has nothing to do with the harness.
                warm_cursor_cache(connection)
            finally:
                connection.close()
        yield
    finally:
        for spec in specs:
            connection = backend.connect(spec)
            try:
                drop_fixtures(connection)
            finally:
                connection.close()
        backend.shutdown()


@pytest.fixture
def restore_seeded_package(
    oracle_target: oracle_config.OracleTestConfig | None, _oracle_schema: None
) -> Iterator[None]:
    """Put EMPLOYEE_REPORT back to its seeded, invalid state after a test repairs it.

    The stand-in gives every test a fresh database, so a repair never outlived its
    test there. The Oracle schema is built once per run, and a repaired body left
    behind makes every later test that reads the seeded errors wrong - the first run
    against 19c read the previous test's errors this way.
    """

    yield
    if oracle_target is None:
        return
    backend = create_backend(
        "oracledb",
        driver_mode=oracle_target.driver_mode,
        lib_dir=oracle_target.client_lib_dir or None,
    )
    connection = backend.connect(oracle_target.connection_spec("api-suite-restore"))
    try:
        reapply_steps(connection, SEEDED_PACKAGE_STEPS)
    finally:
        connection.close()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "secrets").mkdir()
    (tmp_path / "fake").mkdir()
    return tmp_path


@pytest.fixture
def spare_endpoint(oracle_target: oracle_config.OracleTestConfig | None) -> dict[str, Any]:
    """Connection fields for registering one more target than the seed creates.

    Against the stand-in this is a service name it has not invented yet, so a fresh
    database appears. Against Oracle it is the primary target again, registered under
    a second profile -- which is all the registration workflow needs, and avoids
    demanding a third database to prove that a profile can be created.
    """

    if oracle_target is None:
        return {
            "host": "localhost",
            "port": 1521,
            "serviceName": "WORKFLOW1",
            "username": "harness_app",
            "defaultSchema": "HARNESS_APP",
        }
    return {
        "host": oracle_target.primary.host,
        "port": oracle_target.primary.port,
        "serviceName": oracle_target.primary.service_name,
        "username": oracle_target.primary.username,
        "defaultSchema": oracle_target.primary.schema,
    }


@pytest.fixture
def settings(workspace: Path, oracle_target: oracle_config.OracleTestConfig | None) -> Settings:
    common: dict[str, Any] = {
        "env": "development",
        "metadata_url": f"sqlite+pysqlite:///{(workspace / 'metadata.sqlite3').as_posix()}",
        "secret_dir": str(workspace / "secrets"),
        "auth_mode": "dev",
        "dev_token_secret": "test-secret",
        "copilot_enabled": True,
        "copilot_provider": "fake",
        "worksheet_idle_seconds": 300.0,
        "max_rows": 1000,
        "statement_timeout_seconds": 30.0,
    }
    if oracle_target is None:
        return Settings(
            oracle_backend="fake", oracle_fake_data_dir=str(workspace / "fake"), **common
        )
    return Settings(
        oracle_backend="oracledb",
        oracle_driver_mode=oracle_target.driver_mode,
        oracle_client_lib_dir=oracle_target.client_lib_dir,
        **common,
    )


def _endpoints(config: oracle_config.OracleTestConfig) -> dict[str, TargetEndpoint]:
    """Point the three demonstration targets at the configured database or databases.

    The second target is a genuinely separate database when one is configured. When
    it is not, it falls back to the primary so the rest of the suite still runs; the
    checks that would then be comparing a database with itself are marked
    ``needs_second_target`` and skip.

    Production reuses the primary. It is observation only -- no worksheets, no
    mutations -- so sharing a database with development costs nothing here.
    """

    primary = TargetEndpoint(
        host=config.primary.host,
        port=config.primary.port,
        service_name=config.primary.service_name,
        username=config.primary.username,
        default_schema=config.primary.schema,
        description="Oracle account used by the qualification targets.",
    )
    second = primary
    if config.second is not None:
        second = TargetEndpoint(
            host=config.second.host,
            port=config.second.port,
            service_name=config.second.service_name,
            username=config.second.username,
            default_schema=config.second.schema,
            secret_name="harness-app-second",  # noqa: S106 - a reference name
            secret_locator="harness_app_second.password",  # noqa: S106 - a file name
            description="Oracle account used by the second qualification target.",
        )
    return {"development": primary, "test": second, "production": primary}


@pytest.fixture
def seeded(
    settings: Settings,
    workspace: Path,
    oracle_target: oracle_config.OracleTestConfig | None,
    _oracle_schema: None,
) -> dict:
    if oracle_target is None:
        return seed(settings)

    # The seed resolves a credential reference to a file. Write the real passwords
    # before it writes its placeholders.
    secrets = workspace / "secrets"
    endpoints = _endpoints(oracle_target)
    passwords = {endpoints["development"].secret_locator: oracle_target.primary.password}
    if oracle_target.second is not None:
        passwords[endpoints["test"].secret_locator] = oracle_target.second.password
    for locator, password in passwords.items():
        (secrets / locator).write_text(password, encoding="utf-8")

    return seed(settings, endpoints=endpoints)


@pytest.fixture
def client(settings: Settings, seeded: dict) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def execution(settings: Settings, seeded: dict) -> Iterator[ExecutionService]:
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    service = ExecutionService(settings, factory)
    try:
        yield service
    finally:
        service.shutdown()


def token_for(client: TestClient, subject: str, roles: list[str]) -> str:
    response = client.post("/api/v1/auth/dev-token", json={"subject": subject, "roles": roles})
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def auth(client: TestClient, subject: str, roles: list[str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_for(client, subject, roles)}"}


@pytest.fixture
def developer(client: TestClient) -> dict[str, str]:
    return auth(client, "dev@example.internal", ["developer"])


@pytest.fixture
def second_developer(client: TestClient) -> dict[str, str]:
    """A second signed-in developer, used for the isolation checks."""

    admin = auth(client, "admin@example.internal", ["administrator"])
    client.post(
        "/api/v1/admin/users",
        headers=admin,
        json={
            "subject": "dev2@example.internal",
            "displayName": "Second Developer",
            "roles": ["developer"],
        },
    )
    targets = client.get(
        "/api/v1/targets", headers=auth(client, "dev@example.internal", ["developer"])
    ).json()
    development = next(t for t in targets if t["name"] == "development")
    client.post(
        "/api/v1/admin/grants",
        headers=admin,
        json={
            "subject": "dev2@example.internal",
            "profileId": development["id"],
            "permissions": ["read", "worksheet", "compile"],
        },
    )
    return auth(client, "dev2@example.internal", ["developer"])


@pytest.fixture
def dba(client: TestClient) -> dict[str, str]:
    return auth(client, "dba@example.internal", ["dba"])


@pytest.fixture
def viewer(client: TestClient) -> dict[str, str]:
    return auth(client, "viewer@example.internal", ["viewer"])


@pytest.fixture
def administrator(client: TestClient) -> dict[str, str]:
    return auth(client, "admin@example.internal", ["administrator"])


@pytest.fixture
def targets(client: TestClient, developer: dict[str, str], dba: dict[str, str]) -> dict:
    """Target ids by name, collected across the accounts that can see them."""

    found: dict[str, dict] = {}
    for headers in (developer, dba):
        for target in client.get("/api/v1/targets", headers=headers).json():
            found[target["name"]] = target
    return found


@pytest.fixture(autouse=True)
def _clean_fake_data(workspace: Path) -> Iterator[None]:
    """Discard the stand-in's databases after each test.

    Harmless when running against Oracle: the directory is simply empty, because the
    Oracle backend never writes to it. The fixture schema there is torn down once at
    the end of the session instead.
    """

    yield
    shutil.rmtree(workspace / "fake", ignore_errors=True)


def open_worksheet(client: TestClient, headers: dict, profile_id: str) -> str:
    response = client.post("/api/v1/worksheets", headers=headers, json={"profileId": profile_id})
    assert response.status_code == 201, response.text
    return response.json()["session"]["sessionId"]


def execute(client: TestClient, headers: dict, session_id: str, statement: str, **kwargs) -> dict:
    payload = {"statement": statement, **kwargs}
    return client.post(
        f"/api/v1/worksheets/{session_id}/execute", headers=headers, json=payload
    ).json()


def live_connection(client: TestClient, session_id: str) -> Any:
    """The stand-in connection behind a worksheet session.

    Reaching into the registry is deliberate: the failure modes that matter here --
    a commit whose answer never comes back, a statement that ignores a break -- can
    only be produced from the connection itself.
    """

    registry = client.app.state.harness.execution._registry  # type: ignore[attr-defined]
    return registry._sessions[session_id].connection


def audit_events(client: TestClient, operation_id: str) -> list[Any]:
    """Audit rows for one operation, newest first."""

    from harness_api.models import AuditEvent

    factory = client.app.state.harness.session_factory  # type: ignore[attr-defined]
    with factory() as db:
        from sqlalchemy import select

        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.operation_id == operation_id)
                .order_by(AuditEvent.created_at.desc())
            ).all()
        )
