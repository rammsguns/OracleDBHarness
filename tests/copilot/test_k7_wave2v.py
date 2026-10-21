"""Privilege classification controls on direct and bounded diagnosis paths."""

import pytest

from harness_api.copilot.claim_checks import review_plain_claims
from tests.copilot.test_claim_checks import answer_of
from tests.copilot.test_copilot import base_payload, sse
from tests.copilot.test_plain_claims import Reply, use_replies

BAD = [
    "SELECT ON HR.JOBS is a role.",
    "The role SELECT ON HR.JOBS is disabled.",
    "Object privileges like SELECT are roles.",
    "The `select` role is disabled.",
    "UPDATE ON SCOTT.EMP was granted as a role.",
]
GOOD = (
    "SELECT ANY TABLE is a broad system privilege, not a role; "
    "direct privileges differ from privileges received through roles."
)


def answer(claim):
    return (
        "No code change: a direct object grant is needed.\n\n"
        + claim
        + "\n\nGRANT SELECT ON HR.JOBS TO HARNESS_APP;"
    )


@pytest.mark.parametrize("claim", BAD)
def test_object_privilege_is_not_a_role(claim):
    assert [f.kind for f in review_plain_claims(claim).findings] == ["rights_model"]


@pytest.mark.parametrize("claim", BAD)
@pytest.mark.parametrize("editor", [False, True])
def test_object_privilege_diagnosis_is_revised_once(client, developer, claim, editor):
    payload = base_payload(protocolVersion="1.5")
    if not editor:
        payload.pop("editor")
    provider = use_replies(client, Reply(answer(claim)), Reply(answer(GOOD)))
    events = sse(client, developer, payload)
    checks = [data for name, data in events if name == "claim_check"]
    assert [check["action"] for check in checks] == ["revise", "none"]
    assert [f["kind"] for f in checks[0]["findings"]] == ["rights_model"]
    assert answer_of(events) == answer(GOOD)
    assert len(provider.messages) == 2
    assert not any(name == "proposal" for name, _ in events)


@pytest.mark.parametrize(
    "claim",
    [
        "Grant SELECT to a role.",
        "A role includes SELECT ON HR.JOBS.",
        "SELECT_CATALOG_ROLE is a role.",
        GOOD,
        "Select a role, insert a row, then execute the procedure.",
        "SELECT ON HR.JOBS is not a role.",
    ],
)
def test_correct_privilege_and_ordinary_language_pass(claim):
    assert review_plain_claims(claim).findings == []
