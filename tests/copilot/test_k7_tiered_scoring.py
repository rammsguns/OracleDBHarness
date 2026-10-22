"""Offline qualification scoring, including retained historical rendering."""

import copy
import json
from pathlib import Path

import pytest

from tests.copilot.eval.__main__ import main
from tests.copilot.eval.cases import load_case_set
from tests.copilot.eval.runner import ALWAYS_CHECKED
from tests.copilot.eval.scoring import (
    K7_Q5_FOCUSED_CASES,
    K7_RUBRIC,
    ReportError,
    render_markdown,
    render_tiered_markdown,
    score,
    score_pool,
    score_tiered_run,
)
from tests.copilot.test_evaluation_runner import synthetic_report


def trial(index=1, *, full=False):
    report = synthetic_report()
    case_set = load_case_set()
    template = report["cases"][0]
    cases = []
    cases_by_id = {case.id: case for case in case_set.cases}
    selected = case_set.cases if full else [cases_by_id[cid] for cid in K7_Q5_FOCUSED_CASES]
    for case in selected:
        row = copy.deepcopy(template)
        row.update(id=case.id, category=case.category, gates=list(case.gates))
        row["automated"]["checks"] = [{"name": name, "passed": True} for name in ALWAYS_CHECKED]
        row["review"]["required"] = case.review == "dba"
        row["review"]["rubricVerdict"] = "pass"
        row["review"]["grounding"] = {"verdict": "pass"}
        cases.append(row)
    report.update(cases=cases, runId=f"trial-{index}", diagnostic=not full)
    report["startedAt"] = f"2026-10-22T0{index}:00:00+00:00"
    report["finishedAt"] = f"2026-10-22T0{index}:30:00+00:00"
    report["caseSet"] = case_set.summary()
    report["caseSet"]["caseCount"] = len(cases)
    report["provider"]["accessCheckModel"] = report["provider"]["requestedModel"]
    report["qualificationIdentity"] = dict(
        rubric=K7_RUBRIC,
        configuration="config-sha256",
        trialSequence=index,
        previousRunId=f"trial-{index - 1}",
    )
    return report


def pool():
    return [trial(i) for i in range(1, 4)]


def finding(report, index=0, severity="S2"):
    report["cases"][index]["review"].update(verdict="fail", failurePattern="descriptive claim")
    report["cases"][index]["review"]["grounding"] = dict(verdict="fail", severity=severity)


@pytest.mark.parametrize("mutation", [None, "missing", "added", "reordered", "category", "gates"])
def test_q5_focused_inventory(mutation):
    report = trial()
    assert tuple(case["id"] for case in report["cases"]) == (
        "EXPLAIN-06",
        "FIX-01",
        "FIX-03",
        "FIX-04",
        "FIX-05",
        "FIX-07",
        "DRAFT-02",
        "DRAFT-03",
        "TEST-01",
        "TUNE-03",
        "INACCESSIBLE-03",
        "INJECTION-01",
        "INJECTION-03",
        "DIAG-01",
        "DIAG-02",
        "DIAG-03",
        "KINJ-01",
        "KINJ-02",
    )
    if mutation == "missing":
        report["cases"].pop()
    elif mutation == "added":
        extra = next(case for case in trial(full=True)["cases"] if case["id"] == "EXPLAIN-01")
        report["cases"].append(extra)
    elif mutation == "reordered":
        report["cases"][0], report["cases"][1] = report["cases"][1], report["cases"][0]
    elif mutation == "category":
        report["cases"][0]["category"] = "changed"
    elif mutation == "gates":
        report["cases"][0]["gates"] = []
    report["caseSet"]["caseCount"] = len(report["cases"])
    result = score_tiered_run(report)
    inventory_reasons = [reason for reason in result["reasons"] if "inventory" in reason]
    if mutation is None:
        assert inventory_reasons == []
        assert result["qualified"], result["reasons"]
    else:
        assert "Case-set inventory does not match the fixed Q5/Q6 target." in inventory_reasons
        assert not result["qualified"]


def test_passing_pool_and_markdown():
    reports = pool()
    for i, report in enumerate(reports):
        finding(report, i)
    result = score_pool(reports)
    assert result["qualified"], result["reasons"]
    assert result["s2"] == 3
    assert "54/54" in render_tiered_markdown(result)


@pytest.mark.parametrize(
    "kind",
    [
        "S1",
        "total",
        "concentration",
        "narrow",
        "safety",
        "missing",
        "pending",
        "disputed",
        "sequence",
        "overlap",
        "count",
    ],
)
def test_pool_rejects_incomplete_or_failing_evidence(kind):
    reports = pool()
    case = reports[0]["cases"][0]
    if kind == "S1":
        finding(reports[0], severity="S1")
    elif kind == "total":
        for i in range(4):
            finding(reports[0], i)
    elif kind == "concentration":
        finding(reports[0])
        finding(reports[1])
    elif kind == "narrow":
        case["review"]["rubricVerdict"] = "fail"
    elif kind == "safety":
        case["automated"]["checks"][0]["passed"] = False
    elif kind == "missing":
        del case["review"]["grounding"]
    elif kind == "pending":
        case["review"]["grounding"]["verdict"] = "pending"
    elif kind == "disputed":
        case["review"]["grounding"]["disputed"] = True
    elif kind == "sequence":
        reports[1]["qualificationIdentity"]["trialSequence"] = 4
    elif kind == "overlap":
        reports[1]["startedAt"] = reports[0]["startedAt"]
    else:
        reports.pop()
    result = score_pool(reports)
    assert not result["qualified"]
    for reason in result["reasons"]:
        assert reason in render_tiered_markdown(result)


@pytest.mark.parametrize(
    "field", ["commit", "configuration", "model", "rubric", "caseSet", "limits"]
)
@pytest.mark.parametrize("missing", [False, True])
def test_candidate_identity_change_or_missing(field, missing):
    reports = pool()
    report = reports[1]
    owner, key = {
        "commit": (report["harness"], "commit"),
        "configuration": (report["qualificationIdentity"], "configuration"),
        "model": (report["provider"], "requestedModel"),
        "rubric": (report["qualificationIdentity"], "rubric"),
        "caseSet": (report["caseSet"], "sha256"),
        "limits": (report, "limits"),
    }[field]
    if missing:
        owner.pop(key)
    else:
        owner[key] = {"changed": True} if field == "limits" else "changed"
    result = score_pool(reports)
    assert not result["qualified"]
    assert any(field in reason for reason in result["reasons"])


@pytest.mark.parametrize(
    "count,severity,passes", [(0, "S2", True), (2, "S2", True), (3, "S2", False), (1, "S1", False)]
)
def test_q6_limits(count, severity, passes):
    report = trial(full=True)
    for i in range(count):
        finding(report, i, severity)
    result = score_tiered_run(report, full=True)
    assert result["qualified"] is passes, result["reasons"]


@pytest.mark.parametrize(
    "field,value",
    [("severity", "S3"), ("severity", None), ("disputed", "yes"), ("verdict", "unknown")],
)
def test_invalid_grounding_fields(field, value):
    report = trial()
    report["cases"][0]["review"]["grounding"][field] = value
    with pytest.raises(ReportError):
        score(report)


def test_historical_rendering_byte_identity(tmp_path):
    path = Path("copilot-eval/strata-k7-focused-20261020T155124Z.json")
    if not path.exists():
        pytest.skip("no saved trial reports in this checkout")
    report = json.loads(path.read_text())
    assert render_markdown(report, score(report)).encode() == path.with_suffix(".md").read_bytes()
    assert main(["score", str(path), "--markdown", str(tmp_path / "historical.md")]) == 1
    assert (tmp_path / "historical.md").read_bytes() == path.with_suffix(".md").read_bytes()


def test_tiered_cli(tmp_path):
    paths = []
    for i, report in enumerate(pool()):
        path = tmp_path / f"{i}.json"
        path.write_text(json.dumps(report))
        paths.append(str(path))
    assert main(["pool", *paths, "--markdown", str(tmp_path / "pool.md")]) == 0
    path = tmp_path / "full.json"
    path.write_text(json.dumps(trial(full=True)))
    assert main(["q6", str(path), "--markdown", str(tmp_path / "q6.md")]) == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "dirty",
        "budget",
        "reviewer",
        "unclassified",
        "access",
        "required",
        "checks",
        "date",
        "configuration",
    ],
)
def test_other_integrity_gaps(mutation):
    reports = pool()
    report = reports[0]
    case = report["cases"][0]
    if mutation == "dirty":
        report["harness"]["uncommittedChanges"] = True
    elif mutation == "budget":
        report["budget"]["stoppedForBudget"] = True
    elif mutation == "reviewer":
        case["review"]["reviewer"] = ""
    elif mutation == "unclassified":
        finding(report)
        del case["review"]["grounding"]["severity"]
    elif mutation == "access":
        report["provider"]["accessCheckModel"] = "different"
    elif mutation == "required":
        case["review"]["required"] = False
    elif mutation == "checks":
        case["automated"]["checks"] = []
    elif mutation == "date":
        reports[1]["startedAt"] = "unknown"
    else:
        report["qualificationIdentity"]["configuration"] = " "
    assert not score_pool(reports)["qualified"]


def test_disputed_review_blocks_historical_command_too():
    report = synthetic_report()
    report["cases"][0]["review"]["grounding"] = {"verdict": "pass", "disputed": True}
    assert not score(report)["qualified"]


def test_q6_rejects_diagnostic_report():
    report = trial(full=True)
    report["diagnostic"] = True
    assert not score_tiered_run(report, full=True)["qualified"]


def test_nonadjacent_trial_cannot_be_reused():
    reports = pool()
    reports[2]["runId"] = reports[0]["runId"]
    assert not score_pool(reports)["qualified"]
