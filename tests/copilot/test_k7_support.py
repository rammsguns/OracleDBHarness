"""Which lookup supports which statement, by identity (C24, Q3).

The C23-C26 correction review (docs/k7-c23-c26-review-2026-10-18.md) reproduced five
gaps in the evidence checks, each a match by overlapping words: APP.NIGHTLY's history
bound to a step about APP.WEEKLY because APP matched; a header line of APP.PKG's body
supported a fix in APP.PKG.UNREAD_MEMBER; APP.PKG's status supported "OTHER.UNREAD is
VALID" and "APP.UNREAD is VALID"; and with no lookups, "lookup 9" passed. They are the
first controls here, each with the supported counterpart that must still pass, then
the same contract at the service boundary through the stand-in database: mixed
complete and at-limit targets, specification versus body, and a member read in part
versus whole. The design is in claude_k7.md, "C24 design".
"""

from __future__ import annotations

import sqlite3
import time
import tracemalloc
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.claim_checks import LookupFacts, check_claims
from harness_api.copilot.provider import ScriptedTurn, ToolCall
from harness_api.copilot.support import (
    Identity,
    answer_items,
    cited_segments,
    evidence_map,
    record_entry,
)
from harness_api.copilot.toolbox import tool_name_for
from tests.copilot.test_copilot import sse
from tests.copilot.test_kiwi import kiwi_payload as plain_payload
from tests.copilot.test_kiwi import last, use_script

NIGHTLY = LookupFacts(
    "dba.scheduler_run_history",
    True,
    ["STATUS"],
    [["SUCCEEDED"]],
    {"owner": "APP", "job_name": "NIGHTLY", "row_limit": 20},
    sequence=1,
)
WEEKLY_AT_LIMIT = LookupFacts(
    "dba.scheduler_run_history",
    True,
    ["STATUS"],
    [["FAILED"]] * 5,
    {"owner": "APP", "job_name": "WEEKLY", "row_limit": 5},
    sequence=2,
)
PKG_HEADER = LookupFacts(
    "schema.object_source_range",
    True,
    ["LINE", "TEXT"],
    [[1, "PACKAGE BODY PKG AS"]],
    {"owner": "APP", "object_name": "PKG", "object_type": "PACKAGE BODY", "start_line": 1,
     "end_line": 1},
    sequence=1,
)  # fmt: skip
PKG_MEMBER = LookupFacts(
    "schema.object_source_range",
    True,
    ["LINE", "TEXT"],
    [
        [10, "  PROCEDURE unread_member(p_id NUMBER) IS"],
        [11, "  BEGIN"],
        [12, "    UPDATE t SET x = 1 WHERE id = p_id;"],
        [13, "  END unread_member;"],
    ],
    {"owner": "APP", "object_name": "PKG", "object_type": "PACKAGE BODY", "start_line": 10,
     "end_line": 13},
    sequence=1,
)  # fmt: skip
PKG_STATUS = LookupFacts(
    "schema.object_status",
    True,
    ["OWNER", "OBJECT_NAME", "OBJECT_TYPE", "STATUS"],
    [["APP", "PKG", "PACKAGE BODY", "VALID"]],
    {"owner": "APP", "object_name": "PKG", "object_type": "PACKAGE BODY"},
    sequence=1,
)


# PL/Scope's record of unread_member's definition at line 10: compiled code, so the lines
# from there are known to start in code (C24-B).
PKG_PLSCOPE = LookupFacts(
    "schema.plscope_identifiers",
    True,
    ["OBJECT_TYPE", "PLSCOPE_STATUS", "NAME", "IDENTIFIER_TYPE", "USAGE", "LINE", "COL"],
    [["PACKAGE BODY", "COLLECTED", "UNREAD_MEMBER", "PROCEDURE", "DEFINITION", 10, 13]],
    {"owner": "APP", "object_name": "PKG"},
    sequence=2,
)


def kinds(answer: str, *lookups: LookupFacts) -> list[str]:
    return [finding.kind for finding in check_claims(answer, lookups)]


# -- the review's five controls, and their supported counterparts -------------------------


def test_another_jobs_history_does_not_bind_a_step_because_the_owner_matches() -> None:
    step = (
        "Increase row_limit for dba.scheduler_run_history on APP.WEEKLY to inspect its run history."
    )
    assert kinds(step, NIGHTLY) == []
    assert kinds(step.replace("WEEKLY", "NIGHTLY"), NIGHTLY) == ["next_check_cap"]


def test_a_header_line_does_not_support_a_change_in_an_unread_member() -> None:
    assert kinds("The fix belongs in APP.PKG.UNREAD_MEMBER.", PKG_HEADER) == ["change_location"]
    # Lines 10-13 alone may lie inside a comment opened earlier; PL/Scope's definition
    # row establishes that they are code.
    assert kinds("The fix belongs in APP.PKG.UNREAD_MEMBER.", PKG_MEMBER) == ["change_location"]
    assert kinds("The fix belongs in APP.PKG.UNREAD_MEMBER.", PKG_MEMBER, PKG_PLSCOPE) == []


@pytest.mark.parametrize("name", ["OTHER.UNREAD", "APP.UNREAD"])
def test_a_status_lookup_does_not_support_another_objects_status(name: str) -> None:
    (finding,) = check_claims(f"Lookup 1 shows {name} is VALID.", [PKG_STATUS])
    assert finding.kind == "evidence_reference"
    assert f"is not about {name}" in finding.problem
    assert check_claims("Lookup 1 shows APP.PKG is VALID.", [PKG_STATUS]) == []


def test_a_citation_with_no_lookups_made_is_flagged() -> None:
    (finding,) = check_claims("Lookup 9 shows APP.PKG is VALID.", [])
    assert finding.kind == "evidence_reference"
    assert "this request made no lookups; there is no lookup 9" in finding.problem


# -- coverage per target, and source per member ----------------------------------------


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        ("Pull more of the run history for APP.NIGHTLY.", ["next_check_cap"]),
        ("Pull more of the run history for `NIGHTLY`.", ["next_check_cap"]),
        ("Pull more of the run history for APP.WEEKLY.", []),
        ("Pull more of the run history for WEEKLY.", []),
    ],
)
def test_mixed_coverage_is_judged_for_the_target_named(step: str, expected: list[str]) -> None:
    assert kinds(step, NIGHTLY, WEEKLY_AT_LIMIT) == expected


SPEC = LookupFacts(
    "schema.object_source",
    True,
    ["LINE", "TEXT"],
    [[1, "PACKAGE pkg AS"], [2, "  PROCEDURE unread_member(p_id NUMBER);"], [3, "END pkg;"]],
    {"owner": "APP", "object_name": "PKG", "object_type": "PACKAGE"},
    sequence=1,
)


def test_a_specification_declares_a_member_without_showing_its_code() -> None:
    (finding,) = check_claims("The fix belongs in `APP.PKG.unread_member`.", [SPEC])
    assert "a declaration is not the implementation" in finding.problem


def test_the_map_keeps_identity_type_and_member_coverage() -> None:
    entry = evidence_map([PKG_MEMBER, PKG_PLSCOPE])[1]
    (read,) = entry.source
    assert read.unit.identity == Identity("APP", "PKG")
    assert read.unit.type == "PACKAGE BODY"
    (member,) = read.members
    assert (member.name, member.implementation, member.whole) == ("UNREAD_MEMBER", True, True)
    assert read.describe_lines() == "lines 10-13"
    (alone,) = evidence_map([PKG_MEMBER])[1].source[0].members
    assert (alone.whole, bool(alone.unknown)) == (False, True)


def test_a_value_in_a_cell_is_not_an_identity_the_lookup_is_about() -> None:
    job = LookupFacts(
        "dba.scheduler_job_detail",
        True,
        ["OWNER", "JOB_NAME", "JOB_ACTION"],
        [["APP", "EXPORT", "OTHER.RUN_EXPORT"]],
        {"owner": "APP", "job_name": "EXPORT"},
        sequence=1,
    )
    entry = evidence_map([job])[1]
    assert entry.identities() == [Identity("APP", "EXPORT")]
    # Quoting the value is supported; it is not a claim about OTHER.RUN_EXPORT itself.
    assert kinds("Lookup 1 shows JOB_ACTION `OTHER.RUN_EXPORT`.", job) == []


def test_a_citation_covers_the_text_it_is_attached_to() -> None:
    segments = cited_segments(
        "APP.PKG is VALID (lookup 1), and the change touches APP.OTHER. Lookup 2 shows APP.X."
    )
    assert [(set(s.citations), s.qualified) for s in segments] == [
        ({1}, (("APP", "PKG"),)),
        ({2}, (("APP", "X"),)),
    ]


def test_a_parenthetical_citation_is_about_what_precedes_it() -> None:
    # Trial 18-era DIAG-01 (a reviewed pass), with other names.
    line = "Since I read the whole body (lines 1-18, lookup 3), here it is with `APP.T2` in place."
    (segment,) = cited_segments(line)
    assert segment.qualified == ()


def test_items_follow_the_diagnosis_form_and_other_headings_end_a_section() -> None:
    items = answer_items(
        "**Finding:** the body is invalid.\n\n## Evidence\n- A fact (lookup 1).\n\n"
        "**Suggested fix**\n- Change the table name.\n\nNext steps (max three):\n1. Read it."
    )
    assert [(i.section, i.text) for i in items] == [
        ("finding", "the body is invalid."),
        ("evidence", "A fact (lookup 1)."),
        ("other", "Change the table name."),
        ("next", "Read it."),
    ]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("## Evidence\n- The body is INVALID and line 5 names EMPLOYEE.", ["evidence_reference"]),
        ("## Evidence\n- The body is INVALID (lookup 1).", []),
        ("## Evidence\n- Line 12 updates t (the source read).", []),
        ("## Evidence\n- Line 40 updates t.", ["evidence_reference"]),
        ("## Evidence\n- Hypothesis: the grant is missing.", []),
    ],
)
def test_an_evidence_item_cites_a_lookup_or_lines_read(line: str, expected: list[str]) -> None:
    assert kinds(line, PKG_MEMBER) == expected


# -- at the service boundary, through the stand-in database --------------------------------

STATUS_TOOL = tool_name_for("schema.object_status")
RANGE_TOOL = tool_name_for("schema.object_source_range")
SOURCE_TOOL = tool_name_for("schema.object_source")
HISTORY_TOOL = tool_name_for("dba.scheduler_run_history")


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"kiwi_enabled": True})


def payload(profile_id: str) -> dict:
    return plain_payload(profile_id, protocolVersion="1.5")


def lookups(*calls: ToolCall) -> ScriptedTurn:
    return ScriptedTurn(text="Reading. ", tool_calls=calls)


def call(call_id: str, tool: str, **parameters) -> ToolCall:
    return ToolCall(id=call_id, name=tool, input={"why": "Diagnose.", **parameters})


def body_range(start: int, end: int) -> ToolCall:
    return call(
        "r1",
        RANGE_TOOL,
        owner="HARNESS_APP",
        object_name="EMPLOYEE_REPORT",
        object_type="PACKAGE BODY",
        start_line=start,
        end_line=end,
        row_limit=50,
    )


def checks(events: list) -> list[dict]:
    return [data for name, data in events if name == "claim_check"]


def test_a_status_cited_for_another_object_is_revised(client: TestClient, dba, targets) -> None:
    status = call("s1", STATUS_TOOL, owner="HARNESS_APP", object_name="EMPLOYEE_REPORT")
    wrong = "The specification is fine: lookup 1 shows HARNESS_APP.REPORT_UTIL is VALID."
    right = "Lookup 1 shows HARNESS_APP.EMPLOYEE_REPORT's body is INVALID."
    use_script(client, lookups(status), ScriptedTurn(text=wrong), ScriptedTurn(text=right))
    events = sse(client, dba, payload(targets["development"]["id"]))

    first, revised = checks(events)
    assert [f["kind"] for f in first["findings"]] == ["evidence_reference"]
    assert "is not about HARNESS_APP.REPORT_UTIL" in first["findings"][0]["problem"]
    assert revised["findings"] == []
    assert last(events, "revision")["draft"].endswith(wrong)
    entry = last(events, "lookup_record")["lookups"][0]
    assert "HARNESS_APP.EMPLOYEE_REPORT PACKAGE BODY" in entry["subjects"]


def test_a_member_fix_needs_its_code_read_whole(client: TestClient, dba, targets) -> None:
    claim = "The fix belongs in `HARNESS_APP.EMPLOYEE_REPORT.headcount`, at line 5."
    # A flagged claim gets one revision; here the model keeps it.
    kept = ScriptedTurn(text=claim)
    use_script(client, lookups(body_range(1, 1)), ScriptedTurn(text=claim), kept)
    header_only = sse(client, dba, payload(targets["development"]["id"]))
    finding = checks(header_only)[0]["findings"][0]
    assert finding["kind"] == "change_location"
    assert "HEADCOUNT is not defined in those lines" in finding["problem"]

    use_script(client, lookups(body_range(1, 7)), ScriptedTurn(text=claim))
    whole = sse(client, dba, payload(targets["development"]["id"]))
    assert checks(whole)[0]["findings"] == []
    (read,) = last(whole, "lookup_record")["lookups"][0]["sourceRead"]
    assert read["lines"] == "lines 1-7"
    assert read["members"] == ["HEADCOUNT: implementation from line 2, read whole"]

    # The same member's lines without line 1: whether they are code is not established.
    use_script(client, lookups(body_range(2, 7)), ScriptedTurn(text=claim), kept)
    unanchored = sse(client, dba, payload(targets["development"]["id"]))
    (read,) = last(unanchored, "lookup_record")["lookups"][0]["sourceRead"]
    assert "coverage unknown (the lines from 2 were read without" in read["members"][0]
    assert "is not established" in checks(unanchored)[0]["findings"][0]["problem"]

    use_script(client, lookups(body_range(1, 4)), ScriptedTurn(text=claim), kept)
    part = sse(client, dba, payload(targets["development"]["id"]))
    assert "was found in code among the lines returned" in checks(part)[0]["findings"][0]["problem"]


def test_the_specification_is_not_the_body(client: TestClient, dba, targets) -> None:
    spec = call(
        "p1", SOURCE_TOOL, owner="HARNESS_APP", object_name="EMPLOYEE_REPORT", object_type="PACKAGE"
    )
    claim = "The change belongs in `EMPLOYEE_REPORT.headcount`."
    use_script(client, lookups(spec), ScriptedTurn(text=claim), ScriptedTurn(text=claim))
    events = sse(client, dba, payload(targets["development"]["id"]))
    finding = checks(events)[0]["findings"][0]
    assert "declares HEADCOUNT at line 2" in finding["problem"]


def test_mixed_targets_at_the_service(client: TestClient, dba, targets) -> None:
    # The stand-in keeps one run of EXPORT_ORDERS and none of NIGHTLY_STATS.
    at_limit = call("h1", HISTORY_TOOL, owner="HARNESS_APP", job_name="EXPORT_ORDERS", row_limit=1)
    empty = call("h2", HISTORY_TOOL, owner="HARNESS_APP", job_name="NIGHTLY_STATS", row_limit=50)
    answer = (
        "## Unknown\n- Earlier runs of either job.\n\n## Next steps\n"
        "1. Pull more of the run history for HARNESS_APP.EXPORT_ORDERS."
    )
    use_script(client, lookups(at_limit, empty), ScriptedTurn(text=answer))
    events = sse(client, dba, payload(targets["development"]["id"]))
    record = last(events, "lookup_record")["lookups"]
    assert [entry["coverage"] for entry in record] == ["at_limit", "empty"]
    assert checks(events)[0]["findings"] == []

    flagged = answer.replace("EXPORT_ORDERS", "NIGHTLY_STATS")
    use_script(
        client, lookups(at_limit, empty), ScriptedTurn(text=flagged), ScriptedTurn(text=flagged)
    )
    events = sse(client, dba, payload(targets["development"]["id"]))
    (finding,) = checks(events)[0]["findings"]
    assert finding["kind"] == "next_check_cap"
    assert "job_name=NIGHTLY_STATS" in finding["problem"]


def test_a_citation_with_no_lookup_made_at_the_service(client: TestClient, dba, targets) -> None:
    claim = "Lookup 9 shows HARNESS_APP.EMPLOYEE_REPORT is VALID."
    use_script(client, ScriptedTurn(text=claim), ScriptedTurn(text=claim))
    events = sse(client, dba, payload(targets["development"]["id"]))
    (finding,) = checks(events)[0]["findings"]
    assert "this request made no lookups" in finding["problem"]


# -- the second-pass review's controls (docs/k7-c24-second-pass-review-2026-10-18.md) ----


def source(rows: list[tuple[int, str]], lookup: int = 1, kind: str = "PACKAGE BODY", **more):
    lines = [n for n, _ in rows]
    return LookupFacts(
        "schema.object_source_range",
        True,
        ["LINE", "TEXT"],
        [list(row) for row in rows],
        {"owner": "APP", "object_name": "PKG", "object_type": kind, "start_line": min(lines),
         "end_line": max(lines), **more},
        sequence=lookup,
    )  # fmt: skip


def with_context(rows: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """``rows`` with the unit's lines before them, from line 1, read too."""

    first = rows[0][0]
    return [(1, "PACKAGE BODY pkg AS"), *((n, "  -- earlier code") for n in range(2, first)),
            *rows]  # fmt: skip


def members(*lookups: LookupFacts) -> list[str]:
    return [m for entry in evidence_map(lookups).values()
            for read in record_entry(entry)["sourceRead"] for m in read["members"]]  # fmt: skip


# C24-A: a line reference is of a unit, and every line it gives was read in that unit.


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("## Evidence\n- Lines 10-13 of APP.PKG show the implementation.", []),
        ("## Evidence\n- Lines 10-99 of APP.PKG show the complete implementation.", ["e"]),
        ("## Evidence\n- Lines 10-13 of OTHER.UNREAD show the implementation.", ["e"]),
        ("## Evidence\n- Lines 11-12 of the APP.PKG body show the update.", []),
        ("## Evidence\n- Lines 11-12 of the APP.PKG spec declare it.", ["e"]),
        ("Lookup 1, lines 10-13: the update is unconditional.", []),
        ("Lookup 1, lines 9-13: the update is unconditional.", ["e"]),
        ("Lines 2495-2600 returned no rows (lookup 1).", []),
    ],
)
def test_lines_are_bound_to_the_unit_and_read_whole(line: str, expected: list[str]) -> None:
    found = kinds(line, PKG_MEMBER)
    assert found == ["evidence_reference"] * len(expected)


def test_lookups_of_one_unit_cover_a_range_together_and_another_unit_cannot() -> None:
    first = source([(10, "PROCEDURE a IS"), (11, "BEGIN")], lookup=1)
    second = source([(12, "  NULL;"), (13, "END a;")], lookup=2)
    split = "Lines 10-13 (lookups 1, 2) are all of `a`."
    assert kinds(split, first, second) == []
    gap = source([(13, "END a;")], lookup=2)
    assert kinds("Lines 10-13 (lookups 1, 2) are all of `a`.", first, gap) == ["evidence_reference"]
    other = LookupFacts(
        "schema.object_source_range",
        True,
        ["LINE", "TEXT"],
        [[12, "  NULL;"], [13, "END b;"]],
        {"owner": "APP", "object_name": "OTHER", "object_type": "PACKAGE BODY"},
        sequence=2,
    )
    (finding,) = check_claims("Lines 10-13 of APP.PKG (lookups 1, 2) are all of `a`.",
                              [first, other])  # fmt: skip
    assert "the cited lookups returned lines 10-11 of it" in finding.problem


def test_an_unnamed_line_reference_with_two_units_read_is_unresolved() -> None:
    body = source([(10, "PROCEDURE a IS"), (11, "BEGIN")], lookup=1)
    spec = source([(10, "PROCEDURE a;")], lookup=2, kind="PACKAGE")
    # Both units returned line 10: no need to say which.
    assert kinds("Line 10 (lookups 1, 2) names `a`.", body, spec) == []
    (finding,) = check_claims("Line 11 (lookups 1, 2) opens `a`.", [body, spec])
    assert "without saying which unit" in finding.problem
    assert kinds("Line 11 of the body (lookups 1, 2) opens `a`.", body, spec) == []


def test_a_members_boundaries_are_not_its_contents() -> None:
    # Third-pass review: a search returning only a member's first line and its END.
    search = LookupFacts(
        "schema.object_source_search",
        True,
        ["OWNER", "NAME", "TYPE", "LINE", "TEXT"],
        [["APP", "PKG", "PACKAGE BODY", 10, "PROCEDURE calc IS"],
         ["APP", "PKG", "PACKAGE BODY", 20, "END calc;"]],
        {"owner": "APP", "source_filter": "%CALC%"},
        sequence=1,
    )  # fmt: skip
    assert members(search) == [
        "CALC: implementation from line 10, coverage unknown (the lines from 10 were read "
        "without the lines before them, so whether it is code or inside a comment or "
        "literal opened earlier is not established)"
    ]
    content = "## Evidence\n- Lines 10-20 of APP.PKG show every statement in CALC (lookup 1)."
    (finding,) = check_claims(content, [search])
    assert "lines 10 and 20 came back but not all of those between" in finding.problem
    assert kinds("`calc` spans lines 10-20 (lookup 1).", search) == ["evidence_reference"]
    # Its ends, cited as the single lines they are, and a range read in full.
    assert kinds("`calc` opens at line 10 and `END calc;` is line 20 (lookup 1).", search) == []
    full = source([(10, "PROCEDURE calc IS"), (11, "BEGIN NULL;"), (12, "END calc;")])
    assert kinds("Lines 10-12 (lookup 1) are all of `calc`.", full) == []


# C24-B: comments and literals do not define a member or end one.


UNKNOWN_START = (
    "coverage unknown (the lines from 10 were read without the lines before them, so whether "
    "it is code or inside a comment or literal opened earlier is not established)"
)


def test_an_end_inside_a_block_comment_does_not_end_the_member() -> None:
    rows = [(10, "PROCEDURE calc IS"), (11, "BEGIN"), (12, "/*"), (13, "END calc;")]
    # The second-pass probe: lines 10-13 alone. Their start is not established.
    assert members(source(rows)) == [f"CALC: implementation from line 10, {UNKNOWN_START}"]
    # The same lines with the unit's lines before them: the comment is seen to stay open.
    assert members(source(with_context(rows))) == [
        "CALC: implementation from line 10, coverage unknown (the lines read end inside a comment)"
    ]
    (finding,) = check_claims("The fix belongs in APP.PKG.CALC.", [source(with_context(rows))])
    assert "the lines read end inside a comment" in finding.problem


CALC = [(10, "PROCEDURE calc IS"), (11, "BEGIN"), (12, "NULL;"), (13, "END calc;")]


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        # Third-pass review: these exact lines may be code or comment text.
        ([], [], [f"CALC: implementation from line 10, {UNKNOWN_START}"]),
        # Read from line 1, as code: whole.
        ("context", [], ["CALC: implementation from line 10, read whole"]),
        # Read from line 1, inside a comment opened at line 9 and closed at 14: no member.
        ("comment", [(14, "*/")], []),
        # Inside a literal opened at line 9: no member.
        ("literal", [(14, "';")], []),
    ],
)
def test_identical_lines_inside_and_outside_a_comment(before, after, expected) -> None:
    rows = CALC + after
    if before:
        rows = with_context(rows)
        rows[8] = (9, {"context": "  -- earlier code", "comment": "/*", "literal": "  x := '"}[
            before])  # fmt: skip
    assert members(source(rows)) == expected
    found = kinds("The fix belongs in APP.PKG.CALC.", source(rows))
    assert found == ([] if expected and expected[0].endswith("read whole") else ["change_location"])


def test_pl_scope_establishes_where_code_starts() -> None:
    plscope = LookupFacts(
        "schema.plscope_identifiers",
        True,
        ["OBJECT_TYPE", "PLSCOPE_STATUS", "NAME", "IDENTIFIER_TYPE", "USAGE", "LINE", "COL"],
        [["PACKAGE BODY", "COLLECTED", "CALC", "PROCEDURE", "DEFINITION", 10, 11]],
        {"owner": "APP", "object_name": "PKG"},
        sequence=2,
    )
    assert members(source(CALC), plscope) == ["CALC: implementation from line 10, read whole"]
    # A definition PL/Scope recorded at another line does not establish this one.
    other = LookupFacts(
        plscope.operation_id, True, plscope.columns,
        [["PACKAGE BODY", "COLLECTED", "CALC", "PROCEDURE", "DEFINITION", 30, 11]],
        plscope.parameters, sequence=2,
    )  # fmt: skip
    assert members(source(CALC), other) == [f"CALC: implementation from line 10, {UNKNOWN_START}"]


def test_lexical_state_does_not_carry_across_lines_not_read() -> None:
    search = LookupFacts(
        "schema.object_source_search",
        True,
        ["OWNER", "NAME", "TYPE", "LINE", "TEXT"],
        [["APP", "PKG", "PACKAGE BODY", n, text]
         for n, text in [(1, "PACKAGE BODY pkg AS"), (2, "  /* notes"), *CALC]],
        {"owner": "APP", "source_filter": "%CALC%"},
        sequence=1,
    )  # fmt: skip
    # Line 2 opens a comment; lines 3-9 were not read, so line 10's state is unknown,
    # neither code nor still inside that comment.
    assert members(search) == [f"CALC: implementation from line 10, {UNKNOWN_START}"]


def test_adjoining_reads_of_one_unit_are_read_together() -> None:
    top = source(with_context(CALC[:2]), lookup=1)
    rest = source(CALC[2:], lookup=2)
    assert members(top, rest) == ["CALC: implementation from line 10, read whole"]
    assert kinds("The fix belongs in APP.PKG.CALC.", top, rest) == []


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        # A complete member, and the same with its END only in a closed comment or a string.
        ([(10, "PROCEDURE calc IS"), (11, "BEGIN NULL;"), (12, "END calc;")], "read whole"),
        ([(10, "PROCEDURE calc IS"), (11, "BEGIN /* END calc; */ NULL;")], "read in part"),
        ([(10, "PROCEDURE calc IS"), (11, "BEGIN"), (12, "  x := 'END calc;';")], "read in part"),
        ([(10, "PROCEDURE calc IS"), (11, "BEGIN"), (12, "-- END calc;")], "read in part"),
        ([(10, "PROCEDURE calc IS"), (11, "BEGIN"), (12, "  x := q'[it's"), (13, "END calc;")],
         "coverage unknown (the lines read end inside a literal)"),
        ([(10, "PROCEDURE calc IS"), (11, "BEGIN"), (12, "  x := q'[]';"), (13, "END calc;")],
         "read whole"),
        # A header whose IS is not among the lines read.
        ([(10, "PROCEDURE calc(p NUMBER,")], "coverage unknown (its header does not end in the "
         "lines read)"),
    ],
)  # fmt: skip
def test_member_coverage_follows_the_code_not_comments_or_literals(rows, expected) -> None:
    (shown,) = members(source(with_context(rows)))
    assert shown.endswith(expected)


def test_a_definition_in_a_comment_or_string_is_not_a_member() -> None:
    rows = [(10, "/* PROCEDURE ghost IS */"), (11, "  x := 'PROCEDURE ghost IS';")]
    assert evidence_map([source(with_context(rows))])[1].source[0].members == ()


def test_nested_definitions_each_end_at_their_own_end() -> None:
    rows = [
        (10, "PROCEDURE outer IS"),
        (11, "  PROCEDURE inner IS"),
        (12, "  BEGIN NULL;"),
        (13, "  END inner;"),
        (14, "BEGIN"),
        (15, "  inner;"),
    ]
    assert members(source(with_context(rows))) == [
        "OUTER: implementation from line 10, read in part",
        "INNER: implementation from line 11, read whole",
    ]
    assert members(source(with_context([*rows, (16, "END outer;")])))[0].endswith("read whole")


# C24-C: a value is evidence about its field, and a citation is attached to its claim.

JOB = LookupFacts(
    "dba.scheduler_job_detail",
    True,
    ["OWNER", "JOB_NAME", "JOB_ACTION"],
    [["APP", "EXPORT", "OTHER.RUN_EXPORT"]],
    {"owner": "APP", "job_name": "EXPORT"},
    sequence=1,
)
ERRORS = LookupFacts(
    "schema.object_errors",
    True,
    ["LINE", "POSITION", "TEXT"],
    [[5, 9, "PLS-00201: identifier 'OTHER.HELPER' must be declared"]],
    {"owner": "APP", "object_name": "PKG", "object_type": "PACKAGE BODY"},
    sequence=1,
)
COMMENTED = source([(12, "  -- the old path called OTHER.LEGACY here"), (13, "  NULL;")])


@pytest.mark.parametrize(
    ("answer", "lookup", "flagged"),
    [
        ("Lookup 1 shows JOB_ACTION `OTHER.RUN_EXPORT`.", JOB, False),
        ("Lookup 1 shows the job's action is `OTHER.RUN_EXPORT`.", JOB, False),
        ("The action is `OTHER.RUN_EXPORT` (lookup 1).", JOB, False),
        ("`OTHER.RUN_EXPORT` (lookup 1) is not visible to this account.", JOB, False),
        ("Lookup 1 shows OTHER.RUN_EXPORT is VALID.", JOB, True),
        ("OTHER.RUN_EXPORT raised the error (lookup 1).", JOB, True),
        ("Lookup 1's error TEXT names `OTHER.HELPER` as undeclared.", ERRORS, False),
        ("Lookup 1 shows OTHER.HELPER is INVALID.", ERRORS, True),
        ("Line 12 (lookup 1) is a comment naming `OTHER.LEGACY`.", COMMENTED, False),
        ("Lookup 1 shows OTHER.LEGACY is called.", COMMENTED, True),
    ],
)
def test_a_value_supports_its_field_not_the_object_it_names(answer, lookup, flagged) -> None:
    assert kinds(answer, lookup) == (["evidence_reference"] if flagged else [])


def test_a_value_held_message_says_which_field_holds_it() -> None:
    (finding,) = check_claims("Lookup 1 shows OTHER.RUN_EXPORT is VALID.", [JOB])
    assert "only inside JOB_ACTION" in finding.problem


@pytest.mark.parametrize(
    ("answer", "flagged"),
    [
        # Leading: the citation opens its sentence or clause.
        ("As lookup 1 shows, OTHER.UNREAD is VALID.", True),
        ("According to lookup 1, OTHER.UNREAD is VALID.", True),
        ("As lookup 1 shows, APP.PKG is VALID.", False),
        # Trailing.
        ("OTHER.UNREAD is VALID (lookup 1).", True),
        ("APP.PKG is VALID, per lookup 1.", False),
        # Unresolved placement: both sides are checked.
        ("APP.PKG is VALID per lookup 1, and OTHER.UNREAD is too.", True),
        # Several citations each keep their own claim.
        ("Lookup 1 shows APP.PKG is VALID; lookup 2 shows APP.T has no rows.", False),
        ("Lookup 2 shows APP.T has rows; lookup 1 shows OTHER.UNREAD is VALID.", True),
    ],
)
def test_a_citation_is_attached_to_its_claim(answer: str, flagged: bool) -> None:
    table = LookupFacts(
        "schema.table_statistics",
        True,
        ["OWNER", "TABLE_NAME", "NUM_ROWS"],
        [["APP", "T", 0]],
        {"owner": "APP", "table_name": "T"},
        sequence=2,
    )
    assert kinds(answer, PKG_STATUS, table) == (["evidence_reference"] if flagged else [])


# -- C27: model-written numbers are bounded (docs/k7-c24-third-pass-review-2026-10-18.md) --


def bounded(answer: str, *lookups: LookupFacts) -> list[str]:
    """The findings, checked to take under a second and under 4 MiB at peak."""

    tracemalloc.start()
    started = time.monotonic()
    try:
        found = kinds(answer, *lookups)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert time.monotonic() - started < 1.0
    assert peak < 4 * 1024 * 1024
    return found


HUGE = [
    # The review's probe: a job lookup, no source rows at all.
    ("Lookup 1 shows OTHER.RUN_EXPORT is VALID at lines 1-100000000.", (JOB,)),
    ("Lines 1-100000000 (lookup 1) hold `OTHER.LEGACY`.", (COMMENTED,)),
    ("## Evidence\n- Lines 1-100000000 of APP.PKG are its body.", (COMMENTED,)),
    ("The fix belongs in `APP.PKG.CALC`, at line " + "9" * 5000 + ".", (COMMENTED,)),
    ("Line " + "9" * 5000 + " (lookup 1) names `OTHER.LEGACY`.", (COMMENTED,)),
    ("Lookups 1-" + "9" * 5000 + " show APP.PKG is VALID.", (PKG_STATUS,)),
    ("## Evidence\n" + "".join(f"- Lines 1-99999999{n} (lookup 1) differ.\n" for n in range(200)),
     (COMMENTED,)),
]  # fmt: skip


@pytest.mark.parametrize(("answer", "lookups"), HUGE)
def test_huge_line_ranges_are_checked_without_enumerating_them(answer, lookups) -> None:
    assert bounded(answer, *lookups)  # each is flagged, and none is expanded


def test_a_number_past_the_bound_is_beyond_every_line() -> None:
    (finding,) = check_claims("Line " + "9" * 5000 + " (lookup 1) is blank.", [COMMENTED])
    assert "line 1000000000 of APP.PKG PACKAGE BODY" in finding.problem


@pytest.mark.parametrize(
    "answer",
    [
        "Line 12 (lookup 1) is a comment naming `OTHER.LEGACY`.",
        "Lines 12-13 (lookup 1) are a comment naming `OTHER.LEGACY` and a NULL.",
    ],
)
def test_bounded_parsing_keeps_valid_line_quotations(answer: str) -> None:
    assert bounded(answer, COMMENTED) == []


# -- the second-pass controls at the service boundary ---------------------------------------

JOB_TOOL = tool_name_for("dba.scheduler_job_detail")


def spec_source() -> ToolCall:
    return call(
        "p1", SOURCE_TOOL, owner="HARNESS_APP", object_name="EMPLOYEE_REPORT", object_type="PACKAGE"
    )


def body_ranges(*spans: tuple[int, int]) -> list[ToolCall]:
    return [
        call(f"r{index}", RANGE_TOOL, owner="HARNESS_APP", object_name="EMPLOYEE_REPORT",
             object_type="PACKAGE BODY", start_line=start, end_line=end, row_limit=50)
        for index, (start, end) in enumerate(spans, start=1)
    ]  # fmt: skip


def evidence_findings(client, dba, targets, calls: list[ToolCall], answer: str) -> list[dict]:
    # A flagged answer gets one revision; the model keeps it, so the first check stands.
    use_script(client, lookups(*calls), ScriptedTurn(text=answer), ScriptedTurn(text=answer))
    events = sse(client, dba, payload(targets["development"]["id"]))
    return [f for f in checks(events)[0]["findings"] if f["kind"] == "evidence_reference"]


@pytest.mark.parametrize(
    ("spans", "answer", "flagged"),
    [
        ([(2, 7)], "Lines 2-7 (lookup 1) are all of `headcount`.", False),  # exact
        ([(2, 4), (5, 7)], "Lines 2-7 (lookups 1, 2) are all of `headcount`.", False),  # split
        ([(2, 3), (6, 7)], "Lines 2-7 (lookups 1, 2) are all of `headcount`.", True),  # gap
        ([(2, 7)], "Lines 2-9 (lookup 1) are all of `headcount`.", True),  # past the read
        ([(2, 7)], "Lines 2-7 of HARNESS_APP.REPORT_UTIL (lookup 1) are its code.", True),
    ],
)
def test_line_references_at_the_service(client: TestClient, dba, targets, spans, answer, flagged):
    found = evidence_findings(client, dba, targets, body_ranges(*spans), answer)
    assert bool(found) is flagged


def test_the_spec_lines_are_not_the_body_lines(client: TestClient, dba, targets) -> None:
    calls = [spec_source(), *body_ranges((1, 1))]
    spec = "Lines 2-4 of the specification (lookups 1, 2) declare three members."
    assert evidence_findings(client, dba, targets, calls, spec) == []
    body = spec.replace("specification", "body")
    (finding,) = evidence_findings(client, dba, targets, calls, body)
    assert "HARNESS_APP.EMPLOYEE_REPORT PACKAGE BODY" in finding["problem"]


def test_a_commented_out_end_is_shown_as_unknown_coverage(
    client: TestClient, dba, targets, workspace: Path
) -> None:
    sse(client, dba, payload(targets["development"]["id"]))  # seeds the stand-in database
    for database in (workspace / "fake").glob("*.sqlite3"):
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE all_source SET text = '  /* l_count NUMBER;' WHERE name ="
                " 'EMPLOYEE_REPORT' AND type = 'PACKAGE BODY' AND line = 3"
            )
    claim = "The fix belongs in `HARNESS_APP.EMPLOYEE_REPORT.headcount`."
    use_script(client, lookups(*body_ranges((1, 7))), ScriptedTurn(text=claim),
               ScriptedTurn(text=claim))  # fmt: skip
    events = sse(client, dba, payload(targets["development"]["id"]))
    (read,) = last(events, "lookup_record")["lookups"][0]["sourceRead"]
    assert read["members"] == [
        "HEADCOUNT: implementation from line 2, coverage unknown (the lines read end inside "
        "a comment)"
    ]
    (finding,) = checks(events)[0]["findings"]
    assert finding["kind"] == "change_location"
    assert "the lines read end inside a comment" in finding["problem"]


def test_a_job_action_value_at_the_service(client: TestClient, dba, targets) -> None:
    job = call("j1", JOB_TOOL, owner="HARNESS_APP", job_name="EXPORT_ORDERS")
    quoted = "Lookup 1 shows JOB_ACTION `ORDER_FEED.EXPORT_DAY`."
    assert evidence_findings(client, dba, targets, [job], quoted) == []
    status = "As lookup 1 shows, ORDER_FEED.EXPORT_DAY is VALID."
    (finding,) = evidence_findings(client, dba, targets, [job], status)
    assert "only inside JOB_ACTION" in finding["problem"]
