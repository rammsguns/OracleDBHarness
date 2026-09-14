"""Driving the API with many virtual users, and checking nothing crossed over.

Each user holds one worksheet session per target it works on, as a person in the console
would, and a separate probe session used only to look for other users' uncommitted work.
Transactions write marker rows naming the run, the target they were meant for, the user
and a per-user sequence number. That makes every contamination check a comparison between
what the client knows it did and what each database holds:

* a marker naming another target, found on this one - a request routed to the wrong database;
* a rolled-back or never-committed marker present - uncommitted work that became visible,
  or a write that ran twice;
* a committed marker missing - a write reported as committed that is not durable;
* another user's uncommitted marker visible from a separate session - broken isolation;
* another user's session or execution record reachable - broken ownership.
"""

from __future__ import annotations

import socket
import statistics
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from tests.capacity import metrics, scheduler
from tests.capacity.metrics import Sample, Verification
from tests.capacity.report import CapacityReport
from tests.capacity.workload import PhaseSpec, Target, Workload

#: One cross-user probe per this many transactions.
PROBE_EVERY = 4
#: Sequence numbers per verification page, kept well under any row limit.
PAGE = 200


class Prerequisite(Exception):
    """Something the run needs before it can measure anything."""


# -- one user's view of the API -----------------------------------------------------------


@dataclass
class Response:
    status: int
    body: Any
    client_ms: float

    @property
    def code(self) -> str:
        if isinstance(self.body, dict):
            error = self.body.get("error")
            if isinstance(error, dict):
                return str(error.get("code", ""))
            outcome = self.body.get("outcome")
            if isinstance(outcome, dict) and outcome.get("state") not in (None, "succeeded"):
                inner = outcome.get("error") or {}
                return str(inner.get("code") or outcome.get("state"))
        return "" if self.status < 400 else f"http_{self.status}"

    @property
    def outcome(self) -> dict[str, Any]:
        body = self.body if isinstance(self.body, dict) else {}
        outcome = body.get("outcome")
        return outcome if isinstance(outcome, dict) else {}

    @property
    def message(self) -> str:
        """The API's own error message, short. Never a request body or a token."""

        body = self.body if isinstance(self.body, dict) else {}
        error = body.get("error") if isinstance(body.get("error"), dict) else None
        if error is None:
            error = self.outcome.get("error") if isinstance(self.outcome.get("error"), dict) else {}
        return str((error or {}).get("message", ""))[:200]


class UserClient:
    def __init__(self, workload: Workload, subject: str, report: CapacityReport) -> None:
        self.workload = workload
        self.subject = subject
        self.report = report
        self.http = httpx.Client(
            base_url=workload.base_url, timeout=workload.limits.request_timeout_seconds
        )
        self._token = ""
        self._token_lock = threading.Lock()
        self.sessions: dict[str, str] = {}
        self.probe_sessions: dict[str, str] = {}
        self.probe_lock = threading.Lock()
        self.execution_ids: list[str] = []
        self.sessions_replaced = 0

    # -- credentials -------------------------------------------------------------------

    def refresh_token(self) -> None:
        workload = self.workload
        if workload.auth_kind == "devToken":
            issued = self.http.post(
                "/api/v1/auth/dev-token", json={"subject": self.subject, "roles": []}
            )
            if issued.status_code != 200:
                raise Prerequisite(
                    f"No development token for {self.subject} ({issued.status_code}). devToken "
                    "auth needs HARNESS_AUTH_MODE=dev on a disposable load deployment."
                )
            token = str(issued.json()["accessToken"])
        else:
            path = Path(workload.token_dir) / f"{self.subject}.token"
            if not path.is_file():
                raise Prerequisite(f"No token file for {self.subject} at {path}.")
            token = path.read_text(encoding="utf-8").strip()
        self.report.secret(token)
        with self._token_lock:
            self._token = token

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Response:
        for attempt in (1, 2):
            started = time.perf_counter()
            try:
                answer = self.http.request(
                    method, path, json=body, headers={"Authorization": f"Bearer {self._token}"}
                )
            except httpx.HTTPError as exc:
                return Response(
                    0, {"error": {"code": f"transport_{type(exc).__name__}"}}, _ms(started)
                )
            elapsed = _ms(started)
            if answer.status_code == 401 and attempt == 1:
                # A token file an external refresher has renewed, or an expired dev token.
                self.refresh_token()
                continue
            try:
                parsed = answer.json()
            except ValueError:
                parsed = None
            return Response(answer.status_code, parsed, elapsed)
        raise AssertionError("unreachable")

    # -- sessions ----------------------------------------------------------------------

    def _open(self, target: Target) -> str:
        opened = self.request("POST", "/api/v1/worksheets", {"profileId": target.profile_id})
        if opened.status != 201:
            raise SessionUnavailable(opened)
        return str(opened.body["session"]["sessionId"])

    def session(self, target: Target) -> str:
        if target.name not in self.sessions:
            self.sessions[target.name] = self._open(target)
        return self.sessions[target.name]

    def probe_session(self, target: Target) -> str:
        if target.name not in self.probe_sessions:
            self.probe_sessions[target.name] = self._open(target)
        return self.probe_sessions[target.name]

    def forget(self, target: Target, response: Response) -> None:
        """Drop a session the API no longer serves, so the next step opens a new one."""

        if (
            response.code in ("session_expired", "not_authorized", "session_quarantined")
            or response.status == 404
        ):
            if self.sessions.pop(target.name, None) is not None:
                self.sessions_replaced += 1

    def execute(
        self, session_id: str, statement: str, binds: dict[str, Any] | None = None, **limits: Any
    ) -> Response:
        body: dict[str, Any] = {
            "statement": statement,
            "binds": [{"name": name, "value": value} for name, value in (binds or {}).items()],
            "maxRows": limits.get("max_rows", self.workload.limits.max_rows_per_read),
            "deadlineSeconds": self.workload.limits.statement_deadline_seconds,
        }
        response = self.request("POST", f"/api/v1/worksheets/{session_id}/execute", body)
        execution_id = response.outcome.get("executionId")
        if execution_id:
            self.execution_ids.append(str(execution_id))
        return response

    def close(self) -> None:
        for session_id in [*self.sessions.values(), *self.probe_sessions.values()]:
            self.request("DELETE", f"/api/v1/worksheets/{session_id}")
        self.sessions.clear()
        self.probe_sessions.clear()
        self.http.close()


class SessionUnavailable(Exception):
    def __init__(self, response: Response) -> None:
        super().__init__(response.code)
        self.response = response


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


# -- the ledger of what the client did ----------------------------------------------------


@dataclass
class Ledger:
    """What each user committed, rolled back or could not vouch for, per target."""

    committed: dict[tuple[str, str], set[int]] = field(default_factory=dict)
    rolled_back: dict[tuple[str, str], set[int]] = field(default_factory=dict)
    unknown: dict[tuple[str, str], set[int]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, book: dict[tuple[str, str], set[int]], target: str, user: str, seq: int) -> None:
        with self.lock:
            book.setdefault((target, user), set()).add(seq)


# -- the run ------------------------------------------------------------------------------


class CapacityRun:
    def __init__(self, workload: Workload, report: CapacityReport) -> None:
        self.workload = workload
        self.report = report
        self.run_id = f"cap-{uuid.uuid4().hex[:12]}"
        report.run_id = self.run_id
        self.users = {
            user.subject: UserClient(workload, user.subject, report) for user in workload.users
        }
        self.order = [user.subject for user in workload.users]
        self.targets = {target.name: target for target in workload.targets}
        self.ledger = Ledger()
        self.sequence = scheduler.Sequence()
        self.samples: list[Sample] = []
        self.samples_lock = threading.Lock()
        self.gate = scheduler.Gate(workload.limits.max_in_flight_requests)
        self.verification = Verification()
        self.stop = threading.Event()
        self.clock_start = 0.0
        self.transactions = 0
        self.transactions_lock = threading.Lock()

    # -- setup ---------------------------------------------------------------------------

    def prepare(self) -> None:
        report, workload = self.report, self.workload
        info = httpx.get(f"{workload.base_url}/api/v1/system/info", timeout=30.0)
        if info.status_code != 200:
            raise Prerequisite(
                f"{workload.base_url}/api/v1/system/info answered {info.status_code}."
            )
        report.environment = info.json()
        server_deadline = float(report.environment.get("limits", {}).get("deadlineSeconds", 0) or 0)
        if server_deadline and workload.limits.statement_deadline_seconds > server_deadline:
            report.notes.append(
                f"Declared statement deadline {workload.limits.statement_deadline_seconds:.0f}s exceeds "
                f"the API's {server_deadline:.0f}s; the API's limit applies."
            )
        report.notes.append(
            f"API worker processes declared as {workload.limits.api_worker_processes}; the API "
            "does not report its pool size, so this is not verified by the runner."
        )

        missing: list[str] = []
        for subject, user in self.users.items():
            try:
                user.refresh_token()
            except Prerequisite as problem:
                missing.append(str(problem))
                continue
            me = user.request("GET", "/api/v1/auth/me")
            if me.status != 200:
                missing.append(f"{subject} is not accepted by the API ({me.status} {me.code}).")
                continue
            visible = {
                row["id"]: row
                for row in (user.request("GET", "/api/v1/targets").body or [])
                if isinstance(row, dict)
            }
            for target in workload.targets:
                permissions = visible.get(target.profile_id, {}).get("permissions", [])
                if "worksheet" not in permissions:
                    missing.append(f"{subject} has no worksheet grant on {target.name}.")
        if missing:
            raise Prerequisite("; ".join(missing))

        first = self.users[self.order[0]]
        for target in workload.targets:
            view = first.request("GET", f"/api/v1/targets/{target.profile_id}")
            if not (isinstance(view.body, dict) and view.body.get("identity")):
                first.request("POST", f"/api/v1/targets/{target.profile_id}/test")
                view = first.request("GET", f"/api/v1/targets/{target.profile_id}")
            body = view.body if isinstance(view.body, dict) else {}
            report.identities[target.name] = body.get("identity") or {}
            host, port = body.get("host"), body.get("port")
            if host and port:
                report.network[f"TCP connect to {target.name} ({host}:{port})"] = _tcp_rtt(
                    str(host), int(port)
                )
            session = first.session(target)
            check = first.execute(
                session,
                target.sql["countMarker"],
                {"run_id": self.run_id, "user_name": first.subject, "seq": 0},
            )
            if check.status != 200 or check.code:
                raise Prerequisite(
                    f"The marker table is not usable on {target.name} ({check.code or check.status}). "
                    "Apply oracle/capacity/load_schema.sql to each load target."
                )
        api = urlsplit(workload.base_url)
        report.network["TCP connect to the API"] = _tcp_rtt(
            api.hostname or "localhost", api.port or (443 if api.scheme == "https" else 80)
        )
        report.network["GET /healthz (no database)"] = _http_rtt(workload.base_url)

    # -- steps ---------------------------------------------------------------------------

    def add(self, sample: Sample) -> None:
        with self.samples_lock:
            self.samples.append(sample)

    def offset(self) -> float:
        return time.monotonic() - self.clock_start

    def perform(self, phase: str, user: UserClient, target: Target, step: scheduler.Step) -> Sample:
        started = self.offset()
        try:
            if step.kind == "metadata":
                return self._metadata(phase, user, target, started)
            if step.kind == "boundedRead":
                return self._read(phase, user, target, started)
            if step.kind == "transaction":
                return self._transaction(phase, user, target, started, step.commit)
            return self._cancellation(phase, user, target, started)
        except SessionUnavailable as unavailable:
            response = unavailable.response
            return Sample(
                phase,
                step.kind,
                user.subject,
                target.name,
                started,
                response.client_ms,
                False,
                response.code,
            )
        except Exception as exc:  # noqa: BLE001 - a client fault is recorded, not lost with its thread
            return Sample(
                phase,
                step.kind,
                user.subject,
                target.name,
                started,
                0.0,
                False,
                f"client_{type(exc).__name__}",
            )

    def _sample(
        self,
        phase: str,
        kind: str,
        user: UserClient,
        target: Target,
        started: float,
        response: Response,
        client_ms: float | None = None,
    ) -> Sample:
        outcome = response.outcome
        ok = response.status == 200 and not response.code
        if not ok:
            user.forget(target, response)
        return Sample(
            phase,
            kind,
            user.subject,
            target.name,
            started,
            client_ms if client_ms is not None else response.client_ms,
            ok,
            response.code,
            server_ms=outcome.get("elapsedMs"),
            db_ms=outcome.get("databaseElapsedMs"),
            note="" if ok else response.message,
        )

    def _metadata(self, phase: str, user: UserClient, target: Target, started: float) -> Sample:
        response = user.request("GET", f"/api/v1/targets/{target.profile_id}/schemas")
        body = response.body if isinstance(response.body, dict) else {}
        ok = response.status == 200 and bool(body.get("available"))
        code = response.code or (
            "" if ok else str((body.get("error") or {}).get("code", "unavailable"))
        )
        return Sample(
            phase, "metadata", user.subject, target.name, started, response.client_ms, ok, code
        )

    def _read(self, phase: str, user: UserClient, target: Target, started: float) -> Sample:
        response = user.execute(user.session(target), target.sql["boundedRead"])
        return self._sample(phase, "boundedRead", user, target, started, response)

    def _transaction(
        self, phase: str, user: UserClient, target: Target, started: float, commit: bool
    ) -> Sample:
        seq = self.sequence.take(user.subject)
        binds = {
            "run_id": self.run_id,
            "target_name": target.name,
            "user_name": user.subject,
            "seq": seq,
        }
        session = user.session(target)
        inserted = user.execute(session, target.sql["insertMarker"], binds)
        total = inserted.client_ms
        if inserted.code == "outcome_unknown":
            self.ledger.add(self.ledger.unknown, target.name, user.subject, seq)
        if inserted.status != 200 or inserted.code:
            if inserted.status == 200:
                user.request("POST", f"/api/v1/worksheets/{session}/rollback")
            return self._sample(phase, "transaction", user, target, started, inserted, total)

        with self.transactions_lock:
            self.transactions += 1
            probe = self.transactions % PROBE_EVERY == 0
        if probe:
            self._probe(user, target, seq)

        end = user.request(
            "POST", f"/api/v1/worksheets/{session}/{'commit' if commit else 'rollback'}"
        )
        total += end.client_ms
        if end.status == 200:
            book = self.ledger.committed if commit else self.ledger.rolled_back
            self.ledger.add(book, target.name, user.subject, seq)
        elif end.code == "outcome_unknown":
            self.ledger.add(self.ledger.unknown, target.name, user.subject, seq)
            user.sessions.pop(target.name, None)
        else:
            # A definite refusal leaves the insert pending; discard it.
            user.request("POST", f"/api/v1/worksheets/{session}/rollback")
            self.ledger.add(self.ledger.rolled_back, target.name, user.subject, seq)
        sample = self._sample(phase, "transaction", user, target, started, inserted, total)
        if end.status != 200:
            sample.ok, sample.code = False, end.code or f"http_{end.status}"
        return sample

    def _probe(self, owner: UserClient, target: Target, seq: int) -> None:
        """From another user's own session, look for this user's uncommitted marker."""

        peer = self.users[self.order[(self.order.index(owner.subject) + 1) % len(self.order)]]
        if peer is owner:
            return
        with peer.probe_lock:
            try:
                session = peer.probe_session(target)
            except SessionUnavailable as unavailable:
                self.verification.incomplete.append(
                    f"{peer.subject} could not open a probe session on {target.name} "
                    f"({unavailable}), so probe {seq} for {owner.subject} did not run"
                )
                return
            seen = peer.execute(
                session,
                target.sql["countMarker"],
                {"run_id": self.run_id, "user_name": owner.subject, "seq": seq},
            )
        if seen.status != 200 or seen.code:
            self.verification.incomplete.append(
                f"probe for {owner.subject}'s marker {seq} on {target.name} failed "
                f"({seen.code or seen.status}), so it proves nothing"
            )
            return
        rows = ((seen.outcome.get("resultSet") or {}).get("rows")) or [[None]]
        self.verification.probes += 1
        if rows and rows[0] and rows[0][0] not in (0, "0"):
            self.verification.contaminations.append(
                f"{peer.subject} saw {owner.subject}'s uncommitted marker {seq} on {target.name}"
            )

    def _cancellation(self, phase: str, user: UserClient, target: Target, started: float) -> Sample:
        session = user.session(target)
        result: dict[str, Response] = {}

        def run() -> None:
            result["execute"] = user.execute(session, target.sql["slowRead"], max_rows=1)

        worker = threading.Thread(target=run, daemon=True)
        clock = time.perf_counter()
        worker.start()
        worker.join(self.workload.cancel_after_ms / 1000.0)
        if worker.is_alive():
            user.request("POST", f"/api/v1/worksheets/{session}/cancel")
        worker.join(self.workload.limits.request_timeout_seconds)
        response = result.get("execute") or Response(
            0, {"error": {"code": "client_timeout"}}, _ms(clock)
        )
        state = response.outcome.get("state")
        if response.status == 200 and state in ("cancelled", "succeeded"):
            sample = self._sample(
                phase, "cancellation", user, target, started, response, _ms(clock)
            )
            sample.ok, sample.code = True, ""
            sample.note = "cancelled" if state == "cancelled" else "finished before the cancel"
            return sample
        return self._sample(phase, "cancellation", user, target, started, response, _ms(clock))

    # -- phases --------------------------------------------------------------------------

    def run_phase(self, phase: PhaseSpec) -> bool:
        deadline = time.monotonic() + phase.seconds

        def stream_loop(stream: scheduler.Stream) -> None:
            user, target = self.users[stream.user], self.targets[stream.target]
            for step in scheduler.steps(self.workload, phase, stream):
                if self.stop.is_set() or time.monotonic() >= deadline:
                    return
                with self.gate:
                    sample = self.perform(phase.name, user, target, step)
                self.add(sample)
                pause = min(step.think_ms / 1000.0, max(0.0, deadline - time.monotonic()))
                if self.stop.wait(pause):
                    return

        threads = [
            threading.Thread(
                target=stream_loop,
                args=(stream,),
                daemon=True,
                name=f"{phase.name}:{stream.user}:{stream.target}",
            )
            for stream in scheduler.streams(self.workload, phase)
        ]
        for thread in threads:
            thread.start()
        grace = self.workload.limits.request_timeout_seconds + 5
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()) + grace)
        return not self.stop.is_set() and not any(thread.is_alive() for thread in threads)

    def check_ownership(self) -> None:
        """Each user tries to use the next user's session, and to read their execution."""

        for index, subject in enumerate(self.order):
            user = self.users[subject]
            other = self.users[self.order[(index + 1) % len(self.order)]]
            if other is user:
                continue
            for target_name, session_id in list(other.sessions.items())[:1]:
                attempt = user.execute(session_id, self.targets[target_name].sql["boundedRead"])
                if attempt.status == 200:
                    self.verification.contaminations.append(
                        f"{subject} ran a statement in {other.subject}'s session on {target_name}"
                    )
                elif attempt.status not in (403, 404):
                    # Neither a proven refusal nor a proven breach - a 500 or a dropped
                    # connection is not evidence that ownership was checked at all.
                    self.verification.incomplete.append(
                        f"{subject}'s attempt on {other.subject}'s session on {target_name} "
                        f"got {attempt.status} ({attempt.code}), not a refusal - proves nothing"
                    )
            for execution_id in other.execution_ids[-3:]:
                seen = user.request("GET", f"/api/v1/executions/{execution_id}")
                if seen.status == 200:
                    self.verification.contaminations.append(
                        f"{subject} could read {other.subject}'s execution record"
                    )
                elif seen.status not in (403, 404):
                    self.verification.incomplete.append(
                        f"{subject}'s attempt to read {other.subject}'s execution "
                        f"{execution_id} got {seen.status} ({seen.code}), not a refusal - "
                        "proves nothing"
                    )

    # -- verification --------------------------------------------------------------------

    def verify(self) -> None:
        v = self.verification
        first = self.users[self.order[0]]
        with self.ledger.lock:
            committed = {key: set(value) for key, value in self.ledger.committed.items()}
            rolled_back = {key: set(value) for key, value in self.ledger.rolled_back.items()}
            unknown = {key: set(value) for key, value in self.ledger.unknown.items()}
        v.committed = sum(len(s) for s in committed.values())
        v.rolled_back = sum(len(s) for s in rolled_back.values())
        v.unknown = sum(len(s) for s in unknown.values())

        for target in self.workload.targets:
            try:
                session = first._open(target)  # noqa: SLF001 - a fresh session, not a loaded one
            except SessionUnavailable as unavailable:
                v.incomplete.append(
                    f"could not open a verification session on {target.name} ({unavailable})"
                )
                continue
            try:
                foreign = first.execute(
                    session,
                    target.sql["foreignMarkers"],
                    {"run_id": self.run_id, "target_name": target.name},
                )
                rows = (foreign.outcome.get("resultSet") or {}).get("rows") or []
                if foreign.code or not rows:
                    v.incomplete.append(
                        f"foreign-marker count failed on {target.name} ({foreign.code})"
                    )
                elif rows[0][0] not in (0, "0"):
                    v.contaminations.append(
                        f"{rows[0][0]} marker(s) written for another target are in {target.name}"
                    )
                for subject in self.order:
                    key = (target.name, subject)
                    expected = committed.get(key, set())
                    # Every number this user was ever given, on any target: a marker the
                    # client has no record of is found only by looking where it should not be.
                    high = self.sequence.highest(subject)
                    found: set[int] = set()
                    for low in range(1, high + 1, PAGE):
                        page = first.execute(
                            session,
                            target.sql["listMarkers"],
                            {
                                "run_id": self.run_id,
                                "target_name": target.name,
                                "user_name": subject,
                                "lo": low,
                                "hi": low + PAGE - 1,
                            },
                            max_rows=PAGE + 1,
                        )
                        result_set = page.outcome.get("resultSet") or {}
                        if page.code or result_set.get("truncated"):
                            v.incomplete.append(
                                f"marker page {low} for {subject} on {target.name} ({page.code or 'truncated'})"
                            )
                            break
                        found.update(int(row[0]) for row in result_set.get("rows", []))
                    for seq in sorted(found & rolled_back.get(key, set())):
                        v.contaminations.append(
                            f"rolled-back marker {seq} of {subject} is present on {target.name}"
                        )
                    stray = found - expected - unknown.get(key, set()) - rolled_back.get(key, set())
                    for seq in sorted(stray):
                        v.contaminations.append(
                            f"marker {seq} of {subject} is on {target.name}, which never committed it"
                        )
                    for seq in sorted(expected - found):
                        v.lost_commits.append(
                            f"committed marker {seq} of {subject} is missing from {target.name}"
                        )
                if self.workload.cleanup:
                    cleanup_deleted = first.execute(
                        session, target.sql["deleteMarkers"], {"run_id": self.run_id}
                    )
                    if cleanup_deleted.status != 200 or cleanup_deleted.code:
                        v.incomplete.append(
                            f"cleanup delete failed on {target.name} "
                            f"({cleanup_deleted.code or cleanup_deleted.status}); markers may remain"
                        )
                    else:
                        cleanup_committed = first.request(
                            "POST", f"/api/v1/worksheets/{session}/commit"
                        )
                        if cleanup_committed.status != 200:
                            v.incomplete.append(
                                f"cleanup commit failed on {target.name} "
                                f"({cleanup_committed.status}); markers may remain"
                            )
            finally:
                first.request("DELETE", f"/api/v1/worksheets/{session}")

    def close_and_check_leaks(self) -> None:
        for user in self.users.values():
            probe = UserClient(self.workload, user.subject, self.report)
            probe._token = user._token  # noqa: SLF001 - same credential, fresh connection
            user.close()
            listed = probe.request("GET", "/api/v1/worksheets")
            if listed.status != 200 or not isinstance(listed.body, dict):
                # A failed listing is not evidence of zero leaks - it is evidence this
                # check did not run.
                self.verification.incomplete.append(
                    f"could not list {user.subject}'s worksheets after closing "
                    f"({listed.code or listed.status}); a leaked session there would not be seen"
                )
            else:
                for session in listed.body.get("sessions", []):
                    self.verification.leaked_sessions.append(
                        f"{user.subject}: {session.get('sessionId')}"
                    )
            probe.http.close()


def _tcp_rtt(host: str, port: int, samples: int = 5) -> str:
    timings = []
    for _ in range(samples):
        started = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=5.0):
                timings.append(_ms(started))
        except OSError as exc:
            return f"unreachable from the load client ({type(exc).__name__})"
    return f"{statistics.median(timings):.1f} ms"


def _http_rtt(base_url: str, samples: int = 5) -> str:
    timings = []
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        for _ in range(samples):
            started = time.perf_counter()
            try:
                client.get("/healthz")
            except httpx.HTTPError as exc:
                return f"failed ({type(exc).__name__})"
            timings.append(_ms(started))
    return f"{statistics.median(timings):.1f} ms"


def execute(
    workload: Workload, *, rehearsal: bool = False, on_phase: Callable[[str], None] | None = None
) -> CapacityReport:
    report = CapacityReport(workload=workload, rehearsal=rehearsal)
    run = CapacityRun(workload, report)
    try:
        run.prepare()
    except (Prerequisite, SessionUnavailable, httpx.HTTPError) as problem:
        report.prerequisite_failures.append(str(problem))
        # prepare() can open sessions and connections (checking one target after another)
        # before a later one fails; leaving those live would leak them off any report.
        for user in run.users.values():
            user.close()
        return report

    # Actual spans, not planned ones: a phase ends when its last in-flight request does.
    spans: dict[str, tuple[float, float]] = {}
    run.clock_start = time.monotonic()
    try:
        for phase in workload.phases:
            if on_phase:
                on_phase(phase.name)
            began = run.offset()
            finished = run.run_phase(phase)
            spans[phase.name] = (began, began + phase.seconds)
            if finished:
                report.completed_phases.append(phase.name)
            else:
                break
            if phase.name == "steady":
                run.check_ownership()
    except KeyboardInterrupt:
        run.stop.set()
        report.notes.append("Interrupted by the operator; later phases did not run.")
    finally:
        report.samples = list(run.samples)
        report.peak_in_flight = run.gate.peak
        try:
            run.verify()
        finally:
            run.close_and_check_leaks()

    if "recovery" in report.completed_phases:
        start, end = spans["recovery"]
        report.recovery_s = metrics.recovered_after(report.samples, workload, start, end)
    report.verification = run.verification
    report.criteria = metrics.evaluate(
        workload, report.samples, run.verification, report.recovery_s
    )
    report.eligibility = metrics.eligibility(
        workload,
        report.environment,
        report.identities,
        rehearsal=rehearsal,
        completed_phases=report.completed_phases,
    )
    return report
