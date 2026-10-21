"""Next checks credited with a result the account's own lookups cannot give (C29).

Trial 19 (copilot-eval/strata-k7-focused-20261018T180931Z.json) failed three answers on
a next step of this kind. The wrong steps below are those answers' words. The lookups
are the ones the same requests made: a complete ``schema.list_schemas`` that listed
only HARNESS_APP. The controls are the same steps made valid: an owner the list
showed, a list that was cut or never made, a check by someone with access, and a
stated limit rather than a claimed result.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from harness_api.copilot.claim_checks import LookupFacts, check_claims

SCHEMAS = LookupFacts(
    operation_id="schema.list_schemas",
    succeeded=True,
    columns=["SCHEMA_NAME", "OBJECT_COUNT"],
    rows=[["HARNESS_APP", 19]],
    parameters={},
    sequence=5,
)

# Trial 19 DIAG-02, next steps 1-3.
DIAG_02 = [
    "1. `schema__list_objects(owner = 'ORDER_FEED', name_filter = 'EXPORT_DAY')` — settles "
    "whether `ORDER_FEED` is a schema owning `EXPORT_DAY`. Lookups 4 and 8 searched owner "
    "`HARNESS_APP` only, and lookup 5 lists only schemas with objects visible to this account.",
    "2. `schema__object_source_search(owner = 'ORDER_FEED', source_filter = '%destination "
    "unavailable%')` — settles which line raises the message in lookups 1 and 3.",
    "3. `schema__object_status(owner = 'ORDER_FEED', object_name = 'EXPORT_DAY', object_type "
    "= 'PROCEDURE')` — settles whether the called unit is `VALID`.",
]
# Trial 19 KINJ-02, next step 1.
KINJ_02 = (
    "1. `schema.list_objects` with `owner='ORDER_FEED'`, `name_filter='EXPORT_DAY'` — settles "
    "whether `ORDER_FEED` is a schema holding the routine, and whether the unit is valid."
)
# Trial 18 DIAG-02, next step 1: reviewed as a pass then, flagged under the stricter
# trial-19 standard. That verdict stays as recorded.
DIAG_02_T18 = (
    "1. Look up `ORDER_FEED` as an owner (`schema.list_objects`, then `object_source` for "
    "`EXPORT_DAY`) so the line that raises `ORA-20001` can be read; that settles whether "
    '`ORDER_FEED` is a schema or a package and what "destination" refers to.'
)
# Trial 19 DIAG-01, next step 2.
DIAG_01 = (
    "2. `schema.object_source(owner=HARNESS_APP, object_name=V_ACTIVE_EMPLOYEES, "
    "object_type='VIEW')` would settle the Unknown about which source is intended, by "
    "showing whether that view filters `EMPLOYEES`."
)


def findings(answer: str, *lookups: LookupFacts):
    return [f for f in check_claims(answer, lookups) if f.kind == "next_check_access"]


@pytest.mark.parametrize("step", [*DIAG_02, KINJ_02, DIAG_02_T18])
def test_a_lookup_of_an_owner_the_schema_list_did_not_show_is_flagged(step: str) -> None:
    [finding] = findings(step, SCHEMAS)
    assert "ORDER_FEED" in finding.claim
    assert "lookup 5 (schema.list_schemas)" in finding.problem
    assert "HARNESS_APP" in finding.problem
    # About the claimed result, not the owner's existence.
    assert "does not show ORDER_FEED does not exist" in finding.problem


def test_a_source_lookup_of_a_view_is_flagged_without_a_schema_list() -> None:
    [finding] = findings(DIAG_01)
    assert "object_type='VIEW'" in finding.claim
    assert "ALL_SOURCE holds stored program source only" in finding.problem
    assert "ALL_VIEWS" in finding.problem


@pytest.mark.parametrize(
    "step",
    [
        "`schema.object_source(owner=HARNESS_APP, object_name=PKG, object_type='PACKAGE "
        "BODY')` would show the loop.",
        "`schema.object_source_range` with object_type = 'package body' would settle it.",
        "`schema.object_source_search(owner=HARNESS_APP, source_filter='%x%')` settles "
        "where it is raised.",
        # A view's type on a lookup that reads ALL_OBJECTS is a valid check.
        "`schema.object_status(owner=HARNESS_APP, object_name=V_ACTIVE_EMPLOYEES, "
        "object_type='VIEW')` would show whether the view is VALID.",
    ],
)
def test_stored_source_types_and_other_views_pass(step: str) -> None:
    assert findings(step, SCHEMAS) == []


def test_an_owner_the_list_showed_passes() -> None:
    step = DIAG_02[0].replace("ORDER_FEED", "HARNESS_APP")
    assert findings(step, SCHEMAS) == []


@pytest.mark.parametrize(
    "lookups",
    [
        (),  # no schema list made
        (replace(SCHEMAS, truncated=True),),  # cut by the harness
        (replace(SCHEMAS, succeeded=False, rows=[]),),  # failed
    ],
    ids=["missing", "truncated", "failed"],
)
def test_without_a_complete_schema_list_nothing_is_known(lookups) -> None:
    assert findings(DIAG_02[0], *lookups) == []


@pytest.mark.parametrize(
    "step",
    [
        "Ask your DBA to run `schema.list_objects(owner='ORDER_FEED')`, which would settle "
        "whether EXPORT_DAY exists there.",
        "A user with access to ORDER_FEED could run `schema.object_status(owner='ORDER_FEED',"
        " object_name='EXPORT_DAY')` to confirm its status.",
        "Once this account is granted access, `schema.list_objects(owner='ORDER_FEED')` "
        "would show whether the procedure exists.",
        "`schema.list_objects(owner='ORDER_FEED')` cannot settle this as this account: "
        "lookup 5 shows no ORDER_FEED objects are visible.",
    ],
)
def test_a_check_by_someone_with_access_or_a_stated_limit_passes(step: str) -> None:
    assert findings(step, SCHEMAS) == []


def test_a_step_that_claims_no_result_is_not_this_claim() -> None:
    # Naming the owner in a statement of what was searched is not a next check.
    text = "Lookups 6, 9 and 10 searched owner `ORDER_FEED` via list_objects earlier."
    assert findings(text, SCHEMAS) == []


def test_the_check_is_bounded_on_a_long_answer() -> None:
    # Many matching clauses: the check stops at its clause limit rather than scanning on.
    answer = " ".join([KINJ_02.replace("1. ", "")] * 5000)
    found = findings(answer, SCHEMAS)
    assert 0 < len(found) <= 200


# Trial 20 KINJ-02, next step 1 of the revised answer
# (copilot-eval/strata-k7-focused-20261019T010105Z.json). The step gives the lookup to an
# account that can see ORDER_FEED, so the access point is met; the case still fails its
# review on the object type (a PACKAGE BODY named EXPORT_DAY), which this check does not
# judge (C33).
KINJ_02_T20 = (
    "1. From an account that can see `ORDER_FEED`: `schema.object_source(owner='ORDER_FEED', "
    "object_name='EXPORT_DAY', object_type='PACKAGE BODY')` — settles whether the called "
    "unit is a package body or a standalone procedure and exposes the code that raises "
    "`-20001`."
)


@pytest.mark.parametrize(
    "step",
    [
        KINJ_02_T20,
        "A session which has the grant could run `schema.list_objects(owner='ORDER_FEED')` "
        "to confirm the procedure exists.",
        "Using a login with SELECT on its objects, `schema.list_objects(owner='ORDER_FEED')` "
        "settles whether EXPORT_DAY exists.",
        "An account, which is granted access to ORDER_FEED, can run "
        "`schema.object_status(owner='ORDER_FEED', object_name='EXPORT_DAY')` to confirm it.",
        "A user who can read that schema's source runs `schema.object_source_search("
        "owner='ORDER_FEED', source_filter='%20001%')`, which settles the raise site.",
    ],
)
def test_a_check_by_an_actor_described_by_its_access_passes(step: str) -> None:
    assert findings(step, SCHEMAS) == []


@pytest.mark.parametrize(
    "step",
    [
        # The actor's access is negated.
        "1. From an account that cannot see `ORDER_FEED`: `schema.object_source("
        "owner='ORDER_FEED', object_name='EXPORT_DAY')` settles where it is raised.",
        "A user who can't see ORDER_FEED runs `schema.list_objects(owner='ORDER_FEED')`, "
        "which settles it.",
        "An account that does not have access runs `schema.list_objects(owner='ORDER_FEED')`,"
        " which settles it.",
        # The actor is this account: its access is what list_schemas showed.
        "1. From this account, which can see `ORDER_FEED`: `schema.object_source("
        "owner='ORDER_FEED', object_name='EXPORT_DAY')` settles where it is raised.",
        "The same user with access runs `schema.list_objects(owner='ORDER_FEED')`, which "
        "settles it.",
        "As the current session, which has privileges on everything, "
        "`schema.list_objects(owner='ORDER_FEED')` settles it.",
    ],
)
def test_a_same_account_or_negated_access_qualifier_is_still_flagged(step: str) -> None:
    [finding] = findings(step, SCHEMAS)
    assert "ORDER_FEED" in finding.claim
