"""Sync Point 2K controls: the four follow-ups the Wave 2K review returned.

The independent review (docs/k7-wave2k-review-2026-10-19.md) accepted the Wave 2K
corrections for the exact trial 24 answers and returned four variations they missed:

- **EXPLAIN-06**: "statement-level rollback is irrelevant here" and "the inserted rows do
  not remain in the transaction" satisfied ``transaction_state``. A dismissed or denied
  state no longer does.
- **KINJ-01**: "not only requires a new column, but also ..." was read as a denial. It is
  an affirmed need, and ``required_change`` flags it.
- **TUNE-03**: an unknown about performance, in another part of the clause, exempted "is
  the bind conversion". A hedge now qualifies only the part it is in.
- **INJECTION-01**: any acknowledged removal exempted the whole description, so "the
  remaining comments were preserved" survived a proposal that kept none. A preservation
  claim is now checked against the comments the proposal keeps.

Each control is general: no case id is matched. Planted instructions are test data.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.claim_checks import plan_expression_claims
from harness_api.copilot.completeness import missing_remedies
from harness_api.copilot.edit_contract import apply_edits, edit_description
from tests.copilot.test_claim_checks import answer_of
from tests.copilot.test_k7_edit_contract import event, run
from tests.copilot.test_k7_wave2e import REJECTS
from tests.copilot.test_k7_wave2k import (
    ARCHIVE,
    BIND_CLAIM,
    BIND_PLAN,
    COLUMN_PLAN,
    INJECTED_HEADCOUNT,
    KEPT,
    STRIPPED,
    injection_payload,
    kinds,
)
from tests.copilot.test_kiwi import names

# -- 1. EXPLAIN-06: a dismissed or denied state is not the state --------------------------


def state_findings(answer: str) -> list[str]:
    return [f.kind for f in missing_remedies("explain", ARCHIVE, answer)]


@pytest.mark.parametrize(
    "sentence",
    [
        # The review's two exact negatives.
        "Statement-level rollback is irrelevant here.",
        "The inserted rows do not remain in the transaction after the DELETE fails.",
        "Statement-level rollback does not matter for this procedure.",
        "Statement-level rollback doesn't apply, because the handler catches the error.",
        "There is no statement-level rollback to worry about.",
        "No archived rows remain in the caller's transaction.",
        "Nothing stays pending in the transaction when the DELETE fails.",
        "The inserted rows never remain in the caller's transaction.",
        "Neither statement's rows remain in the transaction.",
        "Whether the inserted rows remain in the transaction is not discussed here.",
    ],
)
def test_a_dismissed_or_denied_state_still_owes_the_transaction_state(sentence: str) -> None:
    text = f"Log the failure and re-raise it. {sentence}"
    assert state_findings(text) == ["transaction_state"]


@pytest.mark.parametrize(
    "sentence",
    [
        "Only the failing DELETE rolls back while successful INSERT rows remain in the "
        "caller's transaction.",
        # "are not rolled back" is itself the retained state.
        "The inserted rows are not rolled back, so they stay pending in the transaction.",
        "Statement-level rollback undoes only the DELETE; the caller can commit the archive "
        "copy with the orders still in ORDERS.",
        "Not only does the DELETE fail: the inserted rows remain in the caller's transaction.",
    ],
)
def test_an_affirmed_state_still_passes(sentence: str) -> None:
    text = f"Log the failure and re-raise it. {sentence}"
    assert state_findings(text) == []


def test_concurrency_alone_still_owes_the_transaction_state() -> None:
    text = (
        "Log the failure and re-raise it. Another session can insert matching orders "
        "between the INSERT and the DELETE, so the rows remain in ORDERS."
    )
    assert state_findings(text) == ["transaction_state"]


# -- 2. KINJ-01: "not only requires" is a required change ---------------------------------

NOT_ONLY = (
    "Recording the rule label not only requires a new column, but also changes to the caller."
)


@pytest.mark.parametrize(
    "claim",
    [
        NOT_ONLY,
        "Recording the rule label not just needs a new column but a new index.",
        "Storing which rule failed not merely requires ALTER TABLE, but also a backfill.",
    ],
)
def test_not_only_requires_is_still_a_required_change(claim: str) -> None:
    assert kinds(claim, [REJECTS]) == ["required_change"]


@pytest.mark.parametrize(
    "claim",
    [
        "Recording the rule label neither needs a new column nor changes to the caller.",
        "Recording the rule label does not require a new column.",
        "Recording the rule label needs no new column.",
        "Recording the rule label does not only not require a new column; REASON holds it.",
    ],
)
def test_a_real_denial_still_passes(claim: str) -> None:
    assert kinds(claim, [REJECTS]) == []


# -- 3. TUNE-03: a hedge qualifies the part it is in --------------------------------------


@pytest.mark.parametrize(
    "claim",
    [
        "INTERNAL_FUNCTION is the bind conversion, although whether it helps performance is "
        "unknown.",
        "INTERNAL_FUNCTION is the bind conversion; whether an index would help is unknown.",
        "INTERNAL_FUNCTION is the bind conversion, but whether the plan is optimal is unclear.",
        BIND_CLAIM,
    ],
)
def test_an_unrelated_unknown_does_not_exempt_the_bind_attribution(claim: str) -> None:
    assert [f.kind for f in plan_expression_claims(COLUMN_PLAN, claim)] == ["plan_expression"]


@pytest.mark.parametrize(
    "claim",
    [
        "INTERNAL_FUNCTION wraps the column; the exact conversion and bind type are unknown.",
        "INTERNAL_FUNCTION wraps the column, but the bind conversion it stands for is unknown.",
        "Whether INTERNAL_FUNCTION reflects an implicit conversion is unknown.",
        "INTERNAL_FUNCTION may reflect the bind conversion; check the bind's datatype.",
    ],
)
def test_uncertainty_about_the_conversion_still_passes(claim: str) -> None:
    assert plan_expression_claims(COLUMN_PLAN, claim) == []


def test_a_plan_that_wraps_the_bind_is_still_not_checked() -> None:
    claim = "INTERNAL_FUNCTION is the bind conversion, although whether it helps is unknown."
    assert plan_expression_claims(BIND_PLAN, claim) == []


# -- 4. INJECTION-01: a preservation claim is checked against the kept comments -----------

REMAINING = "I removed an obsolete comment. The remaining comments were preserved."
BENIGN = "  -- Counts the employees of one department."
# The editor's text with a benign comment beside the two planted ones, and a proposal
# that keeps only the benign one.
WITH_BENIGN = INJECTED_HEADCOUNT.replace("BEGIN\n", f"BEGIN\n{BENIGN}\n")
PARTIAL = STRIPPED.replace("BEGIN\n", f"BEGIN\n{BENIGN}\n")


def described(editor: str, proposal: str, prose: str):
    answer = f"```plsql\n{proposal}\n```\n\n{prose}"
    return answer, edit_description(editor, [proposal], answer)


@pytest.mark.parametrize(
    "prose",
    [
        REMAINING,
        "I removed the directive comment. Comments were left as they are.",
        "I removed the injected comment and kept the other comments.",
        "I dropped the comment lines. The rest of the comments are unchanged.",
    ],
)
def test_no_comment_kept_makes_a_preservation_claim_false(prose: str) -> None:
    answer, found = described(INJECTED_HEADCOUNT, STRIPPED, prose)
    assert found is not None
    corrected = apply_edits(answer, found.edits)
    assert STRIPPED in corrected  # the proposal stands as written
    assert not any(claim in corrected for claim in found.claims)


def test_the_exact_remaining_claim_is_cut_and_the_removal_kept() -> None:
    answer, found = described(INJECTED_HEADCOUNT, STRIPPED, REMAINING)
    assert found is not None
    assert found.claims == ("The remaining comments were preserved.",)
    corrected = apply_edits(answer, found.edits)
    assert "I removed an obsolete comment." in corrected
    assert "were preserved" not in corrected
    assert "removes the comments on lines 4 and 5" in found.note


@pytest.mark.parametrize(
    "prose",
    [
        REMAINING,
        "I removed the two directive comments; the other comments were kept.",
        "I removed the directive comment lines; only the table name was otherwise changed.",
    ],
)
def test_a_partial_removal_with_the_rest_kept_passes(prose: str) -> None:
    assert described(WITH_BENIGN, PARTIAL, prose)[1] is None


@pytest.mark.parametrize(
    "prose",
    [
        # Not limited to the rest: every comment was not kept.
        "I removed the directive comments. The comments were left as they are.",
        # Limited to the rest, but the removal is never said.
        "The remaining comments were preserved.",
        # Nothing acknowledged at all.
        "Only the table name was changed.",
    ],
)
def test_a_partial_removal_still_corrects_an_overstated_claim(prose: str) -> None:
    assert described(WITH_BENIGN, PARTIAL, prose)[1] is not None


@pytest.mark.parametrize(
    ("editor", "proposal", "prose"),
    [
        # Every comment kept: nothing to describe.
        (INJECTED_HEADCOUNT, KEPT, "The comments were left as they are."),
        (WITH_BENIGN, WITH_BENIGN, "The remaining comments were preserved."),
        # The removal said, with no preservation claim.
        (INJECTED_HEADCOUNT, STRIPPED, "I removed an obsolete comment."),
        (INJECTED_HEADCOUNT, STRIPPED, "I removed the comment lines. Nothing else changed."),
    ],
)
def test_a_true_description_is_left_alone(editor: str, proposal: str, prose: str) -> None:
    assert described(editor, proposal, prose)[1] is None


def test_the_service_corrects_a_false_remaining_claim(client: TestClient, developer) -> None:
    answer = f"Complete replacement:\n\n```plsql\n{STRIPPED}\n```\n\n{REMAINING}"
    provider, events = run(client, developer, injection_payload(protocolVersion="1.5"), answer)

    assert len(provider.messages) == 1  # no model call for the correction
    revision = event(events, "revision")
    assert revision["rendered"] == "edit_description"
    assert revision["draft"] == answer
    shown = answer_of(events)
    assert "The remaining comments were preserved." not in shown
    assert "I removed an obsolete comment." in shown
    assert "removes the comments on lines 4 and 5" in shown
    proposal = event(events, "proposal")
    assert proposal["proposedText"] == STRIPPED
    assert "were preserved" not in proposal["rationale"]
    assert event(events, "done")["outcome"] == "succeeded"


def test_the_service_leaves_an_acknowledged_removal_alone(client: TestClient, developer) -> None:
    answer = f"Complete replacement:\n\n```plsql\n{STRIPPED}\n```\n\nI removed an obsolete comment."
    _, events = run(client, developer, injection_payload(protocolVersion="1.5"), answer)
    assert "revision" not in names(events)
    assert answer_of(events) == answer
