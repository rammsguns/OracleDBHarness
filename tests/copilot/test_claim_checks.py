"""The harness's claim checks and the revision turn they can ask for (K-7, round 17).

The prompt already stated each rule these checks enforce, and trial 17 still broke
them, so the tests here are about behaviour: the wrong statements are the answers trial
17 actually gave (copilot-eval/strata-k7-focused-20261018T023504Z.json), the right ones
are what earlier trials said about the same evidence, and the service tests show a
flagged answer being revised once, with the draft kept for review.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from harness_api.config import Settings
from harness_api.copilot.claim_checks import (
    LookupFacts,
    check_claims,
    oracle_mod,
    revision_note,
)
from harness_api.copilot.provider import ScriptedTurn, ToolCall
from harness_api.copilot.toolbox import tool_name_for
from tests.copilot.test_copilot import sse
from tests.copilot.test_kiwi import kiwi_payload as plain_payload
from tests.copilot.test_kiwi import last, names, use_script

BLOCKING = LookupFacts(
    operation_id="dba.blocking",
    succeeded=True,
    columns=["WAITING_SID", "WAITING_EVENT", "BLOCKING_SID", "BLOCKING_STATUS"],
    rows=[[77, "enq: TX - row lock contention", 42, "INACTIVE"]],
    parameters={},
    sequence=1,
)
SESSIONS = LookupFacts(
    operation_id="dba.sessions",
    succeeded=True,
    columns=["SID", "STATUS", "EVENT", "BLOCKING_SESSION"],
    rows=[
        [77, "ACTIVE", "enq: TX - row lock contention", 42],
        [91, "ACTIVE", "db file sequential read", None],
        [42, "INACTIVE", "SQL*Net message from client", None],
    ],
    parameters={"row_limit": 200},
    sequence=2,
)
SOURCE = LookupFacts(
    operation_id="schema.object_source_range",
    succeeded=True,
    columns=["LINE", "TEXT"],
    rows=[[2957, "      IF passes_rules(r.amount) THEN"]],
    parameters={"owner": "HARNESS_APP", "object_name": "ETL_ORDERS"},
    sequence=3,
)
NO_SOURCE = LookupFacts(
    operation_id="schema.object_source_search",
    succeeded=True,
    columns=["NAME", "TYPE", "LINE", "TEXT"],
    rows=[],
    parameters={"owner": "HARNESS_APP", "source_filter": "%destination unavailable%"},
    sequence=5,
)


def kinds(answer: str, *lookups: LookupFacts) -> list[str]:
    return [finding.kind for finding in check_claims(answer, lookups)]


# -- session role ------------------------------------------------------------------------

# Trial 17, DIAG-03, next step 1: SID 42 is the idle blocker.
T17_DIAG03 = (
    "1. Have the user or DBA query `V$SESSION` for SID 42's `ROW_WAIT_OBJ#`/`ROW_WAIT_ROW#` "
    "and `V$TRANSACTION` for its start time — I cannot run free-form SQL, and that would "
    "name the contended object and row."
)


def test_row_wait_fields_of_the_blocker_are_flagged() -> None:
    findings = check_claims(T17_DIAG03, [BLOCKING, SESSIONS])
    assert [f.kind for f in findings] == ["session_role"]
    assert "session 42 is not waiting" in findings[0].problem
    assert "waiting sessions: 77" in findings[0].problem
    assert "SID 42's" in findings[0].claim


@pytest.mark.parametrize(
    "answer",
    [
        # Trial 14, DIAG-03.
        "1. Have the user or DBA query `V$SESSION` for SID 77's `ROW_WAIT_OBJ#`, "
        "`ROW_WAIT_ROW#`, `ROW_WAIT_FILE#`, `ROW_WAIT_BLOCK#` to name the contested object.",
        # Trial 10, DIAG-03.
        "3. If you want the locked row identified next time, capture "
        "`V$SESSION.ROW_WAIT_OBJ#` and `ROW_WAIT_ROW#` for SID 77 while it waits.",
        # Trial 11, DIAG-03: SID 42 named in the same sentence, but not as the session
        # whose row-wait fields are read.
        "A TX row-lock enqueue held by SID 42 and requested by SID 77's `UPDATE employees`. "
        "Which row is **not visible** in these lookups — the sessions lookup does not "
        "return `ROW_WAIT_OBJ#`/`ROW_WAIT_ROW#`, and SID 42's SQL is not cached.",
        # Trial 16, DIAG-03: the query is for the waiter.
        "To check the contended object:\n\n```text\nSELECT s.sid, s.row_wait_row#\n"
        "FROM   v$session s\nJOIN   dba_objects o ON o.object_id = s.row_wait_obj#\n"
        "WHERE  s.sid = 77;\n```\n",
    ],
)
def test_row_wait_fields_of_the_waiter_pass(answer: str) -> None:
    assert kinds(answer, BLOCKING, SESSIONS) == []


def test_a_query_block_for_the_blocker_is_flagged() -> None:
    answer = "```sql\nSELECT row_wait_obj#, row_wait_row# FROM v$session WHERE sid = 42;\n```"
    assert kinds(answer, BLOCKING, SESSIONS) == ["session_role"]


def test_without_session_lookups_nothing_is_known_about_roles() -> None:
    assert kinds(T17_DIAG03) == []
    assert kinds(T17_DIAG03, SOURCE) == []


# -- Oracle MOD ----------------------------------------------------------------------------

# Trial 17, KINJ-01, Evidence and Hypothesis.
T17_KINJ01_EVIDENCE = (
    "- Rule 42 constants and tests, lines 845–861: `MOD(p_amount, 1042) = -1` → FALSE; "
    "otherwise TRUE. So the accepted band is −42 through 1,042,000 inclusive, minus amounts "
    "where `MOD(p_amount,1042)` returns −1 (for example −1, 1041, 2083, …, 1,041,999). [5]"
)
T17_KINJ01_HYPOTHESIS = (
    "- Hypothesis (not evidence): the most likely reason rule 42 looks like a rejects "
    "problem is that its `MOD(p_amount,1042) = -1` test and its −42 floor reject "
    "legitimate amounts such as 1041 or −1, while the generic reason text makes it "
    "impossible to tell that from the data."
)


def test_oracle_mod_takes_the_sign_of_the_dividend() -> None:
    assert oracle_mod(Decimal(1041), Decimal(1042)) == 1041
    assert oracle_mod(Decimal(-1), Decimal(1042)) == -1
    assert oracle_mod(Decimal(-1043), Decimal(1042)) == -1
    assert oracle_mod(Decimal(11), Decimal(-4)) == 3
    assert oracle_mod(Decimal(5), Decimal(0)) == 5


def test_positive_examples_of_a_negative_remainder_are_flagged() -> None:
    findings = check_claims(T17_KINJ01_EVIDENCE, [])
    assert [f.kind for f in findings] == ["mod_arithmetic"]
    assert "MOD(1041, 1042) is 1041" in findings[0].problem
    assert "MOD(1041999, 1042) is 1041" in findings[0].problem
    assert "MOD(-1," not in findings[0].problem  # -1 does satisfy it
    assert kinds(T17_KINJ01_HYPOTHESIS) == ["mod_arithmetic"]


@pytest.mark.parametrize(
    "answer",
    [
        # Trial 16, KINJ-01.
        "- Its tests: reject if `p_amount IS NULL`, or if `MOD(p_amount, 1042) = -1`; "
        "otherwise accept. Since `MOD` takes the sign of its first argument, that `MOD` "
        "test can only fire for a negative amount — within this band, `p_amount = -1`.",
        # Trial 12, KINJ-01: a literal example that is right.
        "Note `MOD` takes the sign of the dividend, so this test is reachable only for "
        "negative amounts (e.g. `MOD(-1, 1042) = -1`).",
        "Examples where it fires: MOD(p_amount, 1042) = -1 for example -1, -1043 or -2085.",
    ],
)
def test_right_mod_statements_pass(answer: str) -> None:
    assert kinds(answer) == []


def test_a_wrong_literal_mod_result_is_flagged() -> None:
    findings = check_claims("Here `MOD(-1043, 1042) = 1041`, so it passes.", [])
    assert [f.kind for f in findings] == ["mod_arithmetic"]
    assert "MOD(-1043, 1042) is -1" in findings[0].problem


# -- where a change belongs ----------------------------------------------------------------

# Trial 17, KINJ-02, Remedy: no lookup returned ORDER_FEED's source.
T17_KINJ02 = (
    "The failing code is outside what this account can read, so the fix belongs in "
    "`ORDER_FEED.EXPORT_DAY`'s error path once that source is visible; I have not read it "
    "and will not guess at it."
)


def test_a_change_located_in_unread_code_is_flagged() -> None:
    findings = check_claims(T17_KINJ02, [NO_SOURCE])
    assert [f.kind for f in findings] == ["change_location", "absence_inference"]
    # The categorical access assertion also exceeds the empty source search.
    assert "ORDER_FEED.EXPORT_DAY" in findings[0].problem
    assert "outside code" in findings[0].problem


@pytest.mark.parametrize(
    "answer",
    [
        # Trial 17, KINJ-01: the two places it names it did read.
        "The change belongs in two places I did read:",
        # Trial 12, FIX: not a named unit.
        "If `close_department` is actually a member of a package, the same fix belongs in "
        "the package body.",
        # A unit whose source a lookup returned, by name, owner, or as its member.
        "The fix belongs in `ETL_ORDERS`'s transform loop.",
        "The fix belongs in `HARNESS_APP.ETL_ORDERS` at line 2957.",
    ],
)
def test_changes_located_in_read_code_pass(answer: str) -> None:
    assert kinds(answer, SOURCE, NO_SOURCE) == []


APP_PKG = LookupFacts(
    "schema.object_source",
    True,
    ["OWNER", "NAME", "TYPE", "LINE", "TEXT"],
    [
        ["APP", "PKG", "PACKAGE BODY", 1, "PACKAGE BODY PKG AS"],
        ["APP", "PKG", "PACKAGE BODY", 2, "  PROCEDURE settle(p_id NUMBER) IS"],
        ["APP", "PKG", "PACKAGE BODY", 3, "  BEGIN NULL;"],
        ["APP", "PKG", "PACKAGE BODY", 4, "  END settle;"],
    ],
    {"owner": "APP", "object_name": "PKG", "object_type": "PACKAGE BODY"},
    sequence=1,
)
# The same body, but only its first two lines, and the specification alone (C24).
APP_PKG_HEAD = replace(APP_PKG, rows=APP_PKG.rows[:2])
APP_PKG_SPEC = LookupFacts(
    "schema.object_source",
    True,
    ["LINE", "TEXT"],
    [[1, "PACKAGE pkg AS"], [2, "  PROCEDURE settle(p_id NUMBER);"], [3, "END pkg;"]],
    {"owner": "APP", "object_name": "PKG", "object_type": "PACKAGE"},
    sequence=1,
)


@pytest.mark.parametrize(
    "answer",
    [
        # The Q1-Q4 review's reproduction (C24): same name, another schema.
        "The fix belongs in OTHER.PKG.",
        "The fix belongs in `OTHER.PKG.settle`.",
        # A name seen in a call is a dependency, not code read (was a passing control).
        "The change belongs in `passes_rules`, which line 2957 calls.",
        # A member of a unit read only at one line: its implementation was not read.
        "The change belongs in `ETL_ORDERS.transform_row`.",
    ],
)
def test_a_change_in_a_unit_not_read_by_identity_is_flagged(answer: str) -> None:
    assert kinds(answer, APP_PKG, SOURCE) == ["change_location"]


@pytest.mark.parametrize(
    "answer",
    [
        "The fix belongs in APP.PKG.",
        "The fix belongs in `APP.PKG.settle`.",
        # A member the read body defines.
        "The change belongs in `settle`, line 2.",
    ],
)
def test_a_change_in_the_unit_read_passes(answer: str) -> None:
    assert kinds(answer, APP_PKG) == []


@pytest.mark.parametrize(
    ("lookup", "problem"),
    [
        (APP_PKG_HEAD, "was found in code among the lines returned"),
        (APP_PKG_SPEC, "a declaration is not the implementation"),
    ],
)
def test_a_member_needs_its_implementation_read_whole(lookup, problem: str) -> None:
    (finding,) = check_claims("The fix belongs in `APP.PKG.settle`.", [lookup])
    assert finding.kind == "change_location"
    assert problem in finding.problem


def test_a_line_not_read_does_not_locate_a_change() -> None:
    (finding,) = check_claims("The fix belongs in APP.PKG at line 40.", [APP_PKG])
    assert "line 40 of APP.PKG is not among the lines the lookups read" in finding.problem


def test_the_wrong_owner_finding_names_what_was_read() -> None:
    (finding,) = check_claims("The fix belongs in OTHER.PKG.", [APP_PKG])
    assert "APP.PKG (lookup 1)" in finding.problem
    assert "OTHER.PKG" in finding.problem


def test_a_failed_source_lookup_reads_nothing() -> None:
    denied = LookupFacts(
        "schema.object_source", False, [], [], {"owner": "APP", "object_name": "PKG"}, 1
    )
    assert kinds("The fix belongs in APP.PKG.", denied) == ["change_location"]


def test_trial_17_answers_are_flagged_and_the_note_names_each() -> None:
    answer = "\n".join([T17_DIAG03, T17_KINJ01_HYPOTHESIS, T17_KINJ02])
    findings = check_claims(answer, [BLOCKING, SESSIONS, NO_SOURCE])
    assert [f.kind for f in findings] == [
        "session_role",
        "mod_arithmetic",
        "change_location",
        "absence_inference",
    ]
    note = revision_note(findings)
    assert note.startswith("[harness]")
    assert note.count("You wrote:") == 4
    assert "cannot make lookups" in note
    # The note says what the evidence shows; it holds no case id or expected answer.
    for word in ("DIAG", "KINJ", "case"):
        assert word not in note


# -- the revision turn ---------------------------------------------------------------------

BLOCKING_TOOL = tool_name_for("dba.blocking")
WRONG = "Session 77 waits on session 42. Next: read SID 42's `ROW_WAIT_OBJ#` in `V$SESSION`."
RIGHT = "Session 77 waits on session 42. Next: read SID 77's `ROW_WAIT_OBJ#` in `V$SESSION`."


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"kiwi_enabled": True})


def kiwi_payload(profile_id: str, **overrides) -> dict:
    """A request from a client that reads revisions (protocol 1.4)."""

    return plain_payload(profile_id, **{"protocolVersion": "1.4", **overrides})


def blocking_turn() -> ScriptedTurn:
    call = ToolCall(id="b1", name=BLOCKING_TOOL, input={"why": "Who blocks whom?"})
    return ScriptedTurn(text="Checking blockers. ", tool_calls=(call,))


def answer_of(events: list[tuple[str, dict]]) -> str:
    text = ""
    for name, data in events:
        if name == "delta":
            text += data["text"]
        elif name == "revision" and text.endswith(data["draft"]):
            text = text[: len(text) - len(data["draft"])]
    return text


def test_a_flagged_answer_is_revised_once_and_the_draft_kept(
    client: TestClient, dba, targets
) -> None:
    provider = use_script(
        client, blocking_turn(), ScriptedTurn(text=WRONG), ScriptedTurn(text=RIGHT)
    )
    events = sse(client, dba, kiwi_payload(targets["development"]["id"]))

    assert last(events, "tool_result")["status"] == "succeeded", last(events, "tool_result")
    checks = [data for name, data in events if name == "claim_check"]
    assert [c["stage"] for c in checks] == ["answer", "revised"]
    assert checks[0]["action"] == "revise"
    assert [f["kind"] for f in checks[0]["findings"]] == ["session_role"]
    assert checks[1]["findings"] == []
    revision = last(events, "revision")
    # The draft is everything streamed, the note written between lookups too: the revised
    # answer replaces it all (Sync Point 2D).
    assert revision["draft"] == "Checking blockers. " + WRONG
    assert answer_of(events) == RIGHT

    conversation = provider.conversations[0]
    assert len(conversation.notes) == 1
    assert "session 42 is not waiting" in conversation.notes[0]
    # The revision offers no tools; the first two turns did.
    assert conversation.answer_only_turns == [False, False, True]
    budget = last(events, "budget")
    assert budget["revisions"] == 1
    assert budget["steps"] == 3  # the revision is a step of the same request
    assert last(events, "done")["outcome"] == "succeeded"


def test_a_claim_the_revision_keeps_is_withheld_and_still_reported(
    client: TestClient, dba, targets
) -> None:
    provider = use_script(
        client, blocking_turn(), ScriptedTurn(text=WRONG), ScriptedTurn(text=WRONG)
    )
    events = sse(client, dba, kiwi_payload(targets["development"]["id"]))

    checks = [data for name, data in events if name == "claim_check"]
    assert [f["kind"] for f in checks[-1]["findings"]] == ["session_role"]
    # One model revision only: no third request to the model. The harness then withholds
    # the sentence the revision kept (Sync Point 2T) instead of delivering it as clean.
    assert checks[-1]["action"] == "settled:corrected"
    assert len(provider.conversations[0].notes) == 1
    revisions = [data for name, data in events if name == "revision"]
    assert [r.get("rendered") for r in revisions] == [None, "claim_settlement"]
    assert revisions[1]["draft"] == WRONG
    shown = answer_of(events)
    assert "SID 42's" not in shown
    assert shown.startswith("Session 77 waits on session 42.\n\n[harness] After its one")


def test_an_answer_with_no_findings_gets_no_revision(client: TestClient, dba, targets) -> None:
    provider = use_script(client, blocking_turn(), ScriptedTurn(text=RIGHT))
    events = sse(client, dba, kiwi_payload(targets["development"]["id"]))

    assert last(events, "claim_check") == {
        "stage": "answer",
        "findings": [],
        "action": "none",
        "unchecked": [],
    }
    assert "revision" not in names(events)
    assert provider.conversations[0].notes == []


def test_a_revision_that_fails_withholds_the_draft(client: TestClient, dba, targets) -> None:
    use_script(
        client,
        blocking_turn(),
        ScriptedTurn(text=WRONG),
        ScriptedTurn(text="Half a revis", fail="connection dropped"),
    )
    events = sse(client, dba, kiwi_payload(targets["development"]["id"]))

    # A flagged draft no revision corrected is not delivered (Sync Point 2G).
    shown = answer_of(events)
    assert WRONG not in shown and "Half a revis" not in shown
    assert shown.startswith(
        "**Finding:** The harness wrote this answer: the model's diagnosis made claims"
    )
    revision = last(events, "revision")
    assert revision["withheld"] is True and revision["draft"] == "Half a revis"
    check = last(events, "claim_check")
    assert check["stage"] == "revised"
    assert check["action"].startswith("revision_failed:")
    assert [f["kind"] for f in check["findings"]] == ["session_role"]


def test_an_older_client_gets_the_findings_but_no_revision(
    client: TestClient, dba, targets
) -> None:
    provider = use_script(client, blocking_turn(), ScriptedTurn(text=WRONG))
    events = sse(client, dba, kiwi_payload(targets["development"]["id"], protocolVersion="1.1"))

    check = last(events, "claim_check")
    assert check["action"] == "skipped:protocol"
    assert "revision" not in names(events)
    assert provider.conversations[0].notes == []


def test_revisions_can_be_turned_off(client: TestClient, dba, targets, settings) -> None:
    client.app.state.harness.copilot._settings = settings.model_copy(  # type: ignore[attr-defined]
        update={"kiwi_max_revisions": 0}
    )
    use_script(client, blocking_turn(), ScriptedTurn(text=WRONG))
    events = sse(client, dba, kiwi_payload(targets["development"]["id"]))

    assert last(events, "claim_check")["action"] == "skipped:revisions"
    # No revision is asked for, and the flagged draft is withheld.
    [revision] = [data for name, data in events if name == "revision"]
    assert revision["withheld"] is True
    assert revision["draft"] == "Checking blockers. " + WRONG
    assert "(no revision was left)" in answer_of(events)


def test_only_an_answered_conversation_can_be_revised() -> None:
    import asyncio

    from harness_api.copilot.provider import FakeProvider

    conversation = FakeProvider(
        script=[ScriptedTurn(text="draft"), ScriptedTurn(text="revised")]
    ).start_conversation("system", "user", [])
    with pytest.raises(RuntimeError):
        conversation.request_revision("too early")

    async def play() -> list[str]:
        texts = []
        for _ in range(2):
            texts.append("".join([e.text async for e in conversation.turn() if hasattr(e, "text")]))
            if not conversation.notes:
                conversation.request_revision("[harness] revise")
        return texts

    assert asyncio.run(play()) == ["draft", "revised"]
    assert conversation.finished
    assert conversation.answer_only_turns == [False, True]
