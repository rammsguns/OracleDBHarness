"""Targets, the schema explorer and the DBA overview.

Every panel here reports its collection time and its permission or error state. A
panel that could not be collected is returned with ``available: false`` and the
reason; it is never rendered as an empty, healthy panel.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from harness_api.deps import CurrentUser, Db, State
from harness_api.execution import load_grant, load_profile
from harness_api.models import ConnectionProfile, UserTargetGrant
from harness_api.schemas import (
    CapabilityView,
    ConnectionTestResult,
    ObjectPage,
    PanelResult,
    TargetView,
)
from harness_worker.errors import HarnessError
from harness_worker.types import utcnow

router = APIRouter(prefix="/api/v1/targets", tags=["targets"])


def _capability_views(profile: ConnectionProfile) -> list[CapabilityView]:
    return [
        CapabilityView(
            capability=row.capability,
            available=row.available,
            detail=row.detail,
            checkedAt=row.checked_at.isoformat() if row.checked_at else None,
        )
        for row in sorted(profile.capabilities, key=lambda r: r.capability)
    ]


def _target_view(profile: ConnectionProfile, grant: UserTargetGrant | None) -> TargetView:
    return TargetView(
        id=profile.id,
        name=profile.name,
        environment=profile.environment,
        host=profile.host,
        port=profile.port,
        serviceName=profile.service_name,
        username=profile.username,
        defaultSchema=profile.default_schema,
        worksheetsEnabled=profile.worksheets_enabled,
        mutatingRunbooksEnabled=profile.mutating_runbooks_enabled,
        permissions=list(grant.permissions) if grant else [],
        identity=profile.identity_json,
        identityCheckedAt=(
            profile.identity_checked_at.isoformat() if profile.identity_checked_at else None
        ),
        capabilities=_capability_views(profile),
    )


@router.get("", response_model=list[TargetView])
def list_targets(principal: CurrentUser, db: Db) -> list[TargetView]:
    """Only targets the caller actually holds a grant on."""

    if principal.user_id is None:
        return []
    rows = db.execute(
        select(UserTargetGrant, ConnectionProfile)
        .join(ConnectionProfile, ConnectionProfile.id == UserTargetGrant.profile_id)
        .where(UserTargetGrant.user_id == principal.user_id)
        .order_by(ConnectionProfile.name)
    ).all()
    return [_target_view(profile, grant) for grant, profile in rows]


@router.get("/{profile_id}", response_model=TargetView)
def get_target(profile_id: str, principal: CurrentUser, db: Db, state: State) -> TargetView:
    profile = load_profile(db, profile_id)
    grant = load_grant(db, principal, profile_id)
    grant = state.execution.policy.require_grant(principal, profile, grant)
    return _target_view(profile, grant)


@router.post("/{profile_id}/test", response_model=ConnectionTestResult)
def test_connection(
    profile_id: str, principal: CurrentUser, db: Db, state: State
) -> ConnectionTestResult:
    """Connect, prove the target identity, and probe every capability."""

    profile = load_profile(db, profile_id)
    grant = load_grant(db, principal, profile_id)
    grant = state.execution.policy.require_grant(principal, profile, grant)
    try:
        identity, _ = state.execution.probe_target(db, profile, grant)
    except HarnessError as exc:
        state.execution.audit_refusal(
            db, principal, operation_id="target.test", profile_id=profile_id, error=exc
        )
        return ConnectionTestResult(
            profileId=profile_id,
            connected=False,
            diagnostics=[exc.message],
            error=exc.as_dict(),
        )

    db.refresh(profile)
    capabilities = _capability_views(profile)
    diagnostics = [
        f"{row.capability} is unavailable: {row.detail}"
        for row in capabilities
        if not row.available
    ]
    return ConnectionTestResult(
        profileId=profile_id,
        connected=True,
        identity=identity.model_dump(by_alias=True, mode="json"),
        capabilities=capabilities,
        diagnostics=diagnostics,
    )


# -- schema explorer -------------------------------------------------------------------


def _panel(
    state: State, db, principal, profile, grant, operation_id: str, params: dict
) -> PanelResult:
    entry = state.execution.catalog.get(operation_id)
    collected = utcnow().isoformat()
    try:
        result = state.execution.run_catalog_operation(
            db, principal, profile, grant, operation_id, params
        )
    except HarnessError as exc:
        state.execution.audit_refusal(
            db, principal, operation_id=operation_id, profile_id=profile.id, error=exc
        )
        return PanelResult(
            operationId=operation_id,
            title=entry.title,
            available=False,
            collectedAt=collected,
            error=exc.as_dict(),
        )
    outcome = result.outcome
    rs = outcome.result_set
    return PanelResult(
        operationId=operation_id,
        title=entry.title,
        available=outcome.state.value == "succeeded",
        collectedAt=collected,
        columns=[c.name for c in rs.columns] if rs else [],
        rows=rs.rows if rs else [],
        truncated=bool(rs and rs.truncated),
        error=outcome.error,
    )


@router.get("/{profile_id}/schemas", response_model=PanelResult)
def list_schemas(profile_id: str, principal: CurrentUser, db: Db, state: State) -> PanelResult:
    profile = load_profile(db, profile_id)
    grant = load_grant(db, principal, profile_id)
    grant = state.execution.policy.require_grant(principal, profile, grant)
    return _panel(state, db, principal, profile, grant, "schema.list_schemas", {})


@router.get("/{profile_id}/objects", response_model=ObjectPage)
def list_objects(
    profile_id: str,
    principal: CurrentUser,
    db: Db,
    state: State,
    owner: str,
    object_type: str | None = Query(default=None, alias="objectType"),
    name_filter: str | None = Query(default=None, alias="nameFilter"),
    offset: int = 0,
    limit: int = Query(default=100, ge=1, le=1000),
) -> ObjectPage:
    """One page of objects. Large lists are paged, never returned whole."""

    profile = load_profile(db, profile_id)
    grant = load_grant(db, principal, profile_id)
    grant = state.execution.policy.require_grant(principal, profile, grant)
    # Fetch one extra row to answer hasMore without a second round trip.
    result = state.execution.run_catalog_operation(
        db,
        principal,
        profile,
        grant,
        "schema.list_objects",
        {
            "owner": owner,
            "object_type": object_type,
            "name_filter": f"%{name_filter.upper()}%" if name_filter else None,
            "row_offset": offset,
            "row_limit": limit + 1,
        },
    )
    rs = result.outcome.result_set
    rows = rs.rows if rs else []
    has_more = len(rows) > limit
    return ObjectPage(
        owner=owner.upper(),
        objectType=object_type,
        offset=offset,
        limit=limit,
        columns=[c.name for c in rs.columns] if rs else [],
        rows=rows[:limit],
        hasMore=has_more,
        collectedAt=utcnow().isoformat(),
    )


@router.get("/{profile_id}/objects/detail")
def object_detail(
    profile_id: str,
    principal: CurrentUser,
    db: Db,
    state: State,
    owner: str,
    object_name: str = Query(alias="objectName"),
    object_type: str = Query(alias="objectType"),
) -> dict[str, Any]:
    """Columns, keys, indexes, source, errors and dependencies for one object."""

    profile = load_profile(db, profile_id)
    grant = load_grant(db, principal, profile_id)
    grant = state.execution.policy.require_grant(principal, profile, grant)

    panels: dict[str, PanelResult] = {}
    kind = object_type.upper()
    if kind in ("TABLE", "VIEW"):
        panels["columns"] = _panel(
            state,
            db,
            principal,
            profile,
            grant,
            "schema.table_columns",
            {"owner": owner, "table_name": object_name},
        )
        panels["constraints"] = _panel(
            state,
            db,
            principal,
            profile,
            grant,
            "schema.table_constraints",
            {"owner": owner, "table_name": object_name},
        )
        panels["indexes"] = _panel(
            state,
            db,
            principal,
            profile,
            grant,
            "schema.table_indexes",
            {"owner": owner, "table_name": object_name},
        )
        panels["statistics"] = _panel(
            state,
            db,
            principal,
            profile,
            grant,
            "schema.table_statistics",
            {"owner": owner, "table_name": object_name},
        )
    else:
        panels["source"] = _panel(
            state,
            db,
            principal,
            profile,
            grant,
            "schema.object_source",
            {"owner": owner, "object_name": object_name, "object_type": kind},
        )
        panels["errors"] = _panel(
            state,
            db,
            principal,
            profile,
            grant,
            "schema.object_errors",
            {"owner": owner, "object_name": object_name, "object_type": kind},
        )
    panels["dependencies"] = _panel(
        state,
        db,
        principal,
        profile,
        grant,
        "schema.object_dependencies",
        {"owner": owner, "object_name": object_name},
    )
    panels["status"] = _panel(
        state,
        db,
        principal,
        profile,
        grant,
        "schema.object_status",
        {"owner": owner, "object_name": object_name, "object_type": kind},
    )
    return {
        "owner": owner.upper(),
        "objectName": object_name.upper(),
        "objectType": kind,
        "panels": {name: panel.model_dump(by_alias=True) for name, panel in panels.items()},
    }


# -- DBA overview --------------------------------------------------------------------------


@router.get("/{profile_id}/dba/overview")
def dba_overview(profile_id: str, principal: CurrentUser, db: Db, state: State) -> dict[str, Any]:
    profile = load_profile(db, profile_id)
    grant = load_grant(db, principal, profile_id)
    grant = state.execution.policy.require_grant(principal, profile, grant)

    panels = {
        "sessions": _panel(
            state, db, principal, profile, grant, "dba.sessions", {"row_limit": 100}
        ),
        "blocking": _panel(state, db, principal, profile, grant, "dba.blocking", {}),
        "tablespaces": _panel(state, db, principal, profile, grant, "dba.tablespace_usage", {}),
        "invalidObjects": _panel(
            state,
            db,
            principal,
            profile,
            grant,
            "dba.invalid_objects",
            {"owner": None, "row_limit": 100},
        ),
        "schedulerJobs": _panel(
            state, db, principal, profile, grant, "dba.scheduler_jobs", {"row_limit": 100}
        ),
        "schedulerFailures": _panel(
            state, db, principal, profile, grant, "dba.scheduler_failures", {"row_limit": 100}
        ),
    }
    unavailable = [name for name, panel in panels.items() if not panel.available]
    return {
        "profileId": profile_id,
        "collectedAt": utcnow().isoformat(),
        "connectionHealth": {
            "identityCheckedAt": (
                profile.identity_checked_at.isoformat() if profile.identity_checked_at else None
            ),
            "identity": profile.identity_json,
            "capabilities": [c.model_dump(by_alias=True) for c in _capability_views(profile)],
        },
        "panels": {name: panel.model_dump(by_alias=True) for name, panel in panels.items()},
        "unavailablePanels": unavailable,
        "note": (
            "AWR, ASH, ADDM, SQL Tuning Advisor and Real-Time SQL Monitoring are not "
            "collected. They carry edition and management-pack conditions that a "
            "technical privilege does not establish."
        ),
    }
