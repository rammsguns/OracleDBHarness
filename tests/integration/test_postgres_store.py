"""The API against its pilot metadata store, across a restart.

Everything else in the suite runs the API on SQLite, which is honest about the application
logic and says nothing about the store the pilot actually deploys. Three things in
particular take a different code path on PostgreSQL and are only ever exercised here:

* ``timestamptz`` columns come back timezone-aware, where SQLite hands back naive
  datetimes. Reconciliation compares them.
* JSON columns are queried with PostgreSQL's own operators, and the outstanding-commit
  query filters on a key inside one.
* A transaction that spans threads meets real locking rather than a serialized writer.

The Oracle backend stays the local stand-in throughout. What is under test is the metadata
store; pulling a real Oracle in would make these impossible to run in CI without making
them a better check of PostgreSQL. See tests/postgres.py and docs/compatibility.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from harness_api.app import create_app
from harness_api.config import Settings
from harness_api.db import SCHEMA_VERSION, build_engine, build_session_factory
from harness_api.models import (
    AuditEvent,
    ConnectionProfile,
    Execution,
    SavedScript,
    User,
    UserTargetGrant,
    WorksheetSessionRecord,
    utcnow,
)
from harness_api.seed import seed
from harness_worker.types import ExecutionState
from tests import postgres
from tests.conftest import auth, open_worksheet
from tests.postgres import requires_postgres

DEAD_RUNTIME = "rt_a_process_that_died"

pytestmark = requires_postgres


@pytest.fixture
def pg_settings(tmp_path: Path) -> Iterator[Settings]:
    """Settings pointing at an empty PostgreSQL store, seeded with the demonstration data."""

    with postgres.empty_store():
        settings = postgres.settings_for(tmp_path)
        seed(settings)
        yield settings


def _factory(settings: Settings) -> sessionmaker[Session]:
    return build_session_factory(build_engine(settings))


def _developer(client: TestClient) -> dict[str, str]:
    return auth(client, "dev@example.internal", ["developer"])


def _development_target(client: TestClient, headers: dict[str, str]) -> str:
    targets = client.get("/api/v1/targets", headers=headers).json()
    return next(target["id"] for target in targets if target["name"] == "development")


def _a_seeded_user_and_target(settings: Settings) -> tuple[str, str]:
    """An existing user id and profile id to hang planted records on.

    ``executions.user_id`` and ``worksheet_sessions.user_id`` are foreign keys to
    ``users.id``. SQLite does not enforce them unless asked; PostgreSQL always does, so a
    fabricated row naming nobody is rejected here even though the same row is accepted by
    every other test in the suite. Planted rows have to look like the application's.
    """

    factory = _factory(settings)
    with factory() as db:
        user_id = db.scalars(select(User.id).order_by(User.subject)).first()
        profile_id = db.scalars(
            select(ConnectionProfile.id).order_by(ConnectionProfile.name)
        ).first()
    assert user_id and profile_id, "the seed created no user or no target"
    return user_id, profile_id


def test_the_store_survives_a_clean_restart(pg_settings: Settings) -> None:
    """Profiles, grants, scripts, history and audit are all still there afterwards.

    The persistence claim the pilot rests on, made against the store the pilot uses. A
    clean restart must also reconcile nothing: a startup that always reported work would
    make the startup that really found some impossible to notice.
    """

    first = create_app(pg_settings)
    with TestClient(first) as client:
        headers = _developer(client)
        profile_id = _development_target(client, headers)
        session = open_worksheet(client, headers, profile_id)
        read = client.post(
            f"/api/v1/worksheets/{session}/execute",
            headers=headers,
            json={"statement": "SELECT employee_id FROM employees WHERE employee_id = 100"},
        )
        assert read.status_code == 200, read.text
        write = client.post(
            f"/api/v1/worksheets/{session}/execute",
            headers=headers,
            json={"statement": "UPDATE employees SET salary = 4321 WHERE employee_id = 100"},
        )
        assert write.status_code == 200, write.text
        committed = client.post(f"/api/v1/worksheets/{session}/commit", headers=headers)
        assert committed.status_code == 200, committed.text
        saved = client.post(
            "/api/v1/scripts",
            headers=headers,
            json={"name": "headcount", "body": "SELECT COUNT(*) FROM employees"},
        )
        assert saved.status_code == 201, saved.text
        client.delete(f"/api/v1/worksheets/{session}", headers=headers)

    second = create_app(pg_settings)
    with TestClient(second) as client:
        headers = _developer(client)
        administrator = auth(client, "admin@example.internal", ["administrator"])

        assert _development_target(client, headers) == profile_id
        scripts = client.get("/api/v1/scripts", headers=headers).json()["scripts"]
        assert [script["name"] for script in scripts] == ["headcount"]
        history = client.get("/api/v1/executions", headers=headers).json()
        assert {row["state"] for row in history} == {"succeeded"}
        assert len(history) == 2, "both the read and the write should still be recorded"

        report = client.get("/api/v1/admin/reconciliation", headers=administrator).json()
        assert report["executionsResolved"] == 0
        assert report["sessionsClosed"] == []
        assert "nothing interrupted" in report["summary"]

        info = client.get("/api/v1/system/info", headers=headers).json()
        assert info["metadataSchemaVersion"] == SCHEMA_VERSION

    factory = _factory(pg_settings)
    with factory() as db:
        assert db.scalars(select(ConnectionProfile)).all()
        assert db.scalars(select(UserTargetGrant)).all()
        assert db.scalars(select(SavedScript)).all()
        # The commit is in the trail, which is where an operator looks after an incident.
        commits = db.scalars(
            select(AuditEvent).where(AuditEvent.operation_id == "worksheet.commit")
        ).all()
        assert [event.outcome for event in commits] == ["succeeded"]


def test_an_interrupted_write_is_reconciled_on_postgresql(pg_settings: Settings) -> None:
    """Reconciliation against the real store, including its aware timestamps.

    ``_as_utc`` exists because SQLite returns naive datetimes; PostgreSQL returns aware
    ones, and the comparison that decides whether a runtime was still heartbeating runs on
    both. A restart that raised here would leave the store unreconciled and the API
    refusing to start.
    """

    user_id, profile_id = _a_seeded_user_and_target(pg_settings)
    factory = _factory(pg_settings)
    with factory() as db:
        db.add(
            Execution(
                id="exe_interrupted_on_pg",
                user_id=user_id,
                profile_id=profile_id,
                operation_id="worksheet.execute",
                statement_kind="dml",
                risk_class="persistent_write",
                statement_fingerprint="a" * 64,
                state=ExecutionState.RUNNING.value,
                owner_id=DEAD_RUNTIME,
                dispatched_at=utcnow(),
            )
        )
        db.add(
            Execution(
                id="exe_never_sent_on_pg",
                user_id=user_id,
                profile_id=profile_id,
                operation_id="worksheet.execute",
                statement_kind="dml",
                risk_class="persistent_write",
                statement_fingerprint="b" * 64,
                state=ExecutionState.QUEUED.value,
                owner_id=DEAD_RUNTIME,
            )
        )
        db.commit()

    app = create_app(pg_settings)
    with TestClient(app) as client:
        administrator = auth(client, "admin@example.internal", ["administrator"])
        report = client.get("/api/v1/admin/reconciliation", headers=administrator).json()

    assert report["outcomeUnknown"] == ["exe_interrupted_on_pg"]
    assert report["cancelledBeforeDispatch"] == ["exe_never_sent_on_pg"]
    assert report["needsVerification"] == 1
    assert [item["id"] for item in report["outstanding"]] == ["exe_interrupted_on_pg"]

    with factory() as db:
        rows = {row.id: row for row in db.scalars(select(Execution)).all()}
        assert rows["exe_interrupted_on_pg"].state == ExecutionState.OUTCOME_UNKNOWN.value
        # A JSON column round trip on the pilot store, not just on SQLite.
        assert rows["exe_interrupted_on_pg"].verification_json["verificationRequired"] is True
        assert rows["exe_never_sent_on_pg"].state == ExecutionState.CANCELLED.value


def test_an_interrupted_commit_is_found_by_the_json_query_on_postgresql(
    pg_settings: Settings,
) -> None:
    """The outstanding-commit query filters on a key inside a JSON column.

    That compiles to PostgreSQL's ``->>`` and to SQLite's ``json_extract``, so the version
    that runs in the pilot is a different query from the one the rest of the suite covers.
    It decides whether a verified commit disappears from an operator's queue, so a
    difference here would either strand the entry forever or hide it before anyone looked.
    """

    user_id, profile_id = _a_seeded_user_and_target(pg_settings)
    factory = _factory(pg_settings)
    with factory() as db:
        db.add(
            WorksheetSessionRecord(
                id="ws_commit_on_pg",
                user_id=user_id,
                profile_id=profile_id,
                owner_id=DEAD_RUNTIME,
                commit_requested_at=utcnow(),
            )
        )
        db.commit()

    app = create_app(pg_settings)
    with TestClient(app) as client:
        administrator = auth(client, "admin@example.internal", ["administrator"])
        before = client.get("/api/v1/admin/reconciliation", headers=administrator).json()
        assert [item["sessionId"] for item in before["outstandingCommits"]] == ["ws_commit_on_pg"]
        assert before["needsVerification"] == 1

        recorded = client.post(
            "/api/v1/admin/worksheets/ws_commit_on_pg/commit-verification",
            headers=administrator,
            json={"finding": "not_applied", "note": "The rows are unchanged."},
        )
        assert recorded.status_code == 200, recorded.text

        after = client.get("/api/v1/admin/reconciliation", headers=administrator).json()

    assert after["outstandingCommits"] == [], "the JSON filter did not match on PostgreSQL"


def test_an_idempotency_key_is_the_actors_own_on_postgresql(pg_settings: Settings) -> None:
    """Two users may choose the same key; one user may not reuse it for another statement.

    The constraint this rests on is the one the version 1 to 2 migration installs, and it
    is a PostgreSQL table constraint. Driving it through the API rather than by inserting
    rows checks the behaviour an IDE adapter actually depends on.
    """

    app = create_app(pg_settings)
    with TestClient(app) as client:
        first = _developer(client)
        profile_id = _development_target(client, first)
        administrator = auth(client, "admin@example.internal", ["administrator"])
        client.post(
            "/api/v1/admin/users",
            headers=administrator,
            json={"subject": "dev2@example.internal", "roles": ["developer"]},
        )
        client.post(
            "/api/v1/admin/grants",
            headers=administrator,
            json={
                "subject": "dev2@example.internal",
                "profileId": profile_id,
                "permissions": ["read", "worksheet"],
            },
        )
        second = auth(client, "dev2@example.internal", ["developer"])

        statement = "UPDATE employees SET salary = 8888 WHERE employee_id = 100"
        one = open_worksheet(client, first, profile_id)
        two = open_worksheet(client, second, profile_id)

        mine = client.post(
            f"/api/v1/worksheets/{one}/execute",
            headers=first,
            json={"statement": statement, "idempotencyKey": "nightly-refresh"},
        )
        assert mine.status_code == 200, mine.text

        # The same key, a different actor: their own request, not a replay of the first.
        theirs = client.post(
            f"/api/v1/worksheets/{two}/execute",
            headers=second,
            json={"statement": statement, "idempotencyKey": "nightly-refresh"},
        )
        assert theirs.status_code == 200, theirs.text
        assert theirs.json()["outcome"]["executionId"] != mine.json()["outcome"]["executionId"], (
            "one actor's idempotency key deduplicated another actor's statement"
        )

        # The same key, same actor, a different statement: refused rather than deduplicated.
        reused = client.post(
            f"/api/v1/worksheets/{one}/execute",
            headers=first,
            json={
                "statement": "UPDATE employees SET salary = 7777 WHERE employee_id = 100",
                "idempotencyKey": "nightly-refresh",
            },
        )
        assert reused.status_code == 400, reused.text
        assert reused.json()["error"]["code"] == "invalid_request"
