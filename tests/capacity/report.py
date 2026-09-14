"""The capacity report: what ran, on what, what it measured, and what that allows saying.

The verdict has three possible heads, and only one of them is a qualification:

* ``FAILED`` - a criterion was not met, or verification found a defect.
* ``NOT A PILOT QUALIFICATION`` - the measurements may all be within the thresholds, but
  the run was a rehearsal, on the stand-in, on fewer than three databases or ten users,
  against proposed thresholds, or cut short. The reasons are listed.
* ``PASSED`` - every criterion met, on real targets, against agreed thresholds.

A rehearsal has its own two heads, ``REHEARSAL SOUND`` and ``REHEARSAL FAILED``, decided by
the correctness criteria alone; neither is a qualification.
"""

from __future__ import annotations

import datetime as dt
import json
import platform
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tests.capacity.metrics import Criterion, Eligibility, Sample, Verification, summarize
from tests.capacity.workload import Workload

_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")

LIMITATIONS = [
    "Network baseline is measured from the load client. Run `python -m tests.capacity baseline` "
    "on the API host too if the client is elsewhere; the API-to-database round trip is what "
    "database time includes.",
    "Database time is the driver call for worksheet statements. Metadata panels report no "
    "execution timings, so their overhead is not separated.",
    "API host CPU and memory are the declared resource limits, not measured by this runner. "
    "Record host metrics alongside the run.",
    "Copilot requests are not part of the workload: provider evaluation (NP-04) has not run "
    "and has no spend allowance.",
    "One load client. Its own CPU and network limits bound the load it can offer.",
]


class LeakError(AssertionError):
    """The report text contains a token."""


def _commit() -> str:
    try:
        result = subprocess.run(  # noqa: S603 - fixed argument list
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


@dataclass
class CapacityReport:
    workload: Workload
    rehearsal: bool
    started: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    commit: str = field(default_factory=_commit)
    run_id: str = ""
    environment: dict[str, Any] = field(default_factory=dict)
    identities: dict[str, dict[str, Any]] = field(default_factory=dict)
    network: dict[str, Any] = field(default_factory=dict)
    samples: list[Sample] = field(default_factory=list)
    completed_phases: list[str] = field(default_factory=list)
    peak_in_flight: int = 0
    recovery_s: float | None = None
    verification: Verification = field(default_factory=Verification)
    criteria: list[Criterion] = field(default_factory=list)
    eligibility: Eligibility = field(default_factory=lambda: Eligibility([]))
    prerequisite_failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    _secrets: set[str] = field(default_factory=set, repr=False)

    def secret(self, value: str) -> None:
        if value and len(value) >= 8:
            self._secrets.add(value)

    #: The criteria about correctness rather than speed. A rehearsal is judged on these
    #: alone: its latencies and error codes describe the stand-in and an in-process SQLite
    #: metadata store, which is not what any threshold is about.
    CORRECTNESS = (
        "outcome_unknown results",
        "cross-user and cross-target contamination",
        "committed work lost",
        "worksheet sessions left open after the run",
        "verification complete",
    )

    @property
    def rehearsal_sound(self) -> bool:
        """Whether a rehearsal exercised everything and found nothing wrong with correctness."""

        complete = len(self.completed_phases) == len(self.workload.phases)
        correct = all(c.passed for c in self.criteria if c.name in self.CORRECTNESS)
        return not self.prerequisite_failures and complete and bool(self.criteria) and correct

    @property
    def failed(self) -> bool:
        return bool(self.prerequisite_failures) or any(not c.passed for c in self.criteria)

    @property
    def qualified(self) -> bool:
        return not self.failed and bool(self.criteria) and self.eligibility.eligible

    @property
    def verdict(self) -> str:
        if self.prerequisite_failures:
            return "FAILED - prerequisites missing before measurement: " + "; ".join(
                self.prerequisite_failures
            )
        failed = [c.name for c in self.criteria if not c.passed]
        if self.rehearsal:
            findings = [name for name in failed if name not in self.CORRECTNESS]
            if not self.rehearsal_sound:
                broken = [name for name in failed if name in self.CORRECTNESS]
                return "REHEARSAL FAILED - " + (", ".join(broken) or "not every phase completed")
            return (
                "REHEARSAL SOUND - every phase ran and every correctness check passed; not a "
                "pilot qualification"
                + (
                    f". Findings against its proposed thresholds: {', '.join(findings)}"
                    if findings
                    else ""
                )
            )
        if failed:
            return f"FAILED - {len(failed)} criterion(s) not met: {', '.join(failed)}"
        if not self.eligibility.eligible:
            return "NOT A PILOT QUALIFICATION - " + "; ".join(self.eligibility.reasons)
        t = self.workload.thresholds
        return f"PASSED - pilot capacity, thresholds agreed by {t.agreed_by} on {t.agreed_on} ({t.reference})"

    def error_examples(self, phase: str) -> dict[str, str]:
        """The first message seen for each error code in a phase, to say what the code meant."""

        examples: dict[str, str] = {}
        for sample in self.samples:
            if sample.phase == phase and not sample.ok and sample.code not in examples:
                examples[sample.code or "unknown"] = sample.note or "(no message)"
        return examples

    # -- rendering ---------------------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        workload = self.workload
        return {
            "kind": "capacity-run",
            "verdict": self.verdict,
            "qualified": self.qualified,
            "rehearsal": self.rehearsal,
            "runId": self.run_id,
            "started": self.started.isoformat(),
            "harnessCommit": self.commit,
            "client": {"platform": platform.platform(), "python": platform.python_version()},
            "workload": {
                "name": workload.name,
                "source": str(workload.source) if workload.source else None,
                "seed": workload.seed,
                "users": len(workload.users),
                "targets": [t.name for t in workload.targets],
                "mix": workload.mix,
                "thinkTimeMs": list(workload.think_ms),
                "phases": [asdict(phase) for phase in workload.phases],
                "resourceLimits": asdict(workload.limits),
                "thresholds": asdict(workload.thresholds),
            },
            "environment": self.environment,
            "targetIdentities": self.identities,
            "networkBaseline": self.network,
            "completedPhases": self.completed_phases,
            "peakInFlight": self.peak_in_flight,
            "phases": {
                phase.name: {
                    "kinds": {
                        kind: asdict(summary)
                        for kind, summary in summarize(self.samples, phase.name).items()
                    },
                    "errorExamples": self.error_examples(phase.name),
                }
                for phase in workload.phases
            },
            "recoverySeconds": self.recovery_s,
            "verification": asdict(self.verification),
            "criteria": [asdict(c) for c in self.criteria],
            "notQualificationBecause": self.eligibility.reasons,
            "prerequisiteFailures": self.prerequisite_failures,
            "notes": self.notes,
            "limitations": LIMITATIONS,
            "samples": len(self.samples),
        }

    def as_markdown(self) -> str:
        workload = self.workload
        t = workload.thresholds
        label = "PROPOSED" if not t.agreed else f"agreed by {t.agreed_by} on {t.agreed_on}"
        lines = [
            f"### Capacity run {self.started:%Y-%m-%d %H:%M} UTC{' (rehearsal)' if self.rehearsal else ''}",
            "",
            f"**{self.verdict}**",
            "",
            f"- Harness commit: `{self.commit}`; run id `{self.run_id}`",
            f"- Workload: `{workload.name}`, seed {workload.seed}, {len(workload.users)} users, "
            f"{len(workload.targets)} targets, mix {workload.mix}",
            f"- API: {self.environment.get('version', '?')}, environment `{self.environment.get('environment', '?')}`, "
            f"backend `{self.environment.get('oracleBackend', '?')}` ({self.environment.get('oracleDriverMode', '?')}), "
            f"metadata schema {self.environment.get('metadataSchemaVersion', '?')}",
            f"- Declared resource limits: {asdict(workload.limits)}",
            f"- Thresholds: **{label}**",
            f"- Client: {platform.platform()}, Python {platform.python_version()}; peak in-flight requests {self.peak_in_flight}",
            "",
            "Targets, as they identified themselves:",
            "",
        ]
        for name, identity in self.identities.items():
            lines.append(
                f"- {name}: database `{identity.get('databaseName')}`, container "
                f"`{identity.get('containerName')}`, host `{identity.get('hostName')}`, version "
                f"`{identity.get('versionFull') or identity.get('version')}`"
            )
        lines += ["", "Network baseline (median of samples, from the load client):", ""]
        for name, value in self.network.items():
            lines.append(f"- {name}: {value}")
        for phase in workload.phases:
            summaries = summarize(self.samples, phase.name)
            done = "" if phase.name in self.completed_phases else " - **did not complete**"
            lines += [
                "",
                f"#### {phase.name}: {phase.seconds:.0f}s, {phase.streams_per_user} stream(s) per user, "
                f"think time x{phase.think_scale}{done}",
                "",
                "| Kind | Requests | Errors | Client p50 / p95 / p99 ms | Server p95 ms | Database p50 / p95 ms | Overhead p95 ms | Error codes |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
            for kind, s in summaries.items():
                lines.append(
                    f"| {kind} | {s.count} | {s.errors} ({s.error_rate:.1%}) | {_ms(s.client_p50)} / "
                    f"{_ms(s.client_p95)} / {_ms(s.client_p99)} | {_ms(s.server_p95)} | {_ms(s.db_p50)} / "
                    f"{_ms(s.db_p95)} | {_ms(s.overhead_p95)} | {s.error_codes or ''} |"
                )
            examples = self.error_examples(phase.name)
            if examples:
                lines += ["", "Example error per code:", ""]
                lines += [f"- `{code}`: {message}" for code, message in examples.items()]
        v = self.verification
        lines += [
            "",
            f"Recovery: {'did not recover within the phase' if self.recovery_s is None else f'{self.recovery_s:.0f}s after load dropped'}",
            "",
            f"Verification: {v.committed} committed, {v.rolled_back} rolled back, {v.unknown} unknown "
            f"markers; {v.probes} cross-user probes; {len(v.contaminations)} contamination(s), "
            f"{len(v.lost_commits)} lost commit(s), {len(v.leaked_sessions)} leaked session(s).",
        ]
        for heading, items in (
            ("Contamination", v.contaminations),
            ("Lost commits", v.lost_commits),
            ("Leaked sessions", v.leaked_sessions),
            ("Incomplete verification", v.incomplete),
        ):
            if items:
                lines += ["", f"{heading}:", ""] + [f"- {item}" for item in items[:50]]
        lines += [
            "",
            f"| Criterion ({label} thresholds) | Threshold | Measured | Met |",
            "| --- | --- | --- | --- |",
        ]
        for c in self.criteria:
            lines.append(
                f"| {c.name} | {c.threshold} | {c.measured} | {'yes' if c.passed else '**no**'} |"
            )
        if self.eligibility.reasons:
            lines += ["", "Why this is not a pilot qualification:", ""]
            lines += [f"- {reason}" for reason in self.eligibility.reasons]
        if self.notes:
            lines += ["", "Notes:", ""] + [f"- {note}" for note in self.notes]
        lines += ["", "Limitations:", ""] + [f"- {item}" for item in LIMITATIONS] + [""]
        return "\n".join(lines)

    def assert_clean(self, text: str) -> None:
        if any(secret in text for secret in self._secrets) or _JWT.search(text):
            raise LeakError("The capacity report contains a bearer token.")

    def write(self, path: Path) -> None:
        markdown = self.as_markdown()
        payload = json.dumps(self.as_dict(), indent=2, default=str)
        self.assert_clean(markdown)
        self.assert_clean(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
        path.with_suffix(".json").write_text(payload, encoding="utf-8")


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}"
