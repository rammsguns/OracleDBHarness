"""The capacity run's scheduling, measurement and verdicts, checked without load.

A load run is expensive and noisy, so the decisions that make one trustworthy are checked
here, deterministically: that a workload file cannot leave out a limit or a threshold,
that the schedule is reproducible and honours its weights and bounds, that percentiles
and recovery are computed as documented, and that nothing but a complete run on three
real databases against agreed thresholds can come out as a pass.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from collections import Counter
from itertools import islice
from pathlib import Path
from typing import Any

import pytest

from tests.capacity import metrics, scheduler
from tests.capacity.metrics import Sample, Verification
from tests.capacity.report import CapacityReport, LeakError
from tests.capacity.workload import Workload, WorkloadProblem, load, parse

EXAMPLE = Path(__file__).resolve().parents[1] / "capacity" / "workload.example.json"


def _document() -> dict[str, Any]:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _workload(**changes: Any) -> Workload:
    document = _document()
    for dotted, value in changes.items():
        cursor = document
        *path, last = dotted.split("__")
        for key in path:
            cursor = cursor[key]
        cursor[last] = value
    return parse(document)


# -- the workload file -------------------------------------------------------------------


def test_the_example_workload_is_valid_and_its_thresholds_are_proposals() -> None:
    workload = load(EXAMPLE)
    assert len(workload.users) == 10
    assert len(workload.targets) == 3
    assert workload.thresholds.status == "proposed"
    assert workload.thresholds.agreed is False


def test_an_empty_workload_names_every_missing_section_at_once() -> None:
    with pytest.raises(WorkloadProblem) as refused:
        parse({})
    text = str(refused.value)
    for section in (
        "version",
        "api",
        "users",
        "targets",
        "workload",
        "phases",
        "resourceLimits",
        "thresholds",
        "cleanup",
    ):
        assert section in text, section


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"thresholds__status": "agreed"}, "proposals until someone is on record"),
        ({"thresholds__maxContaminations": 1}, "never an acceptable amount"),
        (
            {
                "thresholds__saturation": {
                    "maxErrorRate": 0.1,
                    "allowedErrorCodes": ["outcome_unknown"],
                }
            },
            "outcome_unknown can never be an allowed error",
        ),
        (
            {"workload__mix": {"metadata": 1, "boundedRead": 1, "transaction": 1}},
            "workload.mix.cancellation must be stated",
        ),
        (
            {"phases__saturation": {"seconds": 60, "streamsPerUser": 4, "thinkTimeScale": 0}},
            "cannot exceed the 3 target",
        ),
        (
            {"resourceLimits__maxInFlightRequests": "many"},
            "resourceLimits.maxInFlightRequests must be a number",
        ),
        ({"workload__seed": "random"}, "can be reproduced"),
    ],
)
def test_a_workload_cannot_leave_a_decision_implicit(change: dict[str, Any], fragment: str) -> None:
    with pytest.raises(WorkloadProblem, match=fragment):
        _workload(**change)


def test_agreed_thresholds_need_a_name_a_date_and_a_reference() -> None:
    workload = _workload(
        thresholds__status="agreed",
        thresholds__agreedBy="pilot DBA and application owner",
        thresholds__agreedOn="2026-10-01",
        thresholds__reference="capacity review minutes",
    )
    assert workload.thresholds.agreed is True


def test_a_latency_threshold_is_required_for_every_kind_in_the_mix() -> None:
    document = _document()
    del document["thresholds"]["latencyMs"]["cancellation"]
    with pytest.raises(WorkloadProblem, match="latencyMs.cancellation is required"):
        parse(document)
    document["workload"]["mix"]["cancellation"] = 0
    assert "cancellation" not in parse(document).thresholds.latency_ms


# -- the schedule --------------------------------------------------------------------------


def test_every_target_gets_its_share_of_users() -> None:
    workload = load(EXAMPLE)
    one = Counter(stream.target for stream in scheduler.streams(workload, workload.phase("steady")))
    assert sorted(one.values()) == [3, 3, 4]
    three = scheduler.streams(workload, workload.phase("saturation"))
    assert len(three) == 30
    for user in workload.users:
        assert {s.target for s in three if s.user == user.subject} == {
            t.name for t in workload.targets
        }


def test_the_schedule_is_reproducible_and_depends_on_the_seed() -> None:
    workload = load(EXAMPLE)
    phase = workload.phase("steady")
    stream = scheduler.streams(workload, phase)[0]
    first = list(islice(scheduler.steps(workload, phase, stream), 50))
    again = list(islice(scheduler.steps(workload, phase, stream), 50))
    reseeded = list(islice(scheduler.steps(_workload(workload__seed=2), phase, stream), 50))
    assert first == again
    assert first != reseeded


def test_the_mix_weights_and_think_time_bounds_are_honoured() -> None:
    workload = _workload(
        workload__mix={"metadata": 50, "boundedRead": 30, "transaction": 20, "cancellation": 0}
    )
    phase = workload.phase("steady")
    stream = scheduler.streams(workload, phase)[0]
    steps = list(islice(scheduler.steps(workload, phase, stream), 20_000))
    counts = Counter(step.kind for step in steps)
    assert counts["cancellation"] == 0
    assert abs(counts["metadata"] / len(steps) - 0.5) < 0.02
    assert abs(counts["transaction"] / len(steps) - 0.2) < 0.02
    low, high = workload.think_ms
    assert all(low <= step.think_ms <= high for step in steps)
    rollbacks = sum(1 for step in steps if not step.commit) / len(steps)
    assert abs(rollbacks - scheduler.ROLLBACK_SHARE) < 0.02

    saturation = workload.phase("saturation")
    busy = scheduler.streams(workload, saturation)[0]
    assert all(
        step.think_ms == 0 for step in islice(scheduler.steps(workload, saturation, busy), 100)
    )


def test_the_gate_never_lets_more_requests_through_than_declared() -> None:
    gate = scheduler.Gate(3)
    seen: list[int] = []
    lock = threading.Lock()
    inside = 0

    def work() -> None:
        nonlocal inside
        with gate:
            with lock:
                inside += 1
                seen.append(inside)
            time.sleep(0.01)
            with lock:
                inside -= 1

    threads = [threading.Thread(target=work) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert max(seen) <= 3
    assert gate.peak <= 3


def test_the_timeline_runs_the_phases_back_to_back() -> None:
    workload = load(EXAMPLE)
    assert scheduler.timeline(workload.phases) == [
        ("steady", 0.0, 1800.0),
        ("saturation", 1800.0, 2100.0),
        ("recovery", 2100.0, 2700.0),
    ]


def test_sequence_numbers_are_unique_per_user_across_threads() -> None:
    sequence = scheduler.Sequence()
    taken: list[int] = []
    lock = threading.Lock()

    def take() -> None:
        for _ in range(200):
            value = sequence.take("u")
            with lock:
                taken.append(value)

    threads = [threading.Thread(target=take) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(taken) == list(range(1, 1001))
    assert sequence.highest("u") == 1000


# -- measurement ---------------------------------------------------------------------------


def _sample(
    phase: str,
    kind: str,
    started: float,
    client: float,
    *,
    ok: bool = True,
    code: str = "",
    db: float | None = None,
) -> Sample:
    return Sample(phase, kind, "u", "t", started, client, ok, code, server_ms=db, db_ms=db)


def test_percentiles_are_nearest_rank_and_absent_when_nothing_was_measured() -> None:
    values = [float(v) for v in range(1, 101)]
    assert metrics.percentile(values, 0.50) == 50
    assert metrics.percentile(values, 0.95) == 95
    assert metrics.percentile([7.0], 0.99) == 7
    assert metrics.percentile([], 0.95) is None


def test_overhead_is_client_time_less_database_time_and_errors_are_kept_apart() -> None:
    samples = [
        _sample("steady", "boundedRead", 0, 100, db=40),
        _sample("steady", "boundedRead", 1, 200, db=150),
        _sample("steady", "boundedRead", 2, 900, ok=False, code="execution_timeout"),
        _sample("steady", "metadata", 3, 300),
    ]
    summary = metrics.summarize(samples, "steady")
    assert summary["boundedRead"].count == 3
    assert summary["boundedRead"].errors == 1
    assert summary["boundedRead"].error_codes == {"execution_timeout": 1}
    assert summary["boundedRead"].client_p95 == 200  # failures are not latency
    assert summary["boundedRead"].overhead_p95 == 60
    assert summary["metadata"].overhead_p95 is None  # no database time to subtract


def test_recovery_is_the_point_after_which_every_window_meets_the_steady_thresholds() -> None:
    workload = load(EXAMPLE)
    slow = workload.thresholds.latency_ms["boundedRead"]["p95"] + 1
    samples = [_sample("recovery", "boundedRead", t, slow if t < 7 else 10) for t in range(0, 40)]
    assert metrics.recovered_after(samples, workload, 0.0, 40.0) == 10.0
    never = [_sample("recovery", "boundedRead", t, slow) for t in range(0, 40)]
    assert metrics.recovered_after(never, workload, 0.0, 40.0) is None
    assert metrics.recovered_after(samples, workload, 0.0, 5.0) is None


# -- verdicts ------------------------------------------------------------------------------

REAL = {"oracleBackend": "oracledb", "environment": "load"}
IDENTITIES = {
    f"load-db-{n}": {"hostName": f"db{n}", "databaseName": f"LOAD{n}", "containerName": None}
    for n in (1, 2, 3)
}


def _clean_samples(workload: Workload) -> list[Sample]:
    samples = []
    for phase in ("steady", "saturation", "recovery"):
        for t in range(0, 120):
            for kind in workload.thresholds.latency_ms:
                samples.append(_sample(phase, kind, float(t), 50.0, db=20.0))
    return samples


def _report(
    workload: Workload,
    *,
    rehearsal: bool = False,
    environment: dict[str, Any] = REAL,
    identities: dict[str, Any] = IDENTITIES,
    verification: Verification | None = None,
) -> CapacityReport:
    report = CapacityReport(workload=workload, rehearsal=rehearsal)
    report.commit = "abc1234"
    report.samples = _clean_samples(workload)
    report.completed_phases = [phase.name for phase in workload.phases]
    report.environment = dict(environment)
    report.identities = copy.deepcopy(identities)
    report.verification = verification or Verification()
    report.recovery_s = 0.0
    report.criteria = metrics.evaluate(
        workload, report.samples, report.verification, report.recovery_s
    )
    report.eligibility = metrics.eligibility(
        workload,
        report.environment,
        report.identities,
        rehearsal=rehearsal,
        completed_phases=report.completed_phases,
    )
    return report


def _agreed() -> Workload:
    return _workload(
        thresholds__status="agreed",
        thresholds__agreedBy="pilot DBA and application owner",
        thresholds__agreedOn="2026-10-01",
        thresholds__reference="capacity review minutes",
    )


def test_only_a_complete_run_on_three_real_databases_against_agreed_thresholds_passes() -> None:
    report = _report(_agreed())
    assert all(c.passed for c in report.criteria)
    assert report.qualified is True
    assert report.verdict.startswith("PASSED")


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"environment": {"oracleBackend": "fake"}}, "not `oracledb`"),
        (
            {"identities": {name: IDENTITIES["load-db-1"] for name in IDENTITIES}},
            "distinct database",
        ),
        ({"rehearsal": True}, "rehearsal"),
    ],
)
def test_measurements_within_thresholds_are_not_a_qualification_without_real_targets(
    kwargs: dict[str, Any], reason: str
) -> None:
    report = _report(_agreed(), **kwargs)
    assert report.qualified is False
    assert any(reason in item for item in report.eligibility.reasons), report.eligibility.reasons


def test_proposed_thresholds_never_qualify() -> None:
    report = _report(load(EXAMPLE))
    assert report.qualified is False
    assert report.verdict.startswith("NOT A PILOT QUALIFICATION")
    assert "proposed, not agreed" in report.verdict


def test_a_run_cut_short_does_not_qualify() -> None:
    report = _report(_agreed())
    report.completed_phases = ["steady"]
    report.eligibility = metrics.eligibility(
        report.workload,
        report.environment,
        report.identities,
        rehearsal=False,
        completed_phases=report.completed_phases,
    )
    assert report.qualified is False
    assert any("did not complete" in item for item in report.eligibility.reasons)


def test_contamination_fails_the_run_and_a_rehearsal_alike() -> None:
    found = Verification(
        contaminations=["load-user-02 saw load-user-01's uncommitted marker 4 on load-db-1"]
    )
    run = _report(_agreed(), verification=found)
    assert run.verdict.startswith("FAILED")
    rehearsal = _report(load(EXAMPLE), rehearsal=True, verification=found)
    assert rehearsal.rehearsal_sound is False
    assert rehearsal.verdict.startswith("REHEARSAL FAILED")


def test_a_sound_rehearsal_reports_its_threshold_findings_without_failing() -> None:
    workload = load(EXAMPLE)
    report = _report(workload, rehearsal=True)
    report.samples.append(
        _sample("saturation", "transaction", 1.0, 50.0, ok=False, code="oracle_error")
    )
    report.criteria = metrics.evaluate(
        workload, report.samples, report.verification, report.recovery_s
    )
    assert report.rehearsal_sound is True
    assert (
        "Findings" in report.verdict
        and "saturation errors are only allowed codes" in report.verdict
    )
    assert report.qualified is False


def test_a_report_containing_a_token_is_not_written(tmp_path: Path) -> None:
    report = _report(load(EXAMPLE))
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJsb2FkIn0.c2lnbmF0dXJlLXNpZ25hdHVyZQ"
    report.secret(token)
    report.notes.append(f"a careless note with {token}")
    with pytest.raises(LeakError):
        report.write(tmp_path / "capacity.md")
    report.notes.clear()
    report.write(tmp_path / "capacity.md")
    payload = json.loads((tmp_path / "capacity.json").read_text(encoding="utf-8"))
    assert payload["qualified"] is False
    assert payload["workload"]["thresholds"]["status"] == "proposed"
