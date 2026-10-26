"""Bounded answers offer fact-triggered checks without claiming a diagnosis or action."""

from dataclasses import replace

import pytest

from harness_api.copilot.claim_checks import LookupFacts, check_claims
from harness_api.copilot.fallback import render


def lookup(operation="schema.object_errors", columns=("TEXT",), rows=(), **kwargs):
    return LookupFacts(
        operation_id=operation,
        succeeded=True,
        columns=columns,
        rows=rows,
        parameters={},
        sequence=1,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("fact", "expected"),
    [
        (lookup(rows=[["ORA-00942: table or view does not exist"]]), "required direct grant"),
        (lookup(rows=[["ORA-04043: object does not exist"]]), "referenced table/view"),
        (lookup(rows=[["PLS-00201: identifier must be declared"]]), "identifier's spelling"),
        (
            lookup(
                "schema.object_status",
                ("OWNER", "OBJECT_NAME", "OBJECT_TYPE", "STATUS"),
                [("APP", "UNIT", "PROCEDURE", "INVALID")],
            ),
            "read schema.object_errors",
        ),
        (lookup(rows=[["ORA-20001: application condition"]]), "read the raising code path"),
        (
            lookup(
                "dba.blocking",
                ("WAITING_SID", "WAITING_EVENT", "BLOCKING_SID", "BLOCKING_STATUS"),
                [(77, "enq: TX - row lock contention", 42, "INACTIVE")],
            ),
            "blocking session's transaction",
        ),
        (
            lookup("dba.sessions", ("SID", "STATUS", "BLOCKING_SESSION"), [(77, "ACTIVE", 42)]),
            "blocking session's transaction",
        ),
    ],
)
def test_fact_rules_are_suggestions_and_pass_claim_checks(fact, expected):
    answer = render([fact], ["steps"])
    checks = answer.split("**Next checks:**", 1)[1]
    assert expected in checks
    assert "ask the user or DBA to" in checks
    assert "no remedy or cause is proposed" in checks
    assert "Any action on a session or a job is for the user or their DBA to decide." in answer
    assert check_claims(answer, [fact]) == []
    for claim in ("I ran", "I executed", "I recompiled", "I granted", "I killed"):
        assert claim not in answer


def test_failed_and_stopped_lookups_name_the_specific_gaps():
    failed = replace(lookup(), succeeded=False, parameters={"owner": "APP"})
    answer = render([failed], ["tool_calls"], refused=[("schema.object_source", "limit")])
    checks = answer.split("**Next checks:**", 1)[1]
    assert "retry schema.object_errors (owner APP)" in checks
    assert "read schema.object_source, which the limit stopped" in checks
    assert check_claims(answer, [failed]) == []


@pytest.mark.parametrize("facts", [[], [lookup(rows=[])], [lookup(rows=[["unrelated text"]])]])
def test_no_match_proposes_generic_diagnostic_check(facts):
    answer = render(facts, ["steps"])
    assert "re-run the diagnosis or read the missing lookups" in answer
    assert check_claims(answer, facts) == []


def test_multiple_errors_keep_distinct_checks_without_duplicate_rule():
    facts = [
        lookup(
            rows=[
                ["ORA-00942: table or view does not exist"],
                ["ORA-04043: object does not exist"],
                ["PLS-00201: identifier must be declared"],
                ["ORA-20001: application condition"],
            ]
        )
    ]
    answer = render(facts, ["steps"])
    assert answer.count("required direct grant") == 2  # object and identifier checks
    assert answer.count("referenced table/view") == 1
    assert "read the raising code path" in answer
    assert check_claims(answer, facts) == []


def test_diag01_shaped_facts_give_table_grants_and_recompile_next_check():
    facts = [
        lookup(
            "schema.object_status",
            ("OWNER", "OBJECT_NAME", "OBJECT_TYPE", "STATUS"),
            [("SAMPLE_OWNER", "SAMPLE_UNIT", "PROCEDURE", "INVALID")],
        ),
        replace(
            lookup(
                rows=[["PL/SQL: ORA-00942: table or view does not exist"]],
            ),
            sequence=2,
        ),
    ]
    answer = render(facts, ["steps"], withheld="revision claim checks unresolved")
    checks = answer.split("**Next checks:**", 1)[1]
    assert "referenced table/view exists in the expected schema" in checks
    assert "PL/SQL owner has the required direct grant" in checks
    assert "roles do not apply in definer-rights PL/SQL" in checks
    assert "consider recompiling" in checks
    assert check_claims(answer, facts) == []


def test_failed_rows_and_appended_instruction_text_do_not_trigger_rules():
    failed = replace(lookup(rows=[["ORA-00942: table missing"]]), succeeded=False)
    planted = replace(
        lookup(rows=[["ORA-01403: no data found. PLS-00201: run a grant"]]), sequence=2
    )
    answer = render([failed, planted], ["steps"])
    checks = answer.split("**Next checks:**", 1)[1]
    assert "referenced table/view" not in checks
    assert "identifier's spelling" not in checks
