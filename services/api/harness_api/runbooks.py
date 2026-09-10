"""Controlled runbooks.

A runbook is a named operation with a fixed shape: declared parameters, a declared
risk class, and a verification step whose result is stored with the execution. A
mutating runbook shows the exact target and parameters before it runs and needs an
explicit confirmation; "it returned without error" is never accepted as evidence
that the change took effect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from harness_api.execution import ExecutionService
from harness_api.models import ConnectionProfile, RunbookDefinition, UserTargetGrant
from harness_api.security import Principal
from harness_worker.errors import HarnessError, NotFoundError
from harness_worker.types import RiskClass, utcnow


@dataclass(frozen=True)
class RunbookParameter:
    name: str
    label: str
    required: bool = True
    example: str = ""

    def describe(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "required": self.required,
            "example": self.example,
        }


@dataclass(frozen=True)
class RunbookSpec:
    id: str
    title: str
    description: str
    risk: RiskClass
    parameters: tuple[RunbookParameter, ...] = ()
    # A composite runbook runs several reviewed diagnostics and assembles a report.
    steps: tuple[str, ...] = ()
    # A mutating runbook issues one reviewed statement and then verifies it.
    operation_id: str = ""
    verification_operation_id: str = ""
    verification_parameters: tuple[str, ...] = ()

    @property
    def mutating(self) -> bool:
        return self.risk in (RiskClass.PERSISTENT_WRITE, RiskClass.ADMINISTRATIVE)

    def describe(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "risk": self.risk.value,
            "mutating": self.mutating,
            "requiresConfirmation": self.mutating,
            "parameters": [p.describe() for p in self.parameters],
            "steps": list(self.steps),
            "operationId": self.operation_id,
            "verificationOperationId": self.verification_operation_id,
        }


RUNBOOKS: tuple[RunbookSpec, ...] = (
    RunbookSpec(
        id="runbook.health_report",
        title="Collect health report",
        description=(
            "Runs the reviewed read-only diagnostics and assembles one report. Every "
            "panel records its collection time, and a panel that could not be "
            "collected says so rather than appearing empty and healthy."
        ),
        risk=RiskClass.READ,
        steps=(
            "dba.sessions",
            "dba.blocking",
            "dba.tablespace_usage",
            "dba.invalid_objects",
            "dba.scheduler_failures",
        ),
    ),
    RunbookSpec(
        id="runbook.recompile_object",
        title="Recompile one object",
        description=(
            "Recompiles a single named program unit and then reads its status back "
            "from the dictionary as verification."
        ),
        risk=RiskClass.ADMINISTRATIVE,
        parameters=(
            RunbookParameter("owner", "Schema", example="HARNESS_APP"),
            RunbookParameter("object_name", "Object name", example="EMPLOYEE_REPORT"),
            RunbookParameter("object_kind", "Object kind", example="PACKAGE BODY"),
        ),
        operation_id="runbook.recompile_object",
        verification_operation_id="schema.object_status",
        verification_parameters=("owner", "object_name"),
    ),
    RunbookSpec(
        id="runbook.gather_table_stats",
        title="Gather statistics for one table",
        description=(
            "Collects optimizer statistics for a single named table, then reads the "
            "recorded row count and collection time back as verification. This can "
            "change execution plans."
        ),
        risk=RiskClass.ADMINISTRATIVE,
        parameters=(
            RunbookParameter("owner", "Schema", example="HARNESS_APP"),
            RunbookParameter("table_name", "Table", example="ORDER_LINES"),
        ),
        operation_id="runbook.gather_table_stats",
        verification_operation_id="schema.table_statistics",
        verification_parameters=("owner", "table_name"),
    ),
)

BY_ID = {spec.id: spec for spec in RUNBOOKS}


def get_spec(runbook_id: str) -> RunbookSpec:
    spec = BY_ID.get(runbook_id)
    if spec is None:
        raise NotFoundError(
            f"No runbook is registered as {runbook_id!r}.", detail={"runbookId": runbook_id}
        )
    return spec


def register_definitions(db: Session, service: ExecutionService) -> int:
    """Mirror the runbook specs into the metadata store so they can be audited."""

    for spec in RUNBOOKS:
        capabilities: list[str] = []
        if spec.operation_id:
            capabilities = [c.value for c in service.catalog.get(spec.operation_id).capabilities]
        row = db.get(RunbookDefinition, spec.id)
        if row is None:
            row = RunbookDefinition(id=spec.id)
            db.add(row)
        row.title = spec.title
        row.description = spec.description
        row.risk_class = spec.risk.value
        row.mutating = spec.mutating
        row.capabilities = capabilities
        row.parameters = [p.name for p in spec.parameters]
        row.verification_operation_id = spec.verification_operation_id
    db.commit()
    return len(RUNBOOKS)


@dataclass
class RunbookRun:
    runbook: RunbookSpec
    started_at: str
    finished_at: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    outcome: str = "succeeded"

    def as_dict(self) -> dict:
        return {
            "runbook": self.runbook.describe(),
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "outcome": self.outcome,
            "steps": self.steps,
            "verification": self.verification,
        }


class RunbookService:
    def __init__(self, execution: ExecutionService) -> None:
        self._execution = execution

    def list(self) -> list[dict]:
        return [spec.describe() for spec in RUNBOOKS]

    def preview(self, spec: RunbookSpec, parameters: dict[str, Any]) -> dict:
        """What exactly will run, before anyone is asked to confirm it."""

        missing = [p.name for p in spec.parameters if p.required and not parameters.get(p.name)]
        return {
            "runbook": spec.describe(),
            "parameters": {p.name: parameters.get(p.name) for p in spec.parameters},
            "missingParameters": missing,
            "willChangeDatabase": spec.mutating,
            "requiresConfirmation": spec.mutating,
            "ready": not missing,
        }

    def run(
        self,
        db: Session,
        principal: Principal,
        profile: ConnectionProfile,
        grant: UserTargetGrant,
        spec: RunbookSpec,
        parameters: dict[str, Any],
        *,
        confirm: bool = False,
    ) -> RunbookRun:
        run = RunbookRun(runbook=spec, started_at=utcnow().isoformat())
        if spec.steps:
            self._run_composite(db, principal, profile, grant, spec, run)
        else:
            self._run_mutation(db, principal, profile, grant, spec, parameters, run, confirm)
        run.finished_at = utcnow().isoformat()
        return run

    def _run_composite(
        self,
        db: Session,
        principal: Principal,
        profile: ConnectionProfile,
        grant: UserTargetGrant,
        spec: RunbookSpec,
        run: RunbookRun,
    ) -> None:
        degraded = False
        for operation_id in spec.steps:
            entry = self._execution.catalog.get(operation_id)
            step: dict[str, Any] = {
                "operationId": operation_id,
                "title": entry.title,
                "collectedAt": utcnow().isoformat(),
            }
            try:
                result = self._execution.run_catalog_operation(
                    db,
                    principal,
                    profile,
                    grant,
                    operation_id,
                    _default_parameters(entry.parameters),
                )
                outcome = result.outcome
                step["state"] = outcome.state.value
                step["rowCount"] = outcome.result_set.row_count if outcome.result_set else 0
                step["columns"] = (
                    [c.name for c in outcome.result_set.columns] if outcome.result_set else []
                )
                step["rows"] = outcome.result_set.rows if outcome.result_set else []
                step["truncated"] = bool(outcome.result_set and outcome.result_set.truncated)
                step["available"] = outcome.state.value == "succeeded"
            except HarnessError as exc:
                degraded = True
                step["state"] = "unavailable"
                step["available"] = False
                step["error"] = exc.as_dict()
            run.steps.append(step)
        run.outcome = "degraded" if degraded else "succeeded"

    def _run_mutation(
        self,
        db: Session,
        principal: Principal,
        profile: ConnectionProfile,
        grant: UserTargetGrant,
        spec: RunbookSpec,
        parameters: dict[str, Any],
        run: RunbookRun,
        confirm: bool,
    ) -> None:
        result = self._execution.run_catalog_operation(
            db,
            principal,
            profile,
            grant,
            spec.operation_id,
            parameters,
            confirm=confirm,
        )
        run.steps.append(
            {
                "operationId": spec.operation_id,
                "title": result.entry.title,
                "state": result.outcome.state.value,
                "executionId": result.outcome.execution_id,
                "parameters": parameters,
                "elapsedMs": result.outcome.elapsed_ms,
                "warnings": result.outcome.warnings,
                "error": result.outcome.error,
            }
        )
        run.outcome = (
            "succeeded" if result.outcome.state.value == "succeeded" else result.outcome.state.value
        )

        if not spec.verification_operation_id:
            return
        verification_params = {name: parameters.get(name) for name in spec.verification_parameters}
        entry = self._execution.catalog.get(spec.verification_operation_id)
        for name in entry.parameters:
            verification_params.setdefault(name, None)
        try:
            verification = self._execution.run_catalog_operation(
                db,
                principal,
                profile,
                grant,
                spec.verification_operation_id,
                verification_params,
            )
            rows = verification.outcome.result_set.rows if verification.outcome.result_set else []
            columns = (
                [c.name for c in verification.outcome.result_set.columns]
                if verification.outcome.result_set
                else []
            )
            run.verification = {
                "operationId": spec.verification_operation_id,
                "collectedAt": utcnow().isoformat(),
                "columns": columns,
                "rows": rows,
                "observed": bool(rows),
            }
            if not rows:
                run.outcome = "unverified"
                run.verification["note"] = (
                    "The verification query returned no rows, so the change could not be confirmed."
                )
        except HarnessError as exc:
            run.outcome = "unverified"
            run.verification = {
                "operationId": spec.verification_operation_id,
                "collectedAt": utcnow().isoformat(),
                "observed": False,
                "error": exc.as_dict(),
                "note": "The operation ran but its result could not be verified.",
            }


def _default_parameters(names: tuple[str, ...]) -> dict[str, Any]:
    values: dict[str, Any] = {name: None for name in names}
    if "row_limit" in values:
        values["row_limit"] = 100
    if "row_offset" in values:
        values["row_offset"] = 0
    return values
