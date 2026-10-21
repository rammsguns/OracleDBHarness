"""Kiwi's tools: reviewed read catalog entries, run as the requesting user.

The toolbox is the only way a model request reaches a database, and it adds nothing to
what the user could already do. Each tool is a catalog entry carrying
``-- @kiwi: allowed`` and ``risk: read``; there is no free-form SQL, and no runbook,
worksheet, compile or commit. Every call is authorised afresh through
``ExecutionService`` -- grant, permission, capability, version -- and leaves an
``Execution`` row and an ``AuditEvent`` like the console's panels do. A refusal comes
back to the model as a typed error it can report; it never falls back to anything.

What the model gets back is data: rows serialised as JSON inside UNTRUSTED markers,
capped in rows and bytes, with the cap stated when it applies.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from harness_api.config import Settings
from harness_api.copilot.provider import ToolCall, ToolResult, ToolSpec
from harness_api.execution import ExecutionService, load_grant, load_profile
from harness_api.models import Execution, new_id
from harness_api.security import Principal
from harness_worker.catalog import CatalogEntry
from harness_worker.errors import HarnessError, NotFoundError, PolicyError, ValidationError
from harness_worker.types import RiskClass

# Parameters bound as numbers; everything else is text.
_INTEGER_PARAMETERS = frozenset(
    {"child_number", "end_line", "row_limit", "row_offset", "start_line"}
)
# Row limits the model may ask for are clamped to the per-tool cap.
_ROW_LIMIT_PARAMETER = "row_limit"
_WHY = "why"
# How the reviewed queries match each parameter, so the model does not spend lookups
# guessing: names are compared exactly after upper-casing, and only the filters are
# LIKE patterns. Keep these in step with oracle/diagnostics.
_NAME = "Exact name, matched upper-cased (no wildcards)."
_PARAMETER_DESCRIPTIONS = {
    "owner": "Schema that owns the object. " + _NAME,
    "object_name": _NAME,
    "table_name": _NAME,
    "package_name": _NAME,
    "job_name": _NAME,
    "chain_name": _NAME,
    "program_name": _NAME,
    "object_type": (
        "One object type, exactly, such as TABLE, VIEW, PACKAGE BODY or SYNONYM; "
        "not a list. Leave it out for every type."
    ),
    "name_filter": (
        "LIKE pattern on the object name, matched upper-cased: use % as the wildcard "
        "(EMP% or %EMPLOYEE%). Without % it matches one exact name."
    ),
    "text_filter": "LIKE pattern on the SQL text, case-insensitive: use % as the wildcard.",
    "source_filter": (
        "LIKE pattern on one line of source, case-insensitive: use % as the wildcard "
        "(%CHECK_RULE_042% or %PROCEDURE TRANSFORM%). Without % it matches a whole line."
    ),
    "sql_id": "The cursor's SQL_ID, exactly as reported (case-sensitive).",
    "child_number": "The cursor's child number.",
    "statement_id": "The plan's STATEMENT_ID, exactly.",
    "row_offset": "Rows to skip, for the next page.",
    "row_limit": "Most rows to return; capped by the harness.",
    "start_line": "First source line to return.",
    "end_line": "Last source line to return.",
}
# An empty result is an answer; say so where the model reads it.
_NO_ROWS = "No rows: nothing matched these parameters as given."
_MAX_TEXT_PARAMETER = 512
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _source_view(entry: CatalogEntry) -> str:
    """The dictionary view an entry reads, from its privilege header."""

    for privilege in entry.privileges:
        if privilege.upper().startswith("SELECT ON "):
            return privilege[len("SELECT on ") :].strip()
    return "its dictionary view"


def _object_types_description(entry: CatalogEntry) -> str:
    text = (
        f"One type of stored source, exactly: {', '.join(entry.object_types)} (the TYPE "
        f"values {_source_view(entry)} can hold; JAVA SOURCE is Java text, LIBRARY a "
        "library's definition and MLE MODULE JavaScript, not PL/SQL; MLE MODULE exists "
        "from Oracle 23ai only and returns no rows on 19c). "
        f"{_source_view(entry)} holds no TABLE, VIEW, SYNONYM, INDEX or SEQUENCE, so "
        "this lookup cannot return a view's query; any other type is refused."
    )
    if "object_type" not in entry.required_parameters:
        text += " Leave it out for every type."
    return text


def tool_name_for(operation_id: str) -> str:
    """``schema.object_status`` -> ``schema__object_status``; provider names allow no dot."""

    return operation_id.replace(".", "__")


@dataclass
class ToolOutcome:
    """What one tool call did. ``content`` goes to the model; the rest is the record."""

    call_id: str
    tool_name: str
    operation_id: str
    status: str  # succeeded, failed, refused, invalid
    parameters: dict[str, Any] = field(default_factory=dict)
    why: str = ""
    execution_id: str | None = None
    row_count: int | None = None
    result_bytes: int = 0
    truncated: bool = False
    error_code: str = ""
    content: str = ""
    # The rows the model was sent, for callers that read results themselves (the K-6
    # explainer). Never put in the stream event.
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    # The 1-based position of this lookup in its request; set by the caller.
    sequence: int = 0

    @property
    def is_error(self) -> bool:
        return self.status != "succeeded"

    def tool_result(self) -> ToolResult:
        return ToolResult(call_id=self.call_id, content=self.content, is_error=self.is_error)

    def event(self) -> dict[str, Any]:
        """The ``tool_result`` stream event. It never carries the rows."""

        return {
            "callId": self.call_id,
            "operationId": self.operation_id,
            "status": self.status,
            "rowCount": self.row_count,
            "bytes": self.result_bytes,
            "truncated": self.truncated,
            "executionId": self.execution_id,
            "errorCode": self.error_code,
        }

    def evidence(self, max_bytes: int, *, target: str = "", schema: str = "") -> dict[str, Any]:
        """What the model was shown by this lookup, for evaluation review (opt-in).

        The content is the catalog result already sent to the model, cut to
        ``max_bytes`` with the cut stated. Lookups are reviewed catalog queries, so
        no application result rows, bind values or credentials can be in it. Errors,
        refusals and truncation are kept explicit: a missing result is not an empty one.
        """

        encoded = self.content.encode("utf-8")
        cut = len(encoded) > max_bytes
        shown = encoded[:max_bytes].decode("utf-8", errors="ignore") if cut else self.content
        lines = self._line_range()
        return {
            "lookup": self.sequence,
            "callId": self.call_id,
            "operationId": self.operation_id,
            "status": self.status,
            "errorCode": self.error_code,
            "target": target,
            "schema": self.parameters.get("owner") or schema,
            "parameters": {k: v for k, v in self.parameters.items() if v is not None},
            "why": self.why,
            "rowCount": self.row_count,
            "lookupTruncated": self.truncated,
            "sourceLines": lines,
            "resultBytes": self.result_bytes,
            "contentTruncated": cut,
            "content": shown,
        }

    def _line_range(self) -> list[int] | None:
        """First and last source line returned, when the lookup returned numbered lines."""

        upper = [str(c).upper() for c in self.columns]
        for name in ("LINE", "START_LINE"):
            if name in upper:
                index = upper.index(name)
                numbers = [r[index] for r in self.rows if isinstance(r[index], int)]
                if numbers:
                    return [min(numbers), max(numbers)]
        return None


class KiwiToolbox:
    def __init__(self, execution: ExecutionService, settings: Settings) -> None:
        self._execution = execution
        self._settings = settings
        entries = [
            entry
            for entry in execution.catalog.list()
            if entry.kiwi and entry.risk == RiskClass.READ
        ]
        self._entries = {tool_name_for(entry.operation_id): entry for entry in entries}
        for name in self._entries:
            if not _TOOL_NAME.match(name):  # pragma: no cover - catalog ids are short
                raise ValueError(f"Catalog id maps to an invalid tool name: {name!r}")

    # -- what the model is offered ---------------------------------------------------

    def specs(self) -> list[ToolSpec]:
        return [self._spec(name, entry) for name, entry in sorted(self._entries.items())]

    def entries(self) -> list[CatalogEntry]:
        return [self._entries[name] for name in sorted(self._entries)]

    def entry_for(self, tool_name: str) -> CatalogEntry | None:
        return self._entries.get(tool_name)

    def _spec(self, name: str, entry: CatalogEntry) -> ToolSpec:
        properties: dict[str, Any] = {}
        for parameter in entry.parameters:
            if parameter in _INTEGER_PARAMETERS:
                properties[parameter] = {"type": "integer", "minimum": 0}
            else:
                properties[parameter] = {"type": "string", "maxLength": _MAX_TEXT_PARAMETER}
            if parameter == "object_type" and entry.object_types:
                properties[parameter]["description"] = _object_types_description(entry)
            elif parameter in _PARAMETER_DESCRIPTIONS:
                properties[parameter]["description"] = _PARAMETER_DESCRIPTIONS[parameter]
        properties[_WHY] = {
            "type": "string",
            "description": "One sentence: what you expect this lookup to tell you.",
            "maxLength": _MAX_TEXT_PARAMETER,
        }
        description = entry.title
        if entry.description:
            description += ". " + entry.description
        if entry.required_parameters:
            description += f" Requires {', '.join(entry.required_parameters)}."
        description += f" Needs {', '.join(entry.privileges) or 'no extra privilege'}."
        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if entry.required_parameters:
            schema["required"] = list(entry.required_parameters)
        return ToolSpec(name=name, description=description, input_schema=schema)

    # -- running one call --------------------------------------------------------------

    def run(
        self, db: Session, principal: Principal, profile_id: str, call: ToolCall
    ) -> ToolOutcome:
        """Run one tool call as ``principal`` on ``profile_id``. Never raises HarnessError."""

        entry = self._entries.get(call.name)
        raw = call.input if isinstance(call.input, dict) else {}
        why = raw.get(_WHY)
        outcome = ToolOutcome(
            call_id=call.id,
            tool_name=call.name,
            operation_id=entry.operation_id if entry else "",
            status="refused",
            why=why[:_MAX_TEXT_PARAMETER] if isinstance(why, str) else "",
        )

        if entry is None:
            # Unknown names, write operations and anything not marked for Kiwi all
            # arrive here. The model gets a typed refusal, never a substitute.
            error: HarnessError = PolicyError(
                f"{call.name!r} is not a tool Kiwi can use. Only reviewed read-only "
                "catalog lookups are available.",
                detail={"tool": call.name},
            )
            self._execution.audit_refusal(
                db, principal, operation_id=call.name[:120], profile_id=profile_id, error=error
            )
            return self._error(outcome, "refused", error)

        try:
            parameters = self._parameters(entry, raw)
        except ValidationError as exc:
            # Keep what was asked for, so a review sees the call that was refused.
            outcome.parameters = {
                name: value[:_MAX_TEXT_PARAMETER] if isinstance(value, str) else value
                for name, value in raw.items()
                if name in entry.parameters and isinstance(value, str | int)
            }
            return self._error(outcome, "invalid", exc)
        outcome.parameters = parameters

        cap = self._settings.kiwi_max_rows_per_tool
        execution_id = new_id("exe")
        try:
            profile = load_profile(db, profile_id)
            grant = self._execution.policy.require_grant(
                principal, profile, load_grant(db, principal, profile_id)
            )
            result = self._execution.run_catalog_operation(
                db,
                principal,
                profile,
                grant,
                entry.operation_id,
                parameters,
                row_limit=cap,
                execution_id=execution_id,
            )
        except HarnessError as exc:
            if db.get(Execution, execution_id) is not None:
                # It got as far as running; the Execution row is its record.
                outcome.execution_id = execution_id
                return self._error(outcome, "failed", exc)
            self._execution.audit_refusal(
                db,
                principal,
                operation_id=entry.operation_id,
                profile_id=None if isinstance(exc, NotFoundError) else profile_id,
                error=exc,
            )
            return self._error(outcome, "refused", exc)

        run = result.outcome
        outcome.execution_id = run.execution_id
        if run.state.value != "succeeded":
            error_dict = run.error or {}
            outcome.status = "failed"
            outcome.error_code = str(error_dict.get("code", "oracle_error"))
            body = {"error": error_dict or {"code": outcome.error_code}}
            outcome.content = self._wrap(entry.operation_id, json.dumps(body, default=str))
            outcome.result_bytes = len(outcome.content.encode("utf-8"))
            return outcome

        rs = run.result_set
        columns = [c.name for c in rs.columns] if rs else []
        rows = list(rs.rows) if rs else []
        truncated = bool(rs and rs.truncated)
        payload, rows_sent, byte_capped = self._serialise(columns, rows)
        outcome.status = "succeeded"
        outcome.row_count = rows_sent
        outcome.columns = columns
        outcome.rows = [list(row) for row in rows[:rows_sent]]
        outcome.truncated = truncated or byte_capped
        note = ""
        if outcome.truncated:
            note = (
                f"\n(truncated: {rows_sent} row(s) shown; the lookup is capped at "
                f"{cap} rows and {self._settings.kiwi_max_result_bytes} bytes)"
            )
        elif not rows:
            note = f"\n({_NO_ROWS})"
        outcome.content = self._wrap(entry.operation_id, payload + note)
        outcome.result_bytes = len(outcome.content.encode("utf-8"))
        return outcome

    def _parameters(self, entry: CatalogEntry, raw: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(raw) - set(entry.parameters) - {_WHY})
        if unknown:
            raise ValidationError(
                f"{entry.operation_id} does not take the parameter(s) {', '.join(unknown)}.",
                detail={"unknown": unknown, "parameters": list(entry.parameters)},
            )
        # A required selector the query compares directly would bind NULL and match no
        # row, which reads as "nothing exists" when nothing was searched. Refuse the call
        # before it runs; never widen it to every schema or guess a value (C19).
        missing = [
            name
            for name in entry.required_parameters
            if raw.get(name) is None or (isinstance(raw.get(name), str) and not raw[name].strip())
        ]
        if missing:
            hint = (
                " An omitted owner is not a search of every schema; name the owner"
                " (schema.list_schemas lists the visible ones)."
                if "owner" in missing
                else ""
            )
            raise ValidationError(
                f"{entry.operation_id} requires {', '.join(missing)}; the lookup did not "
                f"run.{hint}",
                detail={"missing": missing, "required": list(entry.required_parameters)},
            )
        self._check_object_type(entry, raw.get("object_type"))
        parameters: dict[str, Any] = {}
        for name in entry.parameters:
            value = raw.get(name)
            if value is None or value == "":
                parameters[name] = None
                continue
            if name in _INTEGER_PARAMETERS:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValidationError(
                        f"{name} must be a non-negative integer.", detail={"parameter": name}
                    )
                if name == _ROW_LIMIT_PARAMETER:
                    value = min(value, self._settings.kiwi_max_rows_per_tool)
            elif not isinstance(value, str) or len(value) > _MAX_TEXT_PARAMETER:
                raise ValidationError(
                    f"{name} must be a string of at most {_MAX_TEXT_PARAMETER} characters.",
                    detail={"parameter": name},
                )
            if name == "object_type" and entry.object_types and isinstance(value, str):
                # The check above compared the trimmed value; bind what it accepted.
                value = value.strip()
            parameters[name] = value
        if _ROW_LIMIT_PARAMETER in entry.parameters and parameters[_ROW_LIMIT_PARAMETER] is None:
            parameters[_ROW_LIMIT_PARAMETER] = self._settings.kiwi_max_rows_per_tool
        return parameters

    @staticmethod
    def _check_object_type(entry: CatalogEntry, value: Any) -> None:
        """Refuse a type the entry's view cannot hold before it runs (C28).

        ALL_SOURCE holds stored program source only, so ``object_type='VIEW'`` would
        bind, match no row and read as "this view has no text". An omitted type stays
        valid wherever the query guards it.
        """

        if not entry.object_types or not isinstance(value, str) or not value.strip():
            return
        if value.strip().upper() in entry.object_types:
            return
        raise ValidationError(
            f"{entry.operation_id} cannot return object_type {value[:40]!r}: it reads "
            f"{_source_view(entry)}, which holds only {', '.join(entry.object_types)}. "
            "The lookup did not run. A view's query, a table's columns or a synonym's "
            "target are not stored source; no lookup here returns a view's query.",
            detail={"parameter": "object_type", "allowed": list(entry.object_types)},
        )

    def _serialise(self, columns: list[str], rows: list[list[Any]]) -> tuple[str, int, bool]:
        """JSON rows, dropping trailing rows until the result fits its byte cap."""

        limit = self._settings.kiwi_max_result_bytes
        kept = list(rows)
        while True:
            text = json.dumps({"columns": columns, "rows": kept}, default=str)
            if len(text.encode("utf-8")) <= limit or not kept:
                break
            # Halve until it fits; results are small enough that this is cheap.
            kept = kept[: len(kept) // 2]
        if len(text.encode("utf-8")) > limit:
            text = json.dumps({"columns": columns, "rows": []})
        return text, len(kept), len(kept) < len(rows)

    @staticmethod
    def _wrap(operation_id: str, body: str) -> str:
        return "\n".join(
            [
                f"----- BEGIN UNTRUSTED TOOL_RESULT {operation_id} -----",
                body,
                f"----- END UNTRUSTED TOOL_RESULT {operation_id} -----",
            ]
        )

    def _error(self, outcome: ToolOutcome, status: str, error: HarnessError) -> ToolOutcome:
        outcome.status = status
        outcome.error_code = error.code
        # Oracle error text can quote names the database holds, so it is data too.
        label = outcome.operation_id or "refusal"
        outcome.content = self._wrap(label, json.dumps({"error": error.as_dict()}, default=str))
        outcome.result_bytes = len(outcome.content.encode("utf-8"))
        return outcome


__all__ = ["KiwiToolbox", "ToolOutcome", "tool_name_for"]
