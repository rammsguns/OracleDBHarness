"""Next checks as typed steps the harness validates and renders (C34).

Trials 19 and 20 failed answers on next steps whose prose credited a lookup with a
result it cannot return: an owner the account cannot see, a ``VIEW`` read from
ALL_SOURCE, a source-text search said to count every rule, and a single ``PACKAGE BODY``
lookup on ``ORDER_FEED.EXPORT_DAY`` said to settle which object that two-part name is.
Each prose rule for one of these (C29, C33) matched the wording of one answer. Here a
next check is data instead:

    ```kiwi-next-checks
    [{"actor": "this_account", "operation": "schema.object_status",
      "selectors": {"owner": "HARNESS_APP", "object_name": "ORDER_FEED"},
      "establishes": "name_reading",
      "name": {"text": "ORDER_FEED.EXPORT_DAY", "resolved_in": "HARNESS_APP"},
      "about": "whether the job action names a package in the job owner's schema"}]
    ```

``parse`` reads the block; ``validate`` checks each step against the reviewed catalog
and this request's lookups; ``render`` writes the accepted steps from the operation's
declared result, not from the model's claim, and lists the declined ones with why.

A step names:

- ``actor``: ``this_account`` (the account that made this request's lookups),
  ``account_with_access`` or ``dba`` (with ``access``: what it can see that this one
  cannot), or ``requester`` (the person asking, for a fact outside the lookups).
- ``operation`` and ``selectors``: a Kiwi-allowed catalog entry and its parameters,
  with the required ones given and ``object_type`` among those its view holds. A
  requester step has no operation.
- ``establishes``: a goal from ``GOALS`` that this operation can return
  (``OPERATION_GOALS``). The rendered step states that goal's fact, filled from the
  selectors; a goal the operation cannot return is declined, whatever the prose says.
- ``about``: the open question in the model's words. It is rendered, labelled as the
  model's interpretation, only when it is one plain clause on one line that asks
  (whether, which, what ...) and claims no result; otherwise it stays in the retained
  proposal and is not rendered (C34-B). ``access`` is held to the same plain form
  and rendered as the model's description, not checked; the actor text stays
  harness-owned.

Dotted names. ``A.B`` in a PL/SQL call is, among the forms checked here, either the
standalone ``PROCEDURE``/``FUNCTION`` ``B`` in schema ``A`` or member ``B`` of package
``A`` in the schema the name resolves in. A ``name_reading`` step must test one of those
readings. A ``name_resolution`` step (which object the name is) needs ``resolved_in``
and is accepted only when this request's complete lookups already exclude every other
reading; a lookup that
tests one reading cannot tell which object Oracle would use. Synonyms and type methods
are other readings these lookups do not test, so a resolution says so.

Nothing here is keyed to a case: the goals and their facts come from the catalog's
views, and the readings from how Oracle resolves a two-part name.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from harness_api.copilot.evidence import coverage
from harness_api.copilot.fences import fenced_blocks
from harness_api.copilot.support import heading_name, other_heading
from harness_worker.catalog import CatalogEntry, QueryCatalog, cached_catalog

LABEL = "kiwi-next-checks"
MAX_STEPS = 5
MAX_BLOCK = 8000
MAX_TEXT = 300
ACTORS = ("this_account", "account_with_access", "dba", "requester")
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
# A catalog operation id (schema.object_source) or its tool name (schema__object_source).
_OPERATION_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.|__)[a-z][a-z0-9_]*$")
# Selector keys a model has used to name the lookup instead of operation (trial 21).
_NAMING_SELECTORS = ("tool", "operation")
_PATTERN = re.compile(r"^[A-Za-z0-9_$#%. -]{1,200}$")
_STORED_TYPES = frozenset({"PROCEDURE", "FUNCTION"})
_PACKAGE_TYPES = frozenset({"PACKAGE", "PACKAGE BODY"})

# What each goal lets a step say it returns. {actor} is who sees it; the rest are
# selectors. A fact is what the operation's view holds for that actor, nothing more.
GOALS: dict[str, str] = {
    "schemas_visible": "the schemas owning at least one object {actor} can see",
    "object_listing": "the objects {actor} can see under owner {owner} matching {selectors}, "
    "with type and status",
    "object_status": "the status (VALID or INVALID) of {owner}.{object_name} as {actor} sees it",
    "invalid_objects": "the objects {actor} can see whose status is not VALID",
    "compile_errors": "the compiler errors ALL_ERRORS holds for {owner}.{object_name} "
    "visible to {actor}",
    "source_text": "the stored source lines of {owner}.{object_name} {object_type} visible "
    "to {actor}",
    "source_matches": "the stored source lines of {owner} whose text matches "
    "{source_filter}, up to the row limit; matching text, not a count of definitions",
    "spec_subprograms": "the subprograms declared in the specification of package "
    "{owner}.{package_name}; private body members are not listed",
    "all_definitions": "every definition in {owner}.{object_name}, but only from a "
    "complete result: a whole, untruncated source read, or PL/Scope's record with "
    "PLSCOPE_STATUS COLLECTED; a truncated or uncollected result is not a count",
    "identifier_uses": "the declarations, definitions, references and calls PL/Scope "
    "recorded for {owner}.{object_name}, if collected",
    "static_sql": "the static SQL PL/Scope recorded in {owner}.{object_name}, if collected",
    "dependencies": "the objects {owner}.{object_name} references, as {actor} sees them",
    "dependents": "the objects that reference {owner}.{object_name}, as {actor} sees them",
    "db_link_targets": "the remote objects {owner}'s objects reference through a database "
    "link, and whether each link is visible",
    "columns": "the columns of {owner}.{table_name}",
    "constraints": "the constraints of {owner}.{table_name}",
    "indexes": "the indexes of {owner}.{table_name}",
    "table_statistics": "the optimizer statistics recorded for {owner}.{table_name}",
    "triggers": "the triggers under owner {owner}{on_table}, with timing, event and status",
    "job_definition": "what scheduler job {owner}.{job_name} runs, its schedule and state",
    "job_list": "the scheduler jobs and their state",
    "job_runs": "recent runs of scheduler job {owner}.{job_name}, with status and error",
    "job_failures": "recent failed scheduler job runs",
    "program_action": "what scheduler program {owner}.{program_name} runs",
    "chain_steps": "the steps and rules of scheduler chain {owner}.{chain_name}",
    "sessions": "the connected user sessions and their waits",
    "blocking": "the sessions waiting on another session, with the holder",
    "tablespace_usage": "the used and allocated space per tablespace",
    "cursor_plan": "the plan Oracle used for cursor {sql_id} child {child_number}",
    "cursor_statistics": "the measured counters of SQL ID {sql_id}",
    "cursor_text": "the cached cursors matching {selectors}",
    "name_reading": "whether {actor} can see the object one reading of {name} names",
    "name_resolution": "which object {name} names, among the readings checked here",
}

OPERATION_GOALS: dict[str, frozenset[str]] = {
    "dba.blocking": frozenset({"blocking"}),
    "dba.invalid_objects": frozenset({"invalid_objects"}),
    "dba.scheduler_chain": frozenset({"chain_steps"}),
    "dba.scheduler_failures": frozenset({"job_failures"}),
    "dba.scheduler_job_detail": frozenset({"job_definition"}),
    "dba.scheduler_jobs": frozenset({"job_list"}),
    "dba.scheduler_program": frozenset({"program_action"}),
    "dba.scheduler_run_history": frozenset({"job_runs"}),
    "dba.sessions": frozenset({"sessions"}),
    "dba.tablespace_usage": frozenset({"tablespace_usage"}),
    "schema.db_links_referenced": frozenset({"db_link_targets"}),
    "schema.list_objects": frozenset({"object_listing", "name_reading", "name_resolution"}),
    "schema.list_schemas": frozenset({"schemas_visible"}),
    "schema.object_dependencies": frozenset({"dependencies"}),
    "schema.object_errors": frozenset({"compile_errors"}),
    "schema.object_referenced_by": frozenset({"dependents"}),
    "schema.object_source": frozenset(
        {"source_text", "all_definitions", "name_reading", "name_resolution"}
    ),
    "schema.object_source_range": frozenset({"source_text", "name_reading", "name_resolution"}),
    "schema.object_source_search": frozenset({"source_matches"}),
    "schema.object_status": frozenset({"object_status", "name_reading", "name_resolution"}),
    "schema.package_subprograms": frozenset({"spec_subprograms"}),
    "schema.plscope_identifiers": frozenset({"identifier_uses", "all_definitions"}),
    "schema.plscope_statements": frozenset({"static_sql"}),
    "schema.table_columns": frozenset({"columns"}),
    "schema.table_constraints": frozenset({"constraints"}),
    "schema.table_indexes": frozenset({"indexes"}),
    "schema.table_statistics": frozenset({"table_statistics"}),
    "schema.triggers": frozenset({"triggers"}),
    "tuning.cursor_plan": frozenset({"cursor_plan"}),
    "tuning.cursor_search": frozenset({"cursor_text"}),
    "tuning.cursor_statistics": frozenset({"cursor_statistics"}),
}
# Lookups of ALL_OBJECTS or ALL_SOURCE: what they return depends on which owners the
# actor can see (C29).
_ACCOUNT_VIEWS = frozenset(
    {
        "schema.list_objects",
        "schema.object_status",
        "schema.object_source",
        "schema.object_source_range",
        "schema.object_source_search",
    }
)


@dataclass(frozen=True)
class Reading:
    """One object a dotted name may name."""

    owner: str
    name: str
    types: frozenset[str]  # the types that would make this reading hold
    member: str = ""

    def describe(self) -> str:
        if self.member:
            return f"member {self.member} of package {self.owner}.{self.name}"
        return f"standalone PROCEDURE or FUNCTION {self.owner}.{self.name}"


@dataclass(frozen=True)
class Step:
    actor: str
    operation: str | None
    selectors: dict[str, Any]
    establishes: str
    about: str
    access: str = ""
    name: str = ""
    resolved_in: str = ""


@dataclass(frozen=True)
class Checked:
    step: Step
    declined: tuple[str, ...] = ()  # why, when not accepted
    notes: tuple[str, ...] = field(default=())  # limits the rendering states

    @property
    def accepted(self) -> bool:
        return not self.declined


@dataclass(frozen=True)
class Contract:
    """The block as read: its steps, or why it could not be read."""

    steps: tuple[Step, ...] = ()
    error: str = ""
    present: bool = False


def parse(answer: str) -> Contract:
    """The answer's ``kiwi-next-checks`` block. No block: an answer in the older form."""

    blocks = [b for b in fenced_blocks(answer) if b.language == LABEL]
    if not blocks:
        return Contract()
    if len(blocks) > 1:
        return Contract(error="more than one next-checks block", present=True)
    text = blocks[0].text
    if len(text) > MAX_BLOCK:
        return Contract(error=f"the block is over {MAX_BLOCK} characters", present=True)
    try:
        data = json.loads(text)
    except ValueError as exc:
        detail = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
        return Contract(error=f"the block is not JSON: {detail}", present=True)
    if not isinstance(data, list):
        return Contract(error="the block must be a JSON list of steps", present=True)
    if len(data) > MAX_STEPS:
        return Contract(error=f"more than {MAX_STEPS} next checks", present=True)
    steps = []
    for index, raw in enumerate(data, start=1):
        step = _step(raw)
        if isinstance(step, str):
            return Contract(error=f"step {index}: {step}", present=True)
        steps.append(step)
    return Contract(tuple(steps), present=True)


def _step(raw: Any) -> Step | str:
    if not isinstance(raw, dict):
        return "a step must be an object"
    unknown = set(raw) - {
        "actor",
        "access",
        "operation",
        "selectors",
        "establishes",
        "about",
        "name",
    }
    if unknown:
        return f"unknown field(s) {', '.join(sorted(unknown))}"
    texts = {k: raw.get(k) for k in ("actor", "access", "operation", "establishes", "about")}
    for key, value in texts.items():
        if value is not None and (not isinstance(value, str) or len(value) > MAX_TEXT):
            return f"{key} must be text of at most {MAX_TEXT} characters"
    selectors = raw.get("selectors") or {}
    if not isinstance(selectors, dict) or len(selectors) > 8:
        return "selectors must be an object of at most 8 parameters"
    if any(not isinstance(v, str | int) or isinstance(v, bool) for v in selectors.values()):
        return "each selector must be text or a whole number"
    name = raw.get("name") or {}
    if isinstance(name, str):
        name = {"text": name}
    if not isinstance(name, dict) or set(name) - {"text", "resolved_in"}:
        return "name must be an object with text and resolved_in"
    if any(not isinstance(v, str) or len(v) > 200 for v in name.values()):
        return "name text and resolved_in must be text"
    return Step(
        actor=texts["actor"] or "",
        operation=texts["operation"] or None,
        selectors={str(k).lower(): v for k, v in selectors.items()},
        establishes=texts["establishes"] or "",
        about=texts["about"] or "",
        access=texts["access"] or "",
        name=name.get("text", "").upper(),
        resolved_in=name.get("resolved_in", "").upper(),
    )


# -- validation ---------------------------------------------------------------------------


def validate(
    contract: Contract, lookups: Sequence[Any], catalog: QueryCatalog | None = None
) -> list[Checked]:
    """Each step, accepted or declined, judged against the catalog and the lookups."""

    catalog = catalog if catalog is not None else cached_catalog()
    visible = _visible_owners(lookups)
    return [_check(step, catalog, lookups, visible) for step in contract.steps]


def _check(
    step: Step,
    catalog: QueryCatalog,
    lookups: Sequence[Any],
    visible: tuple[int, set[str]] | None,
) -> Checked:
    why: list[str] = []
    notes: list[str] = []
    if step.actor not in ACTORS:
        why.append(f"actor must be one of {', '.join(ACTORS)}")
    elif step.actor in ("account_with_access", "dba") and not step.access.strip():
        why.append("an actor other than this account must say what access it has")
    if step.access.strip() and not _safe_phrase(step.access):
        why.append(
            "access must be one short phrase on one line saying what the actor can see, "
            "with no claim about what a lookup will find"
        )
    if step.operation is None:
        if step.actor != "requester":
            why.append("a step without a lookup is one the requester takes")
        if step.establishes:
            why.append("a step without a lookup establishes nothing the harness can state")
        if _open_question(step.about) is None:
            why.append(_ABOUT_RULE)
        return Checked(step, tuple(why))
    if step.actor == "requester":
        why.append("the requester does not run catalog lookups; name this account or a DBA")
    step, named = _named_operation(step, catalog)
    if named:
        notes.append(named)
    entry = (
        catalog.get(step.operation)
        if step.operation is not None and step.operation in catalog
        else None
    )
    if entry is None:
        # Not an operation id at all, or one the catalog does not hold: say that, not that
        # a lookup Kiwi may make is forbidden (trial 21, DIAG-03 and KINJ-01).
        why.append(
            "its operation is not a catalog operation id; a step names the lookup as "
            "its id, such as schema.object_source_range"
        )
        return Checked(step, tuple(why), tuple(notes))
    if not entry.kiwi:
        why.append(f"{step.operation} is not a lookup Kiwi may make")
        return Checked(step, tuple(why), tuple(notes))
    why += _selector_problems(step, entry)
    goals = OPERATION_GOALS.get(entry.operation_id, frozenset())
    if step.establishes not in GOALS:
        why.append(
            f"establishes must be one of the goals {step.operation} returns: "
            f"{', '.join(sorted(goals))}"
        )
    elif step.establishes not in goals:
        why.append(
            f"{step.operation} does not return {step.establishes.replace('_', ' ')}; it "
            f"returns {', '.join(sorted(goals))}"
        )
    owner = str(step.selectors.get("owner", "")).upper()
    if (
        step.actor == "this_account"
        and entry.operation_id in _ACCOUNT_VIEWS
        and visible is not None
        and owner
        and "%" not in owner
        and owner not in visible[1]
    ):
        listed = ", ".join(sorted(visible[1])[:10]) or "none"
        why.append(
            f"lookup {visible[0]} (schema.list_schemas) listed every owner this account can "
            f"see ({listed}), and {owner} is not among them, so this account's lookup would "
            "return no rows whatever it holds; name an actor with access"
        )
    if step.establishes in ("name_reading", "name_resolution") and not why:
        why += _name_problems(step, lookups, visible, notes)
    if step.establishes == "all_definitions" and not why:
        why += _definition_problems(step, lookups, notes)
    about = _open_question(step.about)
    if step.about.strip() and about is None:
        if step.operation is None:
            why.append(_ABOUT_RULE)
        else:
            notes.append("the model's interpretation is withheld: " + _ABOUT_RULE)
    return Checked(step, tuple(why), tuple(notes))


def _operation_id(text: str) -> str | None:
    """The catalog id a typed operation names: as written, or from its tool name."""

    text = text.strip()
    if not _OPERATION_ID.match(text):
        return None
    return text.replace("__", ".", 1) if "__" in text else text


def _named_operation(step: Step, catalog: QueryCatalog) -> tuple[Step, str]:
    """The step with its lookup named by catalog id, and a note when it was renamed.

    A tool name (``tuning__cursor_search``) is the same lookup as its id
    (``tuning.cursor_search``). When ``operation`` is a description and a ``tool`` or
    ``operation`` selector names the lookup, that selector is the operation: the
    description is dropped (the harness writes the step from its typed fields) and the
    selector does not reach the lookup's parameters.
    """

    written = step.operation or ""
    operation = _operation_id(written)
    selectors = dict(step.selectors)
    named = [
        (key, found)
        for key in _NAMING_SELECTORS
        if isinstance(selectors.get(key), str)
        and (found := _operation_id(selectors[key])) is not None
    ]
    if operation is None and named:
        operation = named[0][1]
    if operation is None or operation not in catalog:
        return step, ""
    for key, found in named:
        if found == operation:
            del selectors[key]
    if operation == written and selectors == step.selectors:
        return step, ""
    renamed = replace(step, operation=operation, selectors=selectors)
    if written == operation:
        return renamed, ""
    return renamed, f"the step named this lookup as {_quoted(written)}; it is {operation}"


def _quoted(text: str) -> str:
    """A model's text in a rendered note: an id as written, a description by length."""

    text = text.strip()
    if _OPERATION_ID.match(text):
        return f"`{text}`"
    return f"a description ({len(text)} characters) with the lookup in its selectors"


def _selector_problems(step: Step, entry: CatalogEntry) -> list[str]:
    why = []
    extra = set(step.selectors) - set(entry.parameters)
    if extra:
        why.append(f"{entry.operation_id} takes no parameter {', '.join(sorted(extra))}")
    missing = [p for p in entry.required_parameters if p not in step.selectors]
    if missing:
        why.append(f"{entry.operation_id} needs {', '.join(missing)}")
    for key, value in step.selectors.items():
        if key in entry.identifier_parameters and isinstance(value, str):
            if not _IDENTIFIER.match(value):
                why.append(f"{key} {value!r} is not a name")
        elif isinstance(value, str) and not _PATTERN.match(value):
            why.append(f"{key} {value!r} is not a value this lookup takes")
    kind = step.selectors.get("object_type")
    if isinstance(kind, str) and entry.object_types:
        if " ".join(kind.upper().split()) not in entry.object_types:
            why.append(
                f"{entry.operation_id} reads a view that holds only "
                f"{', '.join(entry.object_types)}, so object_type {kind.upper()} is refused"
            )
    return why


# ``about`` is the model's open question. It is rendered only as one bounded clause that
# asks (whether/which/what ...), with no claim about what a lookup will show and nothing
# that Markdown would read as structure: otherwise it stays in the retained proposal and
# is not rendered (C34-B).
_ABOUT_RULE = (
    "about must be one open question on one line, starting whether, which, what, why, "
    "how, if, when, where or who, with no claim about what a lookup will find"
)
_QUESTION = re.compile(r"^(?:whether|which|what|why|how|if|when|where|who)\b", re.IGNORECASE)
_RESULT_CLAIM = re.compile(
    r"\b(?:settl\w*|confirm\w*|prov(?:e|es|ed|en|ing)|establish\w*|show\w*|demonstrat\w*|"
    r"root\s+cause|determin\w*|resolv\w*|answer\w*|guarantee\w*|certain\w*|definitive\w*)\b",
    re.IGNORECASE,
)
# Control characters, line and paragraph separators, and characters Markdown reads.
_UNSAFE = re.compile(r"[\x00-\x1f\x7f\u0085\u2028\u2029`*#|<>\[\]~\\]")
_SENTENCE_BREAK = re.compile(r"[;!]|[.:?](?=\s+\S)")


def _safe_phrase(text: str) -> bool:
    """One clause, on one line, that Markdown renders as plain text and that claims no
    result."""

    text = text.strip()
    return not (
        _UNSAFE.search(text)
        or _SENTENCE_BREAK.search(text.rstrip(".?"))
        or _RESULT_CLAIM.search(text)
    )


def _open_question(about: str) -> str | None:
    """``about`` as it may be rendered, or None when it may not."""

    text = about.strip()
    if not text or not _QUESTION.match(text) or not _safe_phrase(text):
        return None
    return text.rstrip(".?")


def _unit_lookups(step: Step, lookups: Sequence[Any], operation: str) -> list[Any]:
    owner = str(step.selectors.get("owner", "")).upper()
    name = str(step.selectors.get("object_name", "")).upper()
    return [
        lookup
        for lookup in lookups
        if lookup.operation_id == operation
        and str(lookup.parameters.get("owner", "")).upper() == owner
        and str(lookup.parameters.get("object_name", "")).upper() == name
    ]


def _definition_problems(step: Step, lookups: Sequence[Any], notes: list[str]) -> list[str]:
    """``all_definitions`` holds only for a complete result (C34-B). What this request's
    lookups of the same unit already returned is stated, and a PL/Scope lookup is
    declined when any part of the unit it covers is not COLLECTED: a package whose
    specification is COLLECTED and whose body is NOT COLLECTED has no record of the
    body's definitions (Sync Point 1 finding 4). A step that names an ``object_type``
    covers only that part."""

    if step.operation == "schema.plscope_identifiers":
        wanted = str(step.selectors.get("object_type") or "").strip().upper()
        for lookup in _unit_lookups(step, lookups, step.operation):
            upper = [str(c).upper() for c in lookup.columns]
            if not lookup.succeeded or "PLSCOPE_STATUS" not in upper:
                continue
            at = upper.index("PLSCOPE_STATUS")
            kind_at = upper.index("OBJECT_TYPE") if "OBJECT_TYPE" in upper else None
            missing: dict[str, set[str]] = {}
            for row in lookup.rows:
                kind = str(row[kind_at] or "").upper() if kind_at is not None else ""
                if wanted and kind and kind != wanted:
                    continue
                state = str(row[at] or "").split(":", 1)[0].strip().upper()
                if state != "COLLECTED":
                    missing.setdefault(kind, set()).add(state or "EMPTY")
            if missing:
                parts = "; ".join(
                    f"{', '.join(sorted(states))}" + (f" for the {kind}" if kind else "")
                    for kind, states in sorted(missing.items())
                )
                return [
                    f"lookup {lookup.sequence} reports PLSCOPE_STATUS {parts} of this unit, "
                    "so PL/Scope holds no definitions for that part and this lookup cannot "
                    "return every definition; read the whole source of that part instead"
                    + ("" if wanted else ", or name a collected object_type")
                ]
        notes.append(
            "every definition only if the result's PLSCOPE_STATUS is COLLECTED and it is "
            "not truncated or at its row limit"
        )
    else:
        for lookup in _unit_lookups(step, lookups, str(step.operation)):
            state = coverage(lookup).state
            if state in ("truncated", "at_limit"):
                notes.append(
                    f"lookup {lookup.sequence} returned this unit's source "
                    f"{'truncated' if state == 'truncated' else 'at its row limit'}, so the "
                    "same lookup at that limit would not return every line"
                )
        notes.append(
            "every definition only if the whole source comes back, not truncated and under "
            "its row limit; the definitions are then read from that text, not counted by "
            "the lookup"
        )
    return []


def readings(name: str, resolved_in: str) -> tuple[Reading, ...]:
    """The objects ``A.B`` may name as a call, among the forms checked here."""

    parts = name.split(".")
    if len(parts) != 2 or not all(_IDENTIFIER.match(p) for p in parts):
        return ()
    first, second = parts
    out = [Reading(first, second, _STORED_TYPES)]
    if resolved_in:
        out.append(Reading(resolved_in, first, _PACKAGE_TYPES, member=second))
    return tuple(out)


def _tested(step: Step, options: Sequence[Reading]) -> Reading | None:
    """The reading the step's selectors look up, if any."""

    owner = str(step.selectors.get("owner", "")).upper()
    target = str(
        step.selectors.get("object_name") or step.selectors.get("name_filter") or ""
    ).upper()
    kind = " ".join(str(step.selectors.get("object_type", "")).upper().split())
    for reading in options:
        if (owner, target) == (reading.owner, reading.name) and (not kind or kind in reading.types):
            return reading
    return None


def _name_problems(
    step: Step,
    lookups: Sequence[Any],
    visible: tuple[int, set[str]] | None,
    notes: list[str],
) -> list[str]:
    options = readings(step.name, step.resolved_in)
    if not options:
        return ["name must be a two-part name A.B (with resolved_in, the schema it is called from)"]
    if step.establishes == "name_resolution" and not step.resolved_in:
        return [
            f"name_resolution needs resolved_in, the schema {step.name} is called from: "
            "without it the package reading is not considered, so one lookup cannot show "
            "which object the name is. Give resolved_in, or use name_reading"
        ]
    tested = _tested(step, options)
    if tested is None:
        listed = "; ".join(r.describe() for r in options)
        return [
            f"the selectors look up no reading of {step.name}. Its readings here are: "
            f"{listed}. A lookup of another object cannot show which it is"
        ]
    if len(options) == 1:
        notes.append(
            f"resolved_in was not given, so the package reading of {step.name} is not considered"
        )
    if step.establishes == "name_reading":
        return []
    others = [r for r in options if r != tested]
    open_ = [r for r in others if not _excluded(r, lookups, visible)]
    if open_:
        return [
            f"this lookup tests one reading ({tested.describe()}); it cannot show which object "
            f"{step.name} is while {'; '.join(r.describe() for r in open_)} is not excluded by "
            "a complete lookup in this request. Use name_reading"
        ]
    notes.append(
        "the other reading is excluded only as far as this account's ALL_OBJECTS shows it, "
        "and a synonym or a type method could also take this name; these lookups do not "
        "test those"
    )
    return []


def _excluded(
    reading: Reading, lookups: Sequence[Any], visible: tuple[int, set[str]] | None
) -> bool:
    """A complete ALL_OBJECTS lookup in this request for the reading's owner and name
    returned no object of a type that would make it hold, and a complete list of schemas
    showed this account can see that owner's objects. An empty lookup of an owner the
    account cannot see shows nothing (C29)."""

    if visible is None or reading.owner not in visible[1]:
        return False
    for lookup in lookups:
        if lookup.operation_id not in ("schema.list_objects", "schema.object_status"):
            continue
        if not lookup.succeeded or coverage(lookup).state not in ("complete", "empty"):
            continue
        parameters = {k: str(v).upper() for k, v in lookup.parameters.items()}
        if parameters.get("owner") != reading.owner:
            continue
        wanted = parameters.get("object_name") or parameters.get("name_filter") or ""
        if not _like(wanted, reading.name):
            continue
        kind = parameters.get("object_type", "")
        if kind and kind not in reading.types:
            continue
        upper = [str(c).upper() for c in lookup.columns]
        if "OBJECT_NAME" not in upper:
            continue
        name_at = upper.index("OBJECT_NAME")
        type_at = upper.index("OBJECT_TYPE") if "OBJECT_TYPE" in upper else None
        if not any(
            str(row[name_at]).upper() == reading.name
            and (type_at is None or str(row[type_at]).upper() in reading.types)
            for row in lookup.rows
        ):
            return True
    return False


def _like(pattern: str, value: str) -> bool:
    if not pattern:
        return False
    regex = "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in pattern)
    return re.fullmatch(regex, value) is not None


def _visible_owners(lookups: Sequence[Any]) -> tuple[int, set[str]] | None:
    for lookup in lookups:
        if lookup.operation_id != "schema.list_schemas" or not lookup.succeeded:
            continue
        if coverage(lookup).state != "complete":
            continue
        upper = [str(c).upper() for c in lookup.columns]
        if "SCHEMA_NAME" in upper:
            at = upper.index("SCHEMA_NAME")
            return lookup.sequence, {str(r[at]).upper() for r in lookup.rows if r[at]}
    return None


# -- rendering ----------------------------------------------------------------------------

_ACTOR_TEXT = {
    "this_account": "this account",
    "account_with_access": "an account with access",
    "dba": "a DBA",
    "requester": "you",
}


def render(checked: Sequence[Checked]) -> str:
    """The "Next checks" section, written from the accepted steps' declared results."""

    accepted = [c for c in checked if c.accepted]
    declined = [c for c in checked if not c.accepted]
    out = [
        "## Next steps",
        "",
        "Written by the harness from the answer's typed steps: what each lookup returns, "
        "not what the model said it would settle.",
        "",
    ]
    if not accepted:
        out.append("No next check the lookups can make was proposed.")
    for number_, item in enumerate(accepted, start=1):
        out.append(_render_step(number_, item))
    if declined:
        out += ["", "Declined by the harness, with why (each is kept in the model's draft):"]
        for item in declined:
            # Only an id is repeated as the label: a description is the model's text.
            operation = item.step.operation or ""
            what = operation if _OPERATION_ID.match(operation) else "a step"
            out.append(f"- {what}: {'; '.join(item.declined)}.")
    return "\n".join(out)


def _render_step(number_: int, item: Checked) -> str:
    step = item.step
    actor = _ACTOR_TEXT[step.actor]
    # Requester access is unverified metadata: silently omit it, keeping the question.
    # Only actors whose role bears access may publish an access description.
    if step.actor in ("account_with_access", "dba") and step.access:
        actor += f" (the model's description, not checked: {step.access.strip()})"
    question = _open_question(step.about)
    if step.operation is None:
        return f"{number_}. For {actor} to supply, outside these lookups: {question}."
    call = ", ".join(f"{k}={v!r}" for k, v in step.selectors.items())
    fact = _fact(step, _ACTOR_TEXT[step.actor])
    line = f"{number_}. As {actor}: `{step.operation}({call})` returns {fact}."
    for note in item.notes:
        line += f" Limit: {note}."
    if question:
        line += f" Open question (the model's interpretation, not checked): {question}."
    return line


class _Blank(dict):  # type: ignore[type-arg]
    def __missing__(self, key: str) -> str:
        return f"<{key}>"


def _fact(step: Step, actor: str) -> str:
    values: dict[str, Any] = _Blank(
        {
            k: str(v).upper() if k in ("owner", "object_name", "table_name") else v
            for k, v in step.selectors.items()
        }
    )
    values["actor"] = actor
    values["name"] = step.name
    values["selectors"] = ", ".join(f"{k}={v}" for k, v in step.selectors.items()) or "none"
    values["object_type"] = str(step.selectors.get("object_type", "")).upper()
    table = step.selectors.get("table_name")
    values["on_table"] = f" on table {str(table).upper()}" if table else ""
    if step.establishes == "name_reading":
        tested = _tested(step, readings(step.name, step.resolved_in))
        if tested is not None:
            return f"whether {actor} can see the {tested.describe()}, one reading of {step.name}"
    return GOALS[step.establishes].format_map(values)


def replace_section(answer: str, rendered: str) -> str | None:
    """``answer`` with its next-steps section, the one holding its ``kiwi-next-checks``
    block, replaced by ``rendered``; None when there is no single block.

    The section runs from the next-steps heading before the block (or the block itself,
    when there is none) to the next heading after it, so free-text steps the model wrote
    beside the block go with it and only the harness's rendering stands (C34-B)."""

    blocks = [b for b in fenced_blocks(answer) if b.language == LABEL]
    if len(blocks) != 1:
        return None
    block = blocks[0]
    lines = answer[: block.start].splitlines(keepends=True)
    start = block.start
    offset = block.start
    for line in reversed(lines):
        offset -= len(line)
        name = heading_name(line.strip())
        if name == "next":
            start = offset
            break
        if name is not None or other_heading(line.strip()):
            break
    end = len(answer)
    offset = block.end
    for line in answer[block.end :].splitlines(keepends=True):
        if heading_name(line.strip()) not in (None, "next") or other_heading(line.strip()):
            end = offset
            break
        offset += len(line)
    tail = answer[end:]
    return answer[:start] + rendered + ("\n\n" + tail.lstrip("\n") if tail.strip() else "\n")


def generation_contract(catalog: QueryCatalog | None = None) -> str:
    """Generation vocabulary from exactly the same reviewed catalog as validation.

    Descriptions belong in about/access, never in actor/operation. No permissive
    repair of malformed model fields: validation remains the execution boundary.
    """
    catalog = catalog if catalog is not None else cached_catalog()
    pairs = []
    for operation, goals in sorted(OPERATION_GOALS.items()):
        if operation not in catalog:
            continue
        entry = catalog.get(operation)
        if entry.kiwi and entry.risk.value == "read":
            pairs.append(f"{operation}=>{'/'.join(sorted(goals))}")
    return (
        "Use actor EXACTLY one of " + ", ".join(ACTORS) + ". "
        "Use operation EXACTLY a dotted catalog identifier below (not a description), "
        "and establishes one of that operation's goals. Requester has no operation. "
        "Selectors contain only that operation's catalog parameters, never lookup/tool. "
        "Put the human question in about and access description in access. "
        "These are proposed future checks, never completed lookups. "
        'Example: {"actor":"this_account","operation":"tuning.cursor_search",'
        '"selectors":{"text_filter":"%EMPLOYEES%"},"establishes":"cursor_text",'
        '"about":"what cached SQL mentions EMPLOYEES"}. '
        "Catalog operation=>goals: " + "; ".join(pairs) + ". "
    )
