"""Targets, capability discovery and access control.

The rule under test throughout: a direct API call is subject to exactly the same
checks as the console, and a missing privilege degrades one feature with an
explanation rather than producing an empty panel that looks healthy.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from harness_api.models import ConnectionProfile, TargetCapability
from tests.conftest import execute, open_worksheet


def test_two_targets_do_not_mix_identity_or_state(client: TestClient, developer, targets) -> None:
    development = targets["development"]["id"]
    test_target = targets["test"]["id"]

    dev_identity = client.post(f"/api/v1/targets/{development}/test", headers=developer).json()
    test_identity = client.post(f"/api/v1/targets/{test_target}/test", headers=developer).json()
    assert dev_identity["identity"]["databaseName"] != test_identity["identity"]["databaseName"]

    dev_session = open_worksheet(client, developer, development)
    test_session = open_worksheet(client, developer, test_target)

    execute(
        client,
        developer,
        dev_session,
        "UPDATE employees SET salary = 4242 WHERE employee_id = 105",
    )
    client.post(f"/api/v1/worksheets/{dev_session}/commit", headers=developer)

    other = execute(
        client,
        developer,
        test_session,
        "SELECT salary FROM employees WHERE employee_id = 105",
    )
    assert other["outcome"]["resultSet"]["rows"] != [[4242]]

    same = execute(
        client,
        developer,
        dev_session,
        "SELECT salary FROM employees WHERE employee_id = 105",
    )
    assert same["outcome"]["resultSet"]["rows"] == [[4242]]


def test_capabilities_are_probed_not_assumed(client: TestClient, developer, targets) -> None:
    body = client.post(
        f"/api/v1/targets/{targets['development']['id']}/test", headers=developer
    ).json()
    assert body["connected"] is True
    names = {c["capability"] for c in body["capabilities"]}
    assert {"connect", "v_session", "explain_plan", "dbms_stats"} <= names
    for capability in body["capabilities"]:
        assert capability["checkedAt"], capability


def test_a_missing_capability_disables_one_panel_with_a_reason(
    client: TestClient, developer, dba, targets, settings
) -> None:
    """Revoke a capability and confirm the panel says why, and stays empty-free."""

    state = client.app.state.harness
    with state.session_factory() as db:
        profile = db.get(ConnectionProfile, targets["development"]["id"])
        row = next(c for c in profile.capabilities if c.capability == "v_session")
        row.available = False
        row.detail = "ORA-00942: table or view does not exist (V_$SESSION)"
        db.commit()

    body = client.get(
        f"/api/v1/targets/{targets['development']['id']}/dba/overview", headers=dba
    ).json()
    sessions_panel = body["panels"]["sessions"]
    assert sessions_panel["available"] is False
    assert sessions_panel["rows"] == []
    assert sessions_panel["error"]["code"] == "capability_unavailable"
    assert "v_session" in str(sessions_panel["error"]["detail"]["missingCapabilities"])
    assert "sessions" in body["unavailablePanels"]
    # Other panels still work: one missing privilege degrades one feature.
    assert body["panels"]["tablespaces"]["available"] is True


def test_a_user_without_a_grant_cannot_touch_the_target(
    client: TestClient, viewer, targets
) -> None:
    development = targets["development"]["id"]
    for method, path in (
        ("get", f"/api/v1/targets/{development}"),
        ("post", f"/api/v1/targets/{development}/test"),
        ("get", f"/api/v1/targets/{development}/schemas"),
        ("get", f"/api/v1/targets/{development}/dba/overview"),
    ):
        response = getattr(client, method)(path, headers=viewer)
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "not_authorized"

    response = client.post("/api/v1/worksheets", headers=viewer, json={"profileId": development})
    assert response.status_code == 403


def test_production_is_observation_only(client: TestClient, dba, administrator, targets) -> None:
    production = targets["production"]["id"]

    overview = client.get(f"/api/v1/targets/{production}/dba/overview", headers=dba)
    assert overview.status_code == 200

    # Three independent layers refuse a worksheet here: the grant does not carry the
    # permission, the target has worksheets disabled, and the environment is
    # production. The first one reached is the one reported.
    response = client.post("/api/v1/worksheets", headers=dba, json={"profileId": production})
    assert response.status_code == 403
    assert response.json()["error"]["code"] in ("policy_refused", "not_authorized")

    # Even with the runbook permission explicitly granted, the environment refuses it.
    granted = client.post(
        "/api/v1/admin/grants",
        headers=administrator,
        json={
            "subject": "dba@example.internal",
            "profileId": production,
            "permissions": ["read", "runbook"],
        },
    )
    assert granted.status_code == 201, granted.text

    response = client.post(
        "/api/v1/runbooks/runbook.recompile_object/run",
        headers=dba,
        json={
            "profileId": production,
            "parameters": {
                "owner": "HARNESS_APP",
                "object_name": "EMPLOYEE_REPORT",
                "object_kind": "PACKAGE BODY",
            },
            "confirm": True,
        },
    )
    assert response.status_code == 403
    assert "production target" in response.json()["error"]["message"]


def test_a_worksheet_permission_cannot_be_granted_on_a_production_target(
    client: TestClient, administrator, targets
) -> None:
    response = client.post(
        "/api/v1/admin/grants",
        headers=administrator,
        json={
            "subject": "dba@example.internal",
            "profileId": targets["production"]["id"],
            "permissions": ["read", "worksheet"],
        },
    )
    assert response.status_code == 403
    assert "Worksheets are not enabled" in response.json()["error"]["message"]


def test_unauthenticated_and_unregistered_callers_are_refused(client: TestClient) -> None:
    assert client.get("/api/v1/targets").status_code == 401
    assert client.get("/api/v1/targets", headers={"Authorization": "Basic abc"}).status_code == 401

    token = client.post(
        "/api/v1/auth/dev-token",
        json={"subject": "stranger@example.internal", "roles": ["administrator"]},
    ).json()["accessToken"]
    response = client.get("/api/v1/targets", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "not registered" in response.json()["error"]["message"]


def test_a_forged_role_claim_does_not_grant_anything(client: TestClient) -> None:
    """Roles come from the harness record, not from the token."""

    token = client.post(
        "/api/v1/auth/dev-token",
        json={"subject": "viewer@example.internal", "roles": ["administrator"]},
    ).json()["accessToken"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/me", headers=headers).json()["roles"] == ["viewer"]
    assert client.get("/api/v1/audit", headers=headers).status_code == 403


def test_administrators_do_not_inherit_target_access(
    client: TestClient, administrator, targets
) -> None:
    response = client.get(f"/api/v1/targets/{targets['test']['id']}", headers=administrator)
    assert response.status_code == 403


def test_endpoint_registration_is_checked_against_the_allowlist(
    client: TestClient, administrator
) -> None:
    client.app.state.harness.settings.allowed_endpoints = "db.internal:1521"
    try:
        response = client.post(
            "/api/v1/admin/targets",
            headers=administrator,
            json={
                "name": "rogue",
                "host": "attacker.example.com",
                "port": 1521,
                "serviceName": "X",
                "username": "u",
                "secretReference": "harness-app",
            },
        )
        assert response.status_code == 403
        assert "HARNESS_ALLOWED_ENDPOINTS" in response.json()["error"]["message"]
    finally:
        client.app.state.harness.settings.allowed_endpoints = ""


def test_secret_values_never_appear_in_the_api(
    client: TestClient, administrator, developer, targets
) -> None:
    secrets = client.get("/api/v1/admin/secrets", headers=administrator).json()["secrets"]
    assert secrets
    for secret in secrets:
        assert "not-a-real-password" not in str(secret)
        assert set(secret) == {"id", "name", "provider", "locator", "resolvable", "detail"}

    target = client.get(f"/api/v1/targets/{targets['development']['id']}", headers=developer)
    assert "password" not in target.text.lower()


def test_capability_rows_are_stored_per_target(client: TestClient, developer, targets) -> None:
    state = client.app.state.harness
    client.post(f"/api/v1/targets/{targets['development']['id']}/test", headers=developer)
    with state.session_factory() as db:
        rows = db.query(TargetCapability).all()
        by_profile: dict[str, int] = {}
        for row in rows:
            by_profile[row.profile_id] = by_profile.get(row.profile_id, 0) + 1
        assert len(by_profile) >= 2
        assert all(count == len(set(r.capability for r in rows)) for count in by_profile.values())
