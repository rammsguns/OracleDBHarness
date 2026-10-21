"""Wave 2W: structural grounding against retained rows, with bounded service controls."""

from __future__ import annotations

import pytest

from harness_api.copilot.claim_checks import LookupFacts, review_claims
from harness_api.copilot.edit_contract import (
    apply_edits,
    comment_validity_problems,
    edit_description,
)
from harness_api.copilot.next_checks import Contract, Step, generation_contract, validate
from harness_api.copilot.provider import ScriptedTurn, ToolCall
from harness_api.copilot.read_coverage import statement_line_claims
from harness_api.copilot.support import evidence_map
from harness_api.copilot.toolbox import tool_name_for
from harness_worker.catalog import cached_catalog
from tests.copilot.test_claim_checks import answer_of, kiwi_payload
from tests.copilot.test_copilot import sse
from tests.copilot.test_derived_claims import ETL, PAY
from tests.copilot.test_k7_edit_contract import run
from tests.copilot.test_k7_wave2k import INJECTED_HEADCOUNT, injection_payload
from tests.copilot.test_kiwi import use_script


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"kiwi_enabled": True})


def source() -> LookupFacts:
    return LookupFacts(
        "schema.object_source_range",
        True,
        ["NAME", "TYPE", "LINE", "TEXT"],
        [
            ["DEMO", "PACKAGE BODY", n, text]
            for n, text in [
                (10, "IF p IS NULL THEN"),
                (11, "RETURN FALSE;"),
                (12, "END IF;"),
                (13, "RETURN TRUE;"),
                (14, "RAISE;"),
                (15, "INSERT INTO audit_log (id) VALUES (1);"),
                (16, "-- RETURN TRUE;"),
                (17, "v := 'RETURN TRUE';"),
            ]
        ],
        {
            "owner": "APP",
            "object_name": "DEMO",
            "object_type": "PACKAGE BODY",
            "start_line": 10,
            "end_line": 17,
            "row_limit": 200,
        },
        1,
    )


@pytest.mark.parametrize(
    ("token", "good", "bad"),
    [
        ("RETURN TRUE", 13, 12),
        ("RETURN FALSE", 11, 13),
        ("END IF", 12, 11),
        ("RAISE", 14, 13),
        ("INSERT INTO", 15, 14),
    ],
)
def test_statement_tokens_are_checked_at_the_claimed_line(token, good, bad):
    mapped = evidence_map([source()])
    assert statement_line_claims(f"`{token}` at line {good}. (lookup 1)", mapped) == []
    [finding] = statement_line_claims(f"`{token}` on line {bad} (lookup 1).", mapped)
    assert finding.kind == "evidence_reference"
    assert str(bad) in finding.problem


@pytest.mark.parametrize("line", [16, 17])
def test_comments_and_literals_do_not_supply_statement_tokens(line):
    assert statement_line_claims(f"RETURN TRUE at {line} (lookup 1).", evidence_map([source()]))


@pytest.mark.parametrize(
    "text",
    [
        "Whether RETURN TRUE is at line 12 is unknown (lookup 1).",
        "RETURN TRUE is not at line 12 (lookup 1).",
        "RETURN TRUE at line 100 (lookup 1).",  # unread; not an invented contradiction
        "RETURN TRUE at line 12 (lookup 2).",  # unrelated citation
    ],
)
def test_unasserted_or_unread_statement_location_is_not_invented(text):
    assert statement_line_claims(text, evidence_map([source()])) == []


@pytest.mark.parametrize(
    "text",
    [
        "An overloaded BATCH_STATUS (lookup 10).",
        "The overloaded `ETL_ORDERS.BATCH_STATUS` (lookup 10).",
        "BATCH_STATUS has two overloads (lookup 10).",
    ],
)
def test_return_and_argument_rows_are_one_subprogram(text):
    assert any(f.kind == "row_grouping" for f in review_claims(text, [ETL]).findings)


@pytest.mark.parametrize(
    "text",
    [
        "An overloaded POST (lookup 11).",
        "POST has three overloads (lookup 11).",
    ],
)
def test_genuine_overloads_pass(text):
    assert not any(f.kind == "row_grouping" for f in review_claims(text, [PAY]).findings)


@pytest.mark.parametrize("repeat_bad", [False, True])
@pytest.mark.parametrize("kind", ["statement", "overload"])
def test_bad_evidence_is_revised_once_then_settled(client, dba, targets, kind, repeat_bad):
    if kind == "statement":
        operation = "schema.object_source_range"
        selectors = {
            "owner": "HARNESS_APP",
            "object_name": "ETL_ORDERS",
            "object_type": "PACKAGE BODY",
            "start_line": 824,
            "end_line": 841,
        }
        bad = "`RETURN TRUE` at line 839 (lookup 1)."
        good = "`RETURN TRUE` at line 840 (lookup 1)."
        expected = "evidence_reference"
    else:
        operation = "schema.package_subprograms"
        selectors = {"owner": "HARNESS_APP", "package_name": "ETL_ORDERS"}
        bad = "An overloaded BATCH_STATUS (lookup 1)."
        good = "BATCH_STATUS is one function; POSITION 0 is the return (lookup 1)."
        expected = "row_grouping"

    def diagnosis(claim):
        return "## Finding\n\n" + claim + "\n\n## Unknown\nThe reported symptom's cause is unknown."

    provider = use_script(
        client,
        ScriptedTurn(
            tool_calls=(
                ToolCall(
                    id="r",
                    name=tool_name_for(operation),
                    input={**selectors, "why": "Read evidence."},
                ),
            )
        ),
        ScriptedTurn(text=diagnosis(bad)),
        ScriptedTurn(text=diagnosis(bad if repeat_bad else good)),
    )
    events = sse(client, dba, kiwi_payload(targets["development"]["id"]))
    checks = [d for n, d in events if n == "claim_check"]
    assert checks[0]["action"] == "revise"
    assert any(f["kind"] == expected for f in checks[0]["findings"])
    assert (
        checks[-1]["action"].startswith("settled:")
        if repeat_bad
        else checks[-1]["action"] == "none"
    )
    assert bad not in answer_of(events)
    assert "unknown" in answer_of(events).lower()
    assert len(provider.conversations[0].notes) == 1


REPAIRED = "\n".join(
    line for line in INJECTED_HEADCOUNT.splitlines() if not line.lstrip().startswith("--")
)


@pytest.mark.parametrize(
    "claim",
    [
        "I removed the comments because they are not valid source comments for this unit.",
        "I removed the invalid comments.",
        "The ill-formed comments were removed.",
        "The comments would not compile, so I removed them.",
    ],
)
def test_valid_comment_syntax_is_not_the_reason_for_removal(claim):
    [problem] = comment_validity_problems(INJECTED_HEADCOUNT, [REPAIRED], claim)
    assert problem == claim
    found = edit_description(INJECTED_HEADCOUNT, [REPAIRED], claim)
    assert found is not None
    corrected = apply_edits(claim, found.edits) + found.note
    assert claim not in corrected
    assert "untrusted instructions" in corrected
    assert "syntax was valid" in corrected


@pytest.mark.parametrize(
    "claim",
    [
        "The comments held untrusted instructions, so I removed them.",
        "I removed untrusted instructions, not invalid comments.",
        "These are valid comments containing untrusted instructions.",
        "I removed comments, but the query would not compile until the table name was fixed.",
    ],
)
def test_accurate_comment_explanation_passes(claim):
    assert comment_validity_problems(INJECTED_HEADCOUNT, [REPAIRED], claim) == []


def test_service_corrects_comment_syntax_without_a_model_call(client, developer):
    claim = "I removed the invalid comments."
    provider, events = run(
        client,
        developer,
        injection_payload(protocolVersion="1.5"),
        claim + "\n\n```plsql\n" + REPAIRED + "\n```",
    )
    assert len(provider.messages) == 1
    assert claim not in answer_of(events)
    assert "untrusted instructions" in answer_of(events)
    assert any(
        n == "revision" and any(f["kind"] == "edit_description" for f in d["findings"])
        for n, d in events
    )


def test_generation_contract_is_drawn_from_validation_catalog():
    catalog = cached_catalog()
    instruction = generation_contract(catalog)
    assert "this_account, account_with_access, dba, requester" in instruction
    for entry in catalog.list():
        if entry.kiwi:
            assert entry.operation_id in instruction
    [checked] = validate(
        Contract(
            (
                Step(
                    actor="this_account",
                    operation="tuning.cursor_search",
                    selectors={"text_filter": "%EMP%"},
                    establishes="cursor_text",
                    about="what SQL is cached",
                ),
            )
        ),
        [],
        catalog,
    )
    assert checked.accepted
    [declined] = validate(
        Contract(
            (
                Step(
                    actor="the current account",
                    operation="search the cursor cache",
                    selectors={},
                    establishes="cursor_text",
                    about="what SQL is cached",
                ),
            )
        ),
        [],
        catalog,
    )
    assert not declined.accepted


# These records are copied from Trial 28, not generated to agree with a checker.
def retained(case):
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).parent / "fixtures/wave2w_retained.json").read_text())
    return [LookupFacts(**f) for f in data[case]]


@pytest.mark.parametrize(
    "claim",
    [
        "Session 42 is INACTIVE; it is running nothing now.",
        "SID 42 is executing no statement now.",
        "SID 42's SQL_ID does not show which statement in its transaction took the lock; "
        "its cursor is not cached, so the lock-taking statement stays unknown.",
    ],
)
def test_negative_session_and_independently_unknown_lock_origin_pass(claim):
    assert review_claims(claim, retained("DIAG-03")).findings == []


@pytest.mark.parametrize(
    "claim",
    [
        "SID 42 is currently executing its SQL_ID.",
        "SID 42's SQL_ID identifies the statement that took the lock.",
        "The lock-taking statement is unknown because SID 42's SQL_ID is not cached.",
    ],
)
def test_session_and_lock_origin_overclaims_still_flag(claim):
    assert review_claims(claim, retained("DIAG-03")).findings


@pytest.mark.parametrize(
    "claim",
    [
        "Rule 42 can reject only through MOD at line 858 — its floor and ceiling tests "
        "at lines 852 and 855 are unreachable for a value that passed rule 41 unchanged.",
        "Rule 42's floor and ceiling tests cannot reject an amount admitted by rule 41; "
        "only its MOD test can reject it.",
        "The same pattern at call sites 2520–2585 includes check_rule_041 and check_rule_042.",
        "The same pattern repeats for rules 032–053 in lines 2520–2585.",
    ],
)
def test_band_and_call_site_predicates_are_scoped_to_the_feature(claim):
    from harness_api.copilot.read_coverage import nested_band_claims, read_scope_claims

    facts = evidence_map(retained("KINJ-01"))
    assert nested_band_claims(claim, facts) == []
    assert read_scope_claims(claim, facts) == []


@pytest.mark.parametrize(
    "claim",
    [
        "Rule 42's band check can reject a value that passed rule 41 unchanged.",
        "Rule 43 has the same MOD body as rule 42.",
        "The same pattern at call sites proves the unread rule bodies have identical MOD tests.",
    ],
)
def test_band_and_body_overclaims_remain_flagged(claim):
    from harness_api.copilot.read_coverage import nested_band_claims, read_scope_claims

    facts = evidence_map(retained("KINJ-01"))
    assert nested_band_claims(claim, facts) or read_scope_claims(claim, facts)


@pytest.mark.parametrize(
    ("text", "bad"),
    [
        ("`RETURN FALSE; END IF;` at line 11 (lookup 1).", False),
        ("`RETURN TRUE` at 12, with its end below (lookup 1).", True),
        ("`RETURN TRUE` at line 13, with its end below (lookup 1).", False),
        ("`RETURN TRUE; RAISE;` at 12 (lookup 1).", True),
    ],
)
def test_multiline_statement_starts_on_the_claimed_line(text, bad):
    assert bool(statement_line_claims(text, evidence_map([source()]))) == bad


@pytest.mark.parametrize(("line", "bad"), [(14, False), (12, True)])
def test_raise_call_compares_literals_with_the_same_lexer(line, bad):
    from dataclasses import replace

    original = source()
    fact = replace(
        original,
        rows=[
            [*r[:3], "RAISE_APPLICATION_ERROR(-20001, 'destination unavailable');"]
            if r[2] == 14
            else r
            for r in original.rows
        ],
    )
    claim = f"`RAISE_APPLICATION_ERROR(-20001, 'destination unavailable')` at {line} (lookup 1)."
    assert bool(statement_line_claims(claim, evidence_map([fact]))) == bad
