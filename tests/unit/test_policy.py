"""Policy decisions, independent of HTTP and of any database."""

from __future__ import annotations

import pytest

from harness_api.models import AppRole, ConnectionProfile, TargetCapability, UserTargetGrant
from harness_api.policy import (
    PERMISSION_READ,
    PERMISSION_RUNBOOK,
    PERMISSION_WORKSHEET,
    PolicyEngine,
)
from harness_api.security import Principal
from harness_worker.errors import AuthorizationError, CapabilityError, PolicyError
from harness_worker.types import Capability, ExecutionLimits, RiskClass


def profile(**overrides) -> ConnectionProfile:
    defaults = dict(
        id="tgt_1",
        name="development",
        environment="development",
        host="db",
        port=1521,
        service_name="DEV",
        username="app",
        secret_reference_id="sec_1",
        worksheets_enabled=True,
        mutating_runbooks_enabled=True,
    )
    defaults.update(overrides)
    return ConnectionProfile(**defaults)


def grant(*permissions: str) -> UserTargetGrant:
    return UserTargetGrant(
        id="grt_1", user_id="usr_1", profile_id="tgt_1", permissions=list(permissions)
    )


def principal(*roles: AppRole) -> Principal:
    return Principal(subject="dev@example.internal", roles=set(roles), user_id="usr_1")


def capability(name: Capability, available: bool = True) -> TargetCapability:
    return TargetCapability(profile_id="tgt_1", capability=name.value, available=available)


def test_missing_grant_is_refused_even_for_an_administrator() -> None:
    engine = PolicyEngine()
    with pytest.raises(AuthorizationError):
        engine.require_grant(principal(AppRole.ADMINISTRATOR), profile(), None)


def test_integration_credentials_cannot_open_database_sessions() -> None:
    engine = PolicyEngine()
    integration = Principal(
        subject="integration:int_1",
        integration_id="int_1",
        integration_scopes=("copilot:assist",),
    )
    with pytest.raises(AuthorizationError):
        engine.require_grant(integration, profile(), grant(PERMISSION_READ))


def test_permission_must_be_on_the_grant() -> None:
    engine = PolicyEngine()
    with pytest.raises(PolicyError, match="does not include"):
        engine.authorize(
            principal=principal(AppRole.DEVELOPER),
            profile=profile(),
            grant=grant(PERMISSION_READ),
            permission=PERMISSION_WORKSHEET,
            risk=RiskClass.READ,
        )


def test_role_must_allow_the_permission() -> None:
    engine = PolicyEngine()
    with pytest.raises(PolicyError, match="application roles"):
        engine.authorize(
            principal=principal(AppRole.VIEWER),
            profile=profile(),
            grant=grant(PERMISSION_WORKSHEET),
            permission=PERMISSION_WORKSHEET,
            risk=RiskClass.READ,
        )


def test_worksheets_are_refused_where_the_target_has_not_enabled_them() -> None:
    engine = PolicyEngine()
    with pytest.raises(PolicyError, match="not enabled"):
        engine.authorize(
            principal=principal(AppRole.DEVELOPER),
            profile=profile(worksheets_enabled=False),
            grant=grant(PERMISSION_WORKSHEET),
            permission=PERMISSION_WORKSHEET,
            risk=RiskClass.READ,
        )


def test_production_refuses_mutating_risk_classes() -> None:
    engine = PolicyEngine()
    with pytest.raises(PolicyError, match="production target"):
        engine.authorize(
            principal=principal(AppRole.DBA),
            profile=profile(environment="production", mutating_runbooks_enabled=True),
            grant=grant(PERMISSION_RUNBOOK),
            permission=PERMISSION_RUNBOOK,
            risk=RiskClass.ADMINISTRATIVE,
        )


def test_production_still_allows_reviewed_reads() -> None:
    engine = PolicyEngine()
    decision = engine.authorize(
        principal=principal(AppRole.VIEWER),
        profile=profile(environment="production", worksheets_enabled=False),
        grant=grant(PERMISSION_READ),
        permission=PERMISSION_READ,
        risk=RiskClass.READ,
    )
    assert decision.allowed


def test_missing_capability_names_what_is_missing() -> None:
    engine = PolicyEngine()
    with pytest.raises(CapabilityError) as excinfo:
        engine.authorize(
            principal=principal(AppRole.DBA),
            profile=profile(),
            grant=grant(PERMISSION_READ),
            permission=PERMISSION_READ,
            risk=RiskClass.READ,
            required_capabilities=(Capability.V_SESSION, Capability.DBA_TABLESPACES),
            target_capabilities=[capability(Capability.V_SESSION, available=False)],
        )
    missing = excinfo.value.detail["missingCapabilities"]
    assert set(missing) == {"v_session", "dba_tablespaces"}


def test_version_requirement_is_enforced_when_known() -> None:
    engine = PolicyEngine()
    with pytest.raises(PolicyError, match="requires Oracle 19"):
        engine.authorize(
            principal=principal(AppRole.DEVELOPER),
            profile=profile(),
            grant=grant(PERMISSION_READ),
            permission=PERMISSION_READ,
            risk=RiskClass.READ,
            min_version=19,
            target_major_version=12,
            operation_id="some.operation",
        )


def test_unknown_version_is_reported_not_assumed() -> None:
    engine = PolicyEngine()
    decision = engine.authorize(
        principal=principal(AppRole.DEVELOPER),
        profile=profile(),
        grant=grant(PERMISSION_READ),
        permission=PERMISSION_READ,
        risk=RiskClass.READ,
        min_version=19,
        target_major_version=None,
    )
    assert decision.allowed
    assert any("not been probed" in note for note in decision.notes)


def test_session_write_carries_the_rollback_caveat() -> None:
    engine = PolicyEngine()
    decision = engine.authorize(
        principal=principal(AppRole.DEVELOPER),
        profile=profile(),
        grant=grant(PERMISSION_WORKSHEET),
        permission=PERMISSION_WORKSHEET,
        risk=RiskClass.SESSION_WRITE,
    )
    assert any("DDL" in note for note in decision.notes)


def test_limits_narrow_but_never_widen() -> None:
    configured = ExecutionLimits(maxRows=1000, deadlineSeconds=30)
    asked = ExecutionLimits(maxRows=5000, deadlineSeconds=5)
    effective = configured.narrowed_to(asked)
    assert effective.max_rows == 1000
    assert effective.deadline_seconds == 5
