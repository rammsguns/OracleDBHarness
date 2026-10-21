"""A request's lookups as evidence: what each one covered, and what it cannot show (Q3).

The claim checks and the lookup record sent with a diagnosis read the same request-local
facts the loop collected (``LookupFacts``): call id, lookup number, operation, the
parameters it ran with, status, rows returned and whether the result was cut. The model's
conclusions are never added to them, so an answer cannot become evidence for itself.

Two properties of a lookup decide whether a next step can learn more from it:

* **Coverage.** A result that was not truncated and returned fewer rows than its
  ``row_limit`` returned every row its parameters match: a larger limit or another page
  returns nothing more (trial 18, KINJ-02). One that reached its limit, or was cut by the
  harness's byte cap, may have more.
* **Scope in time.** Most lookups read the catalog or the instance as it is now. Current
  state cannot show whether an absent object was dropped or never existed (trial 18,
  DIAG-01); only a history source could, and none is offered. The scheduler's run log is
  history, but only what is retained: a counter higher than the rows it returns does not
  by itself say why the others are gone.

Nothing here knows a case or an expected answer.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from harness_api.copilot.support import evidence_map, record_entry

ROW_LIMIT = "row_limit"
ROW_OFFSET = "row_offset"

# Lookups whose rows are a retained log of past events, bounded by retention.
RETAINED_HISTORY = frozenset({"dba.scheduler_run_history", "dba.scheduler_failures"})
# Every other Kiwi lookup reads the present state of the catalog or the instance.


class Lookup(Protocol):
    @property
    def operation_id(self) -> str: ...
    @property
    def succeeded(self) -> bool: ...
    @property
    def columns(self) -> Sequence[str]: ...
    @property
    def rows(self) -> Sequence[Sequence[Any]]: ...
    @property
    def parameters(self) -> dict[str, Any]: ...
    @property
    def sequence(self) -> int: ...
    @property
    def row_count(self) -> int | None: ...
    @property
    def truncated(self) -> bool: ...


@dataclass(frozen=True)
class Coverage:
    """How much of what its parameters match a lookup returned."""

    state: str  # complete, empty, at_limit, truncated, failed
    rows: int
    limit: int | None

    @property
    def could_return_more(self) -> bool:
        return self.state in ("at_limit", "truncated")

    def describe(self) -> str:
        rows = f"{self.rows} row{'' if self.rows == 1 else 's'}"
        if self.state == "failed":
            return "did not return rows"
        if self.state == "truncated":
            return f"{rows}, truncated by the harness: more may exist"
        if self.state == "at_limit":
            return f"{rows}, the row_limit: more may exist"
        if self.state == "empty":
            return "no rows" + (f" (row_limit {self.limit})" if self.limit is not None else "")
        if self.limit is not None:
            return f"{rows}, complete (fewer than row_limit {self.limit}, not truncated)"
        return f"{rows}, complete (not truncated)"


def coverage(lookup: Lookup) -> Coverage:
    if not lookup.succeeded:
        return Coverage("failed", 0, None)
    rows = len(lookup.rows) if lookup.row_count is None else lookup.row_count
    limit = lookup.parameters.get(ROW_LIMIT)
    limit = limit if isinstance(limit, int) and not isinstance(limit, bool) else None
    if lookup.truncated:
        return Coverage("truncated", rows, limit)
    if limit is not None and rows >= limit:
        return Coverage("at_limit", rows, limit)
    if rows == 0:
        return Coverage("empty", rows, limit)
    return Coverage("complete", rows, limit)


def is_current_state(operation_id: str) -> bool:
    return operation_id not in RETAINED_HISTORY


def result_note(operation_id: str, row_count: int, row_limit: Any, truncated: bool) -> str:
    """The harness's line on a lookup result's coverage, for the model.

    Said by the harness, outside the result's UNTRUSTED markers, so the model reads how
    much it got rather than inferring it.
    """

    if truncated or not isinstance(row_limit, int) or isinstance(row_limit, bool):
        return ""
    if row_count >= row_limit:
        return (
            f"[harness] Coverage: {row_count} row(s), the row_limit of {row_limit}: more rows "
            "may match; a larger row_limit (or the next page, where row_offset is offered) "
            "could return them."
        )
    note = (
        f"[harness] Coverage: complete. {row_count} row(s), fewer than the row_limit of "
        f"{row_limit}, not truncated: this is every row these parameters match, and a "
        "larger row_limit or another page returns no more."
    )
    if operation_id in RETAINED_HISTORY:
        note += (
            " A log holds only what is retained: a count elsewhere that is higher does "
            "not show why other entries are absent."
        )
    return note


def lookup_record(
    lookups: Iterable[Lookup], refused: Iterable[tuple[str, str]] = ()
) -> dict[str, Any]:
    """The record of a diagnosis's lookups, from the facts, not the model's text.

    One entry per numbered lookup: call, operation, the parameters it ran with, and
    coverage. For the lookups whose rows are the facts a diagnosis rests on (object
    status, compiler error locations, who waits on whom, which source lines were read),
    ``facts`` states them from the rows, so the user can compare the answer with what
    was returned rather than with the model's copy of it (C24). Other rows are not
    repeated. ``subjects`` and ``sourceRead`` are the evidence map's view of the lookup
    (``support.py``): the objects it is about, and per unit read the lines and members
    returned. ``refused`` lists calls the budget stopped, by operation and reason. Sent
    as its own event, so the answer stays the model's words and this the harness's.
    """

    ordered = sorted(lookups, key=lambda item: item.sequence)
    mapped = evidence_map(ordered)
    entries = []
    for lookup in ordered:
        seen = coverage(lookup)
        entries.append(
            {
                "lookup": lookup.sequence,
                "callId": getattr(lookup, "call_id", ""),
                "operationId": lookup.operation_id,
                "parameters": {
                    name: _plain(value)
                    for name, value in lookup.parameters.items()
                    if value is not None and value != ""
                },
                "coverage": seen.state,
                "rows": seen.rows,
                "rowLimit": seen.limit,
                "summary": seen.describe(),
                "facts": facts(lookup),
                # The evidence map's identities and source coverage (C24).
                **(
                    record_entry(mapped[lookup.sequence])
                    if lookup.sequence in mapped
                    else {"subjects": [], "sourceRead": []}
                ),
            }
        )
    return {
        "lookups": entries,
        "notRun": [{"operationId": op, "reason": reason} for op, reason in refused],
    }


_MAX_FACTS = 10


def facts(lookup: Lookup) -> list[str]:
    """The diagnostic facts in a lookup's rows, as the harness states them."""

    if not lookup.succeeded or not lookup.rows:
        return []
    columns = [str(c).upper() for c in lookup.columns]

    def get(row: Sequence[Any], name: str) -> Any:
        return row[columns.index(name)] if name in columns else None

    params = lookup.parameters
    target = ".".join(str(params[k]).upper() for k in ("owner", "object_name") if params.get(k))
    if params.get("object_type"):
        target += f" {str(params['object_type']).upper()}"
    rows = lookup.rows[:_MAX_FACTS]
    stated: list[str] = []
    operation = lookup.operation_id
    if operation == "schema.object_status":
        for row in rows:
            stated.append(
                f"{get(row, 'OWNER')}.{get(row, 'OBJECT_NAME')} {get(row, 'OBJECT_TYPE')}: "
                f"{get(row, 'STATUS')}"
            )
    elif operation == "schema.object_errors":
        for row in rows:
            stated.append(
                f"{target}: line {get(row, 'LINE')}, position {get(row, 'POSITION')}: "
                f"{_plain(get(row, 'TEXT'))}"
            )
    elif operation == "dba.blocking":
        for row in rows:
            stated.append(
                f"session {get(row, 'WAITING_SID')} waits on session "
                f"{get(row, 'BLOCKING_SID')} (blocker {get(row, 'BLOCKING_STATUS')})"
            )
    elif operation in ("schema.object_source", "schema.object_source_range"):
        lines = [get(row, "LINE") for row in lookup.rows]
        numbers = [n for n in lines if isinstance(n, int) and not isinstance(n, bool)]
        if numbers:
            stated.append(
                f"{target}: {len(numbers)} line(s) read, lines {min(numbers)}-{max(numbers)}"
                + (", not contiguous" if max(numbers) - min(numbers) + 1 != len(numbers) else "")
            )
    elif operation == "schema.object_source_search":
        for row in rows:
            stated.append(f"match: {get(row, 'NAME')} {get(row, 'TYPE')} line {get(row, 'LINE')}")
    if len(lookup.rows) > _MAX_FACTS and operation != "schema.object_source":
        stated.append(f"... and {len(lookup.rows) - _MAX_FACTS} more row(s)")
    return stated


def _plain(value: Any) -> Any:
    if isinstance(value, bool | int | float):
        return value
    text = str(value).replace("\n", " ")
    return text if len(text) <= 200 else text[:197] + "..."
