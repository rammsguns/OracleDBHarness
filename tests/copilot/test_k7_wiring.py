"""C34/C35 wired into the service, within the one bounded revision (AGY-2).

Through the stand-in database and the scripted provider; no model and no Oracle call.

- A Kiwi diagnosis's read claims (C34-1) and a malformed ``kiwi-next-checks`` block join
  the claim checks' findings before the one revision decision: never a second turn.
- After the answer settles (revised, restored or not revised), a valid block replaces
  the next-steps section with the harness's rendering of its checked steps, and the
  lines the source lookups returned are appended. Neither is a model call, so the
  budget event is the same as without them.
- A plain explanation of supplied source is checked for the remedies its task asks for
  (C35), with the same one bounded revision as a plain diagnosis, and its revised answer
  is checked the same way.

The stand-in's ``HARNESS_APP.EMPLOYEE_REPORT`` body is the 19-line package of
``oracle/qualification/01_fixtures.sql``: lines 1-9 hold ``headcount`` (2-7) whole and
the start of ``report_department`` (8-11), whose END is not among them.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.provider import ScriptedTurn, ToolCall
from harness_api.copilot.toolbox import tool_name_for
from tests.copilot.test_claim_checks import answer_of, plain_payload
from tests.copilot.test_completeness import (
    DEBUG_LOGS,
    IGNORE_GOTO,
    IGNORE_RETURN,
    LEAVES_OUTER,
    STAYS_INNER,
    SWALLOWS,
    T20,
)
from tests.copilot.test_copilot import sse
from tests.copilot.test_kiwi import last, names, use_script
from tests.copilot.test_plain_claims import Reply, checks, diagnosis, use_replies

RANGE_TOOL = tool_name_for("schema.object_source_range")


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"kiwi_enabled": True})


def body_lines(start: int = 1, end: int = 9) -> ScriptedTurn:
    call = ToolCall(
        id="r1",
        name=RANGE_TOOL,
        input={
            "why": "Read the body.",
            "owner": "HARNESS_APP",
            "object_name": "EMPLOYEE_REPORT",
            "object_type": "PACKAGE BODY",
            "start_line": start,
            "end_line": end,
            "row_limit": 50,
        },
    )
    return ScriptedTurn(text="Reading. ", tool_calls=(call,))


ERRORS_STEP = {
    "actor": "this_account",
    "operation": "schema.object_errors",
    "selectors": {"owner": "HARNESS_APP", "object_name": "EMPLOYEE_REPORT"},
    "establishes": "compile_errors",
    "about": "which line the compiler rejects",
}


def with_block(prose: str, *steps: dict, raw: str | None = None) -> str:
    body = raw if raw is not None else json.dumps(list(steps))
    return (
        f"Finding: {prose}\n\n"
        "Next steps:\n"
        "1. Run something the harness never checked.\n\n"
        f"```kiwi-next-checks\n{body}\n```\n"
    )


def run(client: TestClient, dba, targets, *turns: ScriptedTurn, version: str = "1.5"):
    provider = use_script(client, *turns)
    events = sse(client, dba, plain_payload(targets["development"]["id"], protocolVersion=version))
    return provider, events


GOOD = "The body is INVALID; lines 1-9 show `headcount` (lookup 1)."
OVERCLAIM = "I read report_department in full; it only prints the count (lookup 1)."


def test_a_valid_block_replaces_the_next_steps_and_coverage_is_appended(
    client: TestClient, dba, targets
) -> None:
    answer = with_block(GOOD, ERRORS_STEP)
    _, events = run(client, dba, targets, body_lines(), ScriptedTurn(text=answer))

    shown = answer_of(events)
    # Rendered from the checked step, not the model's prose or raw JSON.
    assert "## Next steps" in shown and "Written by the harness" in shown
    assert "`schema.object_errors(owner='HARNESS_APP', object_name='EMPLOYEE_REPORT')`" in shown
    assert "Open question (the model's interpretation, not checked): which line" in shown
    assert "kiwi-next-checks" not in shown and "never checked" not in shown
    # The harness's coverage, from the lookup, after the answer.
    assert shown.rstrip().endswith("its END was not returned)")
    assert "HEADCOUNT: implementation lines 2-7 returned, from its first line through its END" in (
        shown
    )
    # The model's text is kept for review as the draft the rendering replaced.
    rendered = [d for n, d in events if n == "revision"]
    assert [d.get("rendered") for d in rendered] == ["next_checks"]
    assert "kiwi-next-checks" in rendered[0]["draft"]
    # No model call: no revision turn, and the budget is as without the rendering.
    [check] = checks(events)
    assert check["action"] == "none"
    assert last(events, "budget")["steps"] == 2
    assert last(events, "budget")["revisions"] == 0
    assert last(events, "done")["outcome"] == "succeeded"


def test_an_answer_without_a_block_keeps_its_prose_steps(client: TestClient, dba, targets) -> None:
    answer = f"Finding: {GOOD}\n\nNext steps:\n1. Read the rest of the body."
    _, events = run(client, dba, targets, body_lines(), ScriptedTurn(text=answer))
    shown = answer_of(events)
    assert shown.startswith("Reading. " + answer)
    assert "Read the rest of the body." in shown
    assert "revision" not in names(events)
    assert "## Lines read (from the lookups)" in shown


def test_read_and_claim_findings_share_the_one_revision(client: TestClient, dba, targets) -> None:
    wrong = OVERCLAIM + " Next: read SID 42's `ROW_WAIT_OBJ#` in `V$SESSION` (lookup 9)."
    provider, events = run(
        client, dba, targets, body_lines(), ScriptedTurn(text=wrong), ScriptedTurn(text=GOOD)
    )
    first, revised = checks(events)
    kinds = {f["kind"] for f in first["findings"]}
    assert "read_coverage" in kinds and len(kinds) > 1
    assert first["action"] == "revise" and revised["findings"] == []
    # One revision turn, carrying every finding.
    assert len(provider.conversations[0].notes) == 1
    note = provider.conversations[0].notes[0]
    assert "REPORT_DEPARTMENT" in note
    assert last(events, "budget")["revisions"] == 1
    assert answer_of(events).startswith(GOOD)


def test_a_read_claim_not_corrected_keeps_its_finding(client: TestClient, dba, targets) -> None:
    provider, events = run(
        client,
        dba,
        targets,
        body_lines(),
        ScriptedTurn(text=OVERCLAIM),
        ScriptedTurn(text=OVERCLAIM),
    )
    first, revised = checks(events)
    assert [f["kind"] for f in first["findings"]] == ["read_coverage"]
    assert [f["kind"] for f in revised["findings"]] == ["read_coverage"]
    # Still only one revision: a remaining finding never asks again.
    assert len(provider.conversations[0].notes) == 1


def test_a_malformed_block_is_a_finding_and_a_fixed_one_is_rendered(
    client: TestClient, dba, targets
) -> None:
    broken = with_block(GOOD, raw="[{not json")
    fixed = with_block(GOOD, ERRORS_STEP)
    _, events = run(
        client, dba, targets, body_lines(), ScriptedTurn(text=broken), ScriptedTurn(text=fixed)
    )
    first, revised = checks(events)
    assert [f["kind"] for f in first["findings"]] == ["next_checks_block"]
    assert "could not be read: the block is not JSON" in first["findings"][0]["problem"]
    assert revised["findings"] == []
    shown = answer_of(events)
    assert "`schema.object_errors(" in shown and "{not json" not in shown


def test_a_failed_revision_withholds_the_draft_then_settles_it(
    client: TestClient, dba, targets, settings
) -> None:
    client.app.state.harness.copilot._settings = settings.model_copy(  # type: ignore[attr-defined]
        update={"kiwi_revision_max_wall_seconds": 0.05}
    )
    broken = with_block(GOOD, raw="[{not json")
    _, events = run(
        client,
        dba,
        targets,
        body_lines(),
        ScriptedTurn(text=broken),
        ScriptedTurn(text="", delay=2.0),
    )
    assert last(events, "claim_check")["action"] == "revision_failed:wall_time"
    shown = answer_of(events)
    # The flagged draft is withheld (Sync Point 2G): the harness's bounded answer takes
    # its place, and its coverage is still appended from the saved lookups.
    assert broken not in shown
    assert shown.startswith("**Finding:** The harness wrote this answer")
    assert "## Lines read (from the lookups)" in shown
    rendered = [d["rendered"] for n, d in events if n == "revision" and d.get("rendered")]
    assert rendered == ["bounded_fallback"]


def test_an_earlier_protocol_gets_no_replacement(client: TestClient, dba, targets) -> None:
    answer = with_block(GOOD, ERRORS_STEP)
    _, events = run(client, dba, targets, body_lines(), ScriptedTurn(text=answer), version="1.3")
    assert "revision" not in names(events)
    shown = answer_of(events)
    assert shown.startswith("Reading. " + answer)  # the block as the model wrote it
    assert "## Lines read (from the lookups)" in shown  # text only: any client shows it


def test_a_comment_end_is_not_rendered_as_a_whole_member(client: TestClient, dba, targets) -> None:
    # Lines 8-11 from line 8: read without the lines before them, so report_department's
    # END at line 11 is only apparent.
    _, events = run(
        client, dba, targets, body_lines(8, 11), ScriptedTurn(text="Finding: not settled.")
    )
    shown = answer_of(events)
    assert "REPORT_DEPARTMENT: implementation from line 8, lines 8-11 returned up to an " in shown
    assert "apparent END; whether it was read whole is unknown" in shown


# -- C35: a plain explanation of supplied source -------------------------------------------


def explain(**overrides) -> dict:
    payload = diagnosis(action="explain", **overrides)
    payload["attachments"] = [
        {"category": "selected_source", "name": "purge_log", "content": "\n".join(SWALLOWS)}
    ]
    return payload


REMEDIED = T20 + "\n\nThe remedy is to log the error and then re-raise it with `RAISE;`."


def test_an_explanation_missing_its_remedy_is_revised_once(client: TestClient, developer) -> None:
    provider = use_replies(client, Reply(T20), Reply(REMEDIED))
    events = sse(client, developer, explain())
    first, revised = checks(events)
    assert [f["kind"] for f in first["findings"]] == ["missing_remedy"]
    assert first["action"] == "revise" and revised["findings"] == []
    assert answer_of(events) == REMEDIED
    assert len(provider.messages) == 2
    assert "PREVIOUS_ANSWER" in provider.messages[1]


def test_a_revised_explanation_still_missing_its_remedy_keeps_its_finding(
    client: TestClient, developer
) -> None:
    # Sync Point 1 finding 1: the revised answer is reviewed as an explanation, so a
    # second answer that still gives no remedy is reported, not cleared.
    unremedied = "The handler swallows errors."
    provider = use_replies(client, Reply(T20), Reply(unremedied))
    events = sse(client, developer, explain())
    first, revised = checks(events)
    assert first["action"] == "revise"
    assert [f["kind"] for f in first["findings"]] == ["missing_remedy"]
    assert revised["stage"] == "revised" and revised["action"] == "none"
    assert [f["kind"] for f in revised["findings"]] == ["missing_remedy"]
    assert last(events, "claim_check") == revised
    assert answer_of(events) == unremedied
    # Still one revision: a remaining finding never asks again.
    assert len(provider.messages) == 2


@pytest.mark.parametrize("code", [IGNORE_RETURN, IGNORE_GOTO])
def test_replacement_code_that_can_skip_its_raise_is_revised(
    client: TestClient, developer, code: str
) -> None:
    # Sync Point 1B: a RETURN or GOTO in a branch before the raise is no remedy.
    draft = f"{T20}\n\n```plsql\n{code}\n```"
    provider = use_replies(client, Reply(draft), Reply(REMEDIED))
    events = sse(client, developer, explain())
    first, revised = checks(events)
    assert first["action"] == "revise"
    assert [f["kind"] for f in first["findings"]] == ["missing_remedy"]
    assert revised["findings"] == []
    assert len(provider.messages) == 2


def test_replacement_code_with_a_conditional_log_then_a_raise_is_not_revised(
    client: TestClient, developer
) -> None:
    provider = use_replies(client, Reply(f"{T20}\n\n```plsql\n{DEBUG_LOGS}\n```"))
    events = sse(client, developer, explain())
    [check] = checks(events)
    assert (check["findings"], check["action"]) == ([], "none")
    assert len(provider.messages) == 1


@pytest.mark.parametrize("code", LEAVES_OUTER.values(), ids=LEAVES_OUTER.keys())
def test_replacement_code_that_leaves_a_loop_around_its_handler_is_revised(
    client: TestClient, developer, code: str
) -> None:
    # Sync Point 1C: EXIT or CONTINUE naming the loop around the handler skips the raise.
    draft = f"{T20}\n\n```plsql\n{code}\n```"
    provider = use_replies(client, Reply(draft), Reply(REMEDIED))
    events = sse(client, developer, explain())
    first, revised = checks(events)
    assert first["action"] == "revise"
    assert [f["kind"] for f in first["findings"]] == ["missing_remedy"]
    assert revised["findings"] == []
    assert len(provider.messages) == 2


@pytest.mark.parametrize("code", STAYS_INNER.values(), ids=STAYS_INNER.keys())
def test_replacement_code_that_leaves_only_an_inner_loop_is_not_revised(
    client: TestClient, developer, code: str
) -> None:
    provider = use_replies(client, Reply(f"{T20}\n\n```plsql\n{code}\n```"))
    events = sse(client, developer, explain())
    [check] = checks(events)
    assert (check["findings"], check["action"]) == ([], "none")
    assert len(provider.messages) == 1


def test_a_failed_explain_revision_restores_the_draft(client: TestClient, developer) -> None:
    use_replies(client, Reply(T20), Reply("", stop_reason="end_turn"))
    events = sse(client, developer, explain())
    assert answer_of(events) == T20
    assert last(events, "claim_check")["action"] == "revision_failed:empty"
    assert last(events, "claim_check")["findings"][0]["kind"] == "missing_remedy"


def test_an_explanation_with_its_remedy_is_not_revised(client: TestClient, developer) -> None:
    provider = use_replies(client, Reply(REMEDIED))
    events = sse(client, developer, explain())
    [check] = checks(events)
    assert (check["findings"], check["action"]) == ([], "none")
    assert len(provider.messages) == 1


def test_an_explanation_without_supplied_source_is_not_reviewed(
    client: TestClient, developer
) -> None:
    use_replies(client, Reply(T20))
    payload = explain()
    payload["attachments"] = [
        {"category": "user_message", "name": "question", "content": "What does it do?"}
    ]
    events = sse(client, developer, payload)
    assert "claim_check" not in names(events)


def test_an_earlier_client_gets_no_explain_revision(client: TestClient, developer) -> None:
    provider = use_replies(client, Reply(T20))
    events = sse(client, developer, explain(protocolVersion="1.3"))
    [check] = checks(events)
    assert check["action"] == "skipped:protocol"
    assert len(provider.messages) == 1
