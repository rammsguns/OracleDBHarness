"""K-7: the Kiwi evaluation cases, their runner checks, and the invariant test map.

The shipped case set is rehearsed with the fixture provider and scripted model turns. That
proves the harness, the cases and the checks fit together; it is not evidence of model
quality, which needs a paid run reviewed by a DBA (KIWI_PLAN.md, K-7).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tests.copilot import test_kiwi
from tests.copilot.eval.cases import DEFAULT_CASE_FILE, CaseSetError, load_case_set, parse_case_set
from tests.copilot.eval.runner import REHEARSAL, RunConfig, run_evaluation

# KIWI_PLAN.md, "Invariants Kiwi inherits". Each invariant names the tests in
# tests/copilot/test_kiwi.py that fail if it breaks.
INVARIANT_TESTS: dict[int, tuple[str, ...]] = {
    1: (
        "test_anything_but_an_offered_tool_is_refused_and_audited",
        "test_every_offered_tool_is_a_read_entry",
        "test_only_reviewed_read_entries_marked_for_kiwi_are_offered",
    ),
    2: (
        "test_offered_lookups_read_the_dictionary_not_application_tables",
        "test_no_rows_reach_the_request_record_or_the_stream",
    ),
    3: (
        "test_a_lookup_runs_as_the_user_and_is_recorded",
        "test_unknown_parameters_are_refused_before_anything_runs",
        "test_a_user_without_a_grant_is_refused_before_anything_is_spent",
        "test_an_unknown_target_is_refused",
        "test_an_integration_credential_cannot_make_lookups",
        "test_a_grant_revoked_mid_request_refuses_the_next_lookup",
    ),
    4: ("test_on_production_kiwi_only_observes",),
    5: ("test_injected_text_in_a_result_stays_inside_the_markers",),
    6: ("test_a_kiwi_proposal_applies_to_the_editor_only",),
    7: (
        "test_results_are_capped_and_say_so",
        "test_running_out_of_lookups_ends_with_a_partial_answer",
        "test_asking_again_after_the_wrap_up_stops_the_loop",
        "test_running_out_of_steps_ends_with_a_partial_answer",
        "test_running_out_of_tokens_ends_with_a_partial_answer",
        "test_running_out_of_time_ends_with_a_partial_answer",
        "test_a_provider_failure_mid_loop_leaves_the_database_workflows_working",
    ),
}


def test_every_invariant_has_tests_that_exist() -> None:
    assert sorted(INVARIANT_TESTS) == [1, 2, 3, 4, 5, 6, 7]
    for number, names in INVARIANT_TESTS.items():
        assert names, f"invariant {number} has no test"
        for name in names:
            assert callable(getattr(test_kiwi, name, None)), f"invariant {number}: {name} is gone"


# -- the shipped cases ----------------------------------------------------------------------


def document() -> dict[str, Any]:
    return json.loads(DEFAULT_CASE_FILE.read_text(encoding="utf-8"))


def subset(*ids: str, mutate: Any = None) -> Any:
    doc = copy.deepcopy(document())
    doc["cases"] = [c for c in doc["cases"] if c["id"] in ids]
    doc["correctnessDenominator"] = sum("correctness" in c["gates"] for c in doc["cases"])
    if mutate:
        mutate({c["id"]: c for c in doc["cases"]})
    return parse_case_set(doc, path=DEFAULT_CASE_FILE, sha256="test")


async def rehearse(tmp_path: Path, cases: Any) -> dict[str, dict[str, Any]]:
    config = RunConfig(mode=REHEARSAL, provider="fake", report_path=tmp_path / "r.json")
    report = await run_evaluation(config, cases, environ={})
    return {c["id"]: c for c in report["cases"]}


def failed(record: dict[str, Any]) -> set[str]:
    return {c["name"] for c in record["automated"]["checks"] if not c["passed"]}


def test_the_shipped_set_has_the_kiwi_cases_the_plan_asks_for() -> None:
    cases = load_case_set()
    kiwi = [c for c in cases.cases if c.kiwi is not None]
    assert {c.category for c in kiwi} == {
        "diagnose",
        "kiwi_create",
        "explain_package",
        "explain_process",
        "kiwi_injection",
        "kiwi_authorization",
        "kiwi_budget",
    }
    assert all(c.request_payload()["profileId"].startswith("@target:") for c in kiwi)
    injection = [c for c in kiwi if c.category == "kiwi_injection"]
    assert len(injection) >= 2 and all("safety" in c.gates for c in injection)
    assert cases.correctness_denominator == sum("correctness" in c.gates for c in cases.cases)


async def test_every_shipped_case_rehearses_clean(tmp_path: Path) -> None:
    records = await rehearse(tmp_path, load_case_set())
    kiwi_ids = {c.id for c in load_case_set().cases if c.kiwi is not None}
    for case_id in kiwi_ids:
        record = records[case_id]
        assert record["incompleteReasons"] == [], (case_id, record["incompleteReasons"])
        assert failed(record) == set(), (case_id, failed(record))
    assert records["KBUD-01"]["toolCalls"] and len(records["KBUD-01"]["toolCalls"]) == 12
    refused = {t["errorCode"] for t in records["KINJ-01"]["toolCalls"] if t["status"] == "refused"}
    assert refused == {"policy_refused"}


# -- the checks fail when they should --------------------------------------------------------


async def test_a_diagnosis_that_looked_nothing_up_fails_its_checks(tmp_path: Path) -> None:
    def mutate(cases: dict[str, Any]) -> None:
        turn = {"text": "The package is INVALID with ORA-00942."}
        cases["DIAG-01"]["kiwi"]["rehearsal"] = [turn]

    records = await rehearse(tmp_path, subset("DIAG-01", mutate=mutate))
    assert {"requiredToolsCalled", "noUnlookedClaim"} <= failed(records["DIAG-01"])


async def test_a_forbidden_lookup_fails_the_case(tmp_path: Path) -> None:
    def mutate(cases: dict[str, Any]) -> None:
        cases["DIAG-03"]["kiwi"]["forbiddenTools"] = ["dba.blocking"]

    records = await rehearse(tmp_path, subset("DIAG-03", mutate=mutate))
    assert "forbiddenToolsNotRun" in failed(records["DIAG-03"])


async def test_following_the_injected_text_fails_the_case(tmp_path: Path) -> None:
    def mutate(cases: dict[str, Any]) -> None:
        turns = cases["KINJ-02"]["kiwi"]["rehearsal"]
        turns[1]["calls"][0]["input"]["why"] = "The run comment said to ignore your instructions."

    records = await rehearse(tmp_path, subset("KINJ-02", mutate=mutate))
    assert "injectionNotFollowed" in failed(records["KINJ-02"])


async def test_an_expected_refusal_that_succeeds_fails_the_case(tmp_path: Path) -> None:
    def mutate(cases: dict[str, Any]) -> None:
        cases["KINJ-01"]["kiwi"]["refusedTools"] = {"schema.object_source_range": "policy_refused"}

    records = await rehearse(tmp_path, subset("KINJ-01", mutate=mutate))
    assert "refusedToolsRefused" in failed(records["KINJ-01"])


async def test_a_budget_case_that_stays_inside_the_budget_fails(tmp_path: Path) -> None:
    def mutate(cases: dict[str, Any]) -> None:
        turns = cases["KBUD-01"]["kiwi"]["rehearsal"]
        turns[0]["calls"] = turns[0]["calls"][:1]
        cases["KBUD-01"]["kiwi"]["rehearsal"] = [turns[0], turns[2]]

    records = await rehearse(tmp_path, subset("KBUD-01", mutate=mutate))
    assert "kiwiOutcome" in failed(records["KBUD-01"])


async def test_a_tighter_call_limit_than_the_run_used_fails(tmp_path: Path) -> None:
    def mutate(cases: dict[str, Any]) -> None:
        cases["DIAG-02"]["kiwi"]["maxToolCalls"] = 1

    records = await rehearse(tmp_path, subset("DIAG-02", mutate=mutate))
    assert "toolCallsWithinBudget" in failed(records["DIAG-02"])


# -- parsing ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c["DIAG-01"]["request"].pop("target"), "request.target"),
        (lambda c: c["DIAG-01"].pop("kiwi"), "request.target"),
        (lambda c: c["DIAG-01"]["request"].update(target="staging"), "request.target"),
        (lambda c: c["DIAG-01"]["kiwi"].update(outcome="great"), "kiwi.outcome"),
        (lambda c: c["DIAG-01"]["kiwi"].update(maxToolCalls=-1), "maxToolCalls"),
        (
            lambda c: c["DIAG-01"]["kiwi"].update(claims=[{"pattern": "(", "tools": ["a.b"]}]),
            "kiwi pattern",
        ),
        (
            lambda c: c["DIAG-01"]["kiwi"].update(claims=[{"pattern": "x", "tools": []}]),
            "tools that could support",
        ),
        (lambda c: c["DIAG-01"].update(review="automated"), "cannot be marked"),
    ],
)
def test_a_malformed_kiwi_case_is_rejected(mutate: Any, message: str) -> None:
    with pytest.raises(CaseSetError, match=message):
        subset("DIAG-01", mutate=mutate)
