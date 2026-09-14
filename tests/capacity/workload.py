"""The capacity run's configuration: what to run, for how long, within what, judged how.

Every section is required and every number is explicit. There are no defaults for a
duration, a limit or a threshold, because a default is a decision nobody made: a load run
judged against a number that was never agreed tells nobody anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KINDS = ("metadata", "boundedRead", "transaction", "cancellation")
PHASES = ("steady", "saturation", "recovery")
AUTH_KINDS = ("devToken", "tokenFiles")

#: Statements a target must supply. The runner binds ``:run_id``, ``:target_name``,
#: ``:user_name``, ``:seq``, and ``:lo``/``:hi`` for a page of sequence numbers; the marker
#: statements address the reviewed table in oracle/capacity/load_schema.sql, and
#: workload.example.json has the reference text for each.
REQUIRED_SQL = (
    "boundedRead",
    "slowRead",
    "insertMarker",
    "countMarker",
    "listMarkers",
    "foreignMarkers",
    "deleteMarkers",
)


class WorkloadProblem(Exception):
    """The configuration cannot be run. Lists every problem found, not just the first."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(f"- {problem}" for problem in problems))
        self.problems = problems


@dataclass(frozen=True)
class Target:
    name: str
    profile_id: str
    sql: dict[str, str]


@dataclass(frozen=True)
class User:
    subject: str


@dataclass(frozen=True)
class PhaseSpec:
    name: str
    seconds: float
    #: Parallel request streams per user, one per target at most.
    streams_per_user: int
    #: Multiplies the think time; 0 means back-to-back requests.
    think_scale: float


@dataclass(frozen=True)
class Thresholds:
    status: str
    agreed_by: str
    agreed_on: str
    reference: str
    latency_ms: dict[str, dict[str, float]]
    application_overhead_p95_ms: float
    steady_max_error_rate: float
    saturation_max_error_rate: float
    saturation_allowed_error_codes: tuple[str, ...]
    recovery_within_seconds: float
    max_outcome_unknown: int
    max_contaminations: int

    @property
    def agreed(self) -> bool:
        return self.status == "agreed"


@dataclass(frozen=True)
class ResourceLimits:
    api_cpus: float
    api_memory_mib: float
    api_worker_processes: int
    max_in_flight_requests: int
    request_timeout_seconds: float
    statement_deadline_seconds: float
    max_rows_per_read: int


@dataclass(frozen=True)
class Workload:
    name: str
    base_url: str
    auth_kind: str
    token_dir: str
    seed: int
    users: tuple[User, ...]
    targets: tuple[Target, ...]
    mix: dict[str, float]
    think_ms: tuple[float, float]
    cancel_after_ms: float
    phases: tuple[PhaseSpec, ...]
    limits: ResourceLimits
    thresholds: Thresholds
    cleanup: bool
    source: Path | None = field(default=None, compare=False)

    def phase(self, name: str) -> PhaseSpec:
        return next(phase for phase in self.phases if phase.name == name)


def _number(
    section: dict[str, Any],
    key: str,
    where: str,
    problems: list[str],
    *,
    minimum: float = 0,
    maximum: float | None = None,
) -> float:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        problems.append(f"{where}.{key} must be a number; it is {value!r}.")
        return 0.0
    if value < minimum:
        problems.append(f"{where}.{key} must be at least {minimum}; it is {value}.")
    if maximum is not None and value > maximum:
        problems.append(f"{where}.{key} must be at most {maximum}; it is {value}.")
    return float(value)


def _integer(
    section: dict[str, Any], key: str, where: str, problems: list[str], *, minimum: int = 0
) -> int:
    """Like ``_number``, but truncating a fractional value would run a different load than
    was declared - a ``streamsPerUser`` of 1.5 silently becoming 1 is not what was asked for.
    """

    value = section.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or (isinstance(value, float) and not value.is_integer())
    ):
        problems.append(f"{where}.{key} must be a whole number; it is {value!r}.")
        return 0
    whole = int(value)
    if whole < minimum:
        problems.append(f"{where}.{key} must be at least {minimum}; it is {whole}.")
    return whole


def _text(section: dict[str, Any], key: str, where: str, problems: list[str]) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where}.{key} must be a non-empty string.")
        return ""
    return value.strip()


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _section(document: dict[str, Any], key: str, problems: list[str]) -> dict[str, Any]:
    value = document.get(key)
    if not isinstance(value, dict):
        problems.append(f"{key} is required and must be an object.")
        return {}
    return value


def parse(document: dict[str, Any], source: Path | None = None) -> Workload:  # noqa: C901
    problems: list[str] = []
    if document.get("version") != 1:
        problems.append("version must be 1.")

    api = _section(document, "api", problems)
    base_url = _text(api, "baseUrl", "api", problems).rstrip("/")
    auth = _object(api.get("auth"))
    auth_kind = str(auth.get("kind", ""))
    if auth_kind not in AUTH_KINDS:
        problems.append(f"api.auth.kind must be one of {', '.join(AUTH_KINDS)}.")
    token_dir = str(auth.get("tokenDir", "")) if auth_kind == "tokenFiles" else ""
    if auth_kind == "tokenFiles" and not token_dir:
        problems.append(
            "api.auth.tokenDir is required for tokenFiles (one <subject>.token per user)."
        )

    users_raw = document.get("users")
    users: list[User] = []
    if not isinstance(users_raw, list) or not users_raw:
        problems.append("users must list the harness accounts, one per virtual user.")
    else:
        for index, entry in enumerate(users_raw):
            subject = entry.get("subject") if isinstance(entry, dict) else None
            if not isinstance(subject, str) or not subject:
                problems.append(f"users[{index}].subject is required.")
            else:
                users.append(User(subject))
        if len({user.subject for user in users}) != len(users):
            problems.append("users must be distinct: cross-user checks need separate accounts.")

    targets_raw = document.get("targets")
    targets: list[Target] = []
    if not isinstance(targets_raw, list) or not targets_raw:
        problems.append("targets must list the registered targets to load.")
    else:
        for index, entry in enumerate(targets_raw):
            where = f"targets[{index}]"
            if not isinstance(entry, dict):
                problems.append(f"{where} must be an object.")
                continue
            sql = _object(entry.get("sql"))
            missing = [
                key for key in REQUIRED_SQL if not isinstance(sql.get(key), str) or not sql[key]
            ]
            if missing:
                problems.append(f"{where}.sql is missing {', '.join(missing)}.")
            targets.append(
                Target(
                    _text(entry, "name", where, problems),
                    _text(entry, "profileId", where, problems),
                    dict(sql),
                )
            )
        if len({target.profile_id for target in targets}) != len(targets):
            problems.append("targets must be distinct profiles.")
        if len({target.name for target in targets}) != len(targets):
            problems.append(
                "targets must have distinct names: the name keys every session, stream and "
                "report identity, and a duplicate silently overwrites another target's entry."
            )

    workload = _section(document, "workload", problems)
    mix_raw = _object(workload.get("mix"))
    mix = {}
    for kind, weight in mix_raw.items():
        if kind not in KINDS:
            problems.append(f"workload.mix.{kind} is not a known kind ({', '.join(KINDS)}).")
        elif isinstance(weight, bool) or not isinstance(weight, int | float) or weight < 0:
            problems.append(f"workload.mix.{kind} must be a non-negative weight.")
        else:
            mix[kind] = float(weight)
    if not mix or sum(mix.values()) <= 0:
        problems.append("workload.mix must give at least one kind a positive weight.")
    for kind in KINDS:
        if kind not in mix:
            problems.append(f"workload.mix.{kind} must be stated, even if 0.")
    think = _object(workload.get("thinkTimeMs"))
    think_min = _number(think, "min", "workload.thinkTimeMs", problems)
    think_max = _number(think, "max", "workload.thinkTimeMs", problems)
    if think_max < think_min:
        problems.append("workload.thinkTimeMs.max must not be below min.")
    cancel_after = _number(workload, "cancelAfterMs", "workload", problems, minimum=1)
    seed = workload.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        problems.append("workload.seed must be an integer, so a run's schedule can be reproduced.")
        seed = 0

    phases_raw = _section(document, "phases", problems)
    phases: list[PhaseSpec] = []
    for name in PHASES:
        where = f"phases.{name}"
        spec = phases_raw.get(name)
        if not isinstance(spec, dict):
            problems.append(f"{where} is required.")
            continue
        streams = _integer(spec, "streamsPerUser", where, problems, minimum=1)
        if targets and streams > len(targets):
            problems.append(f"{where}.streamsPerUser cannot exceed the {len(targets)} target(s).")
        phases.append(
            PhaseSpec(
                name,
                _number(spec, "seconds", where, problems, minimum=1),
                streams,
                _number(spec, "thinkTimeScale", where, problems),
            )
        )

    limits_raw = _section(document, "resourceLimits", problems)
    limits = ResourceLimits(
        api_cpus=_number(limits_raw, "apiCpus", "resourceLimits", problems, minimum=0.1),
        api_memory_mib=_number(limits_raw, "apiMemoryMiB", "resourceLimits", problems, minimum=64),
        api_worker_processes=_integer(
            limits_raw, "apiWorkerProcesses", "resourceLimits", problems, minimum=1
        ),
        max_in_flight_requests=_integer(
            limits_raw, "maxInFlightRequests", "resourceLimits", problems, minimum=1
        ),
        request_timeout_seconds=_number(
            limits_raw, "requestTimeoutSeconds", "resourceLimits", problems, minimum=1
        ),
        statement_deadline_seconds=_number(
            limits_raw, "statementDeadlineSeconds", "resourceLimits", problems, minimum=1
        ),
        max_rows_per_read=_integer(
            limits_raw, "maxRowsPerRead", "resourceLimits", problems, minimum=1
        ),
    )

    raw = _section(document, "thresholds", problems)
    status = raw.get("status")
    if status not in ("proposed", "agreed"):
        problems.append("thresholds.status must be 'proposed' or 'agreed'.")
    agreed_by = str(raw.get("agreedBy", "") or "")
    agreed_on = str(raw.get("agreedOn", "") or "")
    reference = str(raw.get("reference", "") or "")
    if status == "agreed" and not (agreed_by and agreed_on and reference):
        problems.append(
            "thresholds.status is 'agreed' but agreedBy, agreedOn and reference are not all "
            "recorded. Thresholds are proposals until someone is on record agreeing them."
        )
    latency_raw = _object(raw.get("latencyMs"))
    latency: dict[str, dict[str, float]] = {}
    for kind in KINDS:
        if mix.get(kind, 0) <= 0:
            continue
        spec = latency_raw.get(kind)
        if not isinstance(spec, dict):
            problems.append(f"thresholds.latencyMs.{kind} is required: that kind is in the mix.")
            continue
        latency[kind] = {
            "p50": _number(spec, "p50", f"thresholds.latencyMs.{kind}", problems, minimum=1),
            "p95": _number(spec, "p95", f"thresholds.latencyMs.{kind}", problems, minimum=1),
        }
    saturation = _object(raw.get("saturation"))
    codes = saturation.get("allowedErrorCodes")
    if not isinstance(codes, list) or not all(isinstance(code, str) for code in codes):
        problems.append(
            "thresholds.saturation.allowedErrorCodes must be a list of error codes (may be empty)."
        )
        codes = []
    if "outcome_unknown" in codes:
        problems.append(
            "outcome_unknown can never be an allowed error: it is a write nobody can vouch for."
        )
    thresholds = Thresholds(
        status=str(status),
        agreed_by=agreed_by,
        agreed_on=agreed_on,
        reference=reference,
        latency_ms=latency,
        application_overhead_p95_ms=_number(
            raw, "applicationOverheadP95Ms", "thresholds", problems, minimum=1
        ),
        steady_max_error_rate=_number(raw, "steadyMaxErrorRate", "thresholds", problems, maximum=1),
        saturation_max_error_rate=_number(
            saturation, "maxErrorRate", "thresholds.saturation", problems, maximum=1
        ),
        saturation_allowed_error_codes=tuple(codes),
        recovery_within_seconds=_number(
            raw, "recoveryWithinSeconds", "thresholds", problems, minimum=1
        ),
        max_outcome_unknown=_integer(raw, "maxOutcomeUnknown", "thresholds", problems),
        max_contaminations=_integer(raw, "maxContaminations", "thresholds", problems),
    )
    if thresholds.max_contaminations != 0:
        problems.append(
            "thresholds.maxContaminations must be 0: contamination is never an acceptable amount."
        )

    cleanup = document.get("cleanup")
    if not isinstance(cleanup, bool):
        problems.append(
            "cleanup must be true or false: whether the run deletes its markers afterwards."
        )
        cleanup = True

    if problems:
        raise WorkloadProblem(problems)
    return Workload(
        name=_text(document, "name", "", []) or "unnamed",
        base_url=base_url,
        auth_kind=auth_kind,
        token_dir=token_dir,
        seed=int(seed),
        users=tuple(users),
        targets=tuple(targets),
        mix=mix,
        think_ms=(think_min, think_max),
        cancel_after_ms=cancel_after,
        phases=tuple(phases),
        limits=limits,
        thresholds=thresholds,
        cleanup=cleanup,
        source=source,
    )


def load(path: Path) -> Workload:
    try:
        # utf-8-sig: a workload saved by a Windows editor may start with a byte-order mark.
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise WorkloadProblem([f"{path} could not be read as JSON: {exc}"]) from exc
    if not isinstance(document, dict):
        raise WorkloadProblem([f"{path} must contain a JSON object."])
    return parse(document, path)
