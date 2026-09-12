"""Execution and audit history.

A user sees their own executions. Audit events are restricted to administrators and
are never edited or deleted through the application.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from harness_api.deps import Administrator, CurrentUser, Db
from harness_api.models import AuditEvent, Execution
from harness_api.schemas import AuditView, ExecutionView
from harness_worker.errors import NotFoundError

router = APIRouter(prefix="/api/v1", tags=["history"])


def execution_view(row: Execution) -> ExecutionView:
    return ExecutionView(
        id=row.id,
        operationId=row.operation_id,
        profileId=row.profile_id,
        statementKind=row.statement_kind,
        riskClass=row.risk_class,
        state=row.state,
        policyDecision=row.policy_decision,
        policyReason=row.policy_reason,
        statementFingerprint=row.statement_fingerprint,
        rowsReturned=row.rows_returned,
        rowsAffected=row.rows_affected,
        truncated=row.truncated,
        elapsedMs=row.elapsed_ms,
        databaseElapsedMs=row.database_elapsed_ms,
        errorCode=row.error_code,
        errorMessage=row.error_message,
        startedAt=row.started_at.isoformat(),
        finishedAt=row.finished_at.isoformat() if row.finished_at else None,
        verification=row.verification_json or {},
    )


@router.get("/executions", response_model=list[ExecutionView])
def list_executions(
    principal: CurrentUser,
    db: Db,
    limit: int = Query(default=50, ge=1, le=500),
    profile_id: str | None = Query(default=None, alias="profileId"),
) -> list[ExecutionView]:
    query = select(Execution).where(Execution.user_id == principal.user_id)
    if profile_id:
        query = query.where(Execution.profile_id == profile_id)
    rows = db.scalars(query.order_by(Execution.started_at.desc()).limit(limit)).all()
    return [execution_view(row) for row in rows]


@router.get("/executions/{execution_id}", response_model=ExecutionView)
def get_execution(execution_id: str, principal: CurrentUser, db: Db) -> ExecutionView:
    row = db.get(Execution, execution_id)
    if row is None or row.user_id != principal.user_id:
        raise NotFoundError("No such execution.", detail={"executionId": execution_id})
    return execution_view(row)


@router.get("/audit", response_model=list[AuditView])
def list_audit(
    _: Administrator,
    db: Db,
    limit: int = Query(default=100, ge=1, le=1000),
    actor: str | None = None,
) -> list[AuditView]:
    query = select(AuditEvent)
    if actor:
        query = query.where(AuditEvent.actor_subject == actor)
    rows = db.scalars(query.order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return [
        AuditView(
            id=row.id,
            createdAt=row.created_at.isoformat(),
            actorSubject=row.actor_subject,
            integrationId=row.integration_id,
            profileId=row.profile_id,
            operationId=row.operation_id,
            executionId=row.execution_id,
            riskClass=row.risk_class,
            policyDecision=row.policy_decision,
            outcome=row.outcome,
            statementFingerprint=row.statement_fingerprint,
            affectedCounts=row.affected_counts or {},
        )
        for row in rows
    ]


@router.get("/copilot/history")
def copilot_history(
    principal: CurrentUser,
    db: Db,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """Copilot requests made by this actor.

    This covers copilot requests only. It is not a record of every database
    operation an IDE performed on its own; those stay with the IDE.
    """

    from harness_api.models import CopilotRequest

    rows = db.scalars(
        select(CopilotRequest)
        .where(CopilotRequest.actor_key == principal.actor_key)
        .order_by(CopilotRequest.created_at.desc())
        .limit(limit)
    ).all()
    return {
        "requests": [
            {
                "id": row.id,
                "action": row.action,
                "targetReference": row.target_reference,
                "provider": row.provider,
                "model": row.model,
                "contextCategories": row.context_categories,
                "contextBytes": row.context_bytes,
                "promptTokens": row.prompt_tokens,
                "completionTokens": row.completion_tokens,
                "latencyMs": row.latency_ms,
                "outcome": row.outcome,
                "errorCode": row.error_code,
                "createdAt": row.created_at.isoformat(),
            }
            for row in rows
        ],
        "note": (
            "Copilot requests only. Database operations an IDE performed by itself are "
            "recorded by that IDE, not here."
        ),
    }
