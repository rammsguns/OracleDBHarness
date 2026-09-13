"""The local API and console that rehearsal and fixture runs sign in to.

The API runs in its own process in OIDC mode, over a throwaway SQLite store seeded with
the demonstration targets. The console is the production build, served by ``vite
preview`` with its proxy pointed at that API - the same origin arrangement the pilot's
nginx gives it, but not that proxy, which is why a fixture run qualifies neither.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from harness_api.config import Settings
from harness_api.db import build_engine, build_session_factory
from harness_api.models import ConnectionProfile, User
from harness_api.seed import seed

REPOSITORY = Path(__file__).resolve().parents[2]
WEB = REPOSITORY / "apps" / "web"
VITE = WEB / "node_modules" / "vite" / "bin" / "vite.js"


class StackProblem(RuntimeError):
    """A local prerequisite is missing. The message says which and how to fix it."""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


@dataclass
class LocalStack:
    workdir: Path
    console_origin: str
    issuer: str
    jwks_url: str
    client_id: str
    api_port: int = 0
    _processes: list[tuple[subprocess.Popen[bytes], TextIO]] = field(default_factory=list)
    _settings: Settings | None = None

    def preflight(self) -> list[str]:
        """Everything that would stop the stack starting, as a list of fixes."""

        problems = []
        if not VITE.exists():
            problems.append(
                "The console's dependencies are not installed. Run `npm --prefix apps/web install`."
            )
        node = _which("node")
        if node is None:
            problems.append("`node` is not on PATH; the console is built and served with it.")
        port = urlsplit(self.console_origin).port or 80
        if not _port_is_free(port):
            problems.append(
                f"Port {port} on 127.0.0.1 is in use. The realm's redirect URIs fix the "
                "console's origin, so stop whatever holds it (often `npm run dev`)."
            )
        return problems

    def start(self) -> None:
        problems = self.preflight()
        if problems:
            raise StackProblem("\n".join(problems))
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / "secrets").mkdir(exist_ok=True)
        (self.workdir / "fake").mkdir(exist_ok=True)
        self.api_port = _free_port()
        settings = Settings(
            env="development",
            metadata_url=f"sqlite+pysqlite:///{(self.workdir / 'metadata.sqlite3').as_posix()}",
            secret_dir=str(self.workdir / "secrets"),
            auth_mode="oidc",
            oidc_issuer=self.issuer,
            oidc_jwks_url=self.jwks_url,
            oidc_client_id=self.client_id,
            oracle_backend="fake",
            oracle_fake_data_dir=str(self.workdir / "fake"),
            cors_origins=self.console_origin,
        )
        self._settings = settings
        # The seed refuses OIDC mode, rightly for a pilot. This store is throwaway: seed the
        # demonstration targets and grants in development mode, then serve it with OIDC.
        seed(settings.model_copy(update={"auth_mode": "dev"}), probe=False)
        self._start_api(settings)
        self._start_console()

    def _spawn(self, name: str, command: list[str], cwd: Path, env: dict[str, str]) -> None:
        log = (self.workdir / f"{name}.log").open("w", encoding="utf-8")
        process = subprocess.Popen(  # noqa: S603 - fixed commands built here
            command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT
        )
        self._processes.append((process, log))

    def _start_api(self, settings: Settings) -> None:
        env = {key: value for key, value in os.environ.items() if not key.startswith("HARNESS_")}
        for key, value in settings.model_dump(mode="json").items():
            env[f"HARNESS_{key.upper()}"] = str(value)
        # Run from the work directory so the repository's .env cannot add settings.
        self._spawn(
            "api",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "harness_api.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.api_port),
                "--log-level",
                "warning",
            ],
            self.workdir,
            env,
        )
        self._wait_for(f"http://127.0.0.1:{self.api_port}/healthz", "api")

    def _start_console(self) -> None:
        node = _which("node")
        assert node is not None
        env = dict(os.environ)
        env["HARNESS_API_PROXY_TARGET"] = f"http://127.0.0.1:{self.api_port}"
        build = subprocess.run(  # noqa: S603 - fixed command
            [node, str(VITE), "build", "--logLevel", "warn"],
            cwd=WEB,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if build.returncode != 0:
            raise StackProblem(f"The console build failed:\n{build.stdout}\n{build.stderr}")
        port = urlsplit(self.console_origin).port or 80
        self._spawn(
            "console",
            [
                node,
                str(VITE),
                "preview",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--strictPort",
            ],
            WEB,
            env,
        )
        self._wait_for(f"{self.console_origin}/healthz", "console")

    def _wait_for(self, url: str, name: str, timeout: float = 90.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for process, _ in self._processes:
                if process.poll() is not None:
                    raise StackProblem(f"The {name} process exited.\n{self.log(name)}")
            try:
                if httpx.get(url, timeout=2.0).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.2)
        raise StackProblem(
            f"The {name} did not answer {url} within {timeout:.0f}s.\n{self.log(name)}"
        )

    def log(self, name: str) -> str:
        path = self.workdir / f"{name}.log"
        return path.read_text(encoding="utf-8", errors="replace")[-4000:] if path.exists() else ""

    def stop(self) -> None:
        for process, log in reversed(self._processes):
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=30)
            finally:
                log.close()
        self._processes.clear()

    # -- what an administrator would do, done directly on the store ----------------------

    def _factory(self) -> Any:
        assert self._settings is not None
        return build_session_factory(build_engine(self._settings))

    def register(self, subject: str, display_name: str) -> None:
        """Register a provider subject as a developer with no target grants."""

        with self._factory()() as db:
            db.add(User(subject=subject, display_name=display_name, roles=["developer"]))
            db.commit()

    def target_id(self, name: str) -> str:
        with self._factory()() as db:
            profile = db.scalars(
                select(ConnectionProfile).where(ConnectionProfile.name == name)
            ).one()
            return str(profile.id)


def _which(name: str) -> str | None:
    return shutil.which(name)
