"""A worksheet session whose record cannot be saved must not keep its Oracle connection.

Opening a worksheet leases a connection first and writes the session record after. The
capacity rehearsal found what happened when that write failed - there, SQLite's metadata
store reporting "database is locked" under saturation; in a pilot, any PostgreSQL error at
that moment. The caller got a 500 and no session id, while the leased connection stayed
open in the registry: a live database session nobody could reach, counted against the
target until the idle reaper found it, and listed to the user as a session they never
opened.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from harness_api.config import Settings
from harness_api.db import build_engine, build_session_factory
from harness_api.execution import ExecutionService
from harness_api.models import ConnectionProfile, User, UserTargetGrant, WorksheetSessionRecord
from harness_api.security import Principal


class _LockedStore(Session):
    """A metadata session whose commit fails the way a locked or unreachable store does."""

    def commit(self) -> None:
        raise OperationalError(
            "INSERT INTO worksheet_sessions", {}, Exception("database is locked")
        )


def _who(factory: Any) -> tuple[Principal, ConnectionProfile, UserTargetGrant]:
    with factory() as db:
        user = db.scalars(select(User).where(User.subject == "dev@example.internal")).one()
        profile = db.scalars(
            select(ConnectionProfile).where(ConnectionProfile.name == "development")
        ).one()
        grant = db.scalars(
            select(UserTargetGrant).where(
                UserTargetGrant.user_id == user.id, UserTargetGrant.profile_id == profile.id
            )
        ).one()
        # Loaded now, so the objects are usable after this session closes.
        _ = list(profile.capabilities), grant.permissions
        db.expunge_all()
    principal = Principal(subject=user.subject, roles=user.role_set(), user_id=user.id)
    return principal, profile, grant


def test_a_session_whose_record_cannot_be_saved_gives_its_connection_back(
    settings: Settings, seeded: dict, execution: ExecutionService
) -> None:
    factory = build_session_factory(build_engine(settings))
    principal, profile, grant = _who(factory)
    opened: list[Any] = []
    registry_open = execution._registry.open  # noqa: SLF001 - the lease under test

    def recording_open(**kwargs: Any) -> Any:
        session = registry_open(**kwargs)
        opened.append(session)
        return session

    execution._registry.open = recording_open  # type: ignore[method-assign]  # noqa: SLF001

    locked = _LockedStore(bind=build_engine(settings))
    with pytest.raises(OperationalError):
        execution.open_worksheet(locked, principal, profile, grant)
    locked.close()

    assert len(opened) == 1, "the test did not reach the point where the lease is taken"
    assert execution.list_worksheets(principal) == [], (
        "the session stayed in the registry although the caller was never given its id"
    )
    assert opened[0].closed, "the leased connection was not closed"
    with factory() as db:
        assert db.get(WorksheetSessionRecord, opened[0].session_id) is None
