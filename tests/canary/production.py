"""Persistent P1 observation canary; AI-agent evidence, never human/fleet approval."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from tests.canary.rehearsal import (
    BUDGET,
    RehearsalFailure,
    check_audit,
    compare_budget,
    load_admin_settings,
    observation_errors,
)
from tests.database_locks import database_locks

LABEL = "agent-qualified on a production-shaped stand-in on z4g5 (AI agent, not a human)"
REPO = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = REPO / "databases/reports/2026-10-25-g3-canary"
DEFAULT_STATE = REPO / "databases/canary/p1"
PRINCIPALS = ["g3-dba-1", "g3-dba-2", "g3-dba-3"]
EXPECTED_REFUSALS = {
    "/tuning/explain": "policy_refused",
    "/worksheets": "policy_refused",
    "/runbooks/runbook.gather_table_stats/run": "policy_refused",
    "/copilot/requests": "policy_refused",
}
ADMIN = "g3-admin"
DSN = "localhost:1523/PRODCDB"
CONTAINER = "harness-canary-pg"
VOLUME = "harness-canary-pgdata"
BUDGET_SCOPE = "HARNESS_RO parsing_user_id"
JUSTIFICATION = (
    "B5 measured peaks across six D4 passes: 1 session, 1.3 seconds SQL elapsed, "
    "105000 buffer gets and 16 executions (PLAN.md B5). Ceilings of 2 sessions, "
    "30 seconds, 1000000 buffer gets and 200 executions leave connection-turnover "
    "and P1 workload headroom while bounding one sequential read-only observer. "
    "Enforced scope: all v$sql cursors whose parsing_user_id is the HARNESS_RO user_id. "
    "Claude (AI agent, acting DBA) approved this correction after pre-start trial: "
    "defaultSchema SHOPAPP changes parsing_schema_name, so schema-only counters miss harness SQL. "
    "SHOPAPP workload and acting-DBA SYSTEM queries are excluded by parsing user. "
    "HARNESS_RO parsing-schema cost is retained as a secondary recorded figure only. "
    "Sessions sampled every 250 ms plus query latency; brief peaks may escape sampling."
)


def stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def record_budget(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "budget.json").open("x") as output:
        json.dump(
            {
                "label": LABEL,
                "ceilings": BUDGET,
                "scope": BUDGET_SCOPE,
                "justification": JUSTIFICATION,
                "at": datetime.now(UTC).isoformat(),
                "recorded_by": "recorded by Claude (AI agent, acting DBA), not a human",
            },
            output,
            indent=2,
        )
        output.write("\n")


def read_budget(directory: Path) -> dict[str, float]:
    record = json.loads((directory / "budget.json").read_text())
    budget = record["ceilings"]
    if (
        record.get("scope") != BUDGET_SCOPE
        or record.get("label") != LABEL
        or set(budget) != set(BUDGET)
        or compare_budget(budget, BUDGET)
        or not record.get("justification")
        or not record.get("at")
    ):
        raise RehearsalFailure("Invalid predeclared budget")
    return dict(budget)


@contextmanager
def state_lock(state: Path):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "runner.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def command(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(arguments, check=True, capture_output=True, **kwargs)
    except subprocess.CalledProcessError as exc:
        # Neither arguments nor captured output are safe to persist wholesale.
        raise RehearsalFailure(
            f"Deployment command failed ({Path(arguments[0]).name}, exit {exc.returncode}); captured output withheld"
        ) from None


def environment(state: Path) -> dict[str, str]:
    settings = json.loads((state / "deployment.json").read_text())
    env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    env.update(
        HARNESS_ENV="production",
        HARNESS_AUTH_MODE="dev",
        HARNESS_ALLOW_DEV_AUTH_OUTSIDE_DEVELOPMENT="true",
        HARNESS_METADATA_URL=f"postgresql+psycopg://harness@127.0.0.1:{settings['pg_port']}/harness",
        HARNESS_METADATA_PASSWORD_FILE=str(state / "secrets/pg.password"),
        HARNESS_SECRET_DIR=str(state / "secrets"),
        HARNESS_ORACLE_BACKEND="oracledb",
        HARNESS_ORACLE_DRIVER_MODE="thin",
        HARNESS_COPILOT_ENABLED="false",
        HARNESS_KIWI_ENABLED="false",
        HARNESS_ALLOWED_ENDPOINTS="localhost:1523",
        HARNESS_WORKER_PROCESSES="1",
        HARNESS_MAX_ROWS="50",
        HARNESS_STATEMENT_TIMEOUT_SECONDS="30",
        HARNESS_ORACLE_EXPIRE_TIME_MINUTES="1",
        HARNESS_SCHEMA_PANEL_CACHE_SECONDS="0",
        TMPDIR=str(Path.home() / ".cache/harness-capacity-tmp"),
    )
    return env


def api_arguments(port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "harness_api.app:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


def api_alive(state: Path) -> bool:
    pid_file = state / "api.pid"
    try:
        pid = int(pid_file.read_text())
        # Do not signal a recycled PID belonging to an unrelated process.
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        return b"harness_api.app:create_app" in arguments and b"uvicorn" in arguments
    except (ValueError, OSError):
        return False


def serve(state: Path) -> None:
    if api_alive(state):
        raise RehearsalFailure(
            "Canary API already running; stop it before starting the user service"
        )
    settings = json.loads((state / "deployment.json").read_text())
    (state / "api.pid").write_text(str(os.getpid()))
    os.chdir(REPO)
    os.execve(sys.executable, api_arguments(settings["port"]), environment(state))


def render_units(repository: Path, state: Path, report: Path) -> dict[str, str]:
    # systemd quoted values require their own escaping, including literal percent.
    def quote(path: Path) -> str:
        return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'

    substitutions = {
        # WorkingDirectory takes the whole raw value; unlike ExecStart it does not unquote.
        "@REPO@": str(repository).replace("%", "%%"),
        "@PYTHON@": quote(repository / ".venv/bin/python"),
        "@STATE@": quote(state),
        "@REPORT@": quote(report),
        "@TMP@": quote(Path.home() / ".cache/harness-capacity-tmp"),
    }
    result = {}
    for template in sorted((REPO / "deploy/canary").glob("*.in")):
        body = template.read_text()
        for key, value in substitutions.items():
            body = body.replace(key, value)
        result[template.name.removesuffix(".in")] = body
    return result


def deploy(args: argparse.Namespace) -> None:
    state = args.state_dir
    secret_dir = state / "secrets"
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    secret_dir.chmod(0o700)
    password = secret_dir / "pg.password"
    if not password.exists():
        with open(password, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as output:
            output.write(secrets.token_urlsafe(32))
    password.chmod(0o600)
    shutil.copyfile(REPO / "databases/secrets/p1_harness_ro.pw", secret_dir / "p1.password")
    (secret_dir / "p1.password").chmod(0o600)
    config_path = state / "deployment.json"
    if not config_path.exists():
        write_json(config_path, {"label": LABEL, "port": args.port, "pg_port": args.pg_port})
    config = json.loads(config_path.read_text())
    probe = subprocess.run(["docker", "inspect", CONTAINER], capture_output=True)
    if probe.returncode:
        command(["docker", "volume", "create", VOLUME])
        command(
            [
                "docker",
                "run",
                "-d",
                "--name",
                CONTAINER,
                "--restart",
                "unless-stopped",
                "-p",
                f"127.0.0.1:{config['pg_port']}:5432",
                "-e",
                "POSTGRES_USER=harness",
                "-e",
                "POSTGRES_DB=harness",
                "-e",
                "POSTGRES_PASSWORD_FILE=/run/secrets/password",
                "-v",
                f"{password}:/run/secrets/password:ro",
                "-v",
                f"{VOLUME}:/var/lib/postgresql/data",
                "postgres:16-alpine",
            ]
        )
    else:
        existing = json.loads(probe.stdout)[0]
        mounts = existing["Mounts"]
        if not any(m.get("Name") == VOLUME for m in mounts) or not any(
            m.get("Source") == str(password) for m in mounts
        ):
            raise RehearsalFailure(
                "Existing canary container belongs to a different state directory"
            )
        command(["docker", "start", CONTAINER])
    for _ in range(60):
        if (
            subprocess.run(
                ["docker", "exec", CONTAINER, "pg_isready", "-U", "harness"], capture_output=True
            ).returncode
            == 0
        ):
            break
        time.sleep(1)
    else:
        raise RehearsalFailure("Persistent PostgreSQL did not become ready")
    manifest = {
        "version": 1,
        "authMode": "dev",
        "users": [{"subject": p, "roles": ["dba"]} for p in PRINCIPALS]
        + [{"subject": ADMIN, "roles": ["administrator"]}],
        "targets": [
            {
                "name": "p1-19c-prod",
                "host": "localhost",
                "port": 1523,
                "serviceName": "PRODCDB",
                "username": "HARNESS_RO",
                "defaultSchema": "SHOPAPP",
                "environment": "production",
                "secretReference": "p1",
                "passwordFile": "p1.password",
                "worksheetsEnabled": False,
                "mutatingRunbooksEnabled": False,
            }
        ],
    }
    write_json(state / "manifest.json", manifest)
    if not api_alive(state):
        command(
            [
                sys.executable,
                "scripts/bootstrap_targets.py",
                "--manifest",
                str(state / "manifest.json"),
            ],
            cwd=REPO,
            env=environment(state),
        )
        with (state / "api.log").open("ab") as log:
            process = subprocess.Popen(
                api_arguments(config["port"]),
                cwd=REPO,
                env=environment(state),
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        (state / "api.pid").write_text(str(process.pid))
    (state / "api.port").write_text(str(config["port"]))
    with httpx.Client(base_url=f"http://127.0.0.1:{config['port']}") as client:
        for _ in range(60):
            try:
                if client.get("/healthz").is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RehearsalFailure("Canary API did not become healthy")
    if args.install_units:
        directory = Path.home() / ".config/systemd/user"
        directory.mkdir(parents=True, exist_ok=True)
        for name, body in render_units(REPO, state, args.report_dir).items():
            (directory / name).write_text(body)
        command(["systemctl", "--user", "daemon-reload"])


def stop(state: Path, *, api_only: bool = False) -> None:
    if api_alive(state):
        pid = int((state / "api.pid").read_text())
        os.kill(pid, signal.SIGTERM)
        for _ in range(60):
            if not api_alive(state):
                break
            time.sleep(0.5)
        else:
            raise RehearsalFailure("API did not stop; PostgreSQL left running")
    (state / "api.pid").unlink(missing_ok=True)
    if not api_only:
        command(["docker", "stop", CONTAINER])


class Deployment:
    def __init__(self, state: Path):
        config = json.loads((state / "deployment.json").read_text())
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{config['port']}/api/v1", timeout=120
        )
        self.tokens: dict[str, str] = {}
        for principal in [*PRINCIPALS, ADMIN]:
            response = self.client.post(
                "/auth/dev-token",
                json={
                    "subject": principal,
                    "roles": ["administrator"] if principal == ADMIN else ["dba"],
                },
            )
            response.raise_for_status()
            self.tokens[principal] = response.json()["accessToken"]
        response = self.request("GET", "/targets")
        response.raise_for_status()
        self.profile = next(p for p in response.json() if p["name"] == "p1-19c-prod")
        self.pid = self.profile["id"]
        if (
            self.profile["environment"] != "production"
            or self.profile["worksheetsEnabled"]
            or self.profile["mutatingRunbooksEnabled"]
        ):
            raise RehearsalFailure("Production boundary configuration differs")

    def request(
        self, method: str, path: str, principal: str = ADMIN, payload: Any = None
    ) -> httpx.Response:
        return self.client.request(
            method,
            path,
            headers={"Authorization": "Bearer " + self.tokens[principal]},
            json=payload,
        )

    def audit(self) -> list[dict[str, Any]]:
        response = self.request("GET", "/audit?limit=1000")
        response.raise_for_status()
        return list(response.json())

    def workflow(
        self,
        method: str,
        path: str,
        principal: str,
        payload: Any = None,
        refusal: str | None = None,
        audit_required: bool = True,
    ) -> dict[str, Any]:
        previous = {row["id"] for row in self.audit()}
        response = self.request(method, path, principal, payload)
        if "text/event-stream" in response.headers.get("content-type", ""):
            events = [
                json.loads(line[5:].strip())
                for line in response.text.splitlines()
                if line.startswith("data:")
            ]
            body = {"events": events}
        else:
            body = response.json()
        if refusal:

            def codes(value: Any) -> list[str]:
                if isinstance(value, dict):
                    return ([value["code"]] if "code" in value else []) + [
                        code for child in value.values() for code in codes(child)
                    ]
                if isinstance(value, list):
                    return [code for child in value for code in codes(child)]
                return []

            if refusal not in codes(body):
                raise RehearsalFailure(f"Expected refusal absent: {path}")
        elif (
            not response.is_success
            or observation_errors(body)
            or body.get("state") in {"failed", "refused"}
            or body.get("outcome") in {"failed", "refused"}
        ):
            raise RehearsalFailure(f"Observation failed: {path}")
        if audit_required:
            check_audit(
                self.audit(), previous, principal, self.pid, "refused" if refusal else "succeeded"
            )
        return {
            "path": path,
            "status": response.status_code,
            "outcome": "refused" if refusal else "succeeded",
            "body": body,
            "audit_checked": audit_required,
        }


def connect(admin: bool = True) -> Any:
    import oracledb

    settings = load_admin_settings(REPO / "databases/qual/p1.env", REPO)
    if settings["HARNESS_QUAL_ADMIN_DSN"] != DSN:
        raise RehearsalFailure("Acting DBA DSN differs from P1; no connection made")
    return oracledb.connect(
        user=settings["HARNESS_QUAL_ADMIN_USER"] if admin else "HARNESS_RO",
        password=settings["HARNESS_QUAL_ADMIN_PASSWORD"]
        if admin
        else (REPO / "databases/secrets/p1_harness_ro.pw").read_text().strip(),
        dsn=DSN,
        expire_time=1,
    )


def snapshot(db: Any) -> dict[str, Any]:
    with db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM v$session WHERE username = 'HARNESS_RO'")
        sessions = cursor.fetchone()[0]

        def counters(predicate: str) -> dict[str, list[float]]:
            cursor.execute(
                "SELECT sql_id, child_number, child_address, first_load_time, elapsed_time, buffer_gets, executions FROM v$sql WHERE "
                + predicate
            )
            return {f"{r[0]}:{r[1]}:{r[2]}:{r[3]}": [float(v or 0) for v in r[4:]] for r in cursor}

        user_counters = counters(
            "parsing_user_id = (SELECT user_id FROM dba_users WHERE username = 'HARNESS_RO')"
        )
        schema_counters = counters("parsing_schema_name = 'HARNESS_RO'")
    return {"sessions": sessions, "counters": user_counters, "schema_counters": schema_counters}


def measure_cost(
    before: dict[str, Any], after: dict[str, Any], samples: list[int]
) -> dict[str, float]:
    if set(before["counters"]) - set(after["counters"]):
        raise RehearsalFailure("v$sql cursors aged out; cost cannot be verified")
    totals = [0.0, 0.0, 0.0]
    for key, counters in after["counters"].items():
        delta = [
            a - b for a, b in zip(counters, before["counters"].get(key, [0, 0, 0]), strict=True)
        ]
        if min(delta) < 0:
            raise RehearsalFailure("v$sql counters reset; cost cannot be verified")
        totals = [a + b for a, b in zip(totals, delta, strict=True)]
    return {
        "sessions": max([before["sessions"], after["sessions"], *samples]),
        "elapsed_seconds": totals[0] / 1_000_000,
        "buffer_gets": totals[1],
        "executions": totals[2],
    }


def run_pass(
    deployment: Deployment, report: dict[str, Any], budget: dict[str, float], directory: Path
) -> None:
    principal = PRINCIPALS[len(list((directory / "passes").glob("*.json"))) % 3]
    report.update(
        principal=principal,
        deviations=[
            "Local dev auth, not deployed OIDC/TLS",
            "Acting DBA approved parsing-user cost enforcement; parsing-schema counters are secondary only",
            "Successful identity probe has no audit event in the existing API; all observation and refusal workflows require fresh audits",
        ],
        cost_scope=JUSTIFICATION,
        budget_scope=BUDGET_SCOPE,
        requests=[],
    )
    with connect() as db:
        before = snapshot(db)
        samples: list[int] = []
        errors: list[str] = []
        done = threading.Event()

        def sample() -> None:
            try:
                with connect() as sampler_db:
                    while not done.wait(0.25):
                        with sampler_db.cursor() as cursor:
                            cursor.execute(
                                "SELECT COUNT(*) FROM v$session WHERE username = 'HARNESS_RO'"
                            )
                            samples.append(cursor.fetchone()[0])
            except Exception:
                errors.append("Session sampler failed")

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        pid = deployment.pid
        workflows = [
            ("POST", f"/targets/{pid}/test", None, None),
            ("GET", f"/targets/{pid}/schemas", None, None),
            ("GET", f"/targets/{pid}/objects?owner=SHOPAPP&limit=10", None, None),
            ("GET", f"/targets/{pid}/dba/overview", None, None),
            ("GET", f"/tuning/{pid}/cursors?limit=5", None, None),
            ("POST", "/runbooks/runbook.health_report/run", {"profileId": pid}, None),
            (
                "POST",
                "/tuning/explain",
                {"profileId": pid, "statement": "SELECT 1 FROM dual"},
                EXPECTED_REFUSALS["/tuning/explain"],
            ),
            ("POST", "/worksheets", {"profileId": pid}, EXPECTED_REFUSALS["/worksheets"]),
            (
                "POST",
                "/runbooks/runbook.gather_table_stats/run",
                {
                    "profileId": pid,
                    "parameters": {"owner": "SHOPAPP", "table_name": "ORDERS"},
                    "confirm": True,
                },
                EXPECTED_REFUSALS["/runbooks/runbook.gather_table_stats/run"],
            ),
            *[
                (
                    "POST",
                    "/copilot/requests",
                    {
                        "action": action,
                        "targetReference": pid,
                        "profileId": pid,
                        "userMessage": "Canary refusal check",
                        "subject": "SHOPAPP.ORDERS",
                    },
                    EXPECTED_REFUSALS["/copilot/requests"],
                )
                for action in ("explain", "kiwi.explain_object")
            ],
        ]
        try:
            for method, path, payload, refusal in workflows:
                result = deployment.workflow(
                    method,
                    path,
                    principal,
                    payload,
                    refusal,
                    audit_required=not path.endswith("/test"),
                )
                body = result.pop("body")
                report["requests"].append(result)
                if path.endswith("/test"):
                    version = body.get("identity", {}).get("version", "")
                    if not body.get("connected") or version.split(".")[0] != "19":
                        raise RehearsalFailure("P1 probe did not report major version 19")
                    report["major_version"] = 19
                if path == f"/tuning/{pid}/cursors?limit=5" and body.get("rows"):
                    columns = [c.lower() for c in body["columns"]]
                    first = body["rows"][0]
                    workflows.append(
                        (
                            "GET",
                            f"/tuning/{pid}/cursors/{first[columns.index('sql_id')]}?childNumber={first[columns.index('child_number')]}",
                            None,
                            None,
                        )
                    )
        finally:
            done.set()
            sampler.join(timeout=10)
        if sampler.is_alive() or errors:
            raise RehearsalFailure("Session sampling incomplete")
        after = snapshot(db)
        cost = measure_cost(before, after, samples)
        try:
            schema_cost = measure_cost(
                {"sessions": before["sessions"], "counters": before["schema_counters"]},
                {"sessions": after["sessions"], "counters": after["schema_counters"]},
                samples,
            )
            report["parsing_schema_cost"] = schema_cost
        except RehearsalFailure:
            # The secondary figure must never override the enforced user-scope gate.
            report["parsing_schema_cost"] = None
            report["parsing_schema_note"] = (
                "Secondary schema counters aged/reset; primary parsing-user measurement remains enforced"
            )
        report.update(
            cost=cost,
            session_samples=len(samples) + 2,
            budget_failures=compare_budget(cost, budget),
        )
        if report["budget_failures"]:
            raise RehearsalFailure("Database-cost budget exceeded")


def drill(deployment: Deployment, report: dict[str, Any]) -> None:
    import oracledb

    pid = deployment.pid
    principal = PRINCIPALS[0]
    report["steps"] = []

    def account_status(db: Any) -> str:
        with db.cursor() as cursor:
            cursor.execute("SELECT account_status FROM dba_users WHERE username = 'HARNESS_RO'")
            return str(cursor.fetchone()[0])

    def toggle(enabled: bool) -> None:
        response = deployment.request(
            "POST",
            f"/admin/targets/{pid}/{'enable' if enabled else 'disable'}",
            payload={
                "reason": "P1 G3 AI-agent failure-gate drill restoration"
                if enabled
                else "P1 G3 AI-agent failure-gate drill"
            },
        )
        response.raise_for_status()

    with connect() as db:
        if account_status(db) != "OPEN" or not deployment.profile.get("enabled", True):
            raise RehearsalFailure("Drill requires initially OPEN account and enabled target")
        lock_attempted = False
        try:
            toggle(False)
            deployment.workflow(
                "GET",
                f"/targets/{pid}/objects?owner=SHOPAPP&limit=1",
                principal,
                refusal="target_disabled",
            )
            deployment.workflow(
                "POST", f"/targets/{pid}/test", principal, refusal="target_disabled"
            )
            report["steps"].append("disabled target refuses observations and connection test")
            lock_attempted = True
            with db.cursor() as cursor:
                cursor.execute("ALTER USER HARNESS_RO ACCOUNT LOCK")
            try:
                connection = connect(False)
            except oracledb.DatabaseError as exc:
                if getattr(exc.args[0], "code", None) != 28000:
                    raise RehearsalFailure(
                        "Direct login failed for a reason other than ORA-28000"
                    ) from None
            else:
                connection.close()
                raise RehearsalFailure("Locked account accepted login")
            report["steps"].append("locked account direct login refused with ORA-28000")
            deployment.workflow(
                "GET",
                f"/targets/{pid}/objects?owner=SHOPAPP&limit=1",
                principal,
                refusal="target_disabled",
            )
            report["steps"].append("locked target harness read refused")
        finally:
            try:
                if lock_attempted:
                    for attempt in range(3):
                        try:
                            with connect() as recovery:
                                with recovery.cursor() as cursor:
                                    cursor.execute("ALTER USER HARNESS_RO ACCOUNT UNLOCK")
                            break
                        except Exception:
                            if attempt == 2:
                                raise RehearsalFailure(
                                    "CRITICAL: HARNESS_RO unlock failed"
                                ) from None
            finally:
                toggle(True)
            report["account_status"] = account_status(db)
            if report["account_status"] != "OPEN":
                raise RehearsalFailure("CRITICAL: HARNESS_RO is not OPEN after restoration")
            deployment.workflow("GET", f"/targets/{pid}/objects?owner=SHOPAPP&limit=1", principal)
            report["restored"] = True
            report["steps"].append(
                "finally restored OPEN account, enabled target and successful audited observation"
            )


def execute(args: argparse.Namespace, budget: dict[str, float]) -> int:
    timestamp = stamp()
    report: dict[str, Any] = {"label": LABEL, "at": datetime.now(UTC).isoformat(), "success": False}
    deployment = None
    try:
        deployment = Deployment(args.state_dir)
        report["target_id"] = deployment.pid
        if args.subcommand == "pass":
            run_pass(deployment, report, budget, args.report_dir)
        else:
            drill(deployment, report)
        report["success"] = True
    except Exception as exc:
        report["failure"] = (
            str(exc)
            if isinstance(exc, RehearsalFailure)
            else "Canary failed; secret-bearing exception text omitted"
        )
        report["failure_type"] = type(exc).__name__
        write_json(args.report_dir / "incidents" / f"{timestamp}.json", report)
    finally:
        if deployment is not None:
            deployment.client.close()
        category = "passes" if args.subcommand == "pass" else "drills"
        path = args.report_dir / category / f"{timestamp}.json"
        write_json(path, report)
        if category == "passes":
            path.with_suffix(".md").write_text(
                f"# P1 G3 canary pass\n\n{LABEL}\n\n```json\n{json.dumps(report, indent=2)}\n```\n"
            )
    print(json.dumps(report))
    return 0 if report["success"] else 1


def evaluate(directory: Path, min_days: int = 14) -> dict[str, Any]:
    budget = read_budget(directory)
    reasons: list[str] = []
    days: set[date] = set()
    records = sorted((directory / "passes").glob("*.json"))
    for path in records:
        try:
            record = json.loads(path.read_text())
            at = datetime.fromisoformat(record["at"])
            if at.tzinfo is None:
                raise ValueError("Missing timezone")
            days.add(at.astimezone(UTC).date())
            if record.get("budget_scope") != BUDGET_SCOPE:
                reasons.append(f"Incorrect enforced budget scope: {path.stem}")
            if record.get("label") != LABEL:
                reasons.append(f"Missing required label: {path.stem}")
            if (
                not record.get("success")
                or record.get("budget_failures")
                or compare_budget(record.get("cost", {}), budget)
            ):
                reasons.append(f"Failed pass or budget breach: {path.stem}")
        except (ValueError, KeyError, TypeError, AttributeError):
            reasons.append(f"Invalid pass evidence: {path.stem}")
    span = (max(days) - min(days)).days + 1 if days else 0
    if not days or len(days) < min_days:
        reasons.append(f"Need {min_days} daily passes; have {len(days)} distinct UTC days")
    if days and span != len(days):
        reasons.append("Gap in daily passes since first pass")
    for incident in sorted((directory / "incidents").glob("*.json")):
        resolution_path = directory / "resolutions" / incident.name
        try:
            resolution = json.loads(resolution_path.read_text())
            resolved = (
                resolution.get("incident") == incident.stem
                and resolution.get("resolved_by") == "Claude (AI agent, acting DBA)"
                and bool(resolution.get("at"))
                and bool(resolution.get("note"))
            )
        except (OSError, ValueError, TypeError, AttributeError):
            resolved = False
        if not resolved:
            reasons.append(f"Unresolved incident: {incident.stem}")
    successful_drill = False
    for path in (directory / "drills").glob("*.json"):
        try:
            record = json.loads(path.read_text())
            successful_drill |= (
                record.get("success") is True
                and record.get("restored") is True
                and record.get("account_status") == "OPEN"
                and record.get("label") == LABEL
            )
        except (ValueError, TypeError, AttributeError):
            reasons.append(f"Invalid drill evidence: {path.stem}")
    if not successful_drill:
        reasons.append("No successful restored failure-gate drill")
    return {
        "label": LABEL,
        "at": datetime.now(UTC).isoformat(),
        "pass": not reasons,
        "reasons": reasons,
        "days": len(days),
        "span_days": span,
        "calendar": "UTC",
        "sign_off": "acting DBA: Claude (AI agent, not a human)" if not reasons else None,
    }


def evaluate_and_record(directory: Path, min_days: int = 14) -> dict[str, Any]:
    """Write exit evidence only when explicitly evaluating the completed window."""
    result = evaluate(directory, min_days)
    write_json(directory / "exit.json", result)
    return result


def review(directory: Path, today: date | None = None) -> dict[str, Any]:
    """Summarize local evidence without connections, locks, or mutations."""
    today = today or datetime.now(UTC).date()
    budget = read_budget(directory)
    provisional = evaluate(directory)
    dated = []
    for path in sorted((directory / "passes").glob("*.json")):
        try:
            record = json.loads(path.read_text())
            at = datetime.fromisoformat(record["at"])
            if at.tzinfo is None:
                continue
            dated.append((at.astimezone(UTC), path.stem, record))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue  # evaluate supplies the invalid-evidence failure
    days = {at.date() for at, _, _ in dated}
    first = min(days) if days else None
    through = today if today in days else today - timedelta(days=1)
    expected = max(0, (through - first).days + 1) if first else 0
    missing = [
        (first + timedelta(days=offset)).isoformat()
        for offset in range(expected)
        if first + timedelta(days=offset) not in days
    ]
    latest = None
    request_failures = []
    for _, stem, record in dated:
        for request in record.get("requests", []):
            if (
                (
                    (
                        EXPECTED_REFUSALS.get(request.get("path"))
                        or request.get("expected_refusal")
                        or request.get("refusal")
                    )
                    and request.get("outcome") != "refused"
                )
                or (
                    request.get("audit_checked") is False
                    and not request.get("path", "").endswith("/test")
                )
                or request.get("audit_missing") is True
            ):
                request_failures.append({"pass": stem, "request": request})
    if dated:
        at, stem, record = max(dated, key=lambda item: (item[0], item[1]))
        latest = {"file": stem, "at": record["at"], "success": record.get("success")}
        if "principal" in record:
            latest["principal"] = record["principal"]
        latest["cost"] = {
            metric: {
                "value": record.get("cost", {}).get(metric),
                "ceiling": ceiling,
                "remaining_headroom_percent": (
                    100 * (ceiling - record["cost"][metric]) / ceiling
                    if metric in record.get("cost", {}) and ceiling
                    else None
                ),
            }
            for metric, ceiling in budget.items()
        }
        for field in ("budget_failures",):
            if field in record:
                latest[field] = record[field]
        if "requests" in record:
            latest["request_failures"] = [
                item["request"] for item in request_failures if item["pass"] == stem
            ]
    failures = [
        reason
        for reason in provisional["reasons"]
        if not (reason.startswith("Need 14 daily passes;") and (first is None or expected < 14))
    ]
    if missing:
        failures.append("Missing daily passes: " + ", ".join(missing))
    if request_failures:
        failures.append("Request refusal or audit failures in pass evidence")
    return {
        "label": LABEL,
        "window_start": first.isoformat() if first else None,
        "days_covered": len(days),
        "days_expected_so_far": expected,
        "missing_days": missing,
        "latest_pass": latest,
        "unresolved_incidents": [
            reason.removeprefix("Unresolved incident: ")
            for reason in provisional["reasons"]
            if reason.startswith("Unresolved incident: ")
        ],
        "request_failures": request_failures,
        "provisional_result": provisional,
        "failures_so_far": failures,
        "pass": not failures,
    }


def record_review(directory: Path, result: dict[str, Any], force: bool = False) -> None:
    path = directory / "reviews" / f"{datetime.now(UTC).date().isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w" if force else "x") as output:
        json.dump(
            {**result, "reviewed_by": "Claude (AI agent, acting DBA), not a human", "label": LABEL},
            output,
            indent=2,
        )
        output.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    for name in ("deploy", "stop", "budget", "pass", "drill", "evaluate", "review", "serve"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT)
        sub.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
        if name == "deploy":
            sub.add_argument("--port", type=int, default=8640)
            sub.add_argument("--pg-port", type=int, default=8641)
            sub.add_argument("--install-units", action="store_true")
        if name == "stop":
            sub.add_argument("--api-only", action="store_true")
        if name == "review":
            sub.add_argument("--record", action="store_true")
            sub.add_argument("--force", action="store_true")
        if name == "evaluate":
            sub.add_argument("--min-days", type=int, default=14)
    args = parser.parse_args()

    def interrupted(_signal: int, _frame: Any) -> None:
        raise RehearsalFailure("Canary interrupted; restoration required for an active drill")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    args.report_dir = args.report_dir.resolve()
    args.state_dir = args.state_dir.resolve()
    try:
        if args.subcommand == "budget":
            record_budget(args.report_dir)
            print(LABEL)
            return 0
        if args.subcommand == "review":
            result = review(args.report_dir)
            if args.record:
                record_review(args.report_dir, result, args.force)
            print(json.dumps(result, indent=2))
            return 0 if result["pass"] else 1
        budget = read_budget(args.report_dir)
        if args.subcommand == "evaluate":
            if args.min_days < 1:
                raise RehearsalFailure("min-days must be positive")
            result = evaluate_and_record(args.report_dir, args.min_days)
            print(json.dumps(result, indent=2))
            return 0 if result["pass"] else 1
        if args.subcommand == "serve":
            serve(args.state_dir)
        with state_lock(args.state_dir):
            if args.subcommand == "deploy":
                deploy(args)
            elif args.subcommand == "stop":
                stop(args.state_dir, api_only=args.api_only)
            else:
                with database_locks([DSN], f"P1 canary {args.subcommand}"):
                    return execute(args, budget)
        print(LABEL)
        return 0
    except Exception as exc:
        note = (
            str(exc)
            if isinstance(exc, RehearsalFailure)
            else f"Canary command failed ({type(exc).__name__}); details omitted to protect secrets"
        )
        print(f"{LABEL}: {note}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
