"""Derived claims: a remedy, a bound or a count one step past a returned row (C31).

Trial 19 (copilot-eval/strata-k7-focused-20261018T180931Z.json) failed three answers on
such a step: a synonym offered instead of a grant (INACCESSIBLE-03), an idle time read as
the most work a kill could lose (DIAG-03), and one function's argument rows counted as
two overloads (KINJ-02). The checks rest on Oracle facts, not on those answers: the
wrong statements below include other wordings, objects and numbers, and each has a
control that says the same thing correctly or with too little evidence to judge.

INACCESSIBLE-03 is a plain diagnosis, not a Kiwi request, and claim checks run on Kiwi
answers only, so the synonym check could not have reviewed that answer.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from harness_api.config import Settings
from harness_api.copilot.claim_checks import (
    LookupFacts,
    check_claims,
    review_claims,
    revision_note,
)
from harness_api.copilot.provider import ScriptedTurn, ToolCall
from harness_api.copilot.toolbox import tool_name_for
from tests.copilot.test_claim_checks import answer_of, kiwi_payload
from tests.copilot.test_copilot import sse
from tests.copilot.test_kiwi import last, use_script


def kinds(answer: str, *lookups: LookupFacts) -> list[str]:
    return [finding.kind for finding in check_claims(answer, lookups)]


def only(kind: str, answer: str, *lookups: LookupFacts):
    return [f for f in check_claims(answer, lookups) if f.kind == kind]


def unchecked(kind: str, answer: str, *lookups: LookupFacts):
    return [f for f in review_claims(answer, lookups).unchecked if f.kind == kind]


# -- a synonym in place of a privilege ---------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        # Trial 19 INACCESSIBLE-03's words.
        "HARNESS_APP lacks visibility to `HR.EMPLOYEES` because there is no grant. The remedy "
        "is for the `HR` owner to grant `SELECT ON HR.EMPLOYEES TO HARNESS_APP`, or to create "
        "an appropriate synonym, and then decide whether that source is intended.",
        "REPORTS has no EXECUTE privilege on APP.PAY_API (ORA-01031). Either grant EXECUTE "
        "on APP.PAY_API to REPORTS or create a public synonym for it.",
        "The account has no SELECT privilege on SALES.ORDERS. Instead of a grant, a private "
        "synonym `ORDERS` would let the query run.",
        # Other names, the same relation.
        "BILLING lacks the SELECT grant on `FIN.LEDGER`. Grant SELECT ON FIN.LEDGER TO "
        "BILLING, or alternatively a synonym for it.",
    ],
)
def test_a_synonym_offered_instead_of_a_needed_privilege_is_flagged(answer: str) -> None:
    [finding] = only("remedy_alternative", answer)
    assert "confers no privilege" in finding.problem
    assert unchecked("remedy_alternative", answer) == []


@pytest.mark.parametrize(
    "answer",
    [
        # Codex's C31 reproduction: the privilege failure is on another object.
        "No SELECT grant exists on HR.SECRET. Separately, APP.MISSING is absent but must "
        "resolve through APP.REAL: grant SELECT on APP.REAL, or create a synonym APP.MISSING "
        "for it. The grant is already present on APP.REAL.",
        "SALES has no SELECT privilege on CRM.ACCOUNTS. For the missing `ORDERS_V`, grant "
        "SELECT on OPS.ORDERS_V, or create a synonym ORDERS_V for it.",
    ],
)
def test_a_synonym_for_another_object_than_the_failure_passes(answer: str) -> None:
    assert only("remedy_alternative", answer) == []
    assert unchecked("remedy_alternative", answer) == []


@pytest.mark.parametrize(
    "answer",
    [
        # The failure names no object, so the relation cannot be checked.
        "ORA-01031: insufficient privileges. Either grant it to REPORTS or create a public "
        "synonym.",
        "The role is not granted to the account. A synonym avoids the grant entirely.",
    ],
)
def test_a_relation_that_cannot_be_established_is_unchecked_not_flagged(answer: str) -> None:
    assert only("remedy_alternative", answer) == []
    [left] = unchecked("remedy_alternative", answer)
    assert "could not tie the two to one object" in left.problem


@pytest.mark.parametrize(
    "answer",
    [
        # ORA-00942 alone does not establish a privilege failure.
        "ORA-00942: grant SELECT on the table to the account, or create a synonym if the "
        "name is meant to resolve elsewhere.",
        # A missing unqualified name: a synonym can be the fix.
        "`EMPLOYEE` does not exist in HARNESS_APP; create that object or a synonym for it.",
        # The synonym with the grant, or said not to be a fix.
        "HARNESS_APP has no grant on HR.EMPLOYEES. Grant SELECT ON HR.EMPLOYEES TO "
        "HARNESS_APP, and also create a synonym if you want the unqualified name.",
        "The account lacks the SELECT privilege. Ask HR to grant it; a synonym alone does "
        "not grant access.",
        "No grant exists. Grant SELECT; once granted, a synonym lets you drop the prefix.",
    ],
)
def test_a_synonym_with_the_grant_or_for_a_missing_name_passes(answer: str) -> None:
    assert only("remedy_alternative", answer) == []


# -- an elapsed duration as an upper bound -------------------------------------------------

BLOCKING = LookupFacts(
    operation_id="dba.blocking",
    succeeded=True,
    columns=["WAITING_SID", "WAITING_SECONDS", "BLOCKING_SID", "BLOCKING_IDLE_SECONDS"],
    rows=[[77, 118, 42, 320]],
    parameters={},
    sequence=1,
)
SESSIONS = LookupFacts(
    operation_id="dba.sessions",
    succeeded=True,
    columns=["SID", "STATUS", "SECONDS_IN_WAIT", "LAST_CALL_ET"],
    rows=[[42, "INACTIVE", 1805, 1805], [77, "ACTIVE", 118, 118]],
    parameters={"row_limit": 200},
    sequence=2,
)


@pytest.mark.parametrize(
    ("answer", "lookup"),
    [
        # Trial 19 DIAG-03's full sentence names the session: see the next test.
        ("SID 42's transaction has been open at most 1805 seconds, so little would roll back.", SESSIONS),
        ("Ending session 42 discards only the last 1,805 seconds of changes.", SESSIONS),
        ("KILL SESSION '42,1201' loses up to 320 seconds of uncommitted work.", BLOCKING),
        ("The blocker has held the lock for no more than 320 seconds.", BLOCKING),
        ("The waiter's transaction has been open up to 118 seconds.", BLOCKING),
    ],
)  # fmt: skip
def test_an_elapsed_duration_as_the_bound_of_older_work_is_flagged(answer, lookup) -> None:
    [finding] = only("elapsed_bound", answer, lookup)
    assert "at least" in finding.problem and "lookup" in finding.problem


def test_trial_19_wording_names_the_session_through_kill_session() -> None:
    answer = (
        "End the session with `ALTER SYSTEM KILL SESSION '42,1201'` or `ALTER SYSTEM "
        "DISCONNECT SESSION` — either one is for the user or DBA to run, and losing up to "
        "320 seconds of uncommitted work in that session is the consequence to weigh."
    )
    [finding] = only("elapsed_bound", answer, BLOCKING)
    assert "BLOCKING_IDLE_SECONDS for SID 42 = 320" in finding.problem


# Two sessions, one idle time each; SID 91's equals SID 42's wait elsewhere.
TWO = LookupFacts(
    operation_id="dba.sessions",
    succeeded=True,
    columns=["SID", "STATUS", "SECONDS_IN_WAIT", "LAST_CALL_ET"],
    rows=[[42, "INACTIVE", 320, 320], [91, "ACTIVE", 45, 600]],
    parameters={"row_limit": 200},
    sequence=2,
)


@pytest.mark.parametrize(
    "answer",
    [
        # Codex's C31 reproduction: the number matches SID 42, the subject is a job.
        "The separate maintenance job has up to 320 seconds of work remaining, according "
        "to its schedule.",
        "Losing up to 320 seconds of work is the consequence.",  # no session named
    ],
)
def test_a_bound_on_no_named_session_is_unchecked_not_flagged(answer: str) -> None:
    assert only("elapsed_bound", answer, BLOCKING) == []
    [left] = unchecked("elapsed_bound", answer, BLOCKING)
    assert "names no session" in left.problem


@pytest.mark.parametrize(
    "answer",
    [
        # SID 91's own values are 45 and 600; 320 is SID 42's.
        "Killing SID 91 loses up to 320 seconds of uncommitted work.",
        # The waiter in BLOCKING waited 118; 320 is the blocker's.
        "The waiter's transaction has been open up to 320 seconds.",
    ],
)
def test_another_sessions_equal_value_is_not_this_claim(answer: str) -> None:
    lookups = (TWO,) if "91" in answer else (BLOCKING,)
    assert only("elapsed_bound", answer, *lookups) == []
    assert unchecked("elapsed_bound", answer, *lookups) == []


def test_each_session_is_judged_by_its_own_value() -> None:
    assert [
        f.kind for f in only("elapsed_bound", "SID 91 loses up to 600 seconds of work.", TWO)
    ] == ["elapsed_bound"]
    assert only("elapsed_bound", "SID 91 loses up to 45 seconds of work.", TWO)  # its wait
    assert only("elapsed_bound", "SID 42 loses up to 600 seconds of work.", TWO) == []


@pytest.mark.parametrize(
    "answer",
    [
        "SID 42 has been idle for 320 seconds while holding the lock.",
        "Its transaction is at least 320 seconds old; how much work it holds is unknown.",
        "SID 77 has waited only 118 seconds so far.",  # the wait itself, not older work
        # A number that is no returned duration.
        "Rolling back could take up to 600 seconds of undo work.",
        # A timeout, not work.
        "Set DDL_LOCK_TIMEOUT to at most 320 seconds.",
    ],
)
def test_a_duration_stated_as_elapsed_or_unrelated_passes(answer: str) -> None:
    assert only("elapsed_bound", answer, BLOCKING) == []


def test_without_a_duration_column_nothing_is_known() -> None:
    other = replace(BLOCKING, columns=["A", "B", "C", "D"])
    assert only("elapsed_bound", "SID 42 loses up to 320 seconds of work.", other) == []
    assert unchecked("elapsed_bound", "Losing up to 320 seconds of work.", other) == []


def test_a_long_number_is_bounded() -> None:
    answer = "SID 42 loses up to " + "9" * 5000 + " seconds of work."
    assert only("elapsed_bound", answer, BLOCKING) == []


# -- argument rows grouped as overloads -----------------------------------------------------


def _subprograms(owner: str, package: str, rows, **extra) -> LookupFacts:
    return LookupFacts(
        operation_id="schema.package_subprograms",
        succeeded=True,
        columns=["SUBPROGRAM_NAME", "SUBPROGRAM_ID", "OVERLOAD", "POSITION", "ARGUMENT_NAME"],
        rows=rows,
        parameters={"owner": owner, "package_name": package, "row_limit": 200},
        sequence=extra.pop("sequence", 10),
        **extra,
    )


ETL = _subprograms(
    "HARNESS_APP",
    "ETL_ORDERS",
    [
        ["RUN_ALL", 4, None, 1, "P_BATCH_ID"],
        ["BATCH_STATUS", 5, None, 0, None],
        ["BATCH_STATUS", 5, None, 1, "P_BATCH_ID"],
    ],
)
PAY = _subprograms(
    "APP",
    "PAY_API",
    [
        ["POST", 1, "1", 1, "P_ID"],
        ["POST", 2, "2", 1, "P_ID"],
        ["POST", 2, "2", 2, "P_AMOUNT"],
        ["POST", 3, "3", 1, "P_REF"],
        ["VOID", 4, None, 1, "P_ID"],
    ],
    sequence=11,
)


@pytest.mark.parametrize(
    ("answer", "lookups"),
    [
        # Trial 19 KINJ-02's words.
        ("Subprograms: `RUN_ALL`, `BATCH_STATUS` (two overloads) — lookup 10.", (ETL,)),
        ("`ETL_ORDERS.BATCH_STATUS` is overloaded (lookup 10).", (ETL,)),
        ("`POST` has two overloads (lookup 11).", (PAY,)),  # it has three
        ("APP.PAY_API.POST comes in 5 overloaded versions.", (PAY,)),  # five argument rows
    ],
)
def test_an_overload_count_the_complete_rows_contradict_is_flagged(answer, lookups) -> None:
    [finding] = only("row_grouping", answer, *lookups)
    assert "SUBPROGRAM_ID" in finding.problem and "POSITION" in finding.problem


@pytest.mark.parametrize(
    ("answer", "lookups"),
    [
        ("`BATCH_STATUS` is a function returning VARCHAR2 (lookup 10).", (ETL,)),
        ("`POST` has three overloads (lookup 11).", (PAY,)),
        ("`POST` is overloaded (lookup 11).", (PAY,)),
        ("`VOID` (one overload).", (PAY,)),
        ("`BATCH_STATUS` is not overloaded.", (ETL,)),
        # Another package's member of the same name: not this result.
        ("`OTHER_PKG.BATCH_STATUS` has two overloads.", (ETL,)),
        # A name in no package result.
        ("`EXPORT_DAY` has two overloads.", (ETL,)),
    ],
)
def test_a_count_the_rows_support_or_cannot_judge_passes(answer, lookups) -> None:
    assert only("row_grouping", answer, *lookups) == []


@pytest.mark.parametrize(
    "lookup",
    [
        replace(ETL, truncated=True),
        replace(ETL, parameters={**ETL.parameters, "row_limit": 3}),  # at the limit
        replace(ETL, succeeded=False, rows=[]),
    ],
    ids=["truncated", "at_limit", "failed"],
)
def test_an_incomplete_package_result_leaves_the_count_unresolved(lookup) -> None:
    assert only("row_grouping", "`BATCH_STATUS` (two overloads).", lookup) == []


def test_a_name_in_two_packages_needs_the_package_named() -> None:
    other = _subprograms(
        "HARNESS_APP", "BATCH_API", [["BATCH_STATUS", 1, None, 1, "P"]], sequence=12
    )
    assert only("row_grouping", "`BATCH_STATUS` (two overloads).", ETL, other) == []
    named = "In ETL_ORDERS, `BATCH_STATUS` (two overloads)."
    assert [f.kind for f in only("row_grouping", named, ETL, other)] == ["row_grouping"]


# -- the revision the model is sent --------------------------------------------------------


def test_the_revision_note_names_each_derived_claim() -> None:
    answer = (
        "The account has no SELECT privilege on HR.EMPLOYEES; grant SELECT ON HR.EMPLOYEES, "
        "or create a synonym.\nKilling SID 42 loses up to 320 seconds of uncommitted work.\n"
        "`BATCH_STATUS` (two overloads) — lookup 10."
    )
    findings = check_claims(answer, [BLOCKING, ETL])
    assert [f.kind for f in findings] == ["remedy_alternative", "elapsed_bound", "row_grouping"]
    note = revision_note(findings)
    assert note.count("You wrote:") == 3
    assert "confers no privilege" in note
    assert "at least 320 seconds old" in note
    assert "returned 1 SUBPROGRAM_ID for BATCH_STATUS" in note


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"kiwi_enabled": True})


def test_an_elapsed_bound_in_a_kiwi_answer_gets_one_revision(
    client: TestClient, dba, targets
) -> None:
    call = ToolCall(id="b1", name=tool_name_for("dba.blocking"), input={"why": "Who blocks?"})
    wrong = "SID 42 blocks SID 77. Killing SID 42 loses up to 320 seconds of uncommitted work."
    right = (
        "SID 42 blocks SID 77. Killing SID 42 rolls back its open transaction, which is at "
        "least 320 seconds old; how much work it holds is unknown."
    )
    provider = use_script(
        client,
        ScriptedTurn(text="Checking blockers. ", tool_calls=(call,)),
        ScriptedTurn(text=wrong),
        ScriptedTurn(text=right),
    )
    events = sse(client, dba, kiwi_payload(targets["development"]["id"]))
    assert last(events, "tool_result")["status"] == "succeeded"
    checks = [data for name, data in events if name == "claim_check"]
    assert [c["action"] for c in checks] == ["revise", "none"]
    assert [f["kind"] for f in checks[0]["findings"]] == ["elapsed_bound"]
    note = provider.conversations[0].notes[0]
    assert "BLOCKING_IDLE_SECONDS for SID 42 = 320" in note and "at least 320 seconds old" in note
    assert answer_of(events) == right
