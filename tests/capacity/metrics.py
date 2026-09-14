"""Samples in, summaries and verdicts out. No I/O, no clock: all of it is unit-testable.

Three durations are kept for each request where the API reports them, because a latency
figure that mixes them cannot say where the time went:

* ``client_ms`` - wall time around the HTTP call, as a user would see it.
* ``server_ms`` - the execution's own elapsed time: waiting for an execution slot plus
  the driver call.
* ``db_ms`` - the driver call alone.

Application overhead is ``client_ms - db_ms``: network, authentication, policy, metadata
store writes and slot queueing. Metadata panels report no execution timings, so for them
only ``client_ms`` exists and overhead is not measurable.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from tests.capacity.workload import KINDS, Workload


@dataclass
class Sample:
    phase: str
    kind: str
    user: str
    target: str
    started_s: float
    client_ms: float
    ok: bool
    code: str = ""
    server_ms: float | None = None
    db_ms: float | None = None
    note: str = ""


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile. ``None`` for no values, rather than a misleading 0."""

    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


@dataclass
class KindSummary:
    kind: str
    count: int
    errors: int
    error_codes: dict[str, int]
    client_p50: float | None
    client_p95: float | None
    client_p99: float | None
    server_p95: float | None
    db_p50: float | None
    db_p95: float | None
    overhead_p95: float | None

    @property
    def error_rate(self) -> float:
        return self.errors / self.count if self.count else 0.0


def summarize(samples: list[Sample], phase: str) -> dict[str, KindSummary]:
    summaries = {}
    for kind in KINDS:
        chosen = [s for s in samples if s.phase == phase and s.kind == kind]
        if not chosen:
            continue
        succeeded = [s for s in chosen if s.ok]
        client = [s.client_ms for s in succeeded]
        server = [s.server_ms for s in succeeded if s.server_ms is not None]
        database = [s.db_ms for s in succeeded if s.db_ms is not None]
        overhead = [s.client_ms - s.db_ms for s in succeeded if s.db_ms is not None]
        summaries[kind] = KindSummary(
            kind=kind,
            count=len(chosen),
            errors=len(chosen) - len(succeeded),
            error_codes=dict(Counter(s.code or "unknown" for s in chosen if not s.ok)),
            client_p50=percentile(client, 0.50),
            client_p95=percentile(client, 0.95),
            client_p99=percentile(client, 0.99),
            server_p95=percentile(server, 0.95),
            db_p50=percentile(database, 0.50),
            db_p95=percentile(database, 0.95),
            overhead_p95=percentile(overhead, 0.95),
        )
    return summaries


def error_rate(samples: list[Sample], phase: str) -> float:
    chosen = [s for s in samples if s.phase == phase]
    return sum(1 for s in chosen if not s.ok) / len(chosen) if chosen else 0.0


def recovered_after(
    samples: list[Sample],
    workload: Workload,
    recovery_start_s: float,
    recovery_end_s: float,
    window_s: float = 10.0,
    step_s: float = 5.0,
) -> float | None:
    """Seconds into the recovery phase after which every window meets the steady thresholds.

    A window meets them when each kind's p95 is within its steady p95 threshold and the
    error rate is within the steady error rate. ``None`` means it never recovered within
    the phase - including a phase too short to hold a single window.
    """

    thresholds = workload.thresholds
    starts = []
    offset = recovery_start_s
    while offset + window_s <= recovery_end_s + 1e-9:
        starts.append(offset)
        offset += step_s
    if not starts:
        return None

    def window_ok(start: float) -> bool:
        chosen = [
            s for s in samples if s.phase == "recovery" and start <= s.started_s < start + window_s
        ]
        if not chosen:
            return False
        if sum(1 for s in chosen if not s.ok) / len(chosen) > thresholds.steady_max_error_rate:
            return False
        for kind, limit in thresholds.latency_ms.items():
            p95 = percentile([s.client_ms for s in chosen if s.kind == kind and s.ok], 0.95)
            # No successful sample for a thresholded kind is not evidence it recovered -
            # it is evidence this window cannot vouch for that kind at all.
            if p95 is None or p95 > limit["p95"]:
                return False
        return True

    results = [window_ok(start) for start in starts]
    for index, start in enumerate(starts):
        if all(results[index:]):
            return start - recovery_start_s
    return None


@dataclass
class Criterion:
    name: str
    threshold: str
    measured: str
    passed: bool


@dataclass
class Verification:
    """What the post-run checks found. Every list is a defect when non-empty."""

    contaminations: list[str] = field(default_factory=list)
    lost_commits: list[str] = field(default_factory=list)
    leaked_sessions: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)
    committed: int = 0
    rolled_back: int = 0
    unknown: int = 0
    probes: int = 0


def evaluate(
    workload: Workload,
    samples: list[Sample],
    verification: Verification,
    recovery_s: float | None,
) -> list[Criterion]:
    thresholds = workload.thresholds
    criteria: list[Criterion] = []
    steady = summarize(samples, "steady")

    def fmt(value: float | None) -> str:
        return "not measured" if value is None else f"{value:.0f} ms"

    for kind, limit in thresholds.latency_ms.items():
        summary = steady.get(kind)
        for key, measured in (
            ("p50", summary.client_p50 if summary else None),
            ("p95", summary.client_p95 if summary else None),
        ):
            criteria.append(
                Criterion(
                    f"steady {kind} {key}",
                    f"<= {limit[key]:.0f} ms",
                    fmt(measured),
                    measured is not None and measured <= limit[key],
                )
            )
    overhead = percentile(
        [
            s.client_ms - s.db_ms
            for s in samples
            if s.phase == "steady" and s.ok and s.db_ms is not None
        ],
        0.95,
    )
    criteria.append(
        Criterion(
            "steady application overhead p95 (client - database)",
            f"<= {thresholds.application_overhead_p95_ms:.0f} ms",
            fmt(overhead),
            overhead is not None and overhead <= thresholds.application_overhead_p95_ms,
        )
    )
    steady_errors = error_rate(samples, "steady")
    criteria.append(
        Criterion(
            "steady error rate",
            f"<= {thresholds.steady_max_error_rate:.2%}",
            f"{steady_errors:.2%}",
            steady_errors <= thresholds.steady_max_error_rate,
        )
    )
    saturation_errors = error_rate(samples, "saturation")
    unexpected = sorted(
        {
            s.code or "unknown"
            for s in samples
            if s.phase == "saturation"
            and not s.ok
            and s.code not in thresholds.saturation_allowed_error_codes
        }
    )
    criteria.append(
        Criterion(
            "saturation error rate",
            f"<= {thresholds.saturation_max_error_rate:.2%}",
            f"{saturation_errors:.2%}",
            saturation_errors <= thresholds.saturation_max_error_rate,
        )
    )
    criteria.append(
        Criterion(
            "saturation errors are only allowed codes",
            ", ".join(thresholds.saturation_allowed_error_codes) or "none allowed",
            ", ".join(unexpected) or "none unexpected",
            not unexpected,
        )
    )
    criteria.append(
        Criterion(
            "recovery after saturation",
            f"<= {thresholds.recovery_within_seconds:.0f} s",
            "did not recover" if recovery_s is None else f"{recovery_s:.0f} s",
            recovery_s is not None and recovery_s <= thresholds.recovery_within_seconds,
        )
    )
    unknown = sum(1 for s in samples if s.code == "outcome_unknown")
    criteria.append(
        Criterion(
            "outcome_unknown results",
            f"<= {thresholds.max_outcome_unknown}",
            str(unknown),
            unknown <= thresholds.max_outcome_unknown,
        )
    )
    criteria.append(
        Criterion(
            "cross-user and cross-target contamination",
            "0",
            str(len(verification.contaminations)),
            not verification.contaminations,
        )
    )
    criteria.append(
        Criterion(
            "committed work lost",
            "0",
            str(len(verification.lost_commits)),
            not verification.lost_commits,
        )
    )
    criteria.append(
        Criterion(
            "worksheet sessions left open after the run",
            "0",
            str(len(verification.leaked_sessions)),
            not verification.leaked_sessions,
        )
    )
    criteria.append(
        Criterion(
            "verification complete",
            "every check ran to completion",
            "; ".join(verification.incomplete) or "complete",
            not verification.incomplete,
        )
    )
    return criteria


@dataclass
class Eligibility:
    """Why a run could not be a pilot qualification, independently of how it measured."""

    reasons: list[str]

    @property
    def eligible(self) -> bool:
        return not self.reasons


def eligibility(
    workload: Workload,
    environment: dict[str, Any],
    identities: dict[str, dict[str, Any]],
    *,
    rehearsal: bool,
    completed_phases: list[str],
) -> Eligibility:
    reasons = []
    if rehearsal:
        reasons.append("this is a rehearsal against a local API")
    backend = environment.get("oracleBackend")
    if backend != "oracledb":
        reasons.append(f"the API's Oracle backend is `{backend}`, not `oracledb`")
    if len(workload.targets) < 3:
        reasons.append(f"{len(workload.targets)} target(s); the pilot target is three databases")
    keys = {
        (
            str(identity.get("hostName", "")).lower(),
            str(identity.get("databaseName", "")).lower(),
            str(identity.get("containerName") or "").lower(),
        )
        for identity in identities.values()
        if identity
    }
    if (
        len(keys) < 3
        or len(identities) < len(workload.targets)
        or any(not i for i in identities.values())
    ):
        reasons.append(
            f"only {len(keys)} distinct database identit(ies) among the targets; three "
            "profiles on one database are not three databases"
        )
    if len(workload.users) < 10:
        reasons.append(f"{len(workload.users)} user(s); the pilot target is ten")
    if not workload.thresholds.agreed:
        reasons.append("the thresholds are proposed, not agreed")
    missing = [phase.name for phase in workload.phases if phase.name not in completed_phases]
    if missing:
        reasons.append(f"phase(s) did not complete: {', '.join(missing)}")
    return Eligibility(reasons)
