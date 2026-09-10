"""Authorization and policy.

Every operation is checked here, in one place, whether it arrived from the console,
a direct API call, or an IDE adapter. The checks are deliberately layered and each
one is recorded on the execution record:

1. Does the actor hold a grant on this target at all?
2. Does the grant carry the permission this class of operation needs?
3. Does the actor hold the application role the operation needs?
4. Does the profile allow this risk class in its environment?
5. Has the target actually got the capabilities the operation needs?
6. Is the target new enough for the statement the operation issues?

A keyword scan of user SQL is not one of these layers. MVP_PLAN.md is explicit that
a SQL keyword filter is not a read-only boundary, so free-form execution is gated by
profile and grant, and constrained by the Oracle account itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from harness_api.models import AppRole, ConnectionProfile, TargetCapability, UserTargetGrant
from harness_api.security import Principal
from harness_worker.catalog import CatalogEntry
from harness_worker.errors import AuthorizationError, CapabilityError, PolicyError
from harness_worker.types import Capability, RiskClass

# Permissions a grant can carry. They describe harness operations, not Oracle
# privileges, which remain the database's business.
PERMISSION_READ = "read"
PERMISSION_WORKSHEET = "worksheet"
PERMISSION_COMPILE = "compile"
PERMISSION_RUNBOOK = "runbook"
ALL_PERMISSIONS = (PERMISSION_READ, PERMISSION_WORKSHEET, PERMISSION_COMPILE, PERMISSION_RUNBOOK)

_ROLE_FOR_PERMISSION: dict[str, tuple[AppRole, ...]] = {
    PERMISSION_READ: (AppRole.VIEWER, AppRole.DEVELOPER, AppRole.DBA, AppRole.ADMINISTRATOR),
    PERMISSION_WORKSHEET: (AppRole.DEVELOPER, AppRole.DBA, AppRole.ADMINISTRATOR),
    PERMISSION_COMPILE: (AppRole.DEVELOPER, AppRole.ADMINISTRATOR),
    PERMISSION_RUNBOOK: (AppRole.DBA, AppRole.ADMINISTRATOR),
}


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str = ""
    permission: str = PERMISSION_READ
    risk: RiskClass = RiskClass.READ
    missing_capabilities: list[Capability] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "permission": self.permission,
            "risk": self.risk.value,
            "missingCapabilities": [c.value for c in self.missing_capabilities],
            "notes": list(self.notes),
        }


class PolicyEngine:
    def __init__(self, *, production_environments: tuple[str, ...] = ("production",)) -> None:
        self._production = production_environments

    # -- target access -------------------------------------------------------------

    def require_grant(
        self, principal: Principal, profile: ConnectionProfile, grant: UserTargetGrant | None
    ) -> UserTargetGrant:
        if principal.is_integration:
            raise AuthorizationError(
                "An integration credential grants copilot access only. It cannot open "
                "harness database sessions.",
                detail={"integrationId": principal.integration_id},
            )
        if grant is None:
            # Administrators manage access; they do not silently inherit it.
            raise AuthorizationError(
                f"You do not have access to the target {profile.name!r}.",
                detail={"profileId": profile.id},
            )
        return grant

    # -- operation authorisation ---------------------------------------------------

    def authorize(
        self,
        *,
        principal: Principal,
        profile: ConnectionProfile,
        grant: UserTargetGrant,
        permission: str,
        risk: RiskClass,
        required_capabilities: tuple[Capability, ...] = (),
        target_capabilities: list[TargetCapability] | None = None,
        min_version: int = 11,
        target_major_version: int | None = None,
        operation_id: str = "",
    ) -> PolicyDecision:
        decision = PolicyDecision(allowed=True, permission=permission, risk=risk)

        if permission not in (grant.permissions or []):
            decision.allowed = False
            decision.reason = (
                f"Your grant on {profile.name!r} does not include the {permission!r} permission."
            )
            raise PolicyError(decision.reason, detail=decision.as_dict())

        allowed_roles = _ROLE_FOR_PERMISSION[permission]
        if not principal.has_role(*allowed_roles):
            decision.allowed = False
            decision.reason = (
                f"The {permission!r} permission needs one of these application roles: "
                + ", ".join(role.value for role in allowed_roles)
                + "."
            )
            raise PolicyError(decision.reason, detail=decision.as_dict())

        self._check_environment(profile, permission, risk, decision)
        self._check_capabilities(required_capabilities, target_capabilities or [], decision)
        self._check_version(min_version, target_major_version, decision, operation_id)
        return decision

    def _check_environment(
        self,
        profile: ConnectionProfile,
        permission: str,
        risk: RiskClass,
        decision: PolicyDecision,
    ) -> None:
        production = profile.environment in self._production

        if permission == PERMISSION_WORKSHEET and not profile.worksheets_enabled:
            decision.allowed = False
            decision.reason = (
                f"Free-form worksheets are not enabled on {profile.name!r}. Production "
                "observation uses the reviewed diagnostic operations instead."
            )
            raise PolicyError(decision.reason, detail=decision.as_dict())

        if production and risk in (RiskClass.PERSISTENT_WRITE, RiskClass.ADMINISTRATIVE):
            decision.allowed = False
            decision.reason = (
                f"{profile.name!r} is a production target. Mutating operations are out "
                "of scope for this release; production is observation only."
            )
            raise PolicyError(decision.reason, detail=decision.as_dict())

        if risk == RiskClass.ADMINISTRATIVE and not profile.mutating_runbooks_enabled:
            decision.allowed = False
            decision.reason = (
                f"Mutating runbooks are not enabled on {profile.name!r}. An "
                "administrator has to turn them on for this target."
            )
            raise PolicyError(decision.reason, detail=decision.as_dict())

        if risk == RiskClass.SESSION_WRITE:
            decision.notes.append(
                "Changes stay in your worksheet transaction until you commit. Oracle "
                "DDL and PL/SQL can commit on their own, so rollback is not a "
                "guarantee for those."
            )

    def _check_capabilities(
        self,
        required: tuple[Capability, ...],
        available: list[TargetCapability],
        decision: PolicyDecision,
    ) -> None:
        if not required:
            return
        present = {row.capability for row in available if row.available}
        missing = [cap for cap in required if cap.value not in present]
        if missing:
            decision.allowed = False
            decision.missing_capabilities = missing
            names = ", ".join(cap.value for cap in missing)
            decision.reason = (
                f"This target is missing the capabilities this operation needs: {names}. "
                "Grant the underlying privileges, or leave the feature unavailable; it "
                "will not fall back to an empty result."
            )
            raise CapabilityError(decision.reason, detail=decision.as_dict())

    def _check_version(
        self,
        min_version: int,
        target_major_version: int | None,
        decision: PolicyDecision,
        operation_id: str,
    ) -> None:
        if target_major_version is None:
            decision.notes.append(
                "The target version has not been probed, so version requirements could "
                "not be checked."
            )
            return
        if target_major_version < min_version:
            decision.allowed = False
            decision.reason = (
                f"{operation_id or 'This operation'} requires Oracle {min_version} or "
                f"later; the target reports major version {target_major_version}."
            )
            raise PolicyError(decision.reason, detail=decision.as_dict())

    # -- catalog helper ------------------------------------------------------------

    def permission_for_entry(self, entry: CatalogEntry) -> str:
        if entry.risk in (RiskClass.ADMINISTRATIVE, RiskClass.PERSISTENT_WRITE):
            return PERMISSION_RUNBOOK
        return PERMISSION_READ
