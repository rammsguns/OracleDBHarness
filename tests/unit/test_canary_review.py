"""Offline review of production canary pass evidence."""

from datetime import date

from tests.canary.production import BUDGET, BUDGET_SCOPE, LABEL, record_budget, review, write_json


def test_review_flags_successful_worksheet_in_c2_pass(tmp_path):
    record_budget(tmp_path)
    request = {
        "path": "/worksheets",
        "status": 200,
        "outcome": "succeeded",
        "audit_checked": True,
    }
    write_json(
        tmp_path / "passes/c2-trial.json",
        {
            "at": "2026-10-25T09:00:00+00:00",
            "label": LABEL,
            "success": True,
            "cost": dict(BUDGET),
            "budget_scope": BUDGET_SCOPE,
            "requests": [request],
        },
    )
    write_json(
        tmp_path / "drills/drill.json",
        {"label": LABEL, "success": True, "restored": True, "account_status": "OPEN"},
    )

    result = review(tmp_path, date(2026, 10, 25))

    assert not result["pass"]
    assert result["request_failures"] == [{"pass": "c2-trial", "request": request}]
    assert result["latest_pass"]["request_failures"] == [request]
    assert result["failures_so_far"] == ["Request refusal or audit failures in pass evidence"]
    assert not (tmp_path / "exit.json").exists()
