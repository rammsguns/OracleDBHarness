"""Production identity boundaries, using fake targets and driver stand-ins only."""

from unittest.mock import Mock

import pytest

from harness_api.models import ConnectionProfile
from harness_api.policy import PERMISSION_READ, PolicyEngine
from harness_worker.backend.fake import FakeOracleConnection
from harness_worker.errors import ConnectionFailedError, TimeoutError_
from harness_worker.types import RiskClass, TargetIdentity
from tests.unit.test_oracle_adapter import DriverError, _identity_pair
from tests.unit.test_policy import AppRole, grant, principal, profile


@pytest.fixture(scope="session")
def oracle_target():
    return None


@pytest.mark.parametrize(
    "version,allowed",
    [
        ("19.0.0.0.0", True),
        ("23.0.0.0.0", False),
        ("26.0.0.0.0", False),
        ("18.0.0.0.0", False),
        ("", False),
        ("unknown", False),
    ],
    ids=["19c", "23ai", "26ai", "18c", "empty", "unparseable"],
)
@pytest.mark.parametrize("environment", ["production", "development"])
def test_probed_identity_boundary(client, dba, targets, monkeypatch, version, allowed, environment):
    target_id = targets[environment]["id"]
    original = FakeOracleConnection.identity

    def identity(connection):
        return original(connection).model_copy(
            update={"version": version, "version_full": "unknown"}
        )

    monkeypatch.setattr(FakeOracleConnection, "identity", identity)
    response = client.post(f"/api/v1/targets/{target_id}/test", headers=dba)
    assert response.status_code == 200
    accepted = allowed or environment != "production"
    assert response.json()["connected"] is accepted
    with client.app.state.harness.session_factory() as db:
        stored = db.get(ConnectionProfile, target_id)
        assert (stored.identity_json is not None) is accepted
    if accepted and version.startswith("19."):
        health = client.get(f"/api/v1/targets/{target_id}/dba/overview", headers=dba).json()
        assert all(panel["available"] for panel in health["panels"].values())
    if not accepted:
        assert response.json()["error"]["code"] == "policy_refused"
        response = client.get(f"/api/v1/targets/{target_id}/dba/overview", headers=dba)
        assert all(not panel["available"] for panel in response.json()["panels"].values())
        assert all(
            panel["error"]["code"] == "policy_refused"
            for panel in response.json()["panels"].values()
        )


@pytest.mark.parametrize("major", [None, 0, 18, 19, 23, 26])
def test_central_authorization_requires_19c(major):
    kwargs = dict(
        principal=principal(AppRole.VIEWER),
        profile=profile(environment="production"),
        grant=grant(PERMISSION_READ),
        permission=PERMISSION_READ,
        risk=RiskClass.READ,
        target_major_version=major,
    )
    if major == 19:
        assert PolicyEngine().authorize(**kwargs).allowed
    else:
        from harness_worker.errors import PolicyError

        with pytest.raises(PolicyError, match="19c"):
            PolicyEngine().authorize(**kwargs)


@pytest.mark.parametrize(
    "failure", [ConnectionFailedError("identity unavailable"), TimeoutError_("identity timed out")]
)
@pytest.mark.parametrize("cached", [False, True], ids=["unprobed", "stale-19c"])
def test_identity_failure_cannot_reuse_cache(client, dba, targets, monkeypatch, failure, cached):
    target_id = targets["production"]["id"]
    with client.app.state.harness.session_factory() as db:
        stored = db.get(ConnectionProfile, target_id)
        assert TargetIdentity.model_validate(stored.identity_json).major_version == 19
        if not cached:
            stored.identity_json = None
            stored.identity_checked_at = None
            db.commit()
    closed = Mock()
    original_close = FakeOracleConnection.close

    def close(connection):
        closed()
        original_close(connection)

    monkeypatch.setattr(FakeOracleConnection, "identity", Mock(side_effect=failure))
    monkeypatch.setattr(FakeOracleConnection, "close", close)
    response = client.post(f"/api/v1/targets/{target_id}/test", headers=dba)
    assert response.status_code == 200
    assert response.json()["connected"] is False
    assert response.json()["error"]["code"] == failure.code
    closed.assert_called_once()
    with client.app.state.harness.session_factory() as db:
        stored = db.get(ConnectionProfile, target_id)
        assert stored.identity_json is None
        assert stored.identity_checked_at is None
    health = client.get(f"/api/v1/targets/{target_id}/dba/overview", headers=dba).json()
    assert all(not panel["available"] for panel in health["panels"].values())
    assert all(panel["error"]["code"] == "policy_refused" for panel in health["panels"].values())


def test_authoritative_driver_version_fallback_allows_19c():
    driver, connection = _identity_pair(
        **{"product_component_version": DriverError("ORA-00942", "view unavailable")}
    )
    driver.version = "19.0.0.0.0"
    identity = connection.identity()
    assert identity.major_version == 19
    PolicyEngine().check_production_identity(
        profile(environment="production"), identity.major_version
    )


def test_failed_connect_invalidates_production_identity(client, dba, targets, monkeypatch):
    target_id = targets["production"]["id"]
    backend = client.app.state.harness.execution._backend
    monkeypatch.setattr(backend, "connect", Mock(side_effect=ConnectionFailedError("unavailable")))
    response = client.post(f"/api/v1/targets/{target_id}/test", headers=dba)
    assert response.json()["connected"] is False
    with client.app.state.harness.session_factory() as db:
        assert db.get(ConnectionProfile, target_id).identity_json is None
