"""Offline checks for B5's evidence gates."""

import pytest

from tests.canary.rehearsal import BUDGET, LABEL, check_audit, compare_budget, render_report


def test_budget_fails_closed_for_missing_negative_and_over_limit():
    assert compare_budget(dict(BUDGET), BUDGET) == []
    assert compare_budget({**BUDGET, "sessions": 3}, BUDGET) == ["sessions"]
    assert compare_budget({**BUDGET, "executions": -1}, BUDGET) == ["executions"]
    assert compare_budget({}, BUDGET) == list(BUDGET)


def test_audit_requires_fresh_principal_target_and_outcome():
    event = {
        "id": "a",
        "actorSubject": "dba",
        "profileId": "d4",
        "outcome": "refused",
        "operationId": "worksheet.open",
    }
    assert check_audit([event], set(), "dba", "d4", "refused") == 1
    with pytest.raises(RuntimeError):
        check_audit([event], set(), "dba", "d4", "succeeded")
    for records, previous in [
        ([event], {"a"}),
        ([{**event, "actorSubject": "other"}], set()),
        ([{**event, "profileId": "other"}], set()),
        ([{**event, "outcome": None}], set()),
    ]:
        with pytest.raises(RuntimeError):
            check_audit(records, previous, "dba", "d4")


def test_report_preserves_budget_and_blocked_g3_label():
    rendered = render_report({"budget": BUDGET, "passes": []})
    assert LABEL in rendered
    assert "G3 stays blocked" in rendered
    assert '"buffer_gets": 1000000' in rendered


def test_unavailable_panel_does_not_look_like_a_success():
    from tests.canary.rehearsal import observation_errors

    assert observation_errors({"panels": {"sessions": {"available": False}}})
    assert observation_errors({"outcome": "degraded", "steps": []})
    assert not observation_errors({"available": True, "rows": []})
