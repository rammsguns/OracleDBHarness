"""C32: the lookup-free claim checks on a plain diagnosis (Kiwi off).

A plain request makes no lookups, so only the checks that rest on Oracle's documented
behaviour run: the sign of MOD and a synonym offered instead of a needed privilege. A
finding gets one revision within the request's deadline; a revision that fails, times
out, is cut off or comes back empty leaves the draft standing with its findings. Other
plain actions are unchanged; an edit diagnosis is reviewed only when it delivers a
no-change line (Sync Point 2T).

The synonym draft is trial 19 INACCESSIBLE-03's wording
(copilot-eval/strata-k7-focused-20261018T180931Z.json). Adding this path does not
resolve that trial's other failures (DIAG-03's cursor and index claims, KINJ-01's
unread floor).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi.testclient import TestClient

from harness_api.copilot.claim_checks import review_plain_claims
from harness_api.copilot.provider import FakeProvider, ProviderUsage
from harness_worker.errors import ProviderError
from tests.copilot.test_claim_checks import answer_of
from tests.copilot.test_copilot import base_payload, sse
from tests.copilot.test_kiwi import last, names

WRONG = (
    "HARNESS_APP lacks visibility to `HR.EMPLOYEES` because there is no grant. The remedy "
    "is for the `HR` owner to grant `SELECT ON HR.EMPLOYEES TO HARNESS_APP`, or to create "
    "an appropriate synonym, and then decide whether that source is intended."
)
RIGHT = (
    "HARNESS_APP lacks visibility to `HR.EMPLOYEES` because there is no grant. The remedy "
    "is for the `HR` owner to grant `SELECT ON HR.EMPLOYEES TO HARNESS_APP`; a synonym "
    "confers no privilege, so it can only follow the grant."
)
FINE = "The query names `EMPLOYEE`, which does not exist in HARNESS_APP; the table is `EMPLOYEES`."


@dataclass
class Reply:
    text: str
    fail: str = ""  # raise a provider failure after the text
    stall: float = 0.0  # wait this long before the text
    stop_reason: str = "end_turn"


class _Replies(FakeProvider):
    """Answers each plain call with the next reply and records what it was sent."""

    def __init__(self, *replies: Reply) -> None:
        super().__init__()
        self._replies = list(replies)
        self.messages: list[str] = []

    async def stream(self, system: str, user_message: str) -> AsyncIterator[str]:
        self.messages.append(user_message)
        reply = self._replies.pop(0)
        if reply.stall:
            await asyncio.sleep(reply.stall)
        self._usage = ProviderUsage(
            provider=self.name, model="fixture", prompt_tokens=10, completion_tokens=5,
            stop_reason=reply.stop_reason,
        )  # fmt: skip
        yield reply.text
        if reply.fail:
            raise ProviderError(reply.fail)


def use_replies(client: TestClient, *replies: Reply) -> _Replies:
    provider = _Replies(*replies)
    client.app.state.harness.copilot._provider = provider  # type: ignore[attr-defined]
    return provider


def diagnosis(**overrides) -> dict:
    """A diagnosis with no editor, as INACCESSIBLE-03 sends, from a current client."""

    payload = base_payload(**{"protocolVersion": "1.5", **overrides})
    payload.pop("editor")
    return payload


def checks(events) -> list[dict]:
    return [data for name, data in events if name == "claim_check"]


def test_only_the_lookup_free_checks_run() -> None:
    assert [f.kind for f in review_plain_claims(WRONG).findings] == ["remedy_alternative"]
    # Checks that need lookups do not run: nothing is known about any session.
    killing = "Killing SID 42 loses up to 320 seconds of uncommitted work."
    assert review_plain_claims(killing).findings == []
    assert review_plain_claims(FINE).findings == []


def test_a_flagged_diagnosis_is_revised_once_and_the_draft_kept(
    client: TestClient, developer
) -> None:
    provider = use_replies(client, Reply(WRONG), Reply(RIGHT))
    events = sse(client, developer, diagnosis())

    [first, second] = checks(events)
    assert (first["stage"], first["action"]) == ("answer", "revise")
    assert [f["kind"] for f in first["findings"]] == ["remedy_alternative"]
    assert (second["stage"], second["action"], second["findings"]) == ("revised", "none", [])
    assert last(events, "revision")["draft"] == WRONG
    assert answer_of(events) == RIGHT
    assert len(provider.messages) == 2
    revision = provider.messages[1]
    assert "BEGIN UNTRUSTED PREVIOUS_ANSWER" in revision and WRONG in revision
    assert "confers no privilege" in revision
    assert "lookup" not in revision.split("END UNTRUSTED PREVIOUS_ANSWER")[1]
    assert last(events, "usage")["calls"] == 2
    assert last(events, "done")["outcome"] == "succeeded"


def test_a_diagnosis_makes_no_database_lookup(client: TestClient, developer) -> None:
    use_replies(client, Reply(WRONG), Reply(RIGHT))
    events = sse(client, developer, diagnosis())
    assert not {"tool_call", "tool_result", "lineage"} & set(names(events))


def test_a_diagnosis_with_no_findings_gets_no_revision(client: TestClient, developer) -> None:
    provider = use_replies(client, Reply(FINE))
    events = sse(client, developer, diagnosis())

    assert checks(events) == [
        {"stage": "answer", "findings": [], "action": "none", "unchecked": []}
    ]
    assert "revision" not in names(events)
    assert len(provider.messages) == 1
    assert answer_of(events) == FINE


def test_a_revision_that_fails_restores_the_draft(client: TestClient, developer) -> None:
    use_replies(client, Reply(WRONG), Reply("Half a revis", fail="connection dropped"))
    events = sse(client, developer, diagnosis())

    assert answer_of(events) == WRONG
    check = last(events, "claim_check")
    assert (check["stage"], check["action"]) == ("revised", "revision_failed:provider_failure")
    assert [f["kind"] for f in check["findings"]] == ["remedy_alternative"]
    assert last(events, "revision")["restored"] is True
    assert last(events, "done")["outcome"] == "succeeded"


def test_a_revision_past_the_deadline_restores_the_draft(
    client: TestClient, developer, settings
) -> None:
    client.app.state.harness.copilot._settings = settings.model_copy(  # type: ignore[attr-defined]
        update={"copilot_request_timeout_seconds": 1}
    )
    use_replies(client, Reply(WRONG), Reply(RIGHT, stall=5))
    events = sse(client, developer, diagnosis())

    assert answer_of(events) == WRONG
    assert last(events, "claim_check")["action"] == "revision_failed:wall_time"
    assert last(events, "usage")["calls"] == 2


def test_a_cut_off_or_empty_revision_restores_the_draft(client: TestClient, developer) -> None:
    for reply, failure in [(Reply(RIGHT, stop_reason="max_tokens"), "max_output_tokens"),
                           (Reply("  "), "empty")]:  # fmt: skip
        use_replies(client, Reply(WRONG), reply)
        events = sse(client, developer, diagnosis())
        assert answer_of(events) == WRONG
        assert last(events, "claim_check")["action"] == f"revision_failed:{failure}"


def test_a_kept_claim_is_withheld_by_the_harness_and_still_reported(
    client: TestClient, developer
) -> None:
    # A claim the one revision keeps is no longer delivered as written: the harness
    # withholds the flagged sentence and says so (Wave 2X; was delivered before).
    provider = use_replies(client, Reply(WRONG), Reply(WRONG))
    events = sse(client, developer, diagnosis())

    answer = answer_of(events)
    assert answer.startswith(
        "HARNESS_APP lacks visibility to `HR.EMPLOYEES` because there is no grant."
    )
    assert "or to create an appropriate synonym" not in answer.split("[harness]")[0]
    assert "The harness withheld them" in answer
    final = checks(events)[-1]
    assert [f["kind"] for f in final["findings"]] == ["remedy_alternative"]
    assert final["action"] == "settled:corrected"
    assert last(events, "revision")["rendered"] == "claim_settlement"
    assert names(events).count("revision") == 2 and len(provider.messages) == 2


def test_an_older_client_gets_the_findings_but_no_revision(client: TestClient, developer) -> None:
    provider = use_replies(client, Reply(WRONG))
    events = sse(client, developer, diagnosis(protocolVersion="1.3"))

    assert last(events, "claim_check")["action"] == "skipped:protocol"
    assert "revision" not in names(events)
    assert len(provider.messages) == 1


def test_an_edit_diagnosis_with_no_change_is_reviewed_too(client: TestClient, developer) -> None:
    # With an editor a diagnosis promises an edit. One that delivers a no-change line is
    # still a diagnosis, so its prose is reviewed (Sync Point 2T, trial 27 INJECTION-03).
    use_replies(client, Reply("No code change: " + WRONG), Reply("No code change: " + RIGHT))
    events = sse(client, developer, base_payload(protocolVersion="1.5"))
    [first, second] = checks(events)
    assert first["action"] == "revise" and second["findings"] == []
    assert answer_of(events) == "No code change: " + RIGHT


def test_other_plain_actions_are_not_reviewed(client: TestClient, developer) -> None:
    use_replies(client, Reply(WRONG))
    events = sse(client, developer, diagnosis(action="explain_plan"))
    assert "claim_check" not in names(events)
    assert answer_of(events) == WRONG


def test_an_explanation_gets_only_the_remedy_review(client: TestClient, developer) -> None:
    # C35: explain is reviewed for its task's remedies only; the diagnosis's claim checks
    # (here, the synonym alternative) do not run on it, so nothing is found or revised.
    use_replies(client, Reply(WRONG))
    events = sse(client, developer, diagnosis(action="explain"))
    [check] = checks(events)
    assert (check["findings"], check["action"]) == ([], "none")
    assert answer_of(events) == WRONG
