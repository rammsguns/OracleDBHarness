"""Sync Point 2L control: a denied commit is not the transaction state.

The independent review (docs/k7-wave2l-review-2026-10-19.md) accepted the Wave 2L
follow-ups but one: a commit of the partial state still satisfied ``transaction_state``
when the answer denied it ("the caller will never commit duplicates", "cannot commit
partial work"). The commit and the partial state are now each read for a denial just
before them, as the statement-rollback and retained-row arms already were.

Each control is general: no case id is matched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.completeness import missing_remedies
from tests.copilot.test_claim_checks import answer_of
from tests.copilot.test_copilot import sse
from tests.copilot.test_k7_wave2k import ARCHIVE, RETAINED, TRIAL_24, checks, explain_payload
from tests.copilot.test_plain_claims import Reply, use_replies

NEVER = "Log the failure and re-raise it. The caller will never commit duplicates."
CANNOT = (
    "Log the failure and re-raise it. The caller cannot commit partial work because Oracle "
    "rolls back the entire transaction."
)


def state_findings(answer: str) -> list[str]:
    return [f.kind for f in missing_remedies("explain", ARCHIVE, answer)]


@pytest.mark.parametrize(
    "answer",
    [
        # The review's two exact negatives.
        NEVER,
        CANNOT,
        # Each denial word, before the commit or before the partial state.
        "Log the failure and re-raise it. The caller can't commit duplicates.",
        "Log the failure and re-raise it. The caller does not commit partial work.",
        "Log the failure and re-raise it. No commit leaves both tables inconsistent.",
        "Log the failure and re-raise it. Neither commit leaves duplicates.",
        "Log the failure and re-raise it. Nothing partial is ever committed.",
        "Log the failure and re-raise it. Whether a commit keeps duplicates is not shown.",
        "Log the failure and re-raise it. A commit happens without duplicates in the tables.",
        "Log the failure and re-raise it. A later commit leaves no duplicates.",
        "Log the failure and re-raise it. A later commit leaves nothing partial.",
        # The Wave 2L negatives stay negatives.
        "Log the failure and re-raise it. Statement-level rollback is irrelevant here.",
        "Log the failure and re-raise it. The inserted rows do not remain in the transaction "
        "after the DELETE fails.",
        # Trial 24's omission and its concurrency sentence.
        TRIAL_24["EXPLAIN-06"],
        "Log the error and re-raise it. Between the INSERT and the DELETE, another session "
        "could insert new rows, and those rows remain uncommitted in that session's "
        "transaction.",
    ],
)
def test_a_denied_commit_still_owes_the_transaction_state(answer: str) -> None:
    assert state_findings(answer) == ["transaction_state"]


@pytest.mark.parametrize(
    "sentence",
    [
        "Only the failing DELETE rolls back; successful INSERT rows remain in the caller's "
        "transaction.",
        "The earlier rows are not rolled back and stay pending.",
        "The caller may commit partial work or leave both copies.",
        "A later commit not only commits the archive rows, but also leaves the original rows "
        "in ORDERS so both copies remain.",
        "A later commit not just keeps the archive rows but also leaves duplicates.",
        "The caller's commit not merely persists the INSERT but leaves the orders in both tables.",
        "If the caller then commits, the orders are in both tables.",
        "The caller sees success and may commit a partially completed archive.",
        "If the caller doesn't roll back, a commit persists duplicates.",
        RETAINED,
    ],
)
def test_an_affirmed_commit_still_passes(sentence: str) -> None:
    assert state_findings(f"Log the failure and re-raise it. {sentence}") == []


@pytest.mark.parametrize("draft", [NEVER, CANNOT])
def test_a_denied_commit_is_revised_once(client: TestClient, developer, draft: str) -> None:
    revised = f"{draft} {RETAINED}"
    provider = use_replies(client, Reply(draft), Reply(revised))
    events = sse(client, developer, explain_payload())

    first, second = checks(events)
    assert (first["action"], [f["kind"] for f in first["findings"]]) == (
        "revise",
        ["transaction_state"],
    )
    assert (second["stage"], second["action"], second["findings"]) == ("revised", "none", [])
    assert len(provider.messages) == 2
    assert "rolls back only the failing statement" in provider.messages[1]
    assert answer_of(events) == revised


def test_an_affirmed_commit_is_not_revised(client: TestClient, developer) -> None:
    answer = (
        "Log the failure and re-raise it. The caller may commit partial work or leave both copies."
    )
    provider = use_replies(client, Reply(answer))
    events = sse(client, developer, explain_payload())

    [check] = checks(events)
    assert (check["action"], check["findings"]) == ("none", [])
    assert len(provider.messages) == 1
    assert answer_of(events) == answer
