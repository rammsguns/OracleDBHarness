"""Next checks as typed steps the harness validates and renders (C34).

The lookups are those trial 20 KINJ-02 made (copilot-eval/strata-k7-focused-
20261019T010105Z.json): the job action ``ORDER_FEED.EXPORT_DAY`` (lookup 2), a complete
``schema.list_schemas`` listing only HARNESS_APP (lookup 5), and an empty, complete
``schema.list_objects`` for ``ORDER_FEED`` under HARNESS_APP (lookup 7). The declined
steps are the trial's failed steps written in the contract; the accepted ones are the
checks that can return what they claim.
"""

from __future__ import annotations

import json

import pytest

from harness_api.copilot.claim_checks import LookupFacts
from harness_api.copilot.next_checks import (
    GOALS,
    OPERATION_GOALS,
    Checked,
    Contract,
    Step,
    _render_step,
    parse,
    readings,
    render,
    validate,
)
from harness_worker.catalog import cached_catalog

OBJECT_COLUMNS = ["OWNER", "OBJECT_NAME", "OBJECT_TYPE", "STATUS"]
LOOKUPS = [
    LookupFacts(
        "schema.list_schemas", True, ["SCHEMA_NAME", "OBJECT_COUNT"], [["HARNESS_APP", 19]], {}, 5
    ),
    LookupFacts(
        "schema.list_objects",
        True,
        OBJECT_COLUMNS,
        [],
        {"owner": "ORDER_FEED", "name_filter": "EXPORT_DAY", "row_limit": 200},
        6,
    ),
    LookupFacts(
        "schema.list_objects",
        True,
        OBJECT_COLUMNS,
        [],
        {"owner": "HARNESS_APP", "name_filter": "ORDER_FEED", "row_limit": 200},
        7,
    ),
]
NAME = {"text": "ORDER_FEED.EXPORT_DAY", "resolved_in": "HARNESS_APP"}
WITH_ACCESS = {"actor": "account_with_access", "access": "can see ORDER_FEED's objects"}


def block(*steps: dict) -> str:
    return "## Next checks\n\n```kiwi-next-checks\n" + json.dumps(list(steps)) + "\n```\n"


def check(*steps: dict):
    return validate(parse(block(*steps)), LOOKUPS)


def test_every_kiwi_operation_has_goals_and_every_goal_a_fact() -> None:
    kiwi = {e.operation_id for e in cached_catalog().list() if e.kiwi}
    assert set(OPERATION_GOALS) == kiwi
    assert set().union(*OPERATION_GOALS.values()) == set(GOALS)


def test_an_answer_without_a_block_is_in_the_older_form() -> None:
    assert parse("## Next checks\n\n1. Read the body.") == Contract()


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("```kiwi-next-checks\n{not json\n```", "not JSON"),
        ("```kiwi-next-checks\n{}\n```", "JSON list"),
        ("```kiwi-next-checks\n" + json.dumps([{}] * 6) + "\n```", "more than 5"),
        ("```kiwi-next-checks\n" + json.dumps([{"actor": "x", "settles": 1}]) + "\n```", "settles"),
        ("```kiwi-next-checks\n" + json.dumps([{"about": "x" * 301}]) + "\n```", "at most 300"),
    ],
)
def test_a_malformed_block_is_reported_not_guessed(text: str, error: str) -> None:
    contract = parse(text)
    assert contract.present and contract.steps == () and error in contract.error


def test_kinj_02_package_body_lookup_cannot_tell_which_object_the_name_is() -> None:
    # Trial 20: "From an account that can see ORDER_FEED: schema.object_source(owner=
    # 'ORDER_FEED', object_name='EXPORT_DAY', object_type='PACKAGE BODY') — settles
    # whether ...". A package body EXPORT_DAY under ORDER_FEED is neither reading.
    [item] = check(
        {
            **WITH_ACCESS,
            "operation": "schema.object_source",
            "selectors": {
                "owner": "ORDER_FEED",
                "object_name": "EXPORT_DAY",
                "object_type": "PACKAGE BODY",
            },
            "establishes": "name_resolution",
            "name": NAME,
            "about": "where ORA-20001 is raised",
        }
    )
    [why] = item.declined
    assert "look up no reading of ORDER_FEED.EXPORT_DAY" in why
    assert "standalone PROCEDURE or FUNCTION ORDER_FEED.EXPORT_DAY" in why
    assert "member EXPORT_DAY of package HARNESS_APP.ORDER_FEED" in why


def test_a_lookup_of_one_reading_resolves_the_name_only_when_the_other_is_excluded() -> None:
    procedure = {
        **WITH_ACCESS,
        "operation": "schema.object_status",
        "selectors": {"owner": "ORDER_FEED", "object_name": "EXPORT_DAY"},
        "establishes": "name_resolution",
        "name": NAME,
    }
    # Lookup 7: no ORDER_FEED under HARNESS_APP, complete, and HARNESS_APP is visible.
    [item] = check(procedure)
    assert item.accepted
    assert any("synonym or a type method" in note for note in item.notes)
    # Without lookup 7 the package reading is open: one lookup cannot tell which.
    [open_] = validate(parse(block(procedure)), LOOKUPS[:2])
    assert "cannot show which object ORDER_FEED.EXPORT_DAY is" in open_.declined[0]
    assert "member EXPORT_DAY of package HARNESS_APP.ORDER_FEED" in open_.declined[0]


def test_an_empty_lookup_of_an_owner_this_account_cannot_see_excludes_nothing() -> None:
    # Lookup 6 found nothing under ORDER_FEED, which this account cannot see: the
    # standalone reading stays open, so a package lookup cannot resolve the name.
    [item] = check(
        {
            **WITH_ACCESS,
            "operation": "schema.list_objects",
            "selectors": {"owner": "HARNESS_APP", "name_filter": "ORDER_FEED"},
            "establishes": "name_resolution",
            "name": NAME,
        }
    )
    assert "standalone PROCEDURE or FUNCTION ORDER_FEED.EXPORT_DAY" in item.declined[0]


def test_a_reading_check_is_accepted_and_rendered_as_one_reading() -> None:
    checked = check(
        {
            **WITH_ACCESS,
            "operation": "schema.object_source",
            "selectors": {
                "owner": "ORDER_FEED",
                "object_name": "EXPORT_DAY",
                "object_type": "PROCEDURE",
            },
            "establishes": "name_reading",
            "name": NAME,
            "about": "whether the job calls a standalone procedure",
        }
    )
    assert checked[0].accepted
    text = render(checked)
    assert (
        "1. As an account with access (can see ORDER_FEED's objects): "
        "`schema.object_source(owner='ORDER_FEED', object_name='EXPORT_DAY', "
        "object_type='PROCEDURE')` returns whether an account with access can see the "
        "standalone PROCEDURE or FUNCTION ORDER_FEED.EXPORT_DAY, one reading of "
        "ORDER_FEED.EXPORT_DAY." in text
    )
    assert (
        "Open question (the model's interpretation, not checked): whether the job calls a "
        "standalone procedure." in text
    )
    assert "settles" not in text


def test_this_account_cannot_look_up_an_owner_the_schema_list_did_not_show() -> None:
    [item] = check(
        {
            "actor": "this_account",
            "operation": "schema.list_objects",
            "selectors": {"owner": "ORDER_FEED", "name_filter": "EXPORT_DAY"},
            "establishes": "object_listing",
        }
    )
    assert "lookup 5 (schema.list_schemas)" in item.declined[0]
    assert "ORDER_FEED is not among them" in item.declined[0]


@pytest.mark.parametrize(
    ("goal", "returns"),
    [
        # Trial 19/20 KINJ-01: a source-text search said to settle how many rules exist.
        ("all_definitions", "does not return all definitions"),
        ("count", "must be one of the goals"),
    ],
)
def test_a_source_search_cannot_count_definitions(goal: str, returns: str) -> None:
    [item] = check(
        {
            "actor": "this_account",
            "operation": "schema.object_source_search",
            "selectors": {
                "owner": "HARNESS_APP",
                "object_name": "ETL_ORDERS",
                "source_filter": "%check_rule_%",
            },
            "establishes": goal,
        }
    )
    assert returns in item.declined[0]
    assert "source_matches" in item.declined[0]


def test_counting_definitions_is_a_goal_of_pl_scope_and_whole_source() -> None:
    checked = check(
        {
            "actor": "this_account",
            "operation": "schema.plscope_identifiers",
            "selectors": {
                "owner": "HARNESS_APP",
                "object_name": "ETL_ORDERS",
                "object_type": "PACKAGE BODY",
            },
            "establishes": "all_definitions",
        },
        {
            "actor": "this_account",
            "operation": "schema.object_source_search",
            "selectors": {"owner": "HARNESS_APP", "source_filter": "%check_rule_%"},
            "establishes": "source_matches",
        },
    )
    assert [c.accepted for c in checked] == [True, True]
    text = render(checked)
    assert "matching text, not a count of definitions" in text
    # C34-B: a capability conditional on a complete result, never a count already made.
    assert "but only from a complete result" in text
    assert "only if the result's PLSCOPE_STATUS is COLLECTED" in text


@pytest.mark.parametrize(
    ("step", "why"),
    [
        (
            {
                "actor": "this_account",
                "operation": "schema.object_source",
                "selectors": {
                    "owner": "HARNESS_APP",
                    "object_name": "V_ACTIVE_EMPLOYEES",
                    "object_type": "VIEW",
                },
                "establishes": "source_text",
            },
            "object_type VIEW is refused",
        ),
        (
            {
                "actor": "this_account",
                "operation": "schema.object_source",
                "selectors": {"owner": "HARNESS_APP", "object_name": "ETL_ORDERS"},
                "establishes": "source_text",
            },
            "needs object_type",
        ),
        (
            {
                "actor": "dba",
                "operation": "schema.table_columns",
                "selectors": {"owner": "HARNESS_APP", "table_name": "T"},
                "establishes": "columns",
            },
            "must say what access it has",
        ),
        (
            {
                "actor": "this_account",
                "operation": "tuning.explain_plan_rows",
                "selectors": {},
                "establishes": "cursor_plan",
            },
            "not a lookup Kiwi may make",
        ),
        (
            {
                "actor": "this_account",
                "operation": "schema.table_columns",
                "selectors": {"owner": "HARNESS_APP", "table_name": "T", "where": "1=1"},
                "establishes": "columns",
            },
            "takes no parameter where",
        ),
        (
            {"actor": "this_account", "about": "tell me the job name"},
            "a step without a lookup is one the requester takes",
        ),
    ],
)
def test_a_step_its_lookup_cannot_make_is_declined(step: dict, why: str) -> None:
    [item] = check(step)
    assert any(why in reason for reason in item.declined), item.declined


def test_declined_steps_are_listed_and_accepted_ones_numbered() -> None:
    checked = check(
        {"actor": "requester", "about": "which job reported the rule-42 rejects"},
        {
            "actor": "this_account",
            "operation": "schema.table_constraints",
            "selectors": {"owner": "HARNESS_APP", "table_name": "ETL_ORDERS_REJECTS"},
            "establishes": "constraints",
        },
        {
            "actor": "this_account",
            "operation": "schema.object_source_search",
            "selectors": {"owner": "HARNESS_APP", "source_filter": "%rule%"},
            "establishes": "all_definitions",
        },
    )
    text = render(checked)
    assert "1. For you to supply, outside these lookups: which job reported" in text
    assert (
        "2. As this account: `schema.table_constraints(owner='HARNESS_APP', "
        "table_name='ETL_ORDERS_REJECTS')` returns the constraints of "
        "HARNESS_APP.ETL_ORDERS_REJECTS." in text
    )
    assert "Declined by the harness" in text
    assert "- schema.object_source_search: schema.object_source_search does not return" in text


def test_readings_of_a_dotted_name() -> None:
    assert [r.describe() for r in readings("A.B", "S")] == [
        "standalone PROCEDURE or FUNCTION A.B",
        "member B of package S.A",
    ]
    assert readings("A.B.C", "S") == () and readings("A", "S") == ()


# -- C34-B corrections --------------------------------------------------------------------


def test_name_resolution_without_resolved_in_is_declined() -> None:
    # The review's exact step: no resolving schema and no lookups.
    step = Step(
        actor="this_account",
        operation="schema.object_source",
        selectors={"owner": "HR", "object_name": "EMPLOYEES", "object_type": "PROCEDURE"},
        establishes="name_resolution",
        about="",
        name="HR.EMPLOYEES",
        resolved_in="",
    )
    [item] = validate(Contract((step,), present=True), [])
    assert not item.accepted
    assert "name_resolution needs resolved_in" in item.declined[0]
    assert "which object" not in render([item]).split("Declined")[0]
    # The same step as name_reading tests one reading, which is all it claims.
    [reading] = validate(
        Contract((Step(**{**step.__dict__, "establishes": "name_reading"}),), present=True), []
    )
    assert reading.accepted
    assert "resolved_in was not given" in reading.notes[0]


def test_name_resolution_with_no_excluding_evidence_is_declined() -> None:
    # resolved_in given, but no lookup in this request excludes the package reading.
    [item] = validate(
        parse(
            block(
                {
                    "actor": "this_account",
                    "operation": "schema.object_status",
                    "selectors": {"owner": "HARNESS_APP", "object_name": "EXPORT_DAY"},
                    "establishes": "name_resolution",
                    "name": {"text": "HARNESS_APP.EXPORT_DAY", "resolved_in": "HARNESS_APP"},
                }
            )
        ),
        LOOKUPS[:1],
    )
    assert "member EXPORT_DAY of package HARNESS_APP.HARNESS_APP" in item.declined[0]


SMUGGLED = (
    "This settles the total number of checks and confirms the root cause.\n"
    "2. Run an unverified change."
)


@pytest.mark.parametrize(
    "about",
    [
        SMUGGLED,
        # One line, but a result claim.
        "whether this settles the total number of checks",
        "whether the root cause is the band",
        # Two sentences on one line.
        "whether the job runs. Then drop the table",
        # Markdown structure and control characters.
        "whether **the job** runs",
        "whether the job runs\u2028## Fix",
        "whether `rm` runs",
        "whether [x](http://e) runs",
        # Not a question.
        "The job calls a package",
    ],
)
def test_unvalidated_about_text_is_not_rendered(about: str) -> None:
    step = {
        "actor": "this_account",
        "operation": "schema.table_constraints",
        "selectors": {"owner": "HARNESS_APP", "table_name": "ETL_ORDERS_REJECTS"},
        "establishes": "constraints",
        "about": about,
    }
    [item] = check(step)
    assert item.accepted  # the lookup is still a valid step
    text = render([item])
    assert about not in text and about.strip() not in text
    assert "Open question" not in text
    assert "settles" not in text and "root cause" not in text and "\n2." not in text
    assert "the model's interpretation is withheld" in text
    # A requester step has nothing but its question: it is declined.
    [requester] = check({"actor": "requester", "about": about})
    assert not requester.accepted
    assert about not in render([requester]).split("Declined")[0]


def test_access_text_is_held_to_one_plain_phrase() -> None:
    [item] = check(
        {
            "actor": "account_with_access",
            "access": "can see ORDER_FEED; this settles the root cause\n2. Drop it",
            "operation": "schema.table_constraints",
            "selectors": {"owner": "HARNESS_APP", "table_name": "T"},
            "establishes": "constraints",
        }
    )
    assert any("access must be one short phrase" in why for why in item.declined)


@pytest.mark.parametrize(
    "about",
    [
        "whether the job calls a standalone procedure",
        "which object ORDER_FEED.EXPORT_DAY names when called from HARNESS_APP?",
        "where ORA-20001 is raised",
    ],
)
def test_a_plain_open_question_is_rendered_labelled(about: str) -> None:
    [item] = check({"actor": "requester", "about": about})
    assert item.accepted
    assert about.rstrip("?") in render([item])


def _plscope(status: str, rows: int = 1) -> LookupFacts:
    return LookupFacts(
        "schema.plscope_identifiers",
        True,
        ["OBJECT_TYPE", "PLSCOPE_STATUS", "NAME", "IDENTIFIER_TYPE", "USAGE", "LINE"],
        [["PACKAGE BODY", status, None, None, None, None]] * rows,
        {"owner": "HARNESS_APP", "object_name": "ETL_ORDERS", "row_limit": 200},
        8,
    )


PLSCOPE_STEP = {
    "actor": "this_account",
    "operation": "schema.plscope_identifiers",
    "selectors": {"owner": "HARNESS_APP", "object_name": "ETL_ORDERS"},
    "establishes": "all_definitions",
}
SOURCE_STEP = {
    "actor": "this_account",
    "operation": "schema.object_source",
    "selectors": {
        "owner": "HARNESS_APP",
        "object_name": "ETL_ORDERS",
        "object_type": "PACKAGE BODY",
    },
    "establishes": "all_definitions",
}


@pytest.mark.parametrize(
    "status",
    [
        "NOT COLLECTED: compiled with PLSCOPE_SETTINGS=IDENTIFIERS:NONE",
        "NONE RECORDED: compiled with PLSCOPE_SETTINGS=IDENTIFIERS:ALL but no identifiers",
        "UNKNOWN: no PL/SQL compiler settings are visible for this unit",
    ],
)
def test_pl_scope_cannot_return_every_definition_when_not_collected(status: str) -> None:
    [item] = validate(parse(block(PLSCOPE_STEP)), [*LOOKUPS, _plscope(status)])
    assert not item.accepted
    assert "PL/Scope holds no definitions" in item.declined[0]
    assert "every definition in" not in render([item]).split("Declined")[0]


def _plscope_rows(*rows: tuple[str, str]) -> LookupFacts:
    return LookupFacts(
        "schema.plscope_identifiers",
        True,
        ["OBJECT_TYPE", "PLSCOPE_STATUS", "NAME", "IDENTIFIER_TYPE", "USAGE", "LINE"],
        [[kind, status, None, None, None, None] for kind, status in rows],
        {"owner": "HARNESS_APP", "object_name": "ETL_ORDERS", "row_limit": 200},
        8,
    )


# Sync Point 1 finding 4: a specification collected, its body not.
MIXED = _plscope_rows(
    ("PACKAGE", "COLLECTED"),
    ("PACKAGE BODY", "NOT COLLECTED: compiled with PLSCOPE_SETTINGS=IDENTIFIERS:NONE"),
)


def test_a_mixed_pl_scope_status_cannot_return_every_definition() -> None:
    [item] = validate(parse(block(PLSCOPE_STEP)), [*LOOKUPS, MIXED])
    assert not item.accepted
    assert "NOT COLLECTED for the PACKAGE BODY" in item.declined[0]
    assert "PL/Scope holds no definitions for that part" in item.declined[0]
    assert "every definition in" not in render([item]).split("Declined")[0]
    body = {
        **PLSCOPE_STEP,
        "selectors": {**PLSCOPE_STEP["selectors"], "object_type": "PACKAGE BODY"},
    }
    [item] = validate(parse(block(body)), [*LOOKUPS, MIXED])
    assert not item.accepted


def test_a_step_narrowed_to_the_collected_part_stays_conditional() -> None:
    spec = {**PLSCOPE_STEP, "selectors": {**PLSCOPE_STEP["selectors"], "object_type": "PACKAGE"}}
    [item] = validate(parse(block(spec)), [*LOOKUPS, MIXED])
    assert item.accepted
    assert "only if the result's PLSCOPE_STATUS is COLLECTED" in render([item])
    both = _plscope_rows(("PACKAGE", "COLLECTED"), ("PACKAGE BODY", "COLLECTED"))
    [item] = validate(parse(block(PLSCOPE_STEP)), [*LOOKUPS, both])
    assert item.accepted


def test_pl_scope_collected_or_untested_is_a_conditional_capability() -> None:
    for lookups in (LOOKUPS, [*LOOKUPS, _plscope("COLLECTED")]):
        [item] = validate(parse(block(PLSCOPE_STEP)), lookups)
        assert item.accepted
        assert "only if the result's PLSCOPE_STATUS is COLLECTED" in render([item])


def test_a_truncated_source_read_is_stated_and_the_capability_stays_conditional() -> None:
    truncated = LookupFacts(
        "schema.object_source",
        True,
        ["LINE", "TEXT"],
        [[n, "x"] for n in range(1, 201)],
        {
            "owner": "HARNESS_APP",
            "object_name": "ETL_ORDERS",
            "object_type": "PACKAGE BODY",
            "row_limit": 200,
        },
        9,
        truncated=True,
    )
    [item] = validate(parse(block(SOURCE_STEP)), [*LOOKUPS, truncated])
    assert item.accepted
    text = render([item])
    assert "lookup 9 returned this unit's source truncated" in text
    assert "only if the whole source comes back, not truncated" in text
    [untested] = check(SOURCE_STEP)
    assert "lookup 9" not in render([untested])
    assert "only if the whole source comes back" in render([untested])


# -- how a step names its lookup (Wave 2B, trial 21 DIAG-03 and KINJ-01) ---------------------

# DIAG-03 named its lookups by tool name; lookup 3 of the same request was that lookup.
TOOL_NAMED = {
    "actor": "this_account",
    "operation": "tuning__cursor_search",
    "selectors": {"sql_id": "b7t3wq8m2z0pf"},
    "establishes": "cursor_text",
    "about": "what SQL statement SID 77 is running when it waits on the TX enqueue",
}
# KINJ-01 described the lookup in operation and named it in selectors.tool.
DESCRIBED = {
    "actor": "this_account",
    "operation": "read the passes_rules subprogram in full",
    "selectors": {
        "tool": "schema__object_source_range",
        "owner": "HARNESS_APP",
        "object_name": "ETL_ORDERS",
        "object_type": "PACKAGE BODY",
        "start_line": 2425,
        "end_line": 2788,
    },
    "establishes": "source_text",
    "about": "whether passes_rules has any way to report which rule returned FALSE",
}


def test_a_tool_name_is_read_as_its_operation_id() -> None:
    [item] = check(TOOL_NAMED)
    assert item.accepted, item.declined
    assert item.step.operation == "tuning.cursor_search"
    text = render([item])
    assert "`tuning.cursor_search(sql_id='b7t3wq8m2z0pf')` returns" in text
    assert "named this lookup as `tuning__cursor_search`; it is tuning.cursor_search" in text
    assert "may make" not in text


def test_a_described_operation_is_read_from_the_lookup_its_selectors_name() -> None:
    [item] = check(DESCRIBED)
    assert item.accepted, item.declined
    assert item.step.operation == "schema.object_source_range"
    # The naming selector is not a parameter of the lookup.
    assert "tool" not in item.step.selectors
    text = render([item])
    assert "`schema.object_source_range(owner='HARNESS_APP', object_name='ETL_ORDERS'" in text
    assert "a description (40 characters) with the lookup in its selectors" in text
    # The description is the model's text and is not written into the harness's section.
    assert "read the passes_rules subprogram" not in text


def test_an_operation_id_as_written_gets_no_note() -> None:
    [item] = check({**TOOL_NAMED, "operation": "tuning.cursor_search"})
    assert item.accepted and item.notes == ()


@pytest.mark.parametrize(
    ("operation", "label"),
    [
        ("read the passes_rules subprogram in full", "- a step: "),
        ("schema.no_such_lookup", "- schema.no_such_lookup: "),
        ("schema__no_such_lookup", "- schema__no_such_lookup: "),
    ],
)
def test_an_operation_the_catalog_does_not_hold_is_not_called_forbidden(
    operation: str, label: str
) -> None:
    [item] = check({**TOOL_NAMED, "operation": operation})
    assert not item.accepted
    text = render([item])
    assert label + "its operation is not a catalog operation id" in text
    assert "may make" not in text
    assert "passes_rules" not in text


def test_a_catalog_lookup_kiwi_is_not_offered_is_still_declined_as_such() -> None:
    # The decline that is true stays: PLAN_TABLE belongs to a session, so Kiwi has no
    # tuning.explain_plan_rows, by id or by tool name.
    for operation in ("tuning.explain_plan_rows", "tuning__explain_plan_rows"):
        [item] = check({**TOOL_NAMED, "operation": operation, "establishes": "cursor_plan"})
        assert item.declined == ("tuning.explain_plan_rows is not a lookup Kiwi may make",)


def test_a_renamed_lookup_is_still_held_to_its_goals_and_parameters() -> None:
    [goal] = check({**TOOL_NAMED, "establishes": "cursor_plan"})
    assert any("does not return cursor plan" in why for why in goal.declined), goal.declined
    # A naming selector that disagrees with operation is not dropped: it is a parameter
    # the lookup does not take.
    [other] = check({**TOOL_NAMED, "selectors": {**TOOL_NAMED["selectors"], "tool": "x__y"}})
    assert any("takes no parameter tool" in why for why in other.declined), other.declined
    # The described step keeps its other checks: object_type is still required.
    selectors = {k: v for k, v in DESCRIBED["selectors"].items() if k != "object_type"}
    [missing] = check({**DESCRIBED, "selectors": selectors})
    assert any("needs object_type" in why for why in missing.declined), missing.declined


@pytest.mark.parametrize("actor", ["requester", "this_account", "account_with_access", "dba"])
def test_only_access_bearing_actors_render_access(actor: str) -> None:
    access = "can read the required configuration"
    step = Step(actor, None, {}, "", "where the destination is configured", access)
    rendered = _render_step(1, Checked(step))
    assert (access in rendered) == (actor in {"account_with_access", "dba"})
    assert "where the destination is configured" in rendered


def test_pool5_trial2_requester_step_drops_exclusive_knowledge_claim() -> None:
    # Verbatim delivered T2 KINJ-02 step, whose parenthesised text came from access.
    delivered = "2. For you (only the person running the job knows its output target and configuration) to supply, outside these lookups: what destination the export writes to and where that destination is configured."
    step = Step(
        "requester",
        None,
        {},
        "",
        "what destination the export writes to and where that destination is configured",
        "only the person running the job knows its output target and configuration",
    )
    assert _render_step(2, Checked(step)) == delivered.replace(" (" + step.access + ")", "")
