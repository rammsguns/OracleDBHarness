"""Health, identity and the operation catalog."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from harness_api import __version__
from harness_api.deps import CurrentUser, Db, State
from harness_api.models import ConnectionProfile, UserTargetGrant
from harness_api.schemas import DevTokenRequest, DevTokenResponse, MeResponse, SystemInfo
from harness_worker.errors import PolicyError

router = APIRouter(tags=["system"])


@router.get("/healthz")
def healthz(state: State) -> dict:
    """Liveness only. It never opens an Oracle connection."""

    return {"status": "ok", "version": __version__, "environment": state.settings.env}


@router.get("/api/v1/system/info", response_model=SystemInfo)
def system_info(state: State) -> SystemInfo:
    settings = state.settings
    return SystemInfo(
        version=__version__,
        environment=settings.env,
        authMode=settings.auth_mode,
        oracleBackend=settings.oracle_backend,
        oracleDriverMode=settings.oracle_driver_mode,
        metadataSchemaVersion=state.metadata_schema_version,
        catalogOperations=len(state.execution.catalog),
        copilotEnabled=settings.copilot_enabled,
        warnings=settings.startup_warnings(),
        limits=settings.default_limits.model_dump(by_alias=True, mode="json"),
    )


@router.post("/api/v1/auth/dev-token", response_model=DevTokenResponse)
def dev_token(payload: DevTokenRequest, state: State) -> DevTokenResponse:
    """Issue a locally signed token. Development identity mode only."""

    if state.settings.auth_mode != "dev":
        raise PolicyError(
            "This deployment authenticates through its identity provider. Development "
            "tokens are not issued."
        )
    token = state.authenticator.issue_dev_token(
        payload.subject, payload.roles, payload.display_name
    )
    return DevTokenResponse(
        accessToken=token,
        warning=(
            "This token is signed locally by the harness and proves nothing about who "
            "you are. Use it for development only."
        ),
    )


@router.get("/api/v1/auth/me", response_model=MeResponse)
def me(principal: CurrentUser, db: Db) -> MeResponse:
    targets = []
    if principal.user_id:
        rows = db.execute(
            select(UserTargetGrant, ConnectionProfile)
            .join(ConnectionProfile, ConnectionProfile.id == UserTargetGrant.profile_id)
            .where(UserTargetGrant.user_id == principal.user_id)
        ).all()
        targets = [
            {
                "profileId": profile.id,
                "name": profile.name,
                "environment": profile.environment,
                "permissions": grant.permissions,
            }
            for grant, profile in rows
        ]
    return MeResponse(
        subject=principal.subject,
        displayName=principal.display_name,
        roles=sorted(role.value for role in principal.roles),
        userId=principal.user_id,
        targets=targets,
    )


@router.get("/api/v1/operations")
def list_operations(state: State, principal: CurrentUser, prefix: str | None = None) -> dict:
    """The reviewed query catalog, with the privileges each entry needs."""

    return {
        "operations": [entry.describe() for entry in state.execution.catalog.list(prefix)],
        "note": (
            "Each entry records the capabilities and Oracle privileges it needs. A "
            "target missing them reports capability_unavailable instead of an empty "
            "result."
        ),
    }
