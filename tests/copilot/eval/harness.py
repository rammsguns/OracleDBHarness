"""A disposable harness for one evaluation run, served over real HTTP.

The cases go through the same path an IDE adapter or the console uses: the router's
authentication and context policy, the service's budget and record keeping, the
provider stream as server-sent events, proposal capture and the apply check. So the
app runs under uvicorn on a loopback port rather than behind an in-process test client,
which would buffer the stream and hide whether anything arrived incrementally.

The metadata store is a throwaway SQLite file and the Oracle backend is the local
stand-in: the copilot never touches a database, and the runner checks that it did not.
The provider key is registered as an ``env`` secret reference, so it is resolved by the
harness's own secret path and never written to disk.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from harness_api.app import create_app
from harness_api.config import Settings
from harness_api.copilot.provider import Provider
from harness_api.db import build_engine, build_session_factory
from harness_api.models import CopilotRequest, Execution, SecretReference, WorksheetSessionRecord
from harness_api.seed import seed

KEY_REFERENCE = "copilot-evaluation-provider-key"
OTHER_TARGET_REFERENCE = "harness:test:HARNESS_APP"
STARTUP_SECONDS = 60.0


class HarnessStartupError(RuntimeError):
    pass


@dataclass
class RunningHarness:
    base_url: str
    settings: Settings
    session_factory: sessionmaker[Session]
    headers: dict[str, dict[str, str]]

    def activity(self) -> dict[str, int]:
        """Durable traces of database work. A copilot request must leave these alone."""

        with self.session_factory() as db:
            return {
                "executions": db.scalar(select(func.count()).select_from(Execution)) or 0,
                "worksheetSessions": db.scalar(
                    select(func.count()).select_from(WorksheetSessionRecord)
                )
                or 0,
            }

    def copilot_record_count(self) -> int:
        with self.session_factory() as db:
            return db.scalar(select(func.count()).select_from(CopilotRequest)) or 0

    def copilot_outcome(self, request_id: str) -> str | None:
        with self.session_factory() as db:
            row = db.get(CopilotRequest, request_id)
            return None if row is None else row.outcome


def evaluation_settings(
    workspace: Path,
    *,
    provider: str,
    model: str,
    api_key_env: str | None,
    max_output_tokens: int,
    request_timeout_seconds: float,
    daily_requests: int,
) -> Settings:
    (workspace / "secrets").mkdir(parents=True, exist_ok=True)
    (workspace / "fake").mkdir(parents=True, exist_ok=True)
    values: dict[str, Any] = {
        "env": "development",
        "metadata_url": f"sqlite+pysqlite:///{(workspace / 'metadata.sqlite3').as_posix()}",
        "secret_dir": str(workspace / "secrets"),
        "auth_mode": "dev",
        "dev_token_secret": secrets.token_urlsafe(32),
        "oracle_backend": "fake",
        "oracle_fake_data_dir": str(workspace / "fake"),
        "cors_origins": "",
        "copilot_enabled": True,
        "copilot_provider": provider,
        "copilot_model": model,
        "copilot_api_key_ref": KEY_REFERENCE if api_key_env else "",
        "copilot_max_output_tokens": max_output_tokens,
        "copilot_request_timeout_seconds": request_timeout_seconds,
        # One dispatch is one billable attempt. See budget.py.
        "copilot_provider_max_retries": 0,
        "copilot_user_daily_requests": daily_requests,
        "copilot_log_prompts": False,
    }
    # No .env: a developer's local file must not quietly change what is being evaluated.
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@contextmanager
def running_harness(
    settings: Settings,
    *,
    api_key_env: str | None,
    provider_override: Provider | None = None,
) -> Iterator[RunningHarness]:
    seed(settings, probe=False)
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    if api_key_env:
        with factory() as db:
            db.add(
                SecretReference(
                    name=KEY_REFERENCE,
                    provider="env",
                    locator=api_key_env,
                    description="Model provider key for a copilot evaluation run.",
                )
            )
            db.commit()

    app = create_app(settings)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="warning",
            access_log=False,
            lifespan="on",
            # A provider call abandoned by a timed-out case must not hold shutdown open.
            timeout_graceful_shutdown=5,
        )
    )
    thread = threading.Thread(target=server.run, name="copilot-evaluation-harness", daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + STARTUP_SECONDS
        while not server.started:
            if not thread.is_alive():
                raise HarnessStartupError("The evaluation harness exited during startup.")
            if time.monotonic() > deadline:
                raise HarnessStartupError("The evaluation harness did not start in time.")
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        if provider_override is not None:
            # Test seam only: the command line cannot reach it.
            app.state.harness.copilot._provider = provider_override  # noqa: SLF001
        base_url = f"http://127.0.0.1:{port}"
        yield RunningHarness(
            base_url=base_url,
            settings=settings,
            session_factory=factory,
            headers=_credentials(base_url),
        )
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        engine.dispose()


def _credentials(base_url: str) -> dict[str, dict[str, str]]:
    with httpx.Client(base_url=base_url, timeout=30) as client:

        def token(subject: str, role: str) -> str:
            response = client.post(
                "/api/v1/auth/dev-token", json={"subject": subject, "roles": [role]}
            )
            response.raise_for_status()
            return str(response.json()["accessToken"])

        admin = {"Authorization": f"Bearer {token('admin@example.internal', 'administrator')}"}

        def integration(name: str, scopes: list[str]) -> dict[str, str]:
            response = client.post(
                "/api/v1/admin/integrations",
                headers=admin,
                json={"name": name, "kind": "dataforge", "scopes": scopes},
            )
            response.raise_for_status()
            return {"Authorization": f"Bearer {response.json()['token']}"}

        return {
            "developer": {"Authorization": f"Bearer {token('dev@example.internal', 'developer')}"},
            "dba": {"Authorization": f"Bearer {token('dba@example.internal', 'dba')}"},
            "integration": integration("copilot-evaluation", ["copilot:assist"]),
            "integration_without_scope": integration("copilot-evaluation-unscoped", []),
        }
