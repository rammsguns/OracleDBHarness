"""Claims past what was read, and a blocker's cursor as its lock (Wave 2B, trial 21).

Trial 21's KINJ-01 read rule 42 whole but its neighbours in part, then wrote that rule 42
"follows the same pattern as rules 41 and 43 in the lines I read", and that
``passes_rules`` - read at its declaration and its calls - "collapses every rule to one
FALSE". The qualifier does not cover lines no lookup returned, and a universal about a
subprogram read in part says what its unread lines do (``read_scope``).

Trial 21's DIAG-03 named the blocker right, then called the blocker's SQL_ID "the
statement that took the lock". ``V$SESSION.SQL_ID`` is the cursor a session runs now or
ran last; the DML that took a row lock may be several statements earlier
(``lock_origin``).

Each check has positive controls (the trial's wording and its variants) and negative
controls (the same claims scoped to what was read, or kept apart), at the claim check
and at the service through the stand-in database. Nothing here calls a model or Oracle.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.claim_checks import review_claims
from harness_api.copilot.provider import ScriptedTurn, ToolCall
from harness_api.copilot.read_coverage import read_scope_claims
from harness_api.copilot.support import evidence_map
from harness_api.copilot.toolbox import tool_name_for
from tests.copilot.test_claim_checks import BLOCKING, SESSIONS, answer_of, blocking_turn
from tests.copilot.test_claim_checks import kiwi_payload as revisable_payload
from tests.copilot.test_copilot import sse
from tests.copilot.test_kiwi import last, use_script
from tests.copilot.test_read_coverage import BODY, DISPATCHER, _range


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"kiwi_enabled": True})


# -- KINJ-01: comparisons and universals past the lines read -------------------------------

# Rules 40 and 41 read whole (lines 803-841); rule 42 from its first line to line 850,
# short of its MOD check and its END.
NEIGHBOURS = evidence_map([_range(2, 803, 850, BODY)])

# A dispatcher read sparsely: its declaration, seven of its call lines, and its END.
DISPATCHER_SOURCE = {
    2500: "  FUNCTION passes_rules(p_amount IN NUMBER) RETURN BOOLEAN IS",
    2501: "  BEGIN",
    **DISPATCHER,
    2577: "    RETURN TRUE;",
    2578: "  END passes_rules;",
}
SPARSE = evidence_map(
    [
        _range(4, 2500, 2501, DISPATCHER_SOURCE),
        _range(5, 2520, 2526, DISPATCHER_SOURCE),
        _range(6, 2578, 2578, DISPATCHER_SOURCE),
    ]
)


def scope_problems(answer: str, evidence) -> list[str]:
    return [finding.problem for finding in read_scope_claims(answer, evidence)]


@pytest.mark.parametrize(
    "answer",
    [
        "Rule 41's MOD check matches rule 42's.",
        "Rule 42 follows the same pattern as rules 41 in the lines I read.",
        "In the lines I read, check_rule_042 is identical to check_rule_041.",
        # Codex's Sync Point 2B probes, in this fixture's numbering.
        "Its band and MOD check match rules 41 and 42 in the lines I read.",
        "Rules 41 and 42 use the identical shape.",
    ],
)
def test_a_comparison_with_a_neighbour_read_in_part_is_flagged(answer: str) -> None:
    [problem] = scope_problems(answer, NEIGHBOURS)
    assert problem.startswith("CHECK_RULE_042 was read in part (lines 844-850;")
    assert '"in the lines I read" or otherwise' in problem


@pytest.mark.parametrize(
    "answer",
    [
        "Every rule check returns FALSE on a NULL amount.",
        "All rules return FALSE on a NULL amount.",
    ],
)
def test_a_universal_over_a_unit_read_in_part_is_flagged(answer: str) -> None:
    [problem] = scope_problems(answer, NEIGHBOURS)
    assert "is said of every rule, but HARNESS_APP.ETL_ORDERS PACKAGE BODY was read" in problem
    assert "(lines 803-850)" in problem


@pytest.mark.parametrize(
    "answer",
    [
        "The rejects problem is that `passes_rules` collapses every rule to one FALSE.",
        "passes_rules always returns FALSE when a rule fails.",
    ],
)
def test_a_universal_about_a_sparsely_read_function_is_flagged(answer: str) -> None:
    [problem] = scope_problems(answer, SPARSE)
    assert "is said of PASSES_RULES, which was read in part (lines 2500-2501;" in problem
    assert "not every line from there to its END was returned" in problem


@pytest.mark.parametrize(
    "answer",
    [
        # Both members read whole, and both hold the MOD compared.
        "check_rule_041 has the same MOD check as check_rule_040.",
        # Scoped at the quantifier to the lines returned.
        "Each rule check on lines 803-850 returns FALSE on a NULL amount.",
        "Every rule I read returns FALSE on a NULL amount.",
        "Each rule call on lines 2520-2526 returns FALSE when it fails.",
        "On lines 2520-2526 each rule call that fails returns FALSE.",
        # Left open, or proposed, or not about the members.
        "Whether check_rule_042 matches check_rule_041 is unknown: its MOD line was not read.",
        "A fix would make every rule raise its own error.",
        "Every rejected row is written to the rejects table.",
        # Said of the lines returned, not of the function.
        "In lines 2520-2526, passes_rules returns FALSE when check_rule_032 fails.",
    ],
)
def test_claims_scoped_to_what_was_read_pass(answer: str) -> None:
    assert scope_problems(answer, NEIGHBOURS) == []
    assert scope_problems(answer, SPARSE) == []


def test_a_whole_unit_read_from_line_one_bears_a_universal() -> None:
    lines = {1: "PACKAGE BODY etl_orders AS"} | {n: BODY[n] for n in range(783, 842)}
    whole = {n: text for n, text in enumerate(lines.values(), start=1)}
    assert (
        scope_problems(
            "Every rule check returns FALSE on a NULL amount.",
            evidence_map([_range(1, 1, len(whole), whole)]),
        )
        == []
    )


# At the service: the stand-in's ETL_ORDERS, read the way trial 21 read it.
RANGE_TOOL = tool_name_for("schema.object_source_range")
STEPS = ((803, 850), (2425, 2425), (2520, 2526), (2788, 2788))
KINJ_DRAFT = (
    "**Finding:** Rule 42 (`check_rule_042`, lines 844-862) compiles and is VALID; it "
    "follows the same pattern as rules 41 and 42 in the lines I read. `passes_rules` "
    "collapses every rule to one FALSE (lookups 1-4).\n\n"
    "**Evidence:**\n- check_rule_042 is identical to check_rule_041 in the lines I read "
    "(lookup 1).\n\n"
    "**Unknown:**\n- Which rule rejected the rows.\n\n"
    "**Remedy:** None proposed."
)
KINJ_REVISED = (
    "**Finding:** Lines 844-850 of `check_rule_042` hold its declaration and its NULL, "
    "floor and limit checks (lookup 1); the rest of it was not read. On lines 2520-2526 "
    "each rule call that fails returns FALSE (lookup 3).\n\n"
    "**Evidence:**\n- check_rule_041 was read whole, lines 824-841 (lookup 1).\n\n"
    "**Unknown:**\n- What check_rule_042 does after line 850, and what the rest of "
    "passes_rules does: those lines were not read.\n\n"
    "**Remedy:** None proposed."
)


def source_reads() -> ScriptedTurn:
    calls = tuple(
        ToolCall(
            id=f"r{n}",
            name=RANGE_TOOL,
            input={
                "why": "Read the rule and its caller.",
                "owner": "HARNESS_APP",
                "object_name": "ETL_ORDERS",
                "object_type": "PACKAGE BODY",
                "start_line": start,
                "end_line": end,
                "row_limit": 200,
            },
        )
        for n, (start, end) in enumerate(STEPS, start=1)
    )
    return ScriptedTurn(text="Reading the rules. ", tool_calls=calls)


def claim_checks(events: list) -> list[dict]:
    return [data for name, data in events if name == "claim_check"]


def test_kinj_01_wording_is_revised_to_what_was_read(client: TestClient, dba, targets) -> None:
    use_script(
        client, source_reads(), ScriptedTurn(text=KINJ_DRAFT), ScriptedTurn(text=KINJ_REVISED)
    )
    events = sse(client, dba, revisable_payload(targets["development"]["id"]))

    assert [r["status"] for n, r in events if n == "tool_result"] == ["succeeded"] * 4
    first, revised = claim_checks(events)
    scoped = [f for f in first["findings"] if f["kind"] == "read_scope"]
    excerpts = " ".join(f["claim"] for f in scoped)
    assert "same pattern as rules 41 and 42 in the lines I read" in excerpts
    assert "collapses every rule to one FALSE" in excerpts
    assert "identical to check_rule_041 in the lines I read" in excerpts
    assert first["action"] == "revise"
    assert revised["findings"] == []
    # The revision keeps the diagnosis form: Finding, Evidence, Unknown, Remedy.
    answer = answer_of(events)
    for section in ("**Finding:**", "**Evidence:**", "**Unknown:**", "**Remedy:**"):
        assert answer.count(section) == 1
    # The draft is all that was streamed, the note between lookups included.
    assert last(events, "revision")["draft"] == "Reading the rules. " + KINJ_DRAFT


def test_kinj_01_answer_scoped_from_the_start_is_not_revised(
    client: TestClient, dba, targets
) -> None:
    use_script(client, source_reads(), ScriptedTurn(text=KINJ_REVISED))
    events = sse(client, dba, revisable_payload(targets["development"]["id"]))

    [check] = claim_checks(events)
    assert check["findings"] == [] and check["action"] == "none"


# -- DIAG-03: a blocker's cursor is not the statement that took its lock -------------------


# Sync Point 2B: an unknown said of something else does not retract a claim in the same
# sentence. In NEIGHBOURS rule 42 is read in part and rule 43 not at all (so not a
# member the lookups returned: the finding names rule 42); SPARSE reads the dispatcher in
# part.
@pytest.mark.parametrize(
    ("answer", "evidence", "named"),
    [
        # Codex's two reproductions, exactly.
        (
            "Rule 42 has the same MOD check as rule 43, but the rest is unknown.",
            NEIGHBOURS,
            "CHECK_RULE_042 was read in part",
        ),
        (
            "passes_rules collapses every rule to one FALSE, although the remaining lines "
            "were not read.",
            SPARSE,
            "said of PASSES_RULES",
        ),
        # The same after a semicolon, a dash, and with the concession first.
        (
            "Rule 42 has the same MOD check as rule 43; the rest is unknown.",
            NEIGHBOURS,
            "CHECK_RULE_042",
        ),
        (
            "Rule 42 has the same MOD check as rule 43 \u2014 what follows line 850 is unread.",
            NEIGHBOURS,
            "CHECK_RULE_042",
        ),
        (
            "Although the remaining lines were not read, passes_rules collapses every rule "
            "to one FALSE.",
            SPARSE,
            "said of PASSES_RULES",
        ),
        (
            "While what follows line 850 is unknown, check_rule_042 is identical to check_rule_041.",
            NEIGHBOURS,
            "CHECK_RULE_042",
        ),
    ],
)
def test_an_unknown_about_something_else_does_not_exempt_a_claim(
    answer: str, evidence, named: str
) -> None:
    [problem] = scope_problems(answer, evidence)
    assert named in problem


@pytest.mark.parametrize(
    "answer",
    [
        # The comparison itself left open, in the clause or referred back to.
        "Whether rule 42 has the same MOD check as rule 43 is unknown.",
        "Rule 42 may have the same MOD check as rule 43, but that is unknown.",
        # Scoped to the lines returned, with an unknown about the rest.
        "Each rule call on lines 2520-2526 returns FALSE, but the rest of passes_rules was "
        "not read.",
        "On lines 803-850 every rule check returns FALSE on a NULL amount; the rest is unknown.",
        # A proposal carries into the aside that spells it out.
        "passes_rules would have to return a label \u2014 for example NULL when all checks pass.",
    ],
)
def test_open_or_scoped_claims_beside_an_unknown_pass(answer: str) -> None:
    assert scope_problems(answer, NEIGHBOURS) == []
    assert scope_problems(answer, SPARSE) == []


def test_mixed_sections_each_claim_is_judged_on_its_own() -> None:
    # An Unknown section does not cancel an asserted comparison in the Finding, and the
    # scoped Evidence item and the Unknown item raise nothing.
    answer = (
        "**Finding:** Rule 42 has the same MOD check as rule 43, but the rest is unknown.\n\n"
        "**Evidence:**\n- Each rule check on lines 803-850 returns FALSE on a NULL amount "
        "(lookup 2).\n\n"
        "**Unknown:**\n- Whether rule 43 has a MOD check: its lines were not read.\n\n"
        "**Remedy:** None proposed."
    )
    [finding] = read_scope_claims(answer, NEIGHBOURS)
    assert finding.claim.endswith("Rule 42 has the same MOD check as rule 43")
    assert finding.problem.startswith("CHECK_RULE_042 was read in part")


def lock_problems(answer: str) -> list[str]:
    findings = review_claims(answer, [BLOCKING, SESSIONS]).findings
    return [f.problem for f in findings if f.kind == "lock_origin"]


@pytest.mark.parametrize(
    "answer",
    [
        # Trial 21's sentence, in its shape.
        "SID 42's SQL_ID 9xk2rj4v0d1qa is not in the cursor cache, so the statement that "
        "took the lock cannot be read.",
        "SID 42's SQL_ID 9xk2rj4v0d1qa is the statement that took the lock.",
        # The visible cursor is a SELECT; the DML that locked the row ran before it.
        "The statement holding the lock is the SELECT in SID 42's SQL_ID.",
        "SID 42's PREV_SQL_ID shows the UPDATE that acquired the lock.",
        "The cursor cache shows the lock-holding statement for SID 42.",
    ],
)
def test_a_blockers_cursor_named_as_the_lock_statement_is_flagged(answer: str) -> None:
    [problem] = lock_problems(answer)
    assert "is its current cursor field (PREV_SQL_ID the one before)" in problem
    assert "not necessarily the statement" in problem
    assert "leave the statement that acquired the lock unknown" in problem


@pytest.mark.parametrize(
    "answer",
    [
        "SID 42's current SQL_ID is not in the cursor cache (lookup 3). The statement that "
        "acquired the lock is unknown.",
        "Whether SID 42's SQL_ID is the statement that took the lock is unknown.",
        # Codex's Sync Point 2B probe: one sentence, the lock statement left unknown.
        "SID 42's current SQL_ID is not in the cursor cache; the statement that took the "
        "lock is unknown.",
        "SID 42's SQL_ID is its last cursor; the DML that took the lock need not be that cursor.",
        "SID 42's SQL_ID does not show the statement that took the lock.",
        # The blocker named without a cursor claim.
        "Session 42 holds the lock that session 77 waits on (lookup 1).",
    ],
)
def test_a_blockers_cursor_kept_apart_from_the_lock_passes(answer: str) -> None:
    assert lock_problems(answer) == []


# Sync Point 2B: "whether" about a decision, or an unknown about something else, does not
# retract the cursor claim.
@pytest.mark.parametrize(
    "answer",
    [
        # Codex's reproduction, exactly.
        "SID 42's SQL_ID identifies the statement that took the lock, but whether to kill "
        "the session is the DBA's decision.",
        # Inverted, after a semicolon, and with the concession first.
        "Whether to kill the session is the DBA's decision, but SID 42's SQL_ID identifies "
        "the statement that took the lock.",
        "SID 42's SQL_ID identifies the statement that took the lock; whether or not to "
        "commit is for its owner.",
        "Although whether to end session 42 is the DBA's decision, its SQL_ID identifies "
        "the statement that took the lock.",
        # Trial 21's sentence: the unknown is what the statement did, not which it was.
        "The statement that took the lock is not in the cursor cache under SID 42's "
        "SQL_ID, so what it did to the row is unknown.",
    ],
)
def test_a_decision_or_another_unknown_does_not_exempt_the_cursor_claim(answer: str) -> None:
    [problem] = lock_problems(answer)
    assert "leave the statement that acquired the lock unknown" in problem


@pytest.mark.parametrize(
    "answer",
    [
        "SID 42's SQL_ID may be the statement that took the lock, but that is not established.",
        "SID 42's SQL_ID does not show the statement that took the lock, and whether to end "
        "the session is the DBA's decision.",
        "Session 42 blocks session 77; whether to end it is the DBA's decision.",
    ],
)
def test_the_cursor_kept_apart_beside_a_decision_passes(answer: str) -> None:
    assert lock_problems(answer) == []


DIAG_03_DRAFT = (
    "**Finding:** Session 42 (INACTIVE) blocks session 77, which waits on enq: TX - row "
    "lock contention (lookup 1). SID 42's SQL_ID 9xk2rj4v0d1qa is the statement that took "
    "the lock.\n\n"
    "**Evidence:**\n- WAITING_SID 77, BLOCKING_SID 42 (lookup 1).\n\n"
    "**Unknown:**\n- Why session 42 has not committed.\n\n"
    "**Remedy:** Whether to commit, roll back or end session 42 is for its owner or the "
    "DBA to decide."
)
DIAG_03_REVISED = (
    "**Finding:** Session 42 (INACTIVE) blocks session 77, which waits on enq: TX - row "
    "lock contention (lookup 1).\n\n"
    "**Evidence:**\n- WAITING_SID 77, BLOCKING_SID 42 (lookup 1).\n\n"
    # Not "the cursor it is running now": session 42 is INACTIVE (Sync Point 2Q).
    "**Unknown:**\n- The statement in session 42's transaction that acquired the lock: its "
    "reported SQL_ID is not necessarily that DML.\n\n"
    "**Remedy:** Whether to commit, roll back or end session 42 is for its owner or the "
    "DBA to decide."
)


def test_diag_03_revision_keeps_the_blocker_and_the_human_decision(
    client: TestClient, dba, targets
) -> None:
    use_script(
        client,
        blocking_turn(),
        ScriptedTurn(text=DIAG_03_DRAFT),
        ScriptedTurn(text=DIAG_03_REVISED),
    )
    events = sse(client, dba, revisable_payload(targets["development"]["id"]))

    first, revised = claim_checks(events)
    # Only the cursor claim: the blocker's identification is right, so no session_role.
    assert [f["kind"] for f in first["findings"]] == ["lock_origin"]
    assert "the statement that took the lock" in first["findings"][0]["claim"]
    assert revised["findings"] == []
    answer = answer_of(events)
    assert "Session 42 (INACTIVE) blocks session 77" in answer
    assert "is for its owner or the DBA to decide" in answer
    assert "is the statement that took the lock" not in answer


def test_diag_03_answer_that_keeps_them_apart_is_not_revised(
    client: TestClient, dba, targets
) -> None:
    use_script(client, blocking_turn(), ScriptedTurn(text=DIAG_03_REVISED))
    events = sse(client, dba, revisable_payload(targets["development"]["id"]))

    [check] = claim_checks(events)
    assert check["findings"] == [] and check["action"] == "none"
