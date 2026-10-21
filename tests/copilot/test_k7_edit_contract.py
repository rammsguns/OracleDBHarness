"""The edit contract: an edit request's answer delivers a proposal or says no change (Q2).

Trial 18's FIX-01 (copilot-eval/strata-k7-focused-20261018T032443Z.json) wrote a correct
``CREATE OR REPLACE FUNCTION`` as plain prose, so no proposal existed. These tests use
that answer as written, and show the harness neither lifting code out of prose nor
accepting the answer as complete: it asks once, within the request's time, and renders
what the reply gives. The other tests keep what proposal capture already did right:
quoted source passed over, deletions, package parts, and structural warnings shown
rather than hidden. Nothing here calls a model or a database.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.edit_contract import (
    CONTRACT,
    EditReading,
    read_edit,
    render,
    repair_message,
)
from harness_api.copilot.fences import Fenced
from harness_api.copilot.provider import Provider, ProviderUsage
from tests.copilot.test_copilot import base_payload, sse
from tests.copilot.test_kiwi_authoring import BODY, SPEC, multi_part_payload

HEADCOUNT = (
    "FUNCTION headcount(p_department_id IN NUMBER) RETURN NUMBER IS\n"
    "  l_count NUMBER;\n"
    "BEGIN\n"
    "  SELECT COUNT(*) INTO l_count FROM employee WHERE department_id = p_department_id;\n"
    "  RETURN l_count;\n"
    "END headcount;"
)
FIXED = (
    "CREATE OR REPLACE FUNCTION headcount(p_department_id IN NUMBER) RETURN NUMBER IS\n"
    "  l_count NUMBER;\n"
    "BEGIN\n"
    "  SELECT COUNT(*) INTO l_count\n"
    "    FROM HARNESS_APP.employees\n"
    "   WHERE department_id = p_department_id;\n"
    "\n"
    "  RETURN l_count;\n"
    "END headcount;"
)
# Trial 18, FIX-01, as the model wrote it: the replacement is prose, not a fence.
T18_FIX01 = (
    "The compiler error is:\n\n"
    "`PL/SQL: ORA-00942: table or view does not exist (line 4, column 37)`\n\n"
    "Line 4 is the `SELECT` statement:\n\n"
    "```text\n"
    "SELECT COUNT(*) INTO l_count FROM employee WHERE department_id = p_department_id;\n"
    "```\n\n"
    "The most likely cause is that the object name `employee` does not resolve in the "
    "`HARNESS_APP` schema. The supplied schema metadata lists `HARNESS_APP.EMPLOYEES`, not "
    "`HARNESS_APP.EMPLOYEE`.\n\n"
    "A complete replacement for the standalone function is:\n\n"
    f"{FIXED}\n\n"
    "What changed: `FROM employee` was replaced with `FROM HARNESS_APP.employees`, using "
    "the table name shown in the supplied schema metadata. I have not compiled this "
    "replacement."
)


def fix01_payload(**overrides) -> dict:
    return base_payload(
        userMessage="This function will not compile.",
        attachments=[
            {"category": "selected_source", "name": "headcount", "content": HEADCOUNT},
            {
                "category": "error_text",
                "name": "compiler output",
                "content": "PL/SQL: ORA-00942: table or view does not exist (line 4, column 37)",
            },
        ],
        editor={"editorId": "eval-fix-01", "revision": "1", "text": HEADCOUNT},
        **overrides,
    )


class Replies(Provider):
    """Answers each stream call with the next reply, optionally after a delay."""

    name = "replies"

    def __init__(self, *replies: str | tuple[str, float]) -> None:
        self._replies = list(replies)
        self.messages: list[str] = []
        self._usage = ProviderUsage()

    @property
    def ready(self) -> bool:
        return True

    async def stream(self, system: str, user_message: str) -> AsyncIterator[str]:
        self.messages.append(user_message)
        reply = self._replies.pop(0)
        text, delay = (reply, 0.0) if isinstance(reply, str) else reply
        for start in range(0, len(text), 50):
            yield text[start : start + 50]
        if delay:
            await asyncio.sleep(delay)
        self._usage = ProviderUsage(
            provider=self.name, model="m", prompt_tokens=100, completion_tokens=10
        )

    def usage(self) -> ProviderUsage:
        return self._usage


def run(client: TestClient, headers, payload: dict, *replies) -> tuple[Replies, list]:
    provider = Replies(*replies)
    client.app.state.harness.copilot._provider = provider  # type: ignore[attr-defined]
    return provider, sse(client, headers, payload)


def event(events: list, name: str) -> dict | None:
    found = [data for n, data in events if n == name]
    return found[-1] if found else None


def answer(events: list) -> str:
    return "".join(data["text"] for n, data in events if n == "delta")


def reading(text: str, editor: str = HEADCOUNT, action: str = "diagnose") -> EditReading:
    return read_edit(text, action=action, editor=editor, sources=[editor])


# -- reading the contract -------------------------------------------------------------------


def test_trial_18_fix01_prose_is_not_read_as_a_proposal() -> None:
    assert reading(T18_FIX01).delivered == "missing"


@pytest.mark.parametrize(
    "text",
    [
        f"Fix:\n\n```plsql\n{FIXED}\n```\n",
        f"Fix:\n\n```sql\n{FIXED}\n```\n",
        f"Fix:\n\n```\n{FIXED}\n```\n",
        # A quoted line in a SQL block first: passed over, the replacement after it is taken.
        "Line 4:\n\n```sql\nSELECT COUNT(*) INTO l_count FROM employee WHERE department_id "
        f"= p_department_id;\n```\n\nFix:\n\n```plsql\n{FIXED}\n```\n",
    ],
)
def test_a_fenced_replacement_keeps_the_contract(text: str) -> None:
    result = reading(text)
    assert result.delivered == "proposal"
    assert result.blocks[0].text == FIXED


@pytest.mark.parametrize(
    "line",
    [
        "No code change: the account lacks SELECT on HARNESS_APP.EMPLOYEES.",
        "**No code change:** the grant is missing.",
        "- No code change: the synonym was dropped by the DBA.",
    ],
)
def test_a_diagnosis_may_say_no_code_change(line: str) -> None:
    result = reading(f"The cause is a privilege.\n\n{line}\n")
    assert result.delivered == "no_change"
    assert result.no_change == line


def test_only_a_diagnosis_may_say_no_code_change() -> None:
    text = "No code change: nothing to draft."
    assert reading(text, action="propose").delivered == "missing"
    assert reading(text, action="test_block").delivered == "missing"


def test_a_no_change_line_inside_a_fence_does_not_count() -> None:
    assert reading("```text\nNo code change: quoted.\n```\n").delivered == "missing"


def test_a_deletion_of_part_of_the_editor_is_a_proposal() -> None:
    editor = "BEGIN\n  NULL;\n  log_it;\nEND;"
    result = read_edit(
        "Drop the call:\n\n```plsql\nBEGIN\n  NULL;\nEND;\n```",
        action="diagnose",
        editor=editor,
        sources=[editor],
    )
    assert result.delivered == "proposal"


def test_package_parts_are_read_by_their_labels() -> None:
    text = f"```plsql part=body\n{BODY}\n```\n\n```plsql part=other\n{SPEC}\n```"
    result = read_edit(
        text, action="propose", editor="", sources=[SPEC, BODY], parts=["spec", "body"]
    )
    assert [b.part for b in result.blocks] == ["body"]
    unlabelled = read_edit(
        f"```plsql\n{BODY}\n```", action="propose", editor="", sources=[], parts=["spec", "body"]
    )
    assert unlabelled.delivered == "missing"


def test_render_uses_the_accepted_block_and_a_fence_the_code_cannot_close() -> None:
    block = Fenced(
        language="", part=None, text="BEGIN\n  -- ``` here\n  NULL;\nEND;", start=0, end=0
    )
    rendered = render(EditReading("proposal", blocks=(block,)))
    assert rendered.startswith("````plsql\n") and rendered.endswith("\n````")
    part = Fenced(language="plsql", part="body", text=BODY, start=0, end=0)
    assert render(EditReading("proposal", blocks=(part,))).startswith("```plsql part=body\n")


def test_the_repair_message_marks_the_answer_as_data_and_names_no_case() -> None:
    message = repair_message(
        "This function will not compile.", T18_FIX01, action="diagnose", parts=[]
    )
    assert "----- BEGIN UNTRUSTED PREVIOUS_ANSWER -----" in message
    assert T18_FIX01 in message
    assert "No code change:" in message
    assert "same kind as the code you were given" in message
    for word in ("FIX-01", "headcount", "EMPLOYEES", "case"):
        assert word not in message.split("----- END UNTRUSTED PREVIOUS_ANSWER -----")[1]


# -- the service: one bounded repair --------------------------------------------------------


def test_trial_18_fix01_is_repaired_into_a_reviewable_proposal(
    client: TestClient, developer
) -> None:
    provider, events = run(client, developer, fix01_payload(), T18_FIX01, f"```plsql\n{FIXED}\n```")

    assert len(provider.messages) == 2
    assert "UNTRUSTED PREVIOUS_ANSWER" in provider.messages[1]
    contract = event(events, "edit_contract")
    assert contract is not None
    assert contract["contract"] == CONTRACT
    assert (contract["delivered"], contract["repair"]) == ("proposal", "repaired")
    proposal = event(events, "proposal")
    assert proposal is not None
    assert proposal["proposedText"] == FIXED
    assert proposal["validation"]["complete"] is True
    assert proposal["validation"]["compiled"] is False
    # The answer the user reads ends with the harness's rendering of the accepted block.
    assert answer(events) == T18_FIX01 + f"\n\n```plsql\n{FIXED}\n```\n"
    # Both calls are in the usage.
    usage = event(events, "usage")
    assert usage["calls"] == 2
    assert usage["promptTokens"] == 200
    names = [n for n, _ in events]
    assert names.index("edit_contract") < names.index("proposal") < names.index("done")


def test_a_repair_that_is_still_prose_is_a_failed_answer(client: TestClient, developer) -> None:
    provider, events = run(client, developer, fix01_payload(), T18_FIX01, f"Here it is:\n{FIXED}")

    contract = event(events, "edit_contract")
    assert (contract["delivered"], contract["repair"]) == ("missing", "failed:missing")
    assert contract["repairReply"] == f"Here it is:\n{FIXED}"
    assert event(events, "proposal") is None
    # Nothing from the reply is added to the answer.
    assert answer(events) == T18_FIX01


def test_a_repair_may_say_no_code_change(client: TestClient, developer) -> None:
    _, events = run(
        client, developer, fix01_payload(), "The grant is missing.", "No code change: grant SELECT."
    )
    contract = event(events, "edit_contract")
    assert (contract["delivered"], contract["repair"]) == ("no_change", "repaired")
    assert answer(events).endswith("\n\nNo code change: grant SELECT.\n")
    assert event(events, "proposal") is None


def test_a_bare_member_in_a_repair_is_not_wrapped_by_the_harness(
    client: TestClient, developer
) -> None:
    _, events = run(
        client,
        developer,
        fix01_payload(),
        T18_FIX01,
        f"```plsql\n{HEADCOUNT.replace('employee ', 'employees ')}\n```",
    )
    proposal = event(events, "proposal")
    assert proposal["proposedText"].startswith("FUNCTION headcount")
    assert proposal["validation"]["complete"] is False
    assert proposal["validation"]["kind"] == "fragment"


def test_several_statements_stay_a_proposal_with_its_warning(client: TestClient, developer) -> None:
    provider, events = run(
        client, developer, base_payload(), "```sql\nSELECT 1 FROM dual; SELECT 2 FROM dual;\n```"
    )
    assert len(provider.messages) == 1
    assert event(events, "edit_contract")["repair"] == "none"
    assert event(events, "proposal")["validation"]["complete"] is False


def test_an_answer_that_keeps_the_contract_gets_no_repair(client: TestClient, developer) -> None:
    provider, events = run(client, developer, fix01_payload(), f"Fix:\n\n```plsql\n{FIXED}\n```")
    assert len(provider.messages) == 1
    assert event(events, "edit_contract") == {
        "contract": CONTRACT,
        "delivered": "proposal",
        "repair": "none",
    }


def test_the_repair_has_only_what_is_left_of_the_request_time(
    client: TestClient, developer, settings
) -> None:
    copilot = client.app.state.harness.copilot  # type: ignore[attr-defined]
    copilot._settings = settings.model_copy(update={"copilot_request_timeout_seconds": 0.5})
    started = time.perf_counter()
    _, events = run(client, developer, fix01_payload(), T18_FIX01, (f"```plsql\n{FIXED}\n```", 5.0))

    assert time.perf_counter() - started < 3
    contract = event(events, "edit_contract")
    assert (contract["delivered"], contract["repair"]) == ("missing", "failed:wall_time")
    assert event(events, "proposal") is None
    assert event(events, "usage")["complete"] is False
    # No usable edit within the request's time is a failed request (C25).
    assert event(events, "done")["outcome"] == "failed"
    assert event(events, "done")["errorCode"] == "edit_output_missing"


def test_a_first_answer_past_the_request_time_is_a_provider_failure(
    client: TestClient, developer, settings
) -> None:
    copilot = client.app.state.harness.copilot  # type: ignore[attr-defined]
    copilot._settings = settings.model_copy(update={"copilot_request_timeout_seconds": 0.3})
    started = time.perf_counter()
    provider, events = run(client, developer, fix01_payload(), (T18_FIX01, 5.0))

    assert time.perf_counter() - started < 3
    assert len(provider.messages) == 1
    error = event(events, "error")
    assert error is not None and error["code"] == "provider_failure"
    assert event(events, "edit_contract") is None


def test_multi_part_answers_are_repaired_with_part_labels(client: TestClient, developer) -> None:
    changed = BODY.replace("NULL;", "DBMS_OUTPUT.PUT_LINE('dry');")
    provider, events = run(
        client,
        developer,
        multi_part_payload(),
        f"Change the body:\n\n```plsql\n{changed}\n```",
        f"```plsql part=body\n{changed}\n```",
    )
    assert "part=spec" in provider.messages[1]
    assert event(events, "edit_contract")["repair"] == "repaired"
    proposal = event(events, "proposal")
    assert proposal["multiPart"] is True
    assert {p["part"]: p["changed"] for p in proposal["parts"]} == {"spec": False, "body": True}


@pytest.mark.parametrize("action", ["explain", "validate", "explain_plan"])
def test_actions_that_do_not_edit_have_no_contract(
    client: TestClient, developer, action: str
) -> None:
    provider, events = run(client, developer, base_payload(action=action), "Prose only.")
    assert len(provider.messages) == 1
    assert event(events, "edit_contract") is None


def test_a_request_without_an_editor_has_no_contract(client: TestClient, developer) -> None:
    payload = base_payload()
    del payload["editor"]
    provider, events = run(client, developer, payload, "Prose only.")
    assert len(provider.messages) == 1
    assert event(events, "edit_contract") is None
