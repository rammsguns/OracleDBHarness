"""The tuning workbench.

The distinction this module exists to preserve: an *estimated* plan comes from the
optimizer for a statement that was never run, and *measured* values come from
counters Oracle recorded for executions that actually happened. They are never mixed
in one number, and a comparison always states which it is showing.

AWR, ASH, ADDM, SQL Tuning Advisor and Real-Time SQL Monitoring are not used here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from harness_api.deps import CurrentUser, Db, State
from harness_api.execution import load_grant, load_profile
from harness_api.models import DiagnosticObservation
from harness_api.schemas import ExplainRequest, ObservationIn
from harness_worker.errors import HarnessError, NotFoundError
from harness_worker.types import ExecutionOutcome, ExecutionState, utcnow

router = APIRouter(prefix="/api/v1/tuning", tags=["tuning"])


@router.post("/explain")
def explain(
    payload: ExplainRequest, principal: CurrentUser, db: Db, state: State
) -> dict[str, Any]:
    """Produce an estimated plan without executing the statement."""

    profile = load_profile(db, payload.profile_id)
    grant = load_grant(db, principal, payload.profile_id)
    grant = state.execution.policy.require_grant(principal, profile, grant)

    # The plan is written and read back inside one connection: PLAN_TABLE holds
    # session-private rows, so a read from anywhere else finds none.
    statement_id, outcome, rows_result = state.execution.explain_statement(
        db, principal, profile, grant, payload.statement
    )
    rs = rows_result.outcome.result_set if rows_result else None
    body: dict[str, Any] = {
        "statementId": statement_id,
        "collectedAt": utcnow().isoformat(),
        "kind": "estimated",
        "columns": [c.name for c in rs.columns] if rs else [],
        "rows": rs.rows if rs else [],
        "explainExecutionId": outcome.execution_id,
        "note": (
            "These are optimizer estimates for a statement that was not executed. "
            "They are not measurements and cannot show how long the statement takes."
        ),
    }
    if outcome.state != ExecutionState.SUCCEEDED:
        # No plan was produced. Saying so beats returning an empty one with no reason.
        body["explainState"] = outcome.state.value
        body["error"] = outcome.error
    return body


@router.get("/{profile_id}/cursors")
def search_cursors(
    profile_id: str,
    principal: CurrentUser,
    db: Db,
    state: State,
    sql_id: str | None = Query(default=None, alias="sqlId"),
    text_filter: str | None = Query(default=None, alias="textFilter"),
    limit: int = Query(default=25, ge=1, le=200),
) -> dict[str, Any]:
    """Find cached cursors. Reading them never re-runs the user statement."""

    profile = load_profile(db, profile_id)
    grant = load_grant(db, principal, profile_id)
    grant = state.execution.policy.require_grant(principal, profile, grant)
    result = state.execution.run_catalog_operation(
        db,
        principal,
        profile,
        grant,
        "tuning.cursor_search",
        {
            "sql_id": sql_id,
            "text_filter": f"%{text_filter.upper()}%" if text_filter else None,
            "row_limit": limit,
        },
    )
    return {
        "collectedAt": utcnow().isoformat(),
        **_measured_payload(result.outcome, "The cursor search did not complete."),
        "note": (
            "These counters were recorded by Oracle for executions that already "
            "happened. Fetching them does not execute anything."
        ),
    }


@router.get("/{profile_id}/cursors/{sql_id}")
def cursor_detail(
    profile_id: str,
    sql_id: str,
    principal: CurrentUser,
    db: Db,
    state: State,
    child_number: int = Query(default=0, alias="childNumber"),
) -> dict[str, Any]:
    """Statistics and, where the views are readable, the plan actually used."""

    profile = load_profile(db, profile_id)
    grant = load_grant(db, principal, profile_id)
    grant = state.execution.policy.require_grant(principal, profile, grant)

    statistics = state.execution.run_catalog_operation(
        db,
        principal,
        profile,
        grant,
        "tuning.cursor_statistics",
        {"sql_id": sql_id, "child_number": child_number},
    )
    plan: dict[str, Any]
    try:
        plan_result = state.execution.run_catalog_operation(
            db,
            principal,
            profile,
            grant,
            "tuning.cursor_plan",
            {"sql_id": sql_id, "child_number": child_number},
        )
    except HarnessError as exc:
        state.execution.audit_refusal(
            db, principal, operation_id="tuning.cursor_plan", profile_id=profile_id, error=exc
        )
        plan = {"available": False, "error": exc.as_dict()}
    else:
        plan = _plan_payload(plan_result.outcome)

    return {
        "sqlId": sql_id,
        "childNumber": child_number,
        "collectedAt": utcnow().isoformat(),
        "statistics": _measured_payload(
            statistics.outcome, "The cursor statistics query did not complete."
        ),
        "plan": plan,
    }


def _measured_payload(outcome: ExecutionOutcome, failure: str) -> dict[str, Any]:
    """Counters Oracle recorded, or the reason there are none to show.

    The same distinction ``_plan_payload`` keeps, for the rows rather than the plan.
    A query that failed returns no rows, and presenting those absent rows as measured
    data reads as "Oracle has recorded nothing for this cursor" -- a finding, where
    what happened was a failure. Only a succeeded execution is presented as measured.
    """

    if outcome.state != ExecutionState.SUCCEEDED:
        return {
            "kind": "measured",
            "available": False,
            "error": outcome.error
            or {
                "code": outcome.state.value,
                "message": failure,
                "detail": {"executionId": outcome.execution_id},
                "retryable": False,
            },
        }
    rs = outcome.result_set
    return {
        "kind": "measured",
        "available": True,
        "columns": [c.name for c in rs.columns] if rs else [],
        "rows": rs.rows if rs else [],
    }


def _plan_payload(outcome: ExecutionOutcome) -> dict[str, Any]:
    """Describe the cursor plan query, or say why there is no plan to show.

    A query that did not run has no rows, and reporting those absent rows as an
    available plan reads as "this cursor has no plan" rather than "we could not read
    it" -- the difference between a finding and a failure. Only a succeeded execution
    is presented as a plan.
    """

    if outcome.state != ExecutionState.SUCCEEDED:
        return {
            "available": False,
            "error": outcome.error
            or {
                "code": outcome.state.value,
                "message": "The plan query did not complete.",
                "detail": {"executionId": outcome.execution_id},
                "retryable": False,
            },
        }
    rows = outcome.result_set
    return {
        "available": True,
        "kind": "cached_cursor_plan",
        "columns": [c.name for c in rows.columns] if rows else [],
        "rows": rows.rows if rows else [],
        "rowSourceStatistics": False,
        "note": (
            "This is the plan Oracle used. Actual row counts per operation are not "
            "shown: they require statistics that are not collected by default."
        ),
    }


# -- observations ---------------------------------------------------------------------


@router.post("/observations", status_code=201)
def save_observation(
    payload: ObservationIn, principal: CurrentUser, db: Db, state: State
) -> dict[str, Any]:
    """Save a before or after observation so a change can be compared later."""

    profile = load_profile(db, payload.profile_id)
    grant = load_grant(db, principal, payload.profile_id)
    grant = state.execution.policy.require_grant(principal, profile, grant)
    row = DiagnosticObservation(
        user_id=principal.user_id or "",
        profile_id=payload.profile_id,
        label=payload.label,
        sql_id=payload.sql_id,
        child_number=payload.child_number,
        source=payload.source,
        measured_json=payload.measured,
        estimated_json=payload.estimated,
        context_json={
            **payload.context,
            "recordedAt": utcnow().isoformat(),
            "targetIdentity": profile.identity_json,
        },
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "label": row.label}


@router.get("/observations")
def list_observations(principal: CurrentUser, db: Db) -> dict[str, Any]:
    rows = db.scalars(
        select(DiagnosticObservation)
        .where(DiagnosticObservation.user_id == principal.user_id)
        .order_by(DiagnosticObservation.created_at.desc())
    ).all()
    return {
        "observations": [
            {
                "id": row.id,
                "label": row.label,
                "profileId": row.profile_id,
                "sqlId": row.sql_id,
                "childNumber": row.child_number,
                "source": row.source,
                "measured": row.measured_json,
                "estimated": row.estimated_json,
                "context": row.context_json,
                "createdAt": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }


@router.get("/observations/compare")
def compare_observations(
    principal: CurrentUser,
    db: Db,
    before_id: str = Query(alias="beforeId"),
    after_id: str = Query(alias="afterId"),
) -> dict[str, Any]:
    """Compare two saved observations, keeping estimates and measurements apart."""

    before = db.get(DiagnosticObservation, before_id)
    after = db.get(DiagnosticObservation, after_id)
    for row, name in ((before, before_id), (after, after_id)):
        if row is None or row.user_id != principal.user_id:
            raise NotFoundError("No such observation.", detail={"observationId": name})
    assert before is not None and after is not None

    caveats: list[str] = []
    if before.profile_id != after.profile_id:
        caveats.append(
            "The observations are from different targets, so the comparison does not "
            "isolate the change."
        )
    if before.sql_id and after.sql_id and before.sql_id != after.sql_id:
        caveats.append(
            "The observations are for different SQL IDs, so they are not the same statement."
        )
    if not before.measured_json or not after.measured_json:
        caveats.append(
            "At least one side has no measured values, so no improvement can be "
            "claimed from this comparison."
        )
    if before.context_json.get("dataSetLabel") != after.context_json.get("dataSetLabel"):
        caveats.append("The recorded data or statistics context differs between the two sides.")

    deltas: dict[str, Any] = {}
    for key in sorted(set(before.measured_json) | set(after.measured_json)):
        left, right = before.measured_json.get(key), after.measured_json.get(key)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            deltas[key] = {
                "before": left,
                "after": right,
                "delta": right - left,
                "kind": "measured",
            }
        else:
            deltas[key] = {"before": left, "after": right, "kind": "measured"}

    return {
        "before": {
            "id": before.id,
            "label": before.label,
            "createdAt": before.created_at.isoformat(),
        },
        "after": {
            "id": after.id,
            "label": after.label,
            "createdAt": after.created_at.isoformat(),
        },
        "measuredDeltas": deltas,
        "estimatedBefore": before.estimated_json,
        "estimatedAfter": after.estimated_json,
        "caveats": caveats,
        "conclusionPermitted": not caveats,
        "note": (
            "Estimated plan figures are shown separately and are never differenced "
            "against measured values. An improvement can only be claimed from measured "
            "values collected under equivalent conditions."
        ),
    }
