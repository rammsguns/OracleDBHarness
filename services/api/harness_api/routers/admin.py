"""Administration: accounts, credentials references, targets, grants, integrations.

Everything here is restricted to the Administrator role. Administrators manage
access; they do not silently inherit access to every target.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from harness_api.deps import Administrator, Db, State
from harness_api.models import (
    AppRole,
    ConnectionProfile,
    IntegrationInstance,
    SecretReference,
    User,
    UserTargetGrant,
    utcnow,
)
from harness_api.policy import ALL_PERMISSIONS
from harness_api.schemas import (
    GrantIn,
    IntegrationCreated,
    IntegrationIn,
    ProfileIn,
    SecretReferenceIn,
    UserIn,
)
from harness_api.secrets import SecretResolver
from harness_api.security import generate_integration_token
from harness_worker.errors import NotFoundError, PolicyError, ValidationError

router = APIRouter(prefix="/api/v1/admin", tags=["administration"])


# -- secret references ---------------------------------------------------------------


@router.get("/secrets")
def list_secrets(_: Administrator, db: Db, state: State) -> dict:
    resolver = SecretResolver(state.settings.secret_dir)
    rows = db.scalars(select(SecretReference).order_by(SecretReference.name)).all()
    return {"secrets": [resolver.describe(row) for row in rows]}


@router.post("/secrets", status_code=201)
def create_secret(payload: SecretReferenceIn, _: Administrator, db: Db, state: State) -> dict:
    if db.scalars(select(SecretReference).where(SecretReference.name == payload.name)).first():
        raise ValidationError(
            f"A secret reference named {payload.name!r} already exists.",
            detail={"name": payload.name},
        )
    row = SecretReference(
        name=payload.name,
        provider=payload.provider,
        locator=payload.locator,
        description=payload.description,
    )
    db.add(row)
    db.commit()
    resolver = SecretResolver(state.settings.secret_dir)
    return resolver.describe(row)


# -- users ---------------------------------------------------------------------------


@router.get("/users")
def list_users(_: Administrator, db: Db) -> dict:
    rows = db.scalars(select(User).order_by(User.subject)).all()
    return {
        "users": [
            {
                "id": row.id,
                "subject": row.subject,
                "displayName": row.display_name,
                "email": row.email,
                "roles": row.roles,
                "disabled": row.disabled,
            }
            for row in rows
        ]
    }


@router.post("/users", status_code=201)
def create_user(payload: UserIn, _: Administrator, db: Db) -> dict:
    for role in payload.roles:
        try:
            AppRole(role)
        except ValueError as exc:
            raise ValidationError(
                f"Unknown application role {role!r}.",
                detail={"roles": [r.value for r in AppRole]},
            ) from exc
    existing = db.scalars(select(User).where(User.subject == payload.subject)).first()
    if existing:
        existing.display_name = payload.display_name or existing.display_name
        existing.email = payload.email or existing.email
        existing.roles = payload.roles
        db.commit()
        return {"id": existing.id, "subject": existing.subject, "roles": existing.roles}
    row = User(
        subject=payload.subject,
        display_name=payload.display_name,
        email=payload.email,
        roles=payload.roles,
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "subject": row.subject, "roles": row.roles}


# -- targets ---------------------------------------------------------------------------


@router.post("/targets", status_code=201)
def create_target(payload: ProfileIn, _: Administrator, db: Db, state: State) -> dict:
    allowlist = state.settings.endpoint_allowlist
    endpoint = f"{payload.host}:{payload.port}"
    if allowlist and endpoint not in allowlist and payload.host not in allowlist:
        raise PolicyError(
            f"The endpoint {endpoint} is not in HARNESS_ALLOWED_ENDPOINTS.",
            detail={"endpoint": endpoint, "allowed": allowlist},
        )
    secret = db.scalars(
        select(SecretReference).where(SecretReference.name == payload.secret_reference)
    ).first()
    if secret is None:
        raise NotFoundError(
            f"No secret reference named {payload.secret_reference!r}.",
            detail={"secretReference": payload.secret_reference},
        )
    if payload.environment == "production" and (
        payload.worksheets_enabled or payload.mutating_runbooks_enabled
    ):
        raise PolicyError(
            "Production targets are observation only in this release. Free-form "
            "worksheets and mutating runbooks cannot be enabled on them.",
            detail={"environment": payload.environment},
        )
    profile = ConnectionProfile(
        name=payload.name,
        environment=payload.environment,
        host=payload.host,
        port=payload.port,
        service_name=payload.service_name,
        username=payload.username,
        default_schema=payload.default_schema,
        protocol=payload.protocol,
        wallet_dir=payload.wallet_dir,
        secret_reference_id=secret.id,
        worksheets_enabled=payload.worksheets_enabled,
        mutating_runbooks_enabled=payload.mutating_runbooks_enabled,
        notes=payload.notes,
    )
    db.add(profile)
    db.commit()
    return {"id": profile.id, "name": profile.name, "environment": profile.environment}


# -- grants -----------------------------------------------------------------------------


@router.post("/grants", status_code=201)
def create_grant(payload: GrantIn, admin: Administrator, db: Db) -> dict:
    unknown = set(payload.permissions) - set(ALL_PERMISSIONS)
    if unknown:
        raise ValidationError(
            f"Unknown permission(s): {', '.join(sorted(unknown))}.",
            detail={"permissions": list(ALL_PERMISSIONS)},
        )
    user = db.scalars(select(User).where(User.subject == payload.subject)).first()
    if user is None:
        raise NotFoundError(
            f"No registered user with subject {payload.subject!r}.",
            detail={"subject": payload.subject},
        )
    profile = db.get(ConnectionProfile, payload.profile_id)
    if profile is None:
        raise NotFoundError("No such connection profile.", detail={"profileId": payload.profile_id})
    if "worksheet" in payload.permissions and not profile.worksheets_enabled:
        raise PolicyError(
            f"Worksheets are not enabled on {profile.name!r}, so a worksheet permission "
            "would never take effect. Enable them on the target first.",
            detail={"profileId": profile.id},
        )

    secret_id = None
    if payload.secret_reference:
        secret = db.scalars(
            select(SecretReference).where(SecretReference.name == payload.secret_reference)
        ).first()
        if secret is None:
            raise NotFoundError(
                f"No secret reference named {payload.secret_reference!r}.",
                detail={"secretReference": payload.secret_reference},
            )
        secret_id = secret.id

    grant = db.scalars(
        select(UserTargetGrant).where(
            UserTargetGrant.user_id == user.id,
            UserTargetGrant.profile_id == profile.id,
        )
    ).first()
    if grant is None:
        grant = UserTargetGrant(user_id=user.id, profile_id=profile.id)
        db.add(grant)
    grant.permissions = payload.permissions
    grant.secret_reference_id = secret_id
    grant.granted_by = admin.user_id or ""
    db.commit()
    return {
        "id": grant.id,
        "subject": user.subject,
        "profileId": profile.id,
        "permissions": grant.permissions,
        "usesOwnCredential": bool(secret_id),
    }


@router.delete("/grants/{grant_id}")
def revoke_grant(grant_id: str, _: Administrator, db: Db, state: State) -> dict:
    grant = db.get(UserTargetGrant, grant_id)
    if grant is None:
        raise NotFoundError("No such grant.", detail={"grantId": grant_id})
    user_id, profile_id = grant.user_id, grant.profile_id
    db.delete(grant)
    # Revocation reaches the sessions that are already open, not just the next
    # request. Their uncommitted work is rolled back.
    closed = state.execution.revoke_target_access(db, user_id=user_id, target_id=profile_id)
    db.commit()
    return {
        "revoked": True,
        "grantId": grant_id,
        "closedSessions": closed,
        "note": (
            "Any worksheet session this user held on the target was closed and its "
            "uncommitted work rolled back."
        ),
    }


# -- integrations --------------------------------------------------------------------------


@router.get("/integrations")
def list_integrations(_: Administrator, db: Db) -> dict:
    rows = db.scalars(select(IntegrationInstance).order_by(IntegrationInstance.name)).all()
    return {
        "integrations": [
            {
                "id": row.id,
                "name": row.name,
                "kind": row.kind,
                "scopes": row.scopes,
                "enabled": row.enabled and row.revoked_at is None,
                "adapterVersion": row.adapter_version,
                "protocolVersion": row.protocol_version,
                "lastSeenAt": row.last_seen_at.isoformat() if row.last_seen_at else None,
            }
            for row in rows
        ]
    }


@router.post("/integrations", status_code=201, response_model=IntegrationCreated)
def create_integration(payload: IntegrationIn, _: Administrator, db: Db) -> IntegrationCreated:
    """Issue a revocable integration credential.

    The scope is deliberately narrow. ``copilot:assist`` lets an adapter ask for
    assistance; it grants no database execution capability and no harness target
    access, and it does not make a DataForge administrator a harness administrator.
    """

    unknown = set(payload.scopes) - {"copilot:assist"}
    if unknown:
        raise ValidationError(
            f"Unsupported integration scope(s): {', '.join(sorted(unknown))}.",
            detail={"supported": ["copilot:assist"]},
        )
    if db.scalars(
        select(IntegrationInstance).where(IntegrationInstance.name == payload.name)
    ).first():
        raise ValidationError(
            f"An integration named {payload.name!r} already exists.",
            detail={"name": payload.name},
        )
    token, prefix, digest = generate_integration_token()
    row = IntegrationInstance(
        name=payload.name,
        kind=payload.kind,
        scopes=payload.scopes,
        token_hash=digest,
        token_prefix=prefix,
    )
    db.add(row)
    db.commit()
    return IntegrationCreated(
        id=row.id,
        name=row.name,
        kind=row.kind,
        scopes=row.scopes,
        token=token,
        note=(
            "This is the only time the credential is shown. Store it as a backend "
            "secret in the adapter; it must never reach a browser or a URL."
        ),
    )


@router.post("/integrations/{integration_id}/revoke")
def revoke_integration(integration_id: str, _: Administrator, db: Db) -> dict:
    row = db.get(IntegrationInstance, integration_id)
    if row is None:
        raise NotFoundError("No such integration.", detail={"integrationId": integration_id})
    row.enabled = False
    row.revoked_at = utcnow()
    db.commit()
    return {
        "revoked": True,
        "integrationId": integration_id,
        "note": (
            "Copilot access is revoked and active copilot context is no longer usable. "
            "Oracle sessions owned by the IDE are unaffected."
        ),
    }
