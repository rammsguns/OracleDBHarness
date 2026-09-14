"""A disposable harness API process, plus a real ``dataforge`` integration credential.

No schema seeding is needed: the copilot endpoints never open an Oracle connection or
touch a registered target, and a development-mode token is signed locally for
whatever subject and roles are asked for, without a matching user row.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import httpx

from harness_api.config import Settings
from harness_api.db import build_engine, build_session_factory
from harness_api.models import User

REPOSITORY = Path(__file__).resolve().parents[2]

ADMIN_SUBJECT = "admin@dataforge-live.internal"


class StackProblem(RuntimeError):
    """A local prerequisite is missing, or the harness process did not come up."""


@dataclass
class LiveHarness:
    workdir: Path
    port: int = 0
    _process: subprocess.Popen[bytes] | None = None
    _log: TextIO | None = None
    _credential_ids: list[str] = field(default_factory=list)
    _settings: Settings | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / "fake").mkdir(exist_ok=True)
        self.port = _free_port()
        settings = Settings(
            env="development",
            metadata_url=f"sqlite+pysqlite:///{(self.workdir / 'metadata.sqlite3').as_posix()}",
            secret_dir=str(self.workdir / "secrets"),
            oracle_backend="fake",
            oracle_fake_data_dir=str(self.workdir / "fake"),
            copilot_enabled=True,
            copilot_provider="fake",
        )
        self._settings = settings
        env = {key: value for key, value in os.environ.items() if not key.startswith("HARNESS_")}
        for key, value in settings.model_dump(mode="json").items():
            env[f"HARNESS_{key.upper()}"] = str(value)
        self._log = (self.workdir / "api.log").open("w", encoding="utf-8")
        self._process = subprocess.Popen(  # noqa: S603 - fixed command built here
            [
                sys.executable,
                "-m",
                "uvicorn",
                "harness_api.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
            ],
            cwd=self.workdir,
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        self._wait_for_health()
        self._register_administrator()

    def _register_administrator(self) -> None:
        """Dev-mode tokens carry roles as claims, but ``authenticate`` still requires a
        registered ``User`` row - a provider's subject is opaque, and it is the subject
        an administrator has to register. This is that registration, done directly on
        the throwaway store, the same way ``tests/browser/stack.py`` registers a
        developer.
        """

        assert self._settings is not None
        factory = build_session_factory(build_engine(self._settings))
        with factory() as db:
            db.add(
                User(
                    subject=ADMIN_SUBJECT,
                    display_name="DataForge live run",
                    roles=["administrator"],
                )
            )
            db.commit()

    def _wait_for_health(self, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise StackProblem(f"The harness API process exited.\n{self.log()}")
            try:
                if httpx.get(f"{self.base_url}/healthz", timeout=2.0).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.2)
        raise StackProblem(
            f"The harness API did not answer /healthz within {timeout:.0f}s.\n{self.log()}"
        )

    def issue_dataforge_credential(self, name: str = "dataforge-live") -> str:
        """Provision a real ``dataforge``-kind integration credential over real HTTP.

        Returns the bearer token. Raised as ``StackProblem`` on any non-2xx response,
        since a failure here means the checks below never got to run.
        """

        admin_token = self._dev_token(ADMIN_SUBJECT, ["administrator"])
        response = httpx.post(
            f"{self.base_url}/api/v1/admin/integrations",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": name, "kind": "dataforge", "scopes": ["copilot:assist"]},
            timeout=10.0,
        )
        if response.status_code != 201:
            raise StackProblem(
                f"Could not provision a dataforge integration credential ({response.status_code}): "
                f"{response.text}"
            )
        body = response.json()
        self._credential_ids.append(body["id"])
        return str(body["token"])

    def _dev_token(self, subject: str, roles: list[str]) -> str:
        response = httpx.post(
            f"{self.base_url}/api/v1/auth/dev-token",
            json={"subject": subject, "roles": roles},
            timeout=10.0,
        )
        if response.status_code != 200:
            raise StackProblem(f"Could not mint a development token ({response.status_code}).")
        return str(response.json()["accessToken"])

    def log(self) -> str:
        path = self.workdir / "api.log"
        return path.read_text(encoding="utf-8", errors="replace")[-4000:] if path.exists() else ""

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.kill()
        if self._process is not None:
            self._process.wait(timeout=30)
        if self._log is not None:
            self._log.close()
        self._process = None


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
