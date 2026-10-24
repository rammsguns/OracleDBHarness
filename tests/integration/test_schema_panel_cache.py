"""The console alone reuses schema results, after current authorization."""

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from harness_api.models import Execution
from harness_worker.errors import CapabilityError
from tests.conftest import audit_events, auth

pytestmark = pytest.mark.stand_in_only


def panel(client, developer, targets):
    target = targets["development"]["id"]
    return client.get(f"/api/v1/targets/{target}/schemas", headers=developer)


def test_hit_audits_original_execution(client, developer, targets):
    service = client.app.state.harness.execution
    with patch.object(service, "run_catalog_operation", wraps=service.run_catalog_operation) as run:
        first = panel(client, developer, targets).json()
        second = panel(client, developer, targets).json()
    assert first["available"] and first["cached"] is False
    assert second == {**first, "cached": True}
    assert run.call_count == 1
    events = audit_events(client, "schema.list_schemas")
    assert len(events) == 2
    hit = next(event for event in events if event.detail.get("cached"))
    assert hit.actor_subject == "dev@example.internal"
    assert hit.detail["message"] == "served from cache"
    assert hit.execution_id == hit.detail["sourceExecutionId"]
    with client.app.state.harness.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Execution)) == 1


@pytest.mark.parametrize("ttl", [0, 60])
def test_disabled_or_expired_cache(client, developer, targets, monkeypatch, ttl):
    state = client.app.state.harness
    state.settings.schema_panel_cache_seconds = ttl
    clock = [100.0]
    monkeypatch.setattr("harness_api.schema_panel_cache.monotonic", lambda: clock[0])
    with patch.object(
        state.execution, "run_catalog_operation", wraps=state.execution.run_catalog_operation
    ) as run:
        assert panel(client, developer, targets).json()["available"]
        clock[0] += 61
        assert panel(client, developer, targets).json()["cached"] is False
        assert run.call_count == 2


def test_refusals_never_read_cache(client, developer, targets, administrator):
    state = client.app.state.harness
    assert panel(client, developer, targets).json()["available"]
    ungranted = auth(client, "ungranted@example.internal", ["viewer"])
    with patch.object(state.schema_panel_cache, "get", side_effect=AssertionError("cache read")):
        # An authenticated user has no implicit target grant.
        target = targets["development"]["id"]
        assert client.get(f"/api/v1/targets/{target}/schemas", headers=ungranted).status_code == 401
        response = client.post(
            "/api/v1/admin/grants",
            headers=administrator,
            json={"subject": "dev@example.internal", "profileId": target, "permissions": []},
        )
        assert response.status_code == 201
        refused = panel(client, developer, targets)
        assert refused.status_code == 200 and not refused.json()["available"]


def test_errors_and_truncation_not_cached(client, developer, targets):
    state = client.app.state.harness
    with patch.object(
        state.execution, "run_catalog_operation", side_effect=CapabilityError("unavailable")
    ) as run:
        assert not panel(client, developer, targets).json()["available"]
        assert not panel(client, developer, targets).json()["available"]
        assert run.call_count == 2
    original = state.execution.run_catalog_operation

    def truncated(*args, **kwargs):
        result = original(*args, **kwargs)
        result.outcome.result_set.truncated = True
        return result

    with patch.object(state.execution, "run_catalog_operation", side_effect=truncated) as run:
        assert panel(client, developer, targets).json()["truncated"]
        assert panel(client, developer, targets).json()["truncated"]
        assert run.call_count == 2


def test_grant_change_invalidates(client, developer, targets, administrator):
    state = client.app.state.harness
    target = targets["development"]["id"]
    assert panel(client, developer, targets).json()["cached"] is False
    payload = {"subject": "dev@example.internal", "profileId": target, "permissions": ["read"]}
    grant = client.post("/api/v1/admin/grants", headers=administrator, json=payload)
    assert grant.status_code == 201
    assert panel(client, developer, targets).json()["cached"] is False
    assert panel(client, developer, targets).json()["cached"] is True
    assert (
        client.delete(
            f"/api/v1/admin/grants/{grant.json()['id']}", headers=administrator
        ).status_code
        == 200
    )
    assert not state.schema_panel_cache._entries


def test_profile_version_and_direct_execution_are_uncached(client, developer, targets):
    from harness_api.execution import load_grant, load_profile
    from harness_api.models import ConnectionProfile
    from harness_api.security import Principal

    state = client.app.state.harness
    target = targets["development"]["id"]
    assert panel(client, developer, targets).json()["cached"] is False
    with state.session_factory() as db:
        profile = db.get(ConnectionProfile, target)
        profile.default_schema = "CHANGED"
        db.commit()
    assert panel(client, developer, targets).json()["cached"] is False
    # This is the same service entry point used by copilot, without the router.
    with state.session_factory() as db:
        from harness_api.models import AppRole, User

        user = db.scalars(select(User).where(User.subject == "dev@example.internal")).one()
        principal = Principal(
            subject=user.subject, user_id=user.id, roles=frozenset({AppRole.DEVELOPER})
        )
        profile = load_profile(db, target)
        grant = load_grant(db, principal, target)
        with patch.object(
            state.schema_panel_cache, "get", side_effect=AssertionError("cache read")
        ):
            first = state.execution.run_catalog_operation(
                db, principal, profile, grant, "schema.list_schemas"
            )
            second = state.execution.run_catalog_operation(
                db, principal, profile, grant, "schema.list_schemas"
            )
        assert first.outcome.execution_id != second.outcome.execution_id


def test_copilot_toolbox_remains_uncached(client, developer, targets):
    from harness_api.copilot.provider import ToolCall
    from harness_api.copilot.toolbox import KiwiToolbox
    from harness_api.models import AppRole, User
    from harness_api.security import Principal

    state = client.app.state.harness
    target = targets["development"]["id"]
    assert panel(client, developer, targets).json()["available"]
    toolbox = KiwiToolbox(state.execution, state.settings)
    with state.session_factory() as db:
        user = db.scalars(select(User).where(User.subject == "dev@example.internal")).one()
        principal = Principal(subject=user.subject, user_id=user.id, roles={AppRole.DEVELOPER})
        with patch.object(
            state.schema_panel_cache, "get", side_effect=AssertionError("cache read")
        ):
            first = toolbox.run(db, principal, target, ToolCall("one", "schema__list_schemas", {}))
            second = toolbox.run(db, principal, target, ToolCall("two", "schema__list_schemas", {}))
        assert first.status == second.status == "succeeded"
        assert first.execution_id != second.execution_id


def test_credential_scope_is_part_of_key(client, developer, targets):
    from harness_api.models import SecretReference, User, UserTargetGrant

    state = client.app.state.harness
    assert panel(client, developer, targets).json()["cached"] is False
    with state.session_factory() as db:
        user = db.scalars(select(User).where(User.subject == "dev@example.internal")).one()
        grant = db.scalars(
            select(UserTargetGrant).where(
                UserTargetGrant.user_id == user.id,
                UserTargetGrant.profile_id == targets["development"]["id"],
            )
        ).one()
        # A second reference represents a different granted credential scope.
        original = db.get(SecretReference, grant.profile.secret_reference_id)
        other = SecretReference(
            name="other-scope", provider=original.provider, locator=original.locator
        )
        db.add(other)
        db.flush()
        grant.secret_reference_id = other.id
        db.commit()
    assert panel(client, developer, targets).json()["cached"] is False
    assert panel(client, developer, targets).json()["cached"] is True


def test_failed_outcome_not_cached(client, developer, targets):
    from harness_worker.types import ExecutionState

    state = client.app.state.harness
    original = state.execution.run_catalog_operation

    def failed(*args, **kwargs):
        result = original(*args, **kwargs)
        result.outcome.state = ExecutionState.FAILED
        result.outcome.error = {"code": "TEST", "message": "failure"}
        return result

    with patch.object(state.execution, "run_catalog_operation", side_effect=failed) as run:
        assert not panel(client, developer, targets).json()["available"]
        assert not panel(client, developer, targets).json()["available"]
        assert run.call_count == 2
