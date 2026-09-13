"""The parent side: start the API in its own process, kill it, and look at the database.

Everything here is test tooling. It reads the metadata store and the target database
directly, and it talks to the child only over HTTP, so nothing the child believed about
itself can leak into what the parent concludes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import httpx

from harness_api.config import Settings
from harness_worker.backend import OracleBackend, OracleConnection
from harness_worker.backend.base import ConnectionSpec
from harness_worker.errors import HarnessError
from harness_worker.statement import classify
from harness_worker.types import ExecutionLimits, StatementKind
from tests.process_death.child import BARRIER_FILE, BARRIERS, READY_FILE

#: How the operating system ends the child. Recorded with the evidence, because "killed"
#: means different things on the two platforms and neither runs any handler in the child.
KILL_METHOD = "TerminateProcess" if os.name == "nt" else "SIGKILL"

_REPOSITORY = Path(__file__).resolve().parents[2]
_STARTUP_SECONDS = 120.0
_BARRIER_SECONDS = 60.0


class ChildFailed(AssertionError):
    """The child exited or never got where it was sent. Carries its log."""


@dataclass
class ApiProcess:
    """One API process, started from the same settings as the test."""

    process: subprocess.Popen[bytes]
    directory: Path
    base_url: str
    barrier: str | None
    _log: TextIO = field(repr=False)
    killed_at: float | None = None

    @property
    def pid(self) -> int:
        return self.process.pid

    def client(self, timeout: float = 60.0) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=timeout)

    def log_text(self) -> str:
        self._log.flush()
        path = self.directory / "child.log"
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    def wait_for_barrier(self, timeout: float = _BARRIER_SECONDS) -> dict[str, Any]:
        """Block until the child reports it has stopped where it was told to."""

        return self._wait_for_file(BARRIER_FILE, timeout, f"reach the {self.barrier!r} barrier")

    def wait_for_file(self, name: str, timeout: float = _BARRIER_SECONDS) -> dict[str, Any]:
        return self._wait_for_file(name, timeout, f"write {name}")

    def _wait_for_file(self, name: str, timeout: float, what: str) -> dict[str, Any]:
        path = self.directory / name
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
            if self.process.poll() is not None:
                raise ChildFailed(
                    f"The API process exited ({self.process.returncode}) before it could "
                    f"{what}.\n{self.log_text()}"
                )
            time.sleep(0.05)
        raise ChildFailed(
            f"The API process did not {what} within {timeout:.0f}s.\n{self.log_text()}"
        )

    def kill(self) -> None:
        """End the process the way a crash or an OOM kill does: no handler runs."""

        if self.process.poll() is None:
            self.process.kill()
            self.killed_at = time.monotonic()
        try:
            self.process.wait(timeout=30)
        finally:
            self._log.close()

    @property
    def alive(self) -> bool:
        return self.process.poll() is None


def start_api(
    settings: Settings,
    directory: Path,
    *,
    barrier: str | None = None,
    match: str = "",
) -> ApiProcess:
    """Start the API in a new interpreter and wait until it has finished starting.

    "Started" means ``/healthz`` answers, which is after the lifespan has run: the store
    has been claimed and any earlier process's work reconciled.
    """

    if barrier is not None and barrier not in BARRIERS:
        raise ValueError(f"Unknown barrier {barrier!r}.")
    directory.mkdir(parents=True, exist_ok=True)
    config = {
        "settings": settings.model_dump(mode="json"),
        "barrier": barrier,
        "match": match,
        "signalDir": str(directory),
    }
    config_path = directory / "child.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    log = (directory / "child.log").open("w", encoding="utf-8")
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and module
        [sys.executable, "-m", "tests.process_death.child", str(config_path)],
        cwd=_REPOSITORY,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    child = ApiProcess(process=process, directory=directory, base_url="", barrier=barrier, _log=log)
    try:
        ready = child._wait_for_file(READY_FILE, _STARTUP_SECONDS, "bind its port")  # noqa: SLF001
        child.base_url = f"http://127.0.0.1:{ready['port']}"
        _wait_until_serving(child)
    except BaseException:
        child.kill()
        raise
    return child


def _wait_until_serving(child: ApiProcess) -> None:
    deadline = time.monotonic() + _STARTUP_SECONDS
    with child.client(timeout=5.0) as client:
        while time.monotonic() < deadline:
            if not child.alive:
                raise ChildFailed(f"The API process exited during startup.\n{child.log_text()}")
            try:
                if client.get("/healthz").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.1)
    raise ChildFailed(f"The API process did not start serving.\n{child.log_text()}")


def confirm_backend(reached: dict[str, Any], identity: dict[str, Any], *, on_oracle: bool) -> None:
    """The killed process must have been using the backend the run claims to qualify.

    Asked of the child - its barrier signal names the backend its connections came from,
    and its worksheet identity is what the database reported - rather than inferred from
    the parent's configuration. A child that somehow ran on the stand-in fails here, so a
    live run can never quietly be a stand-in run.
    """

    expected = "oracledb" if on_oracle else "fake"
    reported = reached.get("backend")
    assert reported == expected, (
        f"The killed API process was running backend {reported!r}, not {expected!r}. "
        f"Nothing it did can be recorded as {expected} evidence."
    )
    stand_in = "stand-in" in str(identity.get("versionFull", ""))
    assert stand_in is not on_oracle, (
        f"The worksheet identity says versionFull={identity.get('versionFull')!r}, which "
        f"does not match a {expected} run."
    )


def fire_and_abandon(call: Callable[[], Any]) -> threading.Thread:
    """Send a request whose answer will never come, because its process will be killed."""

    def run() -> None:
        try:
            call()
        except Exception:  # noqa: BLE001, S110 - the connection is reset by the kill
            pass

    thread = threading.Thread(target=run, name="abandoned-request", daemon=True)
    thread.start()
    return thread


# -- reading the database without the API ------------------------------------------------

_OBSERVER_LIMITS = ExecutionLimits(
    maxRows=10,
    maxResponseBytes=1 << 16,
    deadlineSeconds=30.0,
    maxDbmsOutputBytes=0,
    lobPreviewBytes=0,
)

#: The row every scenario writes to. Not one another test in the API suites changes,
#: so a value left behind by an earlier test cannot pass for a replayed write.
EMPLOYEE_ID = 101


@dataclass
class Cleanup:
    """What the observer saw while waiting for the database to clean up the dead session."""

    seconds: float
    session_gone: bool | None
    detail: str


class Observer:
    """An independent session on the target, for reading what really happened.

    Against Oracle this is a separate database session under the qualification account.
    Against the stand-in it is a separate SQLite connection to the same file, which is as
    independent as the stand-in gets - and says nothing about Oracle.
    """

    def __init__(self, backend: OracleBackend, spec: ConnectionSpec) -> None:
        self._backend = backend
        self._connection: OracleConnection = backend.connect(spec)
        self.on_oracle = backend.name == "oracledb"

    def close(self) -> None:
        try:
            if not self._connection.is_broken and self._connection.transaction_open:
                self._connection.rollback()
        finally:
            self._connection.close()

    def _run(self, sql: str, binds: dict[str, Any] | None = None) -> Any:
        return self._connection.execute(sql, binds or {}, classify(sql), _OBSERVER_LIMITS)

    def salary(self, employee_id: int = EMPLOYEE_ID) -> Any:
        result = self._run(
            "SELECT salary FROM employees WHERE employee_id = :employee_id",
            {"employee_id": employee_id},
        )
        assert result.result_set is not None and result.result_set.rows, "the row is missing"
        value = result.result_set.rows[0][0]
        return int(value) if value is not None else None

    def set_salary(self, value: int, employee_id: int = EMPLOYEE_ID) -> None:
        """Put the row back. Used after a scenario whose commit really did land."""

        self._run(
            "UPDATE employees SET salary = :salary WHERE employee_id = :employee_id",
            {"salary": value, "employee_id": employee_id},
        )
        self._connection.commit()

    def lock_row(self, employee_id: int = EMPLOYEE_ID) -> None:
        """Hold the row lock, so a write to it waits inside the database."""

        if not self.on_oracle:
            raise RuntimeError("The stand-in has no row locks to hold a statement on.")
        # A locking read, sent as DML so the adapter knows a transaction is now open and
        # release() really does roll it back.
        self._connection.execute(
            "SELECT employee_id FROM employees WHERE employee_id = :employee_id FOR UPDATE",
            {"employee_id": employee_id},
            StatementKind.DML,
            _OBSERVER_LIMITS,
        )

    def release(self) -> None:
        self._connection.rollback()

    def session(self, sid: int | None, serial: int | None) -> dict[str, Any] | None | str:
        """The target's own view of a session: a row, ``None`` if gone, or ``"unreadable"``.

        Only Oracle has a ``V$SESSION`` worth reading; the stand-in's is seeded.
        """

        if not self.on_oracle or sid is None or serial is None:
            return "unreadable"
        try:
            result = self._run(
                "SELECT status, blocking_session, event, module FROM v$session"
                " WHERE sid = :sid AND serial# = :serial",
                {"sid": sid, "serial": serial},
            )
        except HarnessError:
            return "unreadable"
        assert result.result_set is not None
        if not result.result_set.rows:
            return None
        status, blocking, event, module = result.result_set.rows[0]
        return {"status": status, "blockingSession": blocking, "event": event, "module": module}

    def wait_until_blocked(self, sid: int | None, serial: int | None, timeout: float = 30.0) -> str:
        """Wait until the target reports the session waiting on the observer's lock."""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.session(sid, serial)
            if state == "unreadable":
                raise RuntimeError(
                    "V$SESSION is not readable by the qualification account, so it cannot "
                    "be shown that the statement was waiting inside the database."
                )
            if isinstance(state, dict) and state["blockingSession"] is not None:
                return f"{state['event']} (blocked by SID {state['blockingSession']})"
            time.sleep(0.2)
        raise AssertionError(
            f"Session {sid},{serial} was never seen waiting on the row lock within {timeout:.0f}s."
        )

    def wait_for_cleanup(
        self,
        sid: int | None,
        serial: int | None,
        employee_id: int = EMPLOYEE_ID,
        timeout: float = 180.0,
    ) -> Cleanup:
        """Wait until the dead process's session no longer holds anything.

        The database does not learn of a dead client at the moment of the kill. It notices
        when it next reads or writes that socket - immediately for an idle session, only
        after the statement ends for one that was running or waiting - and then rolls the
        session's transaction back. Reading the row any earlier would read a transaction
        still in progress, so both the session and its row lock have to be gone first.
        """

        started = time.monotonic()
        deadline = started + timeout
        last = "nothing observed yet"
        while True:
            state = self.session(sid, serial)
            session_gone = None if state == "unreadable" else state is None
            if session_gone is False:
                last = f"session {sid},{serial} still present: {state}"
            else:
                locked = self._row_locked(employee_id)
                if not locked:
                    elapsed = time.monotonic() - started
                    how = (
                        "the session left V$SESSION and its row lock was free"
                        if session_gone
                        else "the row lock was free (V$SESSION not readable)"
                        if self.on_oracle
                        else "the stand-in's file lock was free"
                    )
                    return Cleanup(seconds=elapsed, session_gone=session_gone, detail=how)
                last = "the row is still locked"
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"The dead process's session was not cleaned up within {timeout:.0f}s "
                    f"({last}). The database effect cannot be read yet, so nothing is "
                    "concluded. Check SQLNET.EXPIRE_TIME and whether the server process "
                    "is still running the statement."
                )
            time.sleep(0.5)

    def _row_locked(self, employee_id: int) -> bool:
        if self.on_oracle:
            sql = "SELECT employee_id FROM employees WHERE employee_id = :employee_id FOR UPDATE NOWAIT"
        else:
            # SQLite has no row locks. A no-op write needs the database write lock, which a
            # killed process's open transaction would still be holding if it were not
            # released.
            sql = "UPDATE employees SET salary = salary WHERE employee_id = :employee_id"
        try:
            self._connection.execute(
                sql, {"employee_id": employee_id}, StatementKind.DML, _OBSERVER_LIMITS
            )
        except HarnessError:
            if self._connection.is_broken:
                raise
            return True
        finally:
            if not self._connection.is_broken:
                self._connection.rollback()
        return False
