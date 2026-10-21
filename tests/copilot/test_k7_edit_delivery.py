"""A repaired proposal is the accepted code, and a missing one is a failed request (C23, C25).

The Q1-Q4 review (docs/k7-q1-q4-review-2026-10-18.md) showed the proposal being parsed
again from the answer with the repair appended: a first answer that left a ```text fence
open lost the proposal, and one that left a ```plsql fence open put its draft into
proposedText. Its two reproductions are here, then fences it did not try (tildes,
longer runs, indented, an info string with backticks), quoted source and part labels,
each checked on both the exact proposedText and the answer the user sees.

It also showed an edit request with no usable answer ending ``succeeded``. Such a
request now ends ``failed`` with error code ``edit_output_missing``, in the stream and
in the stored record, its usage and answer kept; a ``No code change:`` diagnosis still
succeeds, and the evaluator scores the failure as a failed answer, not lost evidence.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.edit_contract import open_fence
from harness_api.copilot.fences import fenced_blocks
from harness_api.models import CopilotRequest
from tests.copilot.test_k7_edit_contract import (
    FIXED,
    HEADCOUNT,
    T18_FIX01,
    answer,
    event,
    fix01_payload,
    run,
)
from tests.copilot.test_kiwi_authoring import BODY, multi_part_payload

DRAFT = "An incomplete draft"


def stored(client: TestClient, events: list) -> CopilotRequest:
    request_id = event(events, "start")["requestId"]
    with client.app.state.harness.session_factory() as db:  # type: ignore[attr-defined]
        row = db.get(CopilotRequest, request_id)
        db.expunge(row)
        return row


def shown_blocks(text: str) -> list[str]:
    return [b.text for b in fenced_blocks(text)]


# -- C23: the proposal is the accepted block ------------------------------------------------


@pytest.mark.parametrize(
    "opening",
    [
        "```text",  # the review's reproductions
        "```plsql",
        "~~~sql",  # not tried by the review
        "````",
        "   ```sql",
        "```plsql part=body",
    ],
)
def test_an_open_fence_in_the_first_answer_cannot_change_the_proposal(
    client: TestClient, developer, opening: str
) -> None:
    first = f"Here is the explanation:\n{opening}\n{DRAFT}"
    _, events = run(client, developer, fix01_payload(), first, f"```plsql\n{FIXED}\n```")

    contract = event(events, "edit_contract")
    assert (contract["delivered"], contract["repair"]) == ("proposal", "repaired")
    proposal = event(events, "proposal")
    assert proposal is not None
    assert proposal["proposedText"] == FIXED
    assert DRAFT in proposal["rationale"]
    # On screen the draft's fence is closed first, so the accepted block is its own block.
    shown = answer(events)
    assert shown.startswith(first)
    assert open_fence(shown) is None
    assert shown_blocks(shown)[-1] == FIXED


def test_the_review_reproduction_with_an_open_plsql_fence_keeps_the_draft_out(
    client: TestClient, developer
) -> None:
    first = f"Here is the explanation:\n```plsql\n{DRAFT}"
    _, events = run(client, developer, fix01_payload(), first, f"```plsql\n{FIXED}\n```")
    proposed = event(events, "proposal")["proposedText"]
    assert DRAFT not in proposed and "```" not in proposed
    assert event(events, "proposal")["validation"]["complete"] is True


def test_code_with_fences_inside_is_captured_exactly_and_shown_whole(
    client: TestClient, developer
) -> None:
    tricky = FIXED.replace("  RETURN l_count;", "  DBMS_OUTPUT.PUT_LINE('```');\n  RETURN l_count;")
    _, events = run(client, developer, fix01_payload(), T18_FIX01, f"````plsql\n{tricky}\n````")

    assert event(events, "proposal")["proposedText"] == tricky
    assert shown_blocks(answer(events))[-1] == tricky


def test_a_repair_that_quotes_source_first_proposes_the_replacement(
    client: TestClient, developer
) -> None:
    quoted = "SELECT COUNT(*) INTO l_count FROM employee WHERE department_id = p_department_id;"
    reply = f"```sql\n{quoted}\n```\n\n```plsql\n{FIXED}\n```"
    _, events = run(client, developer, fix01_payload(), T18_FIX01, reply)
    assert event(events, "proposal")["proposedText"] == FIXED


def test_a_malformed_repair_is_not_a_proposal(client: TestClient, developer) -> None:
    _, events = run(client, developer, fix01_payload(), T18_FIX01, f"```plsql\n{FIXED}")

    contract = event(events, "edit_contract")
    assert (contract["delivered"], contract["repair"]) == ("missing", "failed:missing")
    assert event(events, "proposal") is None
    assert answer(events) == T18_FIX01


def test_parts_are_taken_from_the_accepted_labelled_blocks(client: TestClient, developer) -> None:
    changed = BODY.replace("NULL;", "DBMS_OUTPUT.PUT_LINE('dry');")
    first = f"Change the body:\n```plsql part=body\n{DRAFT}"
    _, events = run(
        client, developer, multi_part_payload(), first, f"```plsql part=body\n{changed}\n```"
    )

    proposal = event(events, "proposal")
    parts = {p["part"]: p for p in proposal["parts"]}
    assert parts["body"]["proposedText"] == changed.rstrip()
    assert parts["spec"]["changed"] is False
    assert open_fence(answer(events)) is None


def test_a_first_answer_that_kept_the_contract_is_captured_as_before(
    client: TestClient, developer
) -> None:
    _, events = run(client, developer, fix01_payload(), f"Fix:\n\n```plsql\n{FIXED}\n```\nDone.")
    proposal = event(events, "proposal")
    assert proposal["proposedText"] == FIXED
    assert proposal["rationale"] == "Fix:\n\nDone."
    assert proposal["baseRevision"] == "1"
    assert event(events, "done")["outcome"] == "succeeded"


def test_the_open_fence_reader() -> None:
    assert open_fence("text\n```sql\nx") == "```"
    assert open_fence("```sql\nx\n```") is None
    assert open_fence("~~~~\nx\n~~~") == "~~~~"
    assert open_fence("~~~~\nx\n~~~~~") is None
    assert open_fence("inline ``` is not a fence") is None
    assert open_fence(HEADCOUNT) is None


# -- C25: a missing edit is a failed request ------------------------------------------------


def test_the_review_reproduction_ends_failed_in_stream_and_record(
    client: TestClient, developer
) -> None:
    _, events = run(client, developer, fix01_payload(), T18_FIX01, "Still no proposal.")

    contract = event(events, "edit_contract")
    assert (contract["delivered"], contract["repair"]) == ("missing", "failed:missing")
    assert event(events, "proposal") is None
    done = event(events, "done")
    assert done["outcome"] == "failed"
    assert done["errorCode"] == "edit_output_missing"
    # No error event: the answer was delivered, and kept, for review.
    assert event(events, "error") is None
    assert answer(events) == T18_FIX01
    usage = event(events, "usage")
    assert usage["calls"] == 2
    row = stored(client, events)
    assert (row.outcome, row.error_code) == ("failed", "edit_output_missing")
    assert row.prompt_tokens == 200


def test_a_repair_that_fails_on_the_provider_also_ends_failed(
    client: TestClient, developer
) -> None:
    from tests.copilot.test_k7_edit_contract import Replies

    class Failing(Replies):
        async def stream(self, system: str, user_message: str):
            if self.messages:
                from harness_worker.errors import ProviderError

                self.messages.append(user_message)
                raise ProviderError("down")
                yield ""  # pragma: no cover
            async for chunk in super().stream(system, user_message):
                yield chunk

    provider = Failing(T18_FIX01)
    client.app.state.harness.copilot._provider = provider  # type: ignore[attr-defined]
    from tests.copilot.test_copilot import sse

    events = sse(client, developer, fix01_payload())
    assert event(events, "edit_contract")["repair"].startswith("failed:")
    assert event(events, "done")["outcome"] == "failed"
    assert stored(client, events).error_code == "edit_output_missing"


def test_a_no_change_diagnosis_still_succeeds(client: TestClient, developer) -> None:
    _, events = run(
        client, developer, fix01_payload(), "No code change: the table needs a SELECT grant."
    )
    assert event(events, "edit_contract")["delivered"] == "no_change"
    assert event(events, "done")["outcome"] == "succeeded"
    assert stored(client, events).outcome == "succeeded"


# -- fresh variations for the C23-C26 checkpoint -------------------------------------------
# Written after the fixes above, with other fences and wording; none was used to build them.


def test_a_long_tilde_draft_and_a_tilde_repair(client: TestClient, developer) -> None:
    first = "The table name is wrong. Draft:\n~~~~\nCREATE OR REPLACE FUNCTION headcount"
    _, events = run(client, developer, fix01_payload(), first, f"~~~plsql\n{FIXED}\n~~~")
    assert event(events, "proposal")["proposedText"] == FIXED
    shown = answer(events)
    assert open_fence(shown) is None
    assert shown_blocks(shown)[-1] == FIXED


def test_a_closed_block_then_an_open_one_then_a_repair_with_prose(
    client: TestClient, developer
) -> None:
    first = "Error:\n```text\nORA-00942\n```\nTry:\n```sql\nSELECT COUNT(*) FROM"
    reply = f"Here is the whole function.\n\n```plsql\n{FIXED}\n```\nIt reads EMPLOYEES."
    _, events = run(client, developer, fix01_payload(), first, reply)
    proposal = event(events, "proposal")
    assert proposal["proposedText"] == FIXED
    assert "SELECT COUNT(*) FROM" in proposal["rationale"]
    assert event(events, "done")["outcome"] == "succeeded"


def test_a_no_change_phrase_that_is_not_the_line_still_fails(client: TestClient, developer) -> None:
    reply = "I think there is no code change needed; the grant is missing."
    _, events = run(client, developer, fix01_payload(), T18_FIX01, reply)
    assert event(events, "edit_contract")["delivered"] == "missing"
    done = event(events, "done")
    assert (done["outcome"], done["errorCode"]) == ("failed", "edit_output_missing")


def test_an_empty_repair_reply_fails_with_usage_kept(client: TestClient, developer) -> None:
    _, events = run(client, developer, fix01_payload(), T18_FIX01, "")
    assert event(events, "done")["errorCode"] == "edit_output_missing"
    assert event(events, "usage")["calls"] == 2
    assert stored(client, events).outcome == "failed"
