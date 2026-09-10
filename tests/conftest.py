"""Shared fixtures.

Every test runs against the local stand-in backend. That exercises the real
execution, session, limit, transaction and policy code, but it is not evidence of
Oracle compatibility: the integration suite has to be re-run against Oracle 19c
before any release claim. See docs/compatibility.md.
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
from harness_api.seed import seed


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "secrets").mkdir()
    (tmp_path / "fake").mkdir()
    return tmp_path


@pytest.fixture
def settings(workspace: Path) -> Settings:
    return Settings(
        env="development",
        metadata_url=f"sqlite+pysqlite:///{(workspace / 'metadata.sqlite3').as_posix()}",
        oracle_backend="fake",
        oracle_fake_data_dir=str(workspace / "fake"),
        secret_dir=str(workspace / "secrets"),
        auth_mode="dev",
        dev_token_secret="test-secret",
        copilot_enabled=True,
        copilot_provider="fake",
        worksheet_idle_seconds=300.0,
        max_rows=1000,
        statement_timeout_seconds=30.0,
    )


@pytest.fixture
def seeded(settings: Settings) -> dict:
    return seed(settings)


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
