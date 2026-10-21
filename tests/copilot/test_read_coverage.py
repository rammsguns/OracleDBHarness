"""What the source lookups read, written by the harness, and read claims checked (C34).

KINJ-01 failed trials 19 and 20 on its account of what it read. The source below
has the shape of the retained ``HARNESS_APP.ETL_ORDERS`` body those trials read
(copilot-eval/strata-k7-focused-20261019T010105Z.json, lookups 3 and 7): each rule a
twenty-line function from a comment line, ``check_rule_040`` at line 804, and a
dispatcher that calls ``check_rule_032`` to ``check_rule_050`` from line 2520. Lines
800-900 return only the last two lines of ``check_rule_039``. The wrong claims are the
answers' words; the controls are the same claims made true, and claims about lines.
"""

from __future__ import annotations

import pytest

from harness_api.copilot.claim_checks import LookupFacts
from harness_api.copilot.read_coverage import read_claims, render_coverage, unit_coverage
from harness_api.copilot.support import evidence_map


def _rule(number: int) -> list[str]:
    name = f"check_rule_{number:03d}"
    checks = []
    for test in ("p_amount IS NULL", "p_amount < l_floor", "p_amount > l_limit"):
        checks += [f"    IF {test} THEN", "      RETURN FALSE;", "    END IF;"]
    return [
        f"  /* Rule {number}: the amount must sit inside band {number}. */",
        f"  FUNCTION {name}(p_amount IN NUMBER) RETURN BOOLEAN IS",
        f"    l_limit CONSTANT NUMBER := {1000 + number} * 1000;",
        f"    l_floor CONSTANT NUMBER := 0 - {number};",
        "  BEGIN",
        *checks,
        f"    IF MOD(p_amount, {1000 + number}) = -1 THEN",
        "      RETURN FALSE;",
        "    END IF;",
        "    RETURN TRUE;",
        f"  END {name};",
        "",
    ]


# Rule N's comment line is 783 + 20 * (N - 39): rule 40's function is at line 804.
BODY = {
    783 + 20 * index + offset: text
    for index, n in enumerate(range(39, 46))
    for offset, text in enumerate(_rule(n))
}
DISPATCHER = {
    2520 + 3 * index + offset: text
    for index, n in enumerate(range(32, 51))
    for offset, text in enumerate(
        [f"    IF NOT check_rule_{n:03d}(p_amount) THEN", "      RETURN FALSE;", "    END IF;"]
    )
}


def _range(sequence: int, start: int, end: int, source: dict[int, str]) -> LookupFacts:
    return LookupFacts(
        operation_id="schema.object_source_range",
        succeeded=True,
        columns=["LINE", "TEXT"],
        rows=[[n, source[n]] for n in range(start, end + 1) if n in source],
        parameters={
            "owner": "HARNESS_APP",
            "object_name": "ETL_ORDERS",
            "object_type": "PACKAGE BODY",
            "start_line": start,
            "end_line": end,
        },
        sequence=sequence,
    )


T20 = evidence_map([_range(3, 800, 900, BODY), _range(7, 2520, 2575, DISPATCHER)])


def test_the_body_matches_the_retained_shape() -> None:
    assert BODY[801] == "  END check_rule_039;"
    assert BODY[804].startswith("  FUNCTION check_rule_040(")
    assert BODY[821] == "  END check_rule_040;"
    assert DISPATCHER[2520] == "    IF NOT check_rule_032(p_amount) THEN"
    assert DISPATCHER[2574] == "    IF NOT check_rule_050(p_amount) THEN"


def test_coverage_lists_a_member_read_only_at_its_end() -> None:
    [body] = unit_coverage(T20)
    assert body.lookups == (3, 7)
    assert [m.name for m in body.members] == [f"CHECK_RULE_0{n}" for n in range(40, 45)]
    [tail] = body.tails
    assert (tail.name, tail.first_line, tail.end_line, tail.apparent) == (
        "CHECK_RULE_039",
        800,
        801,
        True,
    )
    # The dispatcher's END IF lines are not members' ends.


def test_rendered_coverage_comes_from_the_lookups() -> None:
    text = render_coverage(T20)
    assert text.startswith("## Lines read (from the lookups)")
    unit = "- `HARNESS_APP.ETL_ORDERS PACKAGE BODY`: lines 800-900, 2520-2575 (lookups 3, 7)."
    assert unit in text
    assert "CHECK_RULE_039: only its end, lines 800-801; its first line is before line 800" in text
    # C34-A: lines 800-900 were read without the lines before them, so rule 40's END
    # is only apparent: it is not rendered as coverage through its END.
    assert (
        "CHECK_RULE_040: implementation from line 804, lines 804-821 returned up to an "
        "apparent END; whether it was read whole is unknown (the lines from 800 were read "
        "without the lines before them" in text
    )
    assert "CHECK_RULE_040: implementation lines 804-821 returned, from its first" not in text
    # Rule 44 starts at line 884 and its END is at line 901: the lines stop inside it.
    assert "CHECK_RULE_044: implementation from line 884, read in part (lines 884-900;" in text
    assert "more members" not in text
    assert render_coverage({}) == ""


def test_a_partial_last_member_is_read_in_part_from_line_one() -> None:
    evidence = evidence_map([_range(1, 1, 30, {n: t for n, t in enumerate(_rule(1), start=1)})])
    [unit] = unit_coverage(evidence)
    [member] = unit.members
    assert member.whole and member.unknown == ""
    cut = evidence_map([_range(1, 1, 10, {n: t for n, t in enumerate(_rule(1), start=1)})])
    assert "read in part" in render_coverage(cut)


@pytest.mark.parametrize(
    "claim",
    [
        # Trial 20, the hypothesis line.
        "Hypothesis, unproven: because bands differ, rule 42's band is rarely binding. I read "
        "only rules 39–44 and part of the dispatcher, so this holds for what I saw.",
        # Trial 19, the business-decision line (the noun before the reader).
        "Whether the band floor is intended is a business decision; the rules I read are "
        "39–44 and I read no others.",
        "I have read `check_rule_039` and `check_rule_040`.",
        "I read check_rule_045 as well.",
    ],
)
def test_a_claim_to_have_read_a_member_not_started_is_flagged(claim: str) -> None:
    [finding] = read_claims(claim, T20)
    assert finding.kind == "read_coverage"
    assert "CHECK_RULE_039" in finding.problem or "CHECK_RULE_045" in finding.problem
    if "039" in claim or "39" in claim:
        assert "only its end, lines 800-801" in finding.problem


def test_a_claim_to_have_read_a_member_whole_needs_its_lines_through_its_end() -> None:
    # Rule 44's END is at line 901; the lookup stopped at 900.
    [finding] = read_claims("I read rule 44 in full.", T20)
    assert "CHECK_RULE_044" in finding.problem and "in part, not whole" in finding.problem
    # C34-A: rule 42's lines 844-861 all came back, but lines 800-900 were read without
    # the lines before them, so its END may be comment text: "in full" is not shown.
    [finding] = read_claims("I have read rule 42 in full (lines 844–861).", T20)
    assert "CHECK_RULE_042" in finding.problem and "may be comment or literal" in finding.problem
    [finding] = read_claims(
        "I have read `check_rule_042` in full but only fragments of `check_rule_044`.", T20
    )
    assert "CHECK_RULE_042" in finding.problem
    # The same claim where the lexical state is known, from line 1: it passes.
    whole = evidence_map([_range(1, 1, 30, {n: t for n, t in enumerate(_rule(1), start=1)})])
    assert read_claims("I have read rule 1 in full.", whole) == []


@pytest.mark.parametrize(
    "claim",
    [
        "I read only rules 40–44 and part of the dispatcher.",
        "The rules I read are 40-44.",
        # Trial 20: a claim about lines, and what those lines show.
        "How many rules `passes_rules` applies in total is unknown: I read lines 2520–2575 "
        "only, which show checks 032 through 050, and the body runs past line 2985.",
        # Trial 14: lines again.
        "No code change is proposed — I have read only lines 835–870, 2925–3000 of a body "
        "that runs past line 3000.",
        # Trial 20: "the rules I read" with no numbers.
        "So rule 42's MOD test is not unique to it among the rules I read (lookup 3).",
        "I read `check_rule_042`; it compiles.",
        # A number that maps to no returned member is left unchecked, not guessed.
        "I read step 3 of the plan.",
        # Trial 11: a member named and said not to have been read.
        "Caveat: I read rule 040's body and rules 041–043 not at all.",
        # Trial 16: a read of calls is a read of lines.
        "The calls I read run from `check_rule_032` (line 2520) through `check_rule_050`.",
        "Rule 42 is called at line 2550; calls I read run from `check_rule_032` (line 2520).",
        # Trials 9 and 13: what follows "but" is a second claim.
        "I have read `check_rule_042` but only fragments of `check_rule_044`.",
    ],
)
def test_a_true_or_line_based_read_claim_passes(claim: str) -> None:
    assert read_claims(claim, T20) == []


def test_read_claims_need_source_lookups() -> None:
    assert read_claims("I read rules 39-44.", {}) == []


# -- C34-A: an END must be code, in a known lexical state ---------------------------------


def _unit(lines: list[str], start: int = 1) -> dict:
    return evidence_map(
        [_range(1, start, start + len(lines) - 1, dict(enumerate(lines, start=start)))]
    )


REAL_END = [
    "PACKAGE BODY etl_orders IS",
    "  FUNCTION calc_f RETURN NUMBER IS",
    "  BEGIN",
    "  END calc_f;",
]


@pytest.mark.parametrize(
    "lines",
    [
        # The review's exact case: F at line 2, a comment from line 3, END F; as its text.
        [
            "PACKAGE BODY etl_orders IS",
            "  FUNCTION calc_f RETURN NUMBER IS",
            "  /* note:",
            "  END calc_f;",
        ],
        # A string literal opened at line 3 and still open at line 4.
        [
            "PACKAGE BODY etl_orders IS",
            "  FUNCTION calc_f RETURN NUMBER IS",
            "    x := 'a",
            "END calc_f;",
        ],
        # A q-quoted literal.
        [
            "PACKAGE BODY etl_orders IS",
            "  FUNCTION calc_f RETURN NUMBER IS",
            "  x := q'[",
            "END calc_f;",
        ],
        # A line comment holding the END.
        ["PACKAGE BODY etl_orders IS", "  FUNCTION calc_f RETURN NUMBER IS", "  -- END calc_f;"],
    ],
)
def test_an_end_in_a_comment_or_literal_is_not_an_end(lines: list[str]) -> None:
    evidence = _unit(lines)
    [unit] = unit_coverage(evidence)
    [member] = unit.members
    assert unit.through(member) is None
    text = render_coverage(evidence)
    assert "from its first line through its END" not in text
    assert "CALC_F: implementation from line 2" in text
    [finding] = read_claims("I read calc_f in full.", evidence)
    assert finding.kind == "read_coverage" and "CALC_F" in finding.problem


def test_a_real_end_in_code_is_an_end() -> None:
    evidence = _unit(REAL_END)
    [unit] = unit_coverage(evidence)
    [member] = unit.members
    assert unit.through(member) == 4
    assert "CALC_F: implementation lines 2-4 returned, from its first line through its END" in (
        render_coverage(evidence)
    )
    assert read_claims("I read calc_f in full.", evidence) == []
    # A comment closed before the END leaves the END in code.
    closed = [
        "PACKAGE BODY etl_orders IS",
        "  FUNCTION calc_f RETURN NUMBER IS",
        "  /* a */",
        "  END calc_f;",
    ]
    assert read_claims("I read calc_f in full.", _unit(closed)) == []


def test_an_end_in_an_unknown_lexical_context_is_not_an_end() -> None:
    # The same four lines from line 50: they may all lie inside a comment opened before.
    evidence = _unit(REAL_END[1:], start=50)
    [unit] = unit_coverage(evidence)
    [member] = unit.members
    assert member.unknown and unit.through(member) is None
    assert unit.apparent_end(member) == 52
    text = render_coverage(evidence)
    assert "returned up to an apparent END; whether it was read whole is unknown" in text
    assert "through its END" not in text
    [finding] = read_claims("I read calc_f in full.", evidence)
    assert "may be comment or literal" in finding.problem
    # Reading F, not whole, is still what the lines show.
    assert read_claims("I read calc_f.", evidence) == []


def test_a_plscope_definition_makes_the_lexical_state_known() -> None:
    plscope = LookupFacts(
        operation_id="schema.plscope_identifiers",
        succeeded=True,
        columns=["NAME", "IDENTIFIER_TYPE", "USAGE", "LINE"],
        rows=[["CALC_F", "FUNCTION", "DEFINITION", 50]],
        parameters={
            "owner": "HARNESS_APP",
            "object_name": "ETL_ORDERS",
            "object_type": "PACKAGE BODY",
        },
        sequence=2,
    )
    lines = dict(enumerate(REAL_END[1:], start=50))
    evidence = evidence_map([_range(1, 50, 52, lines), plscope])
    [unit] = unit_coverage(evidence)
    [member] = unit.members
    assert unit.through(member) == 52
    assert read_claims("I read calc_f in full.", evidence) == []
    # Known from PL/Scope, a comment-END after it is still not an end.
    commented = dict(
        enumerate(["  FUNCTION calc_f RETURN NUMBER IS", "  /* x", "  END calc_f;"], start=50)
    )
    evidence = evidence_map([_range(1, 50, 52, commented), plscope])
    [unit] = unit_coverage(evidence)
    assert unit.through(unit.members[0]) is None
    assert read_claims("I read calc_f in full.", evidence) != []
