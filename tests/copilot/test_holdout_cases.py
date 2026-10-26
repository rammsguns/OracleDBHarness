"""Independent holdout inventory and unchanged tiered acceptance requirements."""

import copy
import json
import sqlite3
from collections import Counter

import pytest

from harness_worker.backend.fake import _SEED_SCRIPT, _seed_rows
from tests.copilot.eval import __main__ as cli
from tests.copilot.eval.cases import CaseSetError, load_case_set, parse_case_set
from tests.copilot.eval.runner import QUALIFICATION
from tests.copilot.eval.scoring import (
    K7_HOLDOUT_FILE,
    K7_HOLDOUT_SHA256,
    K7_HOLDOUT_VERSION,
    score_pool,
    score_tiered_run,
)
from tests.copilot.test_k7_tiered_scoring import finding, trial


def holdout_trial(index=1):
    report = trial(index)
    case_set = load_case_set(K7_HOLDOUT_FILE)
    template = report["cases"][0]
    report["cases"] = []
    for case in case_set.cases:
        row = copy.deepcopy(template)
        row.update(id=case.id, category=case.category, gates=list(case.gates))
        report["cases"].append(row)
    report["caseSet"] = case_set.summary()
    # A complete --cases holdout run needs no --only diagnostic selection.
    report["diagnostic"] = False
    return report


def test_holdout_case_contract():
    case_set = load_case_set(K7_HOLDOUT_FILE)
    assert case_set.version == K7_HOLDOUT_VERSION
    assert case_set.sha256 == K7_HOLDOUT_SHA256
    assert len(case_set.cases) == 18
    assert Counter((c.category, c.action) for c in case_set.cases) == {
        ("explain", "explain"): 1,
        ("fix", "diagnose"): 5,
        ("draft", "propose"): 2,
        ("test_block", "test_block"): 1,
        ("tuning", "explain_plan"): 1,
        ("inaccessible", "diagnose"): 1,
        ("embedded_instructions", "diagnose"): 2,
        ("diagnose", "kiwi.diagnose"): 3,
        ("kiwi_injection", "kiwi.diagnose"): 2,
    }
    existing = {c.id for c in load_case_set().cases}
    for case in case_set.cases:
        assert case.id.startswith("H-") and case.id not in existing
        assert case.review == "dba"
        assert len(case.rubric) == 3
        if case.kiwi:
            assert case.target == "development"
            assert case.kiwi.required_tools and case.kiwi.claims and case.kiwi.rehearsal


@pytest.mark.parametrize("mutation", ["denominator", "duplicate", "review"])
def test_invalid_holdout_rejected(mutation):
    document = json.loads(K7_HOLDOUT_FILE.read_text())
    if mutation == "denominator":
        document["correctnessDenominator"] += 1
    elif mutation == "duplicate":
        document["cases"][1]["id"] = document["cases"][0]["id"]
    else:
        document["cases"][0]["review"] = "automated"
    with pytest.raises(CaseSetError):
        parse_case_set(document, path=K7_HOLDOUT_FILE, sha256="changed")


@pytest.mark.parametrize(
    "mutation", [None, "missing", "order", "category", "gates", "version", "sha256", "review"]
)
def test_holdout_inventory_selected_by_version(mutation):
    report = holdout_trial()
    if mutation == "missing":
        report["cases"].pop()
        report["caseSet"]["caseCount"] -= 1
    elif mutation == "order":
        report["cases"].reverse()
    elif mutation in ("category", "gates"):
        report["cases"][0][mutation] = "draft" if mutation == "category" else []
        if mutation == "gates":
            report["caseSet"]["correctnessDenominator"] -= 1
    elif mutation in ("version", "sha256"):
        report["caseSet"][mutation] = "unknown"
    elif mutation == "review":
        report["cases"][0]["review"]["required"] = False
    result = score_tiered_run(report)
    assert result["qualified"] is (mutation is None), result["reasons"]


@pytest.mark.parametrize(
    "failure", [None, "S1", "narrow", "safety", "total", "concentration", "pending", "provenance"]
)
def test_holdout_pool_uses_identical_bar(failure):
    reports = [holdout_trial(i) for i in range(1, 4)]
    if failure is None:
        for i, report in enumerate(reports):
            finding(report, i)
    elif failure == "S1":
        finding(reports[0], severity="S1")
    elif failure == "narrow":
        reports[0]["cases"][0]["review"]["rubricVerdict"] = "fail"
    elif failure == "safety":
        reports[0]["cases"][12]["automated"]["checks"][0]["passed"] = False
    elif failure == "total":
        for i in range(4):
            finding(reports[0], i)
    elif failure == "concentration":
        finding(reports[0])
        finding(reports[1])
    elif failure == "pending":
        reports[0]["cases"][0]["review"]["grounding"]["verdict"] = "pending"
    else:
        reports[0]["qualificationIdentity"]["configuration"] = ""
    result = score_pool(reports)
    assert result["qualified"] is (failure is None), result["reasons"]
    if failure is None:
        assert result["s2"] == 3


def test_holdout_and_focused_cannot_share_a_pool():
    assert not score_pool([holdout_trial(1), trial(2), holdout_trial(3)])["qualified"]


def test_complete_holdout_cli_run_needs_no_only(monkeypatch, tmp_path):
    async def offline_run(config, case_set):
        assert config.mode == QUALIFICATION
        assert not config.diagnostic
        assert case_set.version == K7_HOLDOUT_VERSION
        assert len(case_set.cases) == 18
        report = holdout_trial()
        report["evidence"] = "offline test double; no provider called"
        return report

    monkeypatch.setattr(cli, "run_evaluation", offline_run)
    assert (
        cli.main(
            [
                "run",
                "--cases",
                str(K7_HOLDOUT_FILE),
                "--report",
                str(tmp_path / "offline.json"),
                "--model",
                "offline-test-double",
                "--approved-context",
                "user_message",
                "--approval-reference",
                "offline test only",
            ]
        )
        == 0
    )


def test_holdout_catalog_facts_and_existing_fixture_unchanged():
    db = sqlite3.connect(":memory:")
    db.executescript(_SEED_SCRIPT)
    _seed_rows(db.cursor())
    assert db.execute(
        "SELECT status FROM all_objects WHERE object_name='H_ROUTE_CACHE' AND object_type='PACKAGE BODY'"
    ).fetchone() == ("INVALID",)
    assert db.execute(
        "SELECT message_number FROM all_errors WHERE name='H_ROUTE_CACHE'"
    ).fetchone() == (323,)
    assert (
        db.execute(
            "SELECT COUNT(*) FROM all_source WHERE name='H_ROUTE_CACHE' AND type='PACKAGE BODY'"
        ).fetchone()[0]
        > 1200
    )
    assert db.execute(
        "SELECT referenced_link_name FROM all_dependencies WHERE name='H_SCAN_ROLLUP'"
    ).fetchone() == ("SCAN_LINK",)
    assert db.execute(
        "SELECT failure_count FROM all_scheduler_jobs WHERE job_name='H_REBUILD_ZONES'"
    ).fetchone() == (7,)
    assert db.execute(
        "SELECT COUNT(*) FROM all_scheduler_job_run_details WHERE job_name='H_REBUILD_ZONES'"
    ).fetchone() == (2,)
    assert (
        "Approval capsule"
        in db.execute(
            "SELECT additional_info FROM all_scheduler_job_run_details WHERE log_id=9812"
        ).fetchone()[0]
    )
    assert db.execute(
        "SELECT line, position FROM all_errors WHERE name='EMPLOYEE_REPORT' ORDER BY sequence LIMIT 1"
    ).fetchone() == (5, 39)
    assert db.execute(
        "SELECT failure_count FROM all_scheduler_jobs WHERE job_name='EXPORT_ORDERS'"
    ).fetchone() == (3,)
    db.close()
