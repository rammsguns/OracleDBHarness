"""The C30 prototype's ``read_range`` check, which stays outside the harness.

The prototype (copilot-eval/derived_claims_prototype.py) is not wired into the harness;
its other three checks are now in ``claim_checks.py`` (C31, ``test_derived_claims.py``).
The flagged text is trial 19's KINJ-01 (copilot-eval/strata-k7-focused-20261018T180931Z
.json); the control is the range actually read. One test records the known false positive
on an accepted trial-17 statement, so the limit is visible rather than tuned away.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from harness_api.copilot.claim_checks import LookupFacts

_PATH = Path(__file__).resolve().parents[2] / "copilot-eval" / "derived_claims_prototype.py"
_SPEC = importlib.util.spec_from_file_location("derived_claims_prototype", _PATH)
assert _SPEC and _SPEC.loader
prototype = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = prototype
_SPEC.loader.exec_module(prototype)


def kinds(answer: str, *facts: LookupFacts) -> list[str]:
    return [flag.kind for flag in prototype.review(answer, facts)]


# -- a numbered range said to have been read -------------------------------------------


def _range(start: int, headers: dict[int, str]) -> LookupFacts:
    lines = [[n, "  -- body"] for n in range(start, 900)]
    for number, name in headers.items():
        lines[number - start] = [number, f"  FUNCTION {name}(p_amount NUMBER) RETURN BOOLEAN IS"]
    return LookupFacts(
        operation_id="schema.object_source_range",
        succeeded=True,
        columns=["LINE", "TEXT"],
        rows=lines,
        parameters={
            "owner": "HARNESS_APP",
            "object_name": "ETL_ORDERS",
            "object_type": "PACKAGE BODY",
            "start_line": start,
            "end_line": 899,
            "row_limit": 200,
        },
        sequence=3,
    )


# Lines from 790: rule 39's header (line 784) is not among them; 40-44 are.
READ = _range(790, {804: "check_rule_040", 824: "check_rule_041", 844: "check_rule_042",
                    865: "check_rule_043", 885: "check_rule_044"})  # fmt: skip


def test_a_range_said_read_that_includes_an_unread_member_is_flagged() -> None:
    answer = "The band is a business decision; the rules I read are 39–44 and I read no others."
    [flag] = prototype.review(answer, [READ])
    assert flag.kind == "read_range" and "CHECK_RULE_039" in flag.problem


def test_the_range_actually_read_passes() -> None:
    assert kinds("The rules I read are 40–44 and I read no others.", READ) == []


def test_known_false_positive_on_call_lines() -> None:
    # Trial 18 (accepted by review): "every one of the checks I read (rules 32-50 in
    # those lines)" described dispatcher call lines, not member bodies. The prototype
    # counts only member headers, so it flags this. Kept as a stated limit.
    answer = "Every one of the checks I read (rules 32–50 in those lines) is written this way."
    assert kinds(answer, READ) == ["read_range"]
