"""A short capacity run against a local API on the stand-in, to check the tooling end to end.

It provisions what a load deployment needs through the same admin API an operator would
use: three worksheet-enabled targets (three separate stand-in databases), ten developer
accounts with grants on all of them, and the marker table on each. Then it runs the same
runner a pilot run uses, with short phases and proposed thresholds.

The report it produces is a real report of a real run, and it says - through the same
eligibility rules as any other - that it is not a pilot qualification.
"""

from __future__ import annotations

import secrets
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from harness_api.app import create_app
from harness_api.config import Settings
from harness_api.seed import seed
from tests.capacity.workload import Workload, parse

MARKER_TABLE = (
    "CREATE TABLE harness_load_markers ("
    " run_id VARCHAR2(40) NOT NULL,"
    " target_name VARCHAR2(100) NOT NULL,"
    " user_name VARCHAR2(255) NOT NULL,"
    " seq NUMBER(12) NOT NULL,"
    " CONSTRAINT harness_load_markers_pk PRIMARY KEY (run_id, user_name, seq))"
)

#: The marker statements are the same text on Oracle; see workload.example.json.
MARKER_SQL = {
    "insertMarker": "INSERT INTO harness_load_markers (run_id, target_name, user_name, seq)"
    " VALUES (:run_id, :target_name, :user_name, :seq)",
    "countMarker": "SELECT COUNT(*) FROM harness_load_markers"
    " WHERE run_id = :run_id AND user_name = :user_name AND seq = :seq",
    "listMarkers": "SELECT seq FROM harness_load_markers WHERE run_id = :run_id"
    " AND target_name = :target_name AND user_name = :user_name"
    " AND seq BETWEEN :lo AND :hi ORDER BY seq",
    "foreignMarkers": "SELECT COUNT(*) FROM harness_load_markers"
    " WHERE run_id = :run_id AND target_name <> :target_name",
    "deleteMarkers": "DELETE FROM harness_load_markers WHERE run_id = :run_id",
}

STAND_IN_SQL = {
    "boundedRead": "SELECT order_id, product_id, quantity FROM order_lines WHERE ROWNUM <= 50",
    # A 16-million-row join over the stand-in's 4,000 order lines: long enough to cancel.
    "slowRead": "SELECT COUNT(*) FROM order_lines a, order_lines b WHERE a.quantity >= b.quantity",
    **MARKER_SQL,
}


class LocalApi:
    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.settings = Settings(
            env="development",
            metadata_url=f"sqlite+pysqlite:///{(workdir / 'metadata.sqlite3').as_posix()}",
            secret_dir=str(workdir / "secrets"),
            auth_mode="dev",
            dev_token_secret=secrets.token_urlsafe(24),
            oracle_backend="fake",
            oracle_fake_data_dir=str(workdir / "fake"),
            worker_processes=8,
            statement_timeout_seconds=10.0,
            worksheet_idle_seconds=300.0,
            copilot_enabled=False,
            cors_origins="",
        )
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.base_url = ""

    def start(self) -> None:
        (self.workdir / "secrets").mkdir(parents=True, exist_ok=True)
        (self.workdir / "fake").mkdir(exist_ok=True)
        seed(self.settings, probe=False)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        self.base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        self._server = uvicorn.Server(
            uvicorn.Config(create_app(self.settings), log_level="warning", lifespan="on")
        )
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + 60
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("The local API did not start.")
            time.sleep(0.05)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=30)


def _token(client: httpx.Client, subject: str) -> dict[str, str]:
    issued = client.post("/api/v1/auth/dev-token", json={"subject": subject, "roles": []})
    issued.raise_for_status()
    return {"Authorization": f"Bearer {issued.json()['accessToken']}"}


def provision(
    base_url: str, users: int = 10, targets: int = 3
) -> tuple[list[str], list[dict[str, str]]]:
    """Create the load targets, accounts, grants and marker tables. Returns subjects and targets."""

    with httpx.Client(base_url=base_url, timeout=60.0) as client:
        admin = _token(client, "admin@example.internal")
        created_targets = []
        for index in range(targets):
            name = f"load-{chr(ord('a') + index)}"
            response = client.post(
                "/api/v1/admin/targets",
                headers=admin,
                json={
                    "name": name,
                    "environment": "development",
                    "host": "localhost",
                    "port": 1521,
                    "serviceName": f"LOAD{chr(ord('A') + index)}",
                    "username": "harness_app",
                    "defaultSchema": "HARNESS_APP",
                    "secretReference": "harness-app",
                    "worksheetsEnabled": True,
                },
            )
            response.raise_for_status()
            created_targets.append({"name": name, "profileId": response.json()["id"]})
        subjects = [f"load{index:02d}@example.internal" for index in range(users)]
        for subject in subjects:
            client.post(
                "/api/v1/admin/users",
                headers=admin,
                json={"subject": subject, "displayName": subject, "roles": ["developer"]},
            ).raise_for_status()
            for target in created_targets:
                client.post(
                    "/api/v1/admin/grants",
                    headers=admin,
                    json={
                        "subject": subject,
                        "profileId": target["profileId"],
                        "permissions": ["read", "worksheet"],
                    },
                ).raise_for_status()
        owner = _token(client, subjects[0])
        for target in created_targets:
            session = client.post(
                "/api/v1/worksheets", headers=owner, json={"profileId": target["profileId"]}
            )
            session.raise_for_status()
            session_id = session.json()["session"]["sessionId"]
            made = client.post(
                f"/api/v1/worksheets/{session_id}/execute",
                headers=owner,
                json={"statement": MARKER_TABLE},
            )
            made.raise_for_status()
            if made.json()["outcome"]["state"] != "succeeded":
                raise RuntimeError(
                    f"Could not create the marker table on {target['name']}: {made.text}"
                )
            client.delete(f"/api/v1/worksheets/{session_id}", headers=owner)
    return subjects, created_targets


def rehearsal_workload(
    base_url: str, subjects: list[str], targets: list[dict[str, str]], scale: float = 1.0
) -> Workload:
    document: dict[str, Any] = {
        "version": 1,
        "name": "local rehearsal",
        "api": {"baseUrl": base_url, "auth": {"kind": "devToken"}},
        "users": [{"subject": subject} for subject in subjects],
        "targets": [{**target, "sql": STAND_IN_SQL} for target in targets],
        "workload": {
            "seed": 20260913,
            "mix": {"metadata": 20, "boundedRead": 45, "transaction": 28, "cancellation": 7},
            "thinkTimeMs": {"min": 100, "max": 400},
            "cancelAfterMs": 150,
        },
        "phases": {
            "steady": {"seconds": round(20 * scale), "streamsPerUser": 1, "thinkTimeScale": 1},
            # Twenty back-to-back streams against eight execution slots. Expect the rehearsal's
            # own environment to show in the findings here: its SQLite metadata store queues
            # writers past SQLAlchemy's lock timeout (the API answers 500, "database is
            # locked"), and SQLite takes one writer per stand-in database. The pilot's store
            # is PostgreSQL; a rehearsal is judged on correctness, not on these.
            "saturation": {"seconds": round(15 * scale), "streamsPerUser": 2, "thinkTimeScale": 0},
            "recovery": {"seconds": round(20 * scale), "streamsPerUser": 1, "thinkTimeScale": 1},
        },
        "resourceLimits": {
            "apiCpus": 1,
            "apiMemoryMiB": 512,
            "apiWorkerProcesses": 8,
            "maxInFlightRequests": 30,
            "requestTimeoutSeconds": 60,
            "statementDeadlineSeconds": 10,
            "maxRowsPerRead": 50,
        },
        "thresholds": {
            "status": "proposed",
            "latencyMs": {
                "metadata": {"p50": 1000, "p95": 5000},
                "boundedRead": {"p50": 1000, "p95": 5000},
                "transaction": {"p50": 2000, "p95": 8000},
                "cancellation": {"p50": 5000, "p95": 15000},
            },
            "applicationOverheadP95Ms": 5000,
            "steadyMaxErrorRate": 0.05,
            "saturation": {
                "maxErrorRate": 0.5,
                # oracle_error is allowed here only: SQLite takes one writer per database file,
                # so under saturation a transaction waiting for an execution slot holds the
                # file's write lock past the stand-in's five-second busy timeout ("database is
                # locked"). Oracle locks rows, and these markers never share one. A pilot
                # workload has no reason to allow it.
                "allowedErrorCodes": ["execution_timeout", "session_busy", "oracle_error"],
            },
            "recoveryWithinSeconds": round(15 * scale) or 1,
            "maxOutcomeUnknown": 0,
            "maxContaminations": 0,
        },
        "cleanup": True,
    }
    return parse(document)


def workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="harness-capacity-"))
