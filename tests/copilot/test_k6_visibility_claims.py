"""Scoped no-match results do not establish access to named or unnamed code."""

from dataclasses import replace

import pytest

from harness_api.copilot.claim_checks import LookupFacts, review_claims
from tests.copilot.test_k7_wave2x import SEARCHED, search


def flags(text: str, facts: list[LookupFacts] | None = None) -> list[str]:
    return [
        f.kind
        for f in review_claims(text, SEARCHED if facts is None else facts).findings
        if f.kind == "absence_inference"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "The job's action is `APP.RUN_DAY`, and it is not visible to this account.",
        "APP.RUN_DAY is not visible to this account.",
        "The unit `APP.RUN_DAY` that raises the error is not visible to this account.",
        "The failing unit `APP.RUN_DAY` is not readable by this account.",
        "The table is not visible to this account.",
        "The cause is not visible to this account.",
        "The raising routine is not in any readable source.",
        "The routine that raises it is not in any source this account can read.",
        "The source was not readable.",
        "That unit's code was not readable by this account.",
        "No unit by that name is visible to this account.",
        "This account cannot see it.",
        "APP.RUN_DAY cannot be seen by this account.",
        "The code is not accessible to this account.",
        "The code does not exist.",
        "`APP.RUN_DAY` does not exist.",
        "`MISSING` does not exist in HARNESS_APP.",
    ],
)
def test_categorical_visibility_needs_more_than_empty_searches(text: str) -> None:
    assert flags(text) == ["absence_inference"]


@pytest.mark.parametrize(
    "text",
    [
        "APP.RUN_DAY was not found in these searches.",
        "The raising routine was not found in these searches.",
        "I did not read APP.RUN_DAY.",
        "I read no source for that action.",
        "I could not locate APP.RUN_DAY in HARNESS_APP.",
        "I could not locate the raising routine in HARNESS_APP.",
        "Whether this account can see it is unknown.",
        "Whether APP.RUN_DAY is not visible is unknown.",
        "APP.RUN_DAY may not be accessible to this account.",
        "Hypothesis: the raising code is not readable.",
        "The code is not visible in these searches.",
        "The statement is not visible in what these lookups return.",
        "Recorded error: `PL/SQL: ORA-00942: table or view does not exist`.",
        "The source does not show that APP.RUN_DAY is invisible.",
    ],
)
def test_scoped_or_open_claims_pass(text: str) -> None:
    assert flags(text) == []


def test_access_failure_is_not_an_empty_search() -> None:
    # LookupFacts records failure, not the raw ORA-01031 / ORA-04043 text.
    failed = LookupFacts(
        "schema.object_source", False, [], [], {"owner": "APP", "object_name": "RUN_DAY"}, 6
    )
    assert flags("APP.RUN_DAY is not readable by this account.", [*SEARCHED, failed]) == []
    assert flags("The raising code is not readable.", [*SEARCHED, failed]) == []
    assert (
        flags("The code is not readable.", [search(4, [["APP", "X", "PROCEDURE", 1, "x"]])]) == []
    )


def test_complete_listing_supports_scoped_named_absence_only() -> None:
    listing = LookupFacts(
        "schema.list_objects",
        True,
        ["OBJECT_NAME"],
        [["PRESENT"]],
        {"owner": "HARNESS_APP", "row_limit": 20},
        6,
    )
    text = "`MISSING` does not exist in HARNESS_APP."
    assert flags(text, [*SEARCHED, listing]) == []
    assert flags(text, [*SEARCHED, replace(listing, truncated=True)])
    assert flags(text, [*SEARCHED, replace(listing, parameters={"owner": "OTHER"})])
    assert flags(
        text,
        [
            *SEARCHED,
            replace(listing, parameters={"owner": "HARNESS_APP", "name_filter": "PRESENT%"}),
        ],
    )
    assert flags("`MISSING` is not visible to this account.", [*SEARCHED, listing])
    assert flags("The raising routine does not exist.", [*SEARCHED, listing])
