"""``python -m tests.capacity`` - the capacity run's entry point.

    validate <workload.json>                 check a workload file and print its plan
    baseline <workload.json>                 network baseline only, from this machine
    run <workload.json> [--report PATH]      measure against the configured API and targets
    rehearse [--scale N] [--report PATH]     a short run against a local stand-in API

Exit codes for ``run``: 0 when every criterion was met, 1 when one was not, 2 when the workload
is invalid or a prerequisite was missing before anything was measured. A ``run`` that
exits 0 may still not be a pilot qualification; the verdict says whether it is.

``rehearse`` exits 0 when every phase ran and every correctness check passed
(contamination, lost commits, leaked sessions, unknown outcomes, complete verification).
Its latency and error-code results are reported as findings and do not set the exit code:
they describe the stand-in and an in-process SQLite metadata store.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tests.capacity import rehearsal, scheduler
from tests.capacity.report import CapacityReport
from tests.capacity.runner import _http_rtt, execute
from tests.capacity.workload import Workload, WorkloadProblem, load


def _plan(workload: Workload) -> None:
    state = "AGREED" if workload.thresholds.agreed else "PROPOSED - not usable for qualification"
    print(
        f"Workload {workload.name!r}: {len(workload.users)} users, {len(workload.targets)} targets, seed {workload.seed}"
    )
    print(f"Thresholds: {state}")
    for name, start, end in scheduler.timeline(workload.phases):
        phase = workload.phase(name)
        count = len(scheduler.streams(workload, phase))
        print(
            f"  {name:<11} {start:>6.0f}s - {end:>6.0f}s  {count} streams, think x{phase.think_scale}"
        )


def _finish(report: CapacityReport, path: Path | None, *, rehearsal: bool = False) -> int:
    text = report.as_markdown()
    report.assert_clean(text)
    print(text)
    if path is not None:
        report.write(path)
        print(f"Report appended to {path}", file=sys.stderr)
    if report.prerequisite_failures:
        return 2
    if rehearsal:
        # Judged on correctness only; the latency and error findings stay in the report.
        return 0 if report.rehearsal_sound else 1
    return 1 if report.failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.capacity", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "baseline", "run"):
        command = commands.add_parser(name)
        command.add_argument("workload", type=Path)
        if name == "run":
            command.add_argument("--report", type=Path)
    rehearse = commands.add_parser("rehearse")
    rehearse.add_argument("--scale", type=float, default=1.0, help="multiply the phase durations")
    rehearse.add_argument("--report", type=Path)
    arguments = parser.parse_args(argv)

    if arguments.command == "rehearse":
        api = rehearsal.LocalApi(rehearsal.workdir())
        api.start()
        try:
            subjects, targets = rehearsal.provision(api.base_url)
            workload = rehearsal.rehearsal_workload(
                api.base_url, subjects, targets, arguments.scale
            )
            _plan(workload)
            report = execute(
                workload,
                rehearsal=True,
                on_phase=lambda name: print(f"phase {name}", file=sys.stderr),
            )
        finally:
            api.stop()
        report.notes.append(
            "Stand-in targets are SQLite files with no network endpoint, so their TCP baseline "
            "is unreachable by design, and the API, its databases and the load client share "
            "one process and one machine."
        )
        return _finish(report, arguments.report, rehearsal=True)

    try:
        workload = load(arguments.workload)
    except WorkloadProblem as problem:
        print(f"{arguments.workload} cannot be run:\n{problem}", file=sys.stderr)
        return 2
    if arguments.command == "validate":
        _plan(workload)
        return 0
    if arguments.command == "baseline":
        print(f"GET /healthz: {_http_rtt(workload.base_url)}")
        print("Database endpoints are listed by `run`, which reads them from the API.")
        return 0
    _plan(workload)
    report = execute(workload, on_phase=lambda name: print(f"phase {name}", file=sys.stderr))
    return _finish(report, arguments.report)


if __name__ == "__main__":
    sys.exit(main())
