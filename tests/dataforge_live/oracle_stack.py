"""Disposable B1 Oracle stack; credentials are staged as files, never arguments."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import select

from harness_api.config import Settings
from harness_api.db import build_engine, build_session_factory
from harness_api.models import ConnectionProfile, SecretReference
from tests.capacity.driver import (
    DriverConfig,
    PostgresConfig,
    TargetConfig,
    generate_manifest,
    prepare_secrets,
    split_dsn,
)
from tests.dataforge_live.stack import (
    ADMIN_SUBJECT,
    REPOSITORY,
    LiveHarness,
    StackProblem,
    _free_port,
)

DEVELOPER = "b1-developer"
VIEWER = "b1-viewer"
MODEL = "qwen3.8-flash-next-iq3_s"
KEY_FILE = Path("/home/kiwi/llm-rig/strata/.config/strata/api-key")


def load_b1_targets(path: Path) -> DriverConfig:
    """Read connection settings only, selecting D1/D2 and refusing other users."""
    data = json.loads(path.read_text(encoding="utf-8"))
    targets = []
    for name in ("load-db-1", "load-db-2"):
        matches = [t for t in data["targets"] if t.get("name") == name]
        if len(matches) != 1 or matches[0].get("user", "").lower() != "harness_app":
            raise ValueError("B1 requires exactly one D1 and D2 harness_app target")
        spec = matches[0]
        host, port, service = split_dsn(spec["dsn"], name)
        targets.append(
            TargetConfig(name, spec["dsn"], spec["user"], spec["passwordFile"], host, port, service)
        )
    return DriverConfig(0, PostgresConfig(), targets)


class OracleHarness(LiveHarness):
    def __init__(self, workdir: Path, targets: Path):
        super().__init__(workdir)
        self.config = load_b1_targets(targets)
        suffix = secrets.token_hex(5)
        self.pg_name = f"harness-b1-pg-{suffix}"
        self.nginx_name = f"harness-b1-nginx-{suffix}"
        self.proxy_port = 0
        self.env: dict[str, str] = {}
        self.profiles: dict[str, str] = {}
        self.versions: dict[str, str] = {}

    @property
    def proxy_url(self) -> str:
        return f"http://127.0.0.1:{self.proxy_port}"

    def command(self, args: list[str]) -> str:
        result = subprocess.run(
            args, cwd=REPOSITORY, env=self.env or None, capture_output=True, text=True, check=False
        )  # noqa: S603
        if result.returncode:
            # Do not include subprocess output: a driver/connection exception can carry secrets.
            raise StackProblem(f"B1 subprocess {args[0]} failed with code {result.returncode}")
        return result.stdout

    def start(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.workdir.chmod(0o700)
        health = httpx.get("http://127.0.0.1:8080/health", timeout=10).json()
        if health.get("model") != MODEL or health.get("status") != "ok":
            raise StackProblem("Requested local model is not healthy")
        pg_password = self.workdir / "pg.password"
        pg_password.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        pg_password.chmod(0o600)
        pg_port = _free_port()
        self.command(
            [
                "docker",
                "run",
                "-d",
                "--name",
                self.pg_name,
                "-p",
                f"127.0.0.1:{pg_port}:5432",
                "-e",
                "POSTGRES_USER=harness",
                "-e",
                "POSTGRES_DB=harness",
                "-e",
                "POSTGRES_PASSWORD_FILE=/run/secrets/password",
                "-v",
                f"{pg_password}:/run/secrets/password:ro",
                "postgres:16-alpine",
            ]
        )
        for _ in range(60):
            ready = subprocess.run(
                ["docker", "exec", self.pg_name, "pg_isready", "-U", "harness"],
                capture_output=True,
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise StackProblem("Disposable PostgreSQL did not become ready")
        secret_dir = self.workdir / "secrets"
        prepare_secrets(self.config, secret_dir)
        shutil.copyfile(KEY_FILE, secret_dir / "copilot.key")
        (secret_dir / "copilot.key").chmod(0o600)
        self._settings = Settings(
            env="development",
            auth_mode="dev",
            metadata_url=f"postgresql+psycopg://harness@127.0.0.1:{pg_port}/harness",
            metadata_password_file=str(pg_password),
            secret_dir=str(secret_dir),
            oracle_backend="oracledb",
            oracle_driver_mode="thin",
            copilot_enabled=True,
            copilot_provider="local",
            copilot_local_only=True,
            copilot_base_url="http://127.0.0.1:8080/v1",
            copilot_model=MODEL,
            copilot_api_key_ref="b1-model-key",
            copilot_max_output_tokens=1600,
        )
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
        self.env.update(
            {
                f"HARNESS_{k.upper()}": str(v)
                for k, v in self._settings.model_dump(mode="json").items()
            }
        )
        manifest = generate_manifest(self.config)
        manifest["targets"][1]["mutatingRunbooksEnabled"] = True
        manifest["users"] = [
            {"subject": ADMIN_SUBJECT, "roles": ["administrator"]},
            {"subject": DEVELOPER, "roles": ["developer", "dba"]},
            {"subject": VIEWER, "roles": ["viewer"]},
        ]
        manifest_path = self.workdir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.command(
            [sys.executable, "scripts/bootstrap_targets.py", "--manifest", str(manifest_path)]
        )
        engine = build_engine(self._settings)
        try:
            with build_session_factory(engine)() as db:
                db.add(SecretReference(name="b1-model-key", provider="file", locator="copilot.key"))
                db.commit()
                for profile in db.scalars(select(ConnectionProfile)):
                    self.profiles[profile.name] = profile.id
                    self.versions[profile.name] = str(
                        profile.identity_json.get("version", "unknown")
                    )
        finally:
            engine.dispose()
        self.port = _free_port()
        self.start_api()
        self.proxy_port = _free_port()
        config = (REPOSITORY / "deploy/nginx.conf").read_text(encoding="utf-8")
        config = config.replace("listen 8080;", f"listen {self.proxy_port};").replace(
            "http://api:8000", self.base_url
        )
        nginx_path = self.workdir / "nginx.conf"
        nginx_path.write_text(config, encoding="utf-8")
        self.command(
            [
                "docker",
                "run",
                "-d",
                "--name",
                self.nginx_name,
                "--network",
                "host",
                "-v",
                f"{nginx_path}:/etc/nginx/conf.d/default.conf:ro",
                "nginx:1.27-alpine",
            ]
        )
        for _ in range(50):
            try:
                if httpx.get(f"{self.proxy_url}/healthz", timeout=1).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.2)
        raise StackProblem("nginx did not become ready")

    def start_api(self) -> None:
        self._log = (self.workdir / "api.log").open("a", encoding="utf-8")
        self._process = subprocess.Popen(
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
            cwd=REPOSITORY,
            env=self.env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )  # noqa: S603
        self._wait_for_health()

    def stop_api(self) -> None:
        super().stop()

    def stop(self) -> None:
        self.stop_api()
        for name in (self.nginx_name, self.pg_name):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
