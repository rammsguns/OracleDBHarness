"""Unit tests verifying AuditEvent generation for all administrative changes (A7 C-2)."""

import json

from sqlalchemy import select

from harness_api.models import AuditEvent, UserTargetGrant


def _get_audit_events_after(client, after_id: str | None = None) -> list[AuditEvent]:
    with client.app.state.harness.session_factory() as db:
        query = select(AuditEvent).order_by(AuditEvent.created_at.asc())
        all_events = list(db.scalars(query))
        if after_id is None:
            return all_events
        idx = -1
        for i, ev in enumerate(all_events):
            if ev.id == after_id:
                idx = i
                break
        return all_events[idx + 1 :]


def _latest_audit_id(client) -> str | None:
    with client.app.state.harness.session_factory() as db:
        latest = db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc())).first()
        return latest.id if latest else None


def _serialize_audit_event(ev: AuditEvent) -> str:
    data = {
        "id": ev.id,
        "actor_id": ev.actor_id,
        "actor_subject": ev.actor_subject,
        "integration_id": ev.integration_id,
        "profile_id": ev.profile_id,
        "operation_id": ev.operation_id,
        "execution_id": ev.execution_id,
        "risk_class": ev.risk_class,
        "policy_decision": ev.policy_decision,
        "outcome": ev.outcome,
        "statement_fingerprint": ev.statement_fingerprint,
        "affected_counts": ev.affected_counts,
        "detail": ev.detail,
    }
    return json.dumps(data)


def test_create_secret_writes_audit_event(client, administrator):
    last_id = _latest_audit_id(client)
    secret_name = "test-audit-secret-ref"
    secret_locator = "SUPER_SECRET_ENV_VAR_NAME"
    resp = client.post(
        "/api/v1/admin/secrets",
        headers=administrator,
        json={
            "name": secret_name,
            "provider": "env",
            "locator": secret_locator,
            "description": "A secret reference for testing audit",
        },
    )
    assert resp.status_code == 201

    events = _get_audit_events_after(client, last_id)
    assert len(events) == 1
    ev = events[0]
    assert ev.operation_id == "admin.secret.create"
    assert ev.actor_subject == "admin@example.internal"
    assert ev.risk_class == "administrative"
    assert ev.policy_decision == "allowed"
    assert ev.outcome == "succeeded"
    assert ev.detail["name"] == secret_name
    assert ev.detail["objectId"] == resp.json()["id"]

    # Verify no secret locator or values leaked into audit event
    serialized = _serialize_audit_event(ev)
    assert secret_locator not in serialized


def test_create_and_update_user_writes_audit_events(client, administrator):
    last_id = _latest_audit_id(client)
    user_subject = "audit-new-user@example.internal"

    # 1. Create user
    resp = client.post(
        "/api/v1/admin/users",
        headers=administrator,
        json={
            "subject": user_subject,
            "displayName": "Audit Test User",
            "email": "audit@example.internal",
            "roles": ["viewer"],
        },
    )
    assert resp.status_code == 201
    user_id = resp.json()["id"]

    events = _get_audit_events_after(client, last_id)
    assert len(events) == 1
    ev = events[0]
    assert ev.operation_id == "admin.user.create"
    assert ev.actor_subject == "admin@example.internal"
    assert ev.risk_class == "administrative"
    assert ev.detail["objectId"] == user_id
    assert ev.detail["subject"] == user_subject
    assert ev.detail["roles"] == ["viewer"]

    # 2. Update user
    last_id = ev.id
    resp_update = client.post(
        "/api/v1/admin/users",
        headers=administrator,
        json={
            "subject": user_subject,
            "displayName": "Audit Test User Promoted",
            "roles": ["viewer", "developer"],
        },
    )
    assert resp_update.status_code == 201

    events = _get_audit_events_after(client, last_id)
    assert len(events) == 1
    ev_update = events[0]
    assert ev_update.operation_id == "admin.user.update"
    assert ev_update.actor_subject == "admin@example.internal"
    assert ev_update.risk_class == "administrative"
    assert ev_update.detail["objectId"] == user_id
    assert ev_update.detail["subject"] == user_subject
    assert ev_update.detail["roles"] == ["viewer", "developer"]


def test_create_target_writes_audit_event(client, administrator):
    last_id = _latest_audit_id(client)
    target_name = "audit-test-target"

    resp = client.post(
        "/api/v1/admin/targets",
        headers=administrator,
        json={
            "name": target_name,
            "environment": "development",
            "host": "localhost",
            "port": 1521,
            "serviceName": "FREEPDB1",
            "username": "audit_user",
            "secretReference": "harness-app",
            "worksheetsEnabled": True,
            "notes": "Target for audit test",
        },
    )
    assert resp.status_code == 201
    profile_id = resp.json()["id"]

    events = _get_audit_events_after(client, last_id)
    assert len(events) == 1
    ev = events[0]
    assert ev.operation_id == "admin.target.create"
    assert ev.actor_subject == "admin@example.internal"
    assert ev.profile_id == profile_id
    assert ev.risk_class == "administrative"
    assert ev.detail["objectId"] == profile_id
    assert ev.detail["name"] == target_name
    assert ev.detail["environment"] == "development"
    assert ev.detail["worksheetsEnabled"] is True


def test_create_update_and_revoke_grant_writes_audit_events(client, administrator, targets):
    last_id = _latest_audit_id(client)
    profile_id = targets["development"]["id"]
    subject = "dev@example.internal"

    # Make sure any existing grant is deleted first for clean create test
    with client.app.state.harness.session_factory() as db:
        existing = db.scalars(
            select(UserTargetGrant).where(UserTargetGrant.profile_id == profile_id)
        ).all()
        for g in existing:
            db.delete(g)
        db.commit()

    # 1. Create grant
    last_id = _latest_audit_id(client)
    resp = client.post(
        "/api/v1/admin/grants",
        headers=administrator,
        json={
            "subject": subject,
            "profileId": profile_id,
            "permissions": ["read"],
        },
    )
    assert resp.status_code == 201
    grant_id = resp.json()["id"]

    events = _get_audit_events_after(client, last_id)
    assert len(events) == 1
    ev = events[0]
    assert ev.operation_id == "admin.grant.create"
    assert ev.actor_subject == "admin@example.internal"
    assert ev.profile_id == profile_id
    assert ev.risk_class == "administrative"
    assert ev.detail["objectId"] == grant_id
    assert ev.detail["permissions"] == ["read"]

    # 2. Update grant
    last_id = ev.id
    resp_update = client.post(
        "/api/v1/admin/grants",
        headers=administrator,
        json={
            "subject": subject,
            "profileId": profile_id,
            "permissions": ["read", "worksheet"],
        },
    )
    assert resp_update.status_code == 201

    events = _get_audit_events_after(client, last_id)
    assert len(events) == 1
    ev_update = events[0]
    assert ev_update.operation_id == "admin.grant.update"
    assert ev_update.actor_subject == "admin@example.internal"
    assert ev_update.profile_id == profile_id
    assert ev_update.detail["objectId"] == grant_id
    assert ev_update.detail["permissions"] == ["read", "worksheet"]

    # 3. Revoke grant
    last_id = ev_update.id
    resp_revoke = client.delete(
        f"/api/v1/admin/grants/{grant_id}",
        headers=administrator,
    )
    assert resp_revoke.status_code == 200

    events = _get_audit_events_after(client, last_id)
    assert len(events) == 1
    ev_revoke = events[0]
    assert ev_revoke.operation_id == "admin.grant.revoke"
    assert ev_revoke.actor_subject == "admin@example.internal"
    assert ev_revoke.profile_id == profile_id
    assert ev_revoke.detail["objectId"] == grant_id
    assert ev_revoke.detail["grantId"] == grant_id


def test_create_and_revoke_integration_writes_audit_events_no_token(client, administrator):
    last_id = _latest_audit_id(client)
    integration_name = "audit-test-dataforge-adapter"

    # 1. Create integration
    resp = client.post(
        "/api/v1/admin/integrations",
        headers=administrator,
        json={
            "name": integration_name,
            "kind": "dataforge",
            "scopes": ["copilot:assist"],
        },
    )
    assert resp.status_code == 201
    created = resp.json()
    integration_id = created["id"]
    token = created["token"]
    assert token, "An issued token must be returned in the response"

    events = _get_audit_events_after(client, last_id)
    assert len(events) == 1
    ev = events[0]
    assert ev.operation_id == "admin.integration.create"
    assert ev.actor_subject == "admin@example.internal"
    assert ev.risk_class == "administrative"
    assert ev.detail["objectId"] == integration_id
    assert ev.detail["name"] == integration_name
    assert ev.detail["scopes"] == ["copilot:assist"]

    # CRITICAL SECURITY CHECK: The token MUST NOT appear anywhere in the audit event
    serialized = _serialize_audit_event(ev)
    assert token not in serialized
    assert "token" not in ev.detail

    # 2. Revoke integration
    last_id = ev.id
    resp_revoke = client.post(
        f"/api/v1/admin/integrations/{integration_id}/revoke",
        headers=administrator,
    )
    assert resp_revoke.status_code == 200

    events = _get_audit_events_after(client, last_id)
    assert len(events) == 1
    ev_revoke = events[0]
    assert ev_revoke.operation_id == "admin.integration.revoke"
    assert ev_revoke.actor_subject == "admin@example.internal"
    assert ev_revoke.risk_class == "administrative"
    assert ev_revoke.detail["objectId"] == integration_id
    assert ev_revoke.detail["integrationId"] == integration_id
    assert ev_revoke.detail["name"] == integration_name
