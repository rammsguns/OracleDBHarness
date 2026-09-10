"""The SQL worksheet and the PL/SQL workspace.

A worksheet session owns one Oracle connection and its transaction. Nothing else in
the harness borrows it: diagnostics, runbooks and compilation all open their own
sessions, so they can never commit work the user has not chosen to commit.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from harness_api.deps import CurrentUser, Db, State
from harness_api.execution import load_grant, load_profile
from harness_api.models import SavedScript
from harness_api.schemas import (
    CompileRequest,
    ExecuteRequest,
    ExecuteResponse,
    OpenSessionRequest,
    SavedScriptIn,
)
from harness_worker.types import BindParameter, ExecutionLimits

router = APIRouter(prefix="/api/v1", tags=["worksheet"])


@router.post("/worksheets", status_code=201)
def open_worksheet(
    payload: OpenSessionRequest, principal: CurrentUser, db: Db, state: State
) -> dict[str, Any]:
    profile = load_profile(db, payload.profile_id)
    grant = load_grant(db, principal, payload.profile_id)
    grant = state.execution.policy.require_grant(principal, profile, grant)
    session = state.execution.open_worksheet(db, principal, profile, grant)
    return {
        "session": session.describe(),
        "note": (
            "This session owns its Oracle connection until you commit, roll back, "
            "close it, or it expires while idle. Expiry rolls back uncommitted work."
        ),
    }


@router.get("/worksheets")
def list_worksheets(principal: CurrentUser, state: State) -> dict[str, Any]:
    return {"sessions": [s.describe() for s in state.execution.list_worksheets(principal)]}


@router.get("/worksheets/{session_id}")
def get_worksheet(session_id: str, principal: CurrentUser, state: State) -> dict[str, Any]:
    return {"session": state.execution.worksheet(principal, session_id).describe()}


@router.post("/worksheets/{session_id}/execute", response_model=ExecuteResponse)
def execute(
    session_id: str,
    payload: ExecuteRequest,
    principal: CurrentUser,
    db: Db,
    state: State,
) -> ExecuteResponse:
    """Run one SQL statement or one complete PL/SQL block."""

    session = state.execution.worksheet(principal, session_id)
    profile = load_profile(db, session.target_id)
    grant = load_grant(db, principal, session.target_id)
    grant = state.execution.policy.require_grant(principal, profile, grant)

    limits = None
    if payload.max_rows or payload.deadline_seconds:
        base = state.settings.default_limits
        limits = ExecutionLimits(
            maxRows=payload.max_rows or base.max_rows,
            maxResponseBytes=base.max_response_bytes,
            deadlineSeconds=payload.deadline_seconds or base.deadline_seconds,
            maxDbmsOutputBytes=base.max_dbms_output_bytes,
            lobPreviewBytes=base.lob_preview_bytes,
        )

    outcome, decision = state.execution.run_worksheet_statement(
        db,
        principal,
        profile,
        grant,
        session,
        statement=payload.statement,
        binds=[
            BindParameter(name=b.name, value=b.value, typeHint=b.type_hint) for b in payload.binds
        ],
        limits=limits,
        dedup_key=payload.idempotency_key,
    )
    return ExecuteResponse(
        outcome=outcome.model_dump(by_alias=True, mode="json"),
        policy=decision.as_dict(),
        session=session.describe(),
    )


@router.post("/worksheets/{session_id}/commit")
def commit(session_id: str, principal: CurrentUser, db: Db, state: State) -> dict[str, Any]:
    return state.execution.commit_worksheet(db, principal, session_id)


@router.post("/worksheets/{session_id}/rollback")
def rollback(session_id: str, principal: CurrentUser, db: Db, state: State) -> dict[str, Any]:
    return state.execution.rollback_worksheet(db, principal, session_id)


@router.post("/worksheets/{session_id}/cancel")
def cancel(session_id: str, principal: CurrentUser, db: Db, state: State) -> dict[str, Any]:
    """Ask Oracle to break the running call. Best effort, and the result says so."""

    return state.execution.cancel_worksheet(db, principal, session_id)


@router.delete("/worksheets/{session_id}")
def close(session_id: str, principal: CurrentUser, db: Db, state: State) -> dict[str, Any]:
    return state.execution.close_worksheet(db, principal, session_id)


# -- PL/SQL workspace ------------------------------------------------------------------


@router.post("/plsql/compile")
def compile_source(
    payload: CompileRequest, principal: CurrentUser, db: Db, state: State
) -> dict[str, Any]:
    profile = load_profile(db, payload.profile_id)
    grant = load_grant(db, principal, payload.profile_id)
    grant = state.execution.policy.require_grant(principal, profile, grant)
    outcome, decision = state.execution.compile_plsql(db, principal, profile, grant, payload.source)
    return {
        "outcome": outcome.model_dump(by_alias=True, mode="json"),
        "policy": decision.as_dict(),
        "compiled": not outcome.compiler_errors and outcome.state.value == "succeeded",
        "errors": [e.model_dump(by_alias=True) for e in outcome.compiler_errors],
        "note": (
            "Compilation ran in its own session. Oracle DDL commits, so this did not "
            "touch any transaction open in your worksheet."
        ),
    }


# -- saved scripts ----------------------------------------------------------------------


@router.get("/scripts")
def list_scripts(principal: CurrentUser, db: Db) -> dict[str, Any]:
    rows = db.scalars(
        select(SavedScript)
        .where(SavedScript.user_id == principal.user_id)
        .order_by(SavedScript.updated_at.desc())
    ).all()
    return {
        "scripts": [
            {
                "id": row.id,
                "name": row.name,
                "profileId": row.profile_id,
                "updatedAt": row.updated_at.isoformat(),
                "body": row.body,
            }
            for row in rows
        ]
    }


@router.post("/scripts", status_code=201)
def save_script(payload: SavedScriptIn, principal: CurrentUser, db: Db) -> dict[str, Any]:
    row = SavedScript(
        user_id=principal.user_id or "",
        profile_id=payload.profile_id,
        name=payload.name,
        body=payload.body,
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name}


@router.delete("/scripts/{script_id}")
def delete_script(script_id: str, principal: CurrentUser, db: Db) -> dict[str, Any]:
    from harness_worker.errors import NotFoundError

    row = db.get(SavedScript, script_id)
    if row is None or row.user_id != principal.user_id:
        raise NotFoundError("No such script.", detail={"scriptId": script_id})
    db.delete(row)
    db.commit()
    return {"deleted": True, "scriptId": script_id}
