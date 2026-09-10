"""Controlled runbooks: preview, confirm, run, verify."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from harness_api.deps import CurrentUser, Db, State
from harness_api.execution import load_grant, load_profile
from harness_api.runbooks import get_spec
from harness_api.schemas import RunbookRunRequest

router = APIRouter(prefix="/api/v1/runbooks", tags=["runbooks"])


@router.get("")
def list_runbooks(_: CurrentUser, state: State) -> dict[str, Any]:
    return {
        "runbooks": state.runbooks.list(),
        "note": (
            "A mutating runbook shows its exact target and parameters first and needs "
            "an explicit confirmation. Its result is stored with verification evidence."
        ),
    }


@router.post("/{runbook_id}/preview")
def preview(
    runbook_id: str,
    payload: RunbookRunRequest,
    principal: CurrentUser,
    db: Db,
    state: State,
) -> dict[str, Any]:
    """Exactly what would run, before anyone confirms it."""

    spec = get_spec(runbook_id)
    profile = load_profile(db, payload.profile_id)
    grant = load_grant(db, principal, payload.profile_id)
    grant = state.execution.policy.require_grant(principal, profile, grant)
    preview = state.runbooks.preview(spec, payload.parameters)
    preview["target"] = {
        "profileId": profile.id,
        "name": profile.name,
        "environment": profile.environment,
        "identity": profile.identity_json,
    }
    return preview


@router.post("/{runbook_id}/run")
def run(
    runbook_id: str,
    payload: RunbookRunRequest,
    principal: CurrentUser,
    db: Db,
    state: State,
) -> dict[str, Any]:
    spec = get_spec(runbook_id)
    profile = load_profile(db, payload.profile_id)
    grant = load_grant(db, principal, payload.profile_id)
    grant = state.execution.policy.require_grant(principal, profile, grant)
    result = state.runbooks.run(
        db, principal, profile, grant, spec, payload.parameters, confirm=payload.confirm
    )
    return result.as_dict()
