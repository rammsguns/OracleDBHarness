"""Who runs what, against which target, and when. Pure and reproducible.

Every request stream has its own random generator, seeded from the workload seed and the
stream's identity. Two runs of one workload therefore choose the same sequence of kinds,
think times and commit-or-rollback decisions per stream, whatever the thread timing, which
is what lets a regression between two runs be attributed to the system rather than to a
different workload.
"""

from __future__ import annotations

import random
import threading
from collections.abc import Iterator
from dataclasses import dataclass

from tests.capacity.workload import KINDS, PhaseSpec, Workload

#: Of transactions, how many end in a rollback rather than a commit. Rolled-back markers
#: are what the verification uses to show uncommitted work never became visible.
ROLLBACK_SHARE = 0.2


@dataclass(frozen=True)
class Stream:
    """One sequential request stream: a user working on one target."""

    user: str
    target: str


@dataclass(frozen=True)
class Step:
    kind: str
    think_ms: float
    commit: bool


def streams(workload: Workload, phase: PhaseSpec) -> list[Stream]:
    """Assign each user ``streams_per_user`` targets, rotating so load stays balanced.

    User *i* starts at target *i mod n*, so with ten users and three targets every target
    gets its share, and in a one-stream phase no target is left idle.
    """

    names = [target.name for target in workload.targets]
    count = len(names)
    return [
        Stream(user.subject, names[(index + offset) % count])
        for index, user in enumerate(workload.users)
        for offset in range(phase.streams_per_user)
    ]


def steps(workload: Workload, phase: PhaseSpec, stream: Stream) -> Iterator[Step]:
    """The endless, reproducible sequence of steps one stream takes in one phase."""

    rng = random.Random(f"{workload.seed}:{phase.name}:{stream.user}:{stream.target}")  # noqa: S311
    kinds = [kind for kind in KINDS if workload.mix.get(kind, 0) > 0]
    weights = [workload.mix[kind] for kind in kinds]
    low, high = workload.think_ms
    while True:
        kind = rng.choices(kinds, weights)[0]
        think = rng.uniform(low, high) * phase.think_scale
        commit = rng.random() >= ROLLBACK_SHARE
        yield Step(kind, think, commit)


def timeline(phases: tuple[PhaseSpec, ...]) -> list[tuple[str, float, float]]:
    """Each phase's start and end, in seconds from the start of the measured run."""

    offset = 0.0
    spans = []
    for phase in phases:
        spans.append((phase.name, offset, offset + phase.seconds))
        offset += phase.seconds
    return spans


class Gate:
    """Bounds how many requests are in flight at once, and remembers the most it saw.

    The bound is the configured ``maxInFlightRequests``: the run never exceeds the load it
    declared, so a saturation result cannot be blamed on the client over-driving.
    """

    def __init__(self, limit: int) -> None:
        self._semaphore = threading.BoundedSemaphore(limit)
        self._lock = threading.Lock()
        self._current = 0
        self.peak = 0

    def __enter__(self) -> Gate:
        self._semaphore.acquire()
        with self._lock:
            self._current += 1
            self.peak = max(self.peak, self._current)
        return self

    def __exit__(self, *_: object) -> None:
        with self._lock:
            self._current -= 1
        self._semaphore.release()


class Sequence:
    """Marker sequence numbers, unique per user across every target and phase."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next: dict[str, int] = {}

    def take(self, user: str) -> int:
        with self._lock:
            value = self._next.get(user, 0) + 1
            self._next[user] = value
            return value

    def highest(self, user: str) -> int:
        with self._lock:
            return self._next.get(user, 0)
