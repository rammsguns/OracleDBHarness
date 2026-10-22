"""Direct API refusals against disposable production stand-ins only."""

from unittest.mock import Mock

import pytest
from sqlalchemy import select

from harness_api.config import Settings
from harness_api.models import (
    AuditEvent,
    ConnectionProfile,
    CopilotBudget,
    CopilotRequest,
    CopilotToolCall,
    Execution,
    UserTargetGrant,
)
from harness_api.policy import PolicyEngine
from harness_worker.errors import PolicyError
from harness_worker.types import RiskClass
from tests.conftest import live_connection, open_worksheet
from tests.copilot.test_copilot import base_payload, sse


@pytest.fixture(scope="session")
def oracle_target():
    return None


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"oracle_backend": "fake", "kiwi_enabled": True})


@pytest.fixture
def production(client, targets):
    """Give the DBA every permission so refusals prove the environment boundary."""
    profile_id = targets["production"]["id"]
    with client.app.state.harness.session_factory() as db:
        for grant in db.scalars(
            select(UserTargetGrant).where(UserTargetGrant.profile_id == profile_id)
        ):
            grant.permissions = ["read", "worksheet", "compile", "runbook"]
        db.commit()
    return profile_id


def assert_no_execution(client):
    with client.app.state.harness.session_factory() as db:
        assert list(db.scalars(select(Execution))) == []
        assert all(row.execution_id is None for row in db.scalars(select(AuditEvent)))
        assert all(
            row.outcome != "succeeded" or row.operation_id == "worksheet.open"
            for row in db.scalars(select(AuditEvent))
        )


@pytest.mark.parametrize("drift", [False, True])
def test_production_worksheet_open_is_refused(client, dba, production, monkeypatch, drift):
    with client.app.state.harness.session_factory() as db:
        db.get(ConnectionProfile, production).worksheets_enabled = drift
        db.commit()
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(client.app.state.harness.execution._backend, "connect", connect)
    response = client.post("/api/v1/worksheets", headers=dba, json={"profileId": production})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "policy_refused"
    connect.assert_not_called()
    assert_no_execution(client)


def test_production_compile_is_refused(client, dba, production, monkeypatch):
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(client.app.state.harness.execution._backend, "connect", connect)
    response = client.post(
        "/api/v1/plsql/compile",
        headers=dba,
        json={
            "profileId": production,
            "source": "CREATE OR REPLACE PROCEDURE boundary_probe IS BEGIN NULL; END;",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "policy_refused"
    connect.assert_not_called()
    assert_no_execution(client)


@pytest.mark.parametrize("action", ["commit", "sql_commit", "dml", "ddl", "plsql", "query"])
def test_existing_session_cannot_write_after_becoming_production(
    client, dba, targets, action, monkeypatch
):
    profile_id = targets["development"]["id"]
    session_id = open_worksheet(client, dba, profile_id)
    connection = live_connection(client, session_id)
    execute = Mock(side_effect=AssertionError("must not execute"))
    commit = Mock(side_effect=AssertionError("must not commit"))
    monkeypatch.setattr(connection, "execute", execute)
    monkeypatch.setattr(connection, "commit", commit)
    with client.app.state.harness.session_factory() as db:
        db.get(ConnectionProfile, profile_id).environment = "production"
        db.commit()
    if action == "commit":
        response = client.post(f"/api/v1/worksheets/{session_id}/commit", headers=dba)
    else:
        statements = {
            "sql_commit": "COMMIT",
            "dml": "UPDATE employees SET salary = salary + 1",
            "ddl": "CREATE TABLE boundary_probe (id NUMBER)",
            "plsql": "BEGIN NULL; END;",
            "query": "SELECT 1 FROM dual",
        }
        response = client.post(
            f"/api/v1/worksheets/{session_id}/execute",
            headers=dba,
            json={"statement": statements[action]},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "policy_refused"
    execute.assert_not_called()
    commit.assert_not_called()
    assert_no_execution(client)


@pytest.mark.parametrize("flag", ["worksheetsEnabled", "mutatingRunbooksEnabled"])
def test_production_create_refuses_mutating_flags(client, administrator, flag):
    response = client.post(
        "/api/v1/admin/targets",
        headers=administrator,
        json={
            "name": "production-stand-in",
            "environment": "production",
            "host": "localhost",
            "serviceName": "STANDIN",
            "username": "harness_app",
            "secretReference": "harness-app",
            flag: True,
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "policy_refused"
    with client.app.state.harness.session_factory() as db:
        assert (
            db.scalar(
                select(ConnectionProfile).where(ConnectionProfile.name == "production-stand-in")
            )
            is None
        )
    assert_no_execution(client)


@pytest.mark.parametrize("method", ["put", "patch"])
def test_profile_update_has_no_route(client, administrator, production, method):
    response = getattr(client, method)(
        f"/api/v1/admin/targets/{production}",
        headers=administrator,
        json={"worksheetsEnabled": True, "mutatingRunbooksEnabled": True},
    )
    assert response.status_code in (404, 405)
    with client.app.state.harness.session_factory() as db:
        profile = db.get(ConnectionProfile, production)
        assert not profile.worksheets_enabled
        assert not profile.mutating_runbooks_enabled
    assert_no_execution(client)


def test_production_worksheet_grant_refused(client, administrator, production):
    response = client.post(
        "/api/v1/admin/grants",
        headers=administrator,
        json={
            "subject": "dba@example.internal",
            "profileId": production,
            "permissions": ["read", "worksheet"],
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "policy_refused"
    assert_no_execution(client)


@pytest.mark.parametrize("runbook", ["runbook.recompile_object", "runbook.gather_table_stats"])
def test_production_mutating_runbooks_refused(client, dba, production, monkeypatch, runbook):
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(client.app.state.harness.execution._backend, "connect", connect)
    with client.app.state.harness.session_factory() as db:
        db.get(ConnectionProfile, production).mutating_runbooks_enabled = True
        db.commit()
    response = client.post(
        f"/api/v1/runbooks/{runbook}/run",
        headers=dba,
        json={
            "profileId": production,
            "confirm": True,
            "parameters": {
                "owner": "HARNESS_APP",
                "object_name": "EMPLOYEE_REPORT",
                "object_kind": "PACKAGE BODY",
                "table_name": "EMPLOYEES",
                "sid": "123",
                "serial": "456",
            },
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "policy_refused"
    connect.assert_not_called()
    assert_no_execution(client)


@pytest.mark.parametrize("kiwi", [False, True])
@pytest.mark.parametrize("binding", ["profile", "id", "name", "raw_id", "raw_name"])
def test_production_copilot_refused_before_provider_budget_or_lookup(
    client, dba, production, monkeypatch, kiwi, binding
):
    service = client.app.state.harness.copilot
    service._settings = service._settings.model_copy(update={"kiwi_enabled": kiwi})
    provider = Mock(side_effect=AssertionError("must not call provider"))
    monkeypatch.setattr(service, "_provider", provider)
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(client.app.state.harness.execution._backend, "connect", connect)
    kwargs = {
        "profile": {"profileId": production},
        "id": {"targetReference": f"harness:{production}:HARNESS_APP"},
        "name": {"targetReference": "harness:production:HARNESS_APP"},
        "raw_id": {"targetReference": production},
        "raw_name": {"targetReference": "production"},
    }[binding]
    events = sse(client, dba, base_payload(**kwargs))
    assert [name for name, _ in events] == ["error"]
    assert events[0][1]["code"] == "policy_refused"
    assert events[0][1]["detail"]["profileId"] == production
    assert provider.mock_calls == []
    connect.assert_not_called()
    with client.app.state.harness.session_factory() as db:
        for model in (CopilotBudget, CopilotRequest, CopilotToolCall):
            assert list(db.scalars(select(model))) == []
        refusal = db.scalar(select(AuditEvent).where(AuditEvent.profile_id == production))
        assert refusal.policy_decision == "refused"
    assert_no_execution(client)


@pytest.mark.parametrize("schema_binding", ["id", "name"])
def test_development_copilot_allows_schema_matching_production(
    client, dba, targets, production, schema_binding
):
    profile_id = targets["development"]["id"]
    schema = production if schema_binding == "id" else "production"
    events = sse(
        client,
        dba,
        base_payload(targetReference=f"harness:{profile_id}:{schema}"),
    )
    assert "done" in [name for name, _ in events]
    assert "error" not in [name for name, _ in events]
    with client.app.state.harness.session_factory() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.profile_id == production)) is None


def test_unbound_editor_only_copilot_remains_available(client, dba):
    events = sse(client, dba, base_payload(targetReference=""))
    assert "done" in [name for name, _ in events]


@pytest.mark.parametrize(
    "permission,risk", [("read", RiskClass.SESSION_WRITE), ("compile", RiskClass.READ)]
)
def test_production_policy_refuses_session_write_and_compile_independent_of_flags(
    client, production, permission, risk
):
    with client.app.state.harness.session_factory() as db:
        profile = db.get(ConnectionProfile, production)
        profile.worksheets_enabled = True
        profile.mutating_runbooks_enabled = True
        with pytest.raises(PolicyError):
            PolicyEngine()._check_environment(profile, permission, risk, Mock())


@pytest.mark.parametrize("flag", ["worksheetsEnabled", "mutatingRunbooksEnabled"])
def test_bootstrap_refuses_unsafe_production_before_storage_or_probe(settings, monkeypatch, flag):
    from scripts import bootstrap_targets

    manifest = {
        "version": 1,
        "authMode": "dev",
        "users": [{"subject": "dba@example.internal", "roles": ["dba"]}],
        "targets": [
            {
                "name": "production-stand-in",
                "environment": "production",
                "host": "localhost",
                "port": 1521,
                "serviceName": "STANDIN",
                "username": "harness_app",
                "defaultSchema": "HARNESS_APP",
                "secretReference": "stand-in",
                "passwordFile": "stand-in.password",
                "worksheetsEnabled": False,
                "mutatingRunbooksEnabled": False,
                flag: True,
            }
        ],
    }
    build = Mock(side_effect=AssertionError("must not initialize storage"))
    monkeypatch.setattr(bootstrap_targets, "build_engine", build)
    with pytest.raises(ValueError, match="production targets must be observation only"):
        bootstrap_targets.bootstrap(manifest, settings, probe=False)
    build.assert_not_called()


def test_production_explain_is_refused(client, dba, production, monkeypatch):
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(client.app.state.harness.execution._backend, "connect", connect)
    response = client.post(
        "/api/v1/tuning/explain",
        headers=dba,
        json={"profileId": production, "statement": "SELECT 1 FROM dual"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "policy_refused"
    connect.assert_not_called()
    assert_no_execution(client)
