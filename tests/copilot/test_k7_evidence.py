"""Answers held to the evidence the request's lookups returned (K-7, Q3).

Trial 18 (copilot-eval/strata-k7-focused-20261018T032443Z.json) failed two diagnoses on
grounding although every rubric item was met: KINJ-02 asked for a larger ``row_limit``
on a run-history lookup that had returned 1 row of the 20 asked for, untruncated, and
DIAG-01 offered a catalog listing to show whether a synonym "was dropped or never
created". The same row-limit step appears in trial 15 DIAG-02 and trial 17 KINJ-02.

The wrong lines here are those answers verbatim, then the same claims with other
identifiers and wording. The controls are the same steps where the evidence does
support them (a result at its limit, a truncated result) and hedged Unknown lines, so a
check that rejects too much shows up as a failure here, not as an evasive answer later.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.claim_checks import LookupFacts, check_claims
from harness_api.copilot.evidence import coverage, lookup_record, result_note
from harness_api.copilot.provider import ScriptedTurn, ToolCall
from harness_api.copilot.toolbox import tool_name_for
from tests.copilot.test_copilot import sse
from tests.copilot.test_kiwi import kiwi_payload as plain_payload
from tests.copilot.test_kiwi import last, use_script

HISTORY = "dba.scheduler_run_history"


def history(
    rows: int, row_limit: int = 20, *, truncated: bool = False, sequence: int = 3
) -> LookupFacts:
    return LookupFacts(
        operation_id=HISTORY,
        succeeded=True,
        columns=["LOG_ID", "STATUS", "ERROR_NUMBER"],
        rows=[[9000 + n, "FAILED"] for n in range(rows)],
        parameters={"owner": "HARNESS_APP", "job_name": "EXPORT_ORDERS", "row_limit": row_limit},
        sequence=sequence,
        call_id=f"call_{sequence}",
        row_count=rows,
        truncated=truncated,
    )


def kinds(answer: str, *lookups: LookupFacts) -> list[str]:
    return [finding.kind for finding in check_claims(answer, lookups)]


# -- coverage --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lookup", "state", "more"),
    [
        (history(1, 20), "complete", False),
        (history(0, 20), "empty", False),
        (history(20, 20), "at_limit", True),
        (history(7, 20, truncated=True), "truncated", True),
        (LookupFacts(HISTORY, False, [], [], {"row_limit": 20}, sequence=1), "failed", False),
    ],
)
def test_coverage_says_whether_more_rows_could_exist(
    lookup: LookupFacts, state: str, more: bool
) -> None:
    seen = coverage(lookup)
    assert seen.state == state
    assert seen.could_return_more is more


def test_a_lookup_without_a_row_limit_is_complete_unless_truncated() -> None:
    status = LookupFacts("schema.object_status", True, ["STATUS"], [["VALID"]], {}, sequence=1)
    assert coverage(status).state == "complete"
    assert coverage(status).limit is None


def test_the_result_note_tells_the_model_a_complete_result_has_no_more() -> None:
    note = result_note(HISTORY, 1, 20, False)
    assert note.startswith("[harness] Coverage: complete.")
    assert "larger row_limit or another page returns no more" in note
    # Said of a log, without naming retention as the cause of anything.
    assert "does not show why other entries are absent" in note
    assert "retention" not in note.replace("retained", "")
    capped = result_note(HISTORY, 20, 20, False)
    assert "more rows may match" in capped
    # A truncated result already carries the toolbox's own truncation line.
    assert result_note(HISTORY, 7, 20, True) == ""
    assert result_note("schema.object_status", 1, None, False) == ""


def test_the_lookup_record_has_identity_and_coverage_but_no_row_values() -> None:
    record = lookup_record(
        [history(1, 20), history(20, 20, sequence=4)],
        [("schema.object_source", "budget: tool_calls")],
    )
    first, second = record["lookups"]
    assert first == {
        "lookup": 3,
        "callId": "call_3",
        "operationId": HISTORY,
        "parameters": {"owner": "HARNESS_APP", "job_name": "EXPORT_ORDERS", "row_limit": 20},
        "coverage": "complete",
        "rows": 1,
        "rowLimit": 20,
        "summary": "1 row, complete (fewer than row_limit 20, not truncated)",
        # A run log's rows are not among the facts the record states.
        "facts": [],
        # The job it is about, from its parameters and typed owner/name columns (C24).
        "subjects": ["HARNESS_APP.EXPORT_ORDERS"],
        "sourceRead": [],
    }
    assert second["coverage"] == "at_limit"
    assert record["notRun"] == [
        {"operationId": "schema.object_source", "reason": "budget: tool_calls"}
    ]
    assert "9000" not in repr(record) and "FAILED" not in repr(record)


# -- a larger cap for a complete result ------------------------------------------------------

TRIAL_18_KINJ_02 = (
    "3. Pull more of the run history (a larger row_limit on `dba__scheduler_run_history`) "
    "to see whether the other two failures carry the same ERROR_NUMBER, which would show "
    "whether this is recurring or new to run 9002."
)
TRIAL_17_KINJ_02 = (
    "2. `dba.scheduler_run_history` with a larger row limit for `HARNESS_APP.EXPORT_ORDERS` "
    "to see whether earlier failures carry the same error number."
)
TRIAL_15_DIAG_02 = (
    "3. Re-run `dba.scheduler_run_history` for `HARNESS_APP.EXPORT_ORDERS` with a larger "
    "row limit — would show whether both failures carry ORA-20001 and whether earlier runs "
    "succeeded, settling the transient-versus-persistent question."
)


@pytest.mark.parametrize(
    "line",
    [
        TRIAL_18_KINJ_02,
        TRIAL_17_KINJ_02,
        TRIAL_15_DIAG_02,
        # Other wording, same lookup.
        "Next: raise the row_limit on the job log for HARNESS_APP.EXPORT_ORDERS to find the "
        "earlier failures.",
        "1. Fetch more rows from `dba.scheduler_run_history` (row_offset 20) for EXPORT_ORDERS.",
        "Increase the cap on the run history lookup to 500 so the older runs show up.",
        "Re-run dba.scheduler_run_history(owner=HARNESS_APP, job_name=EXPORT_ORDERS) with a "
        "larger row_limit.",
    ],
)
def test_a_larger_limit_for_a_complete_result_is_flagged(line: str) -> None:
    assert kinds(line, history(1, 20)) == ["next_check_cap"]


@pytest.mark.parametrize(
    "line",
    [
        # The Q1-Q4 review's reproductions (C24): another job, and the right advice.
        "Increase row_limit for dba.scheduler_run_history(owner=HARNESS_APP, job_name=WEEKLY) "
        "to inspect its run history.",
        "Do not increase row_limit for dba.scheduler_run_history: the result is complete.",
        # Another job named without parameter syntax; a larger limit said to change nothing.
        "Next: raise the row_limit on the job log for SALES.NIGHTLY_LOAD to find its failures.",
        "A larger row_limit on the run history would not return more rows for EXPORT_ORDERS.",
    ],
)
def test_a_step_about_another_target_or_against_a_larger_limit_passes(line: str) -> None:
    assert kinds(line, history(1, 20)) == []


def test_mixed_coverage_is_judged_per_target() -> None:
    weekly = LookupFacts(
        operation_id=HISTORY,
        succeeded=True,
        columns=["LOG_ID", "STATUS", "ERROR_NUMBER"],
        rows=[[n, "FAILED", 1] for n in range(20)],
        parameters={"owner": "HARNESS_APP", "job_name": "WEEKLY", "row_limit": 20},
        sequence=4,
        row_count=20,
        truncated=True,
    )
    complete = history(1, 20)
    assert kinds("Pull more of the run history for WEEKLY.", complete, weekly) == []
    assert kinds("Pull more of the run history for EXPORT_ORDERS.", complete, weekly) == [
        "next_check_cap"
    ]


def test_the_finding_cites_the_lookup_and_does_not_claim_a_cause() -> None:
    (finding,) = check_claims(TRIAL_18_KINJ_02, [history(1, 20)])
    assert "lookup 3 (owner=HARNESS_APP, job_name=EXPORT_ORDERS, row_limit=20) returned 1 row" in (
        finding.problem
    )
    assert "returns nothing more" in finding.problem
    # The counter-versus-history gap is left unexplained, not put down to retention.
    assert "does not by itself show why" in finding.problem
    assert "purge" not in finding.problem


@pytest.mark.parametrize(
    "lookup",
    [history(20, 20), history(7, 20, truncated=True)],
    ids=["at_limit", "truncated"],
)
def test_a_larger_limit_for_a_result_that_may_have_more_passes(lookup: LookupFacts) -> None:
    assert kinds(TRIAL_18_KINJ_02, lookup) == []


@pytest.mark.parametrize(
    "line",
    [
        # Names no lookup the request made.
        "3. Ask your DBA for more rows from the export target's own audit table.",
        # A different lookup, not more of the one made.
        "2. Run `dba.scheduler_job_detail` for SALES.NIGHTLY_LOAD to read its MAX_FAILURES.",
        # About the result, not a request for more of it.
        "- Lookup 3 asked for 20 rows of run history and returned 1, so no more are retained.",
    ],
)
def test_other_next_steps_about_the_run_history_pass(line: str) -> None:
    assert kinds(line, history(1, 20)) == []


def test_a_larger_limit_is_not_judged_for_a_lookup_never_made_or_failed() -> None:
    failed = LookupFacts(HISTORY, False, [], [], {"row_limit": 20}, sequence=1)
    assert kinds(TRIAL_18_KINJ_02) == []
    assert kinds(TRIAL_18_KINJ_02, failed) == []


def test_a_row_limit_in_a_code_block_is_not_a_next_step() -> None:
    answer = (
        "Run this yourself:\n\n```sql\n-- a larger row_limit on run history\n"
        "SELECT 1 FROM dual;\n```\n"
    )
    assert kinds(answer, history(1, 20)) == []


def test_when_any_lookup_of_the_operation_could_have_more_the_step_passes() -> None:
    other = history(20, 20, sequence=5)
    assert kinds(TRIAL_18_KINJ_02, history(1, 20), other) == []


# -- history from current state --------------------------------------------------------------

TRIAL_18_DIAG_01 = (
    "3. If the user expects an `EMPLOYEE` object, list HARNESS_APP objects of type "
    "`SYNONYM` to see whether one was dropped or never created."
)
TRIAL_18_DIAG_01_UNKNOWN = (
    "- An object named `EMPLOYEE` in another schema, or a private/public synonym that used "
    "to resolve it, is unknown: I searched HARNESS_APP by name only, not other schemas or "
    "`ALL_SYNONYMS`."
)


@pytest.mark.parametrize(
    "line",
    [
        TRIAL_18_DIAG_01,
        "2. Query `schema.list_objects` for SALES views named ORDERS_V, which would show "
        "whether it was dropped.",
        "Checking ALL_OBJECTS for CUSTOMER_DIM will tell you whether it was ever renamed.",
        "List the synonyms in APP_OWNER to establish whether the table never existed.",
    ],
)
def test_a_current_state_check_offered_for_history_is_flagged(line: str) -> None:
    assert kinds(line) == ["history_claim"]


@pytest.mark.parametrize(
    "line",
    [
        "The synonym `EMPLOYEE` was dropped, so the body no longer compiles.",
        "ORDERS_V has been dropped from SALES.",
    ],
)
def test_an_unhedged_drop_is_flagged(line: str) -> None:
    assert kinds(line) == ["history_claim"]


@pytest.mark.parametrize(
    "line",
    [
        TRIAL_18_DIAG_01_UNKNOWN,
        "- Whether a synonym `EMPLOYEE` was dropped or never created is unknown: the catalog "
        "shows only objects that exist now (lookup 5).",
        "- Hypothesis: the view may have been dropped during the release; no lookup shows it.",
        "You said the table was dropped yesterday; lookup 5 shows no EMPLOYEE now.",
        "2. Run `schema.list_objects` for SALES, name ORDERS_V, to see whether it exists now.",
        "- If such an object is later dropped, the body becomes INVALID again.",
    ],
)
def test_present_state_and_hedged_history_pass(line: str) -> None:
    # The lines cite lookup 5, so the request made one (C24: a citation must exist).
    listing = LookupFacts(
        "schema.list_objects",
        True,
        ["OBJECT_NAME", "OBJECT_TYPE"],
        [],
        {"owner": "HARNESS_APP", "name_filter": "EMPLOYEE", "row_limit": 50},
        sequence=5,
        row_count=0,
    )
    assert kinds(line, listing) == []


@pytest.mark.parametrize(
    "line",
    [
        # The Q1-Q4 review's reproductions (C24): the limit stated, and a record of the past.
        "A current catalog query cannot show whether the synonym was dropped.",
        "Ask the DBA to check the retained DDL audit trail to determine whether the synonym "
        "was dropped.",
        "If your team keeps change tickets, one would show whether ORDERS_V was removed.",
        "Listing SALES objects will not tell you whether ORDERS_V was ever dropped.",
    ],
)
def test_a_stated_limit_or_a_record_of_the_past_passes(line: str) -> None:
    assert kinds(line) == []


# -- the service: notes, record and revision -------------------------------------------------

HISTORY_TOOL = tool_name_for(HISTORY)


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"kiwi_enabled": True})


def payload(profile_id: str) -> dict:
    return plain_payload(profile_id, protocolVersion="1.5")


def history_turn(row_limit: int) -> ScriptedTurn:
    call = ToolCall(
        id="h1",
        name=HISTORY_TOOL,
        input={
            "owner": "HARNESS_APP",
            "job_name": "EXPORT_ORDERS",
            "row_limit": row_limit,
            "why": "Read the failed run.",
        },
    )
    return ScriptedTurn(text="Reading the run history. ", tool_calls=(call,))


ASKS_FOR_MORE = (
    "Run 9002 failed with ORA-20001.\n\n## Unknown\n\n- Whether earlier runs failed the "
    "same way.\n\n## Next steps\n\n1. Pull more of the run history "
    "(a larger row_limit on `dba__scheduler_run_history`) to see the earlier failures."
)
REVISED = (
    "Run 9002 failed with ORA-20001.\n\n## Unknown\n\n- Whether the earlier failures carry "
    "the same error: lookup 1 returned every retained run, and none of them is shown."
)


def test_a_complete_result_is_marked_so_and_a_larger_limit_is_revised(
    client: TestClient, dba, targets
) -> None:
    provider = use_script(
        client, history_turn(50), ScriptedTurn(text=ASKS_FOR_MORE), ScriptedTurn(text=REVISED)
    )
    events = sse(client, dba, payload(targets["development"]["id"]))

    result = last(events, "tool_result")
    assert result["status"] == "succeeded", result
    assert 0 < result["rowCount"] < 50 and not result["truncated"]
    # The model was told, by the harness and outside the data, that this was all of it.
    received = provider.conversations[0].received[0].content
    end = received.index("----- END UNTRUSTED TOOL_RESULT")
    assert "[harness] Coverage: complete." in received[end:]
    assert "[harness] Coverage" not in received[:end]

    checks = [data for name, data in events if name == "claim_check"]
    assert checks[0]["action"] == "revise"
    assert [f["kind"] for f in checks[0]["findings"]] == ["next_check_cap"]
    assert checks[1]["findings"] == []
    assert last(events, "revision")["draft"].endswith(ASKS_FOR_MORE)

    record = last(events, "lookup_record")
    assert [entry["coverage"] for entry in record["lookups"]] == ["complete"]
    assert record["lookups"][0]["parameters"]["row_limit"] == 50
    assert record["lookups"][0]["callId"] == "h1"
    # Sent after the answer is settled, before the budget that ends the loop.
    order = [name for name, _ in events]
    assert order.index("lookup_record") > order.index("revision")
    assert order[order.index("lookup_record") + 1] == "budget"


def test_a_result_at_its_limit_may_ask_for_more(client: TestClient, dba, targets) -> None:
    provider = use_script(client, history_turn(1), ScriptedTurn(text=ASKS_FOR_MORE))
    events = sse(client, dba, payload(targets["development"]["id"]))

    assert last(events, "tool_result")["rowCount"] == 1
    received = provider.conversations[0].received[0].content
    assert "more rows may match" in received
    check = last(events, "claim_check")
    assert check["findings"] == [] and check["action"] == "none"
    assert "revision" not in [name for name, _ in events]
    assert last(events, "lookup_record")["lookups"][0]["coverage"] == "at_limit"


def test_a_failed_lookup_gets_no_coverage_note(client: TestClient, dba, targets) -> None:
    call = ToolCall(id="x1", name=HISTORY_TOOL, input={"owner": "HARNESS_APP", "nope": 1})
    provider = use_script(
        client, ScriptedTurn(text="Looking. ", tool_calls=(call,)), ScriptedTurn(text="Done.")
    )
    events = sse(client, dba, payload(targets["development"]["id"]))

    assert last(events, "tool_result")["status"] != "succeeded"
    assert "Coverage" not in provider.conversations[0].received[0].content
    assert last(events, "lookup_record")["lookups"][0]["coverage"] == "failed"


# -- citations bound to the lookup they name (C24) --------------------------------------------

STATUS = LookupFacts(
    operation_id="schema.object_status",
    succeeded=True,
    columns=["OWNER", "OBJECT_NAME", "OBJECT_TYPE", "STATUS"],
    rows=[["APP", "PKG", "PACKAGE BODY", "INVALID"]],
    parameters={"owner": "APP", "object_name": "PKG"},
    sequence=1,
)
DENIED = LookupFacts(
    operation_id="schema.object_source",
    succeeded=False,
    columns=[],
    rows=[],
    parameters={"owner": "APP", "object_name": "PKG", "object_type": "PACKAGE BODY"},
    sequence=2,
)


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("- `APP.PKG` body is INVALID (lookup 3).", "there is no lookup 3"),
        ("- The body's line 5 reads `SELECT 1` (lookup 2).", "it failed or was refused"),
        ("- `OTHER.PKG` is INVALID (lookup 1).", "is not about OTHER.PKG"),
        ("- OTHER.PKG is INVALID (lookups 1-2).", "is not about OTHER.PKG"),
    ],
)
def test_a_citation_that_cannot_support_its_statement_is_flagged(line: str, problem: str) -> None:
    (finding,) = check_claims(line, [STATUS, DENIED])
    assert finding.kind == "evidence_reference"
    assert problem in finding.problem


@pytest.mark.parametrize(
    "line",
    [
        "- `APP.PKG` body is INVALID (lookup 1).",
        "- APP.PKG is INVALID (lookups 1-2); its source could not be read: lookup 2 was refused.",
        "- Lookup 2 failed, so the body's source is unknown.",
        "- `PKG.settle` is in the INVALID body (lookup 1).",
        "- `dba.scheduler_run_history` was not called; status is from lookup 1.",
        "No lookup numbers here at all.",
    ],
)
def test_a_citation_that_matches_its_lookup_passes(line: str) -> None:
    assert kinds(line, STATUS, DENIED) == []


def test_the_record_states_status_errors_roles_and_source_coverage_from_rows() -> None:
    errors = LookupFacts(
        "schema.object_errors",
        True,
        ["LINE", "POSITION", "TEXT"],
        [[5, 39, "PL/SQL: ORA-00942: table or view does not exist"]],
        {"owner": "APP", "object_name": "PKG", "object_type": "PACKAGE BODY"},
        sequence=5,
    )
    blocking = LookupFacts(
        "dba.blocking",
        True,
        ["WAITING_SID", "BLOCKING_SID", "BLOCKING_STATUS"],
        [[318, 1205, "INACTIVE"]],
        {},
        sequence=3,
    )
    source = LookupFacts(
        "schema.object_source_range",
        True,
        ["LINE", "TEXT"],
        [[n, "x"] for n in (1, 2, 3, 7)],
        {"owner": "APP", "object_name": "PKG", "object_type": "PACKAGE BODY"},
        sequence=4,
    )
    record = lookup_record([STATUS, errors, blocking, source, DENIED])
    stated = {entry["lookup"]: entry["facts"] for entry in record["lookups"]}
    assert stated[1] == ["APP.PKG PACKAGE BODY: INVALID"]
    assert stated[5] == [
        "APP.PKG PACKAGE BODY: line 5, position 39: PL/SQL: ORA-00942: table or view does not exist"
    ]
    assert stated[3] == ["session 318 waits on session 1205 (blocker INACTIVE)"]
    assert stated[4] == ["APP.PKG PACKAGE BODY: 4 line(s) read, lines 1-7, not contiguous"]
    # A refused lookup states nothing.
    refused = next(entry for entry in record["lookups"] if entry["lookup"] == 2)
    assert (refused["coverage"], refused["facts"]) == ("failed", [])
