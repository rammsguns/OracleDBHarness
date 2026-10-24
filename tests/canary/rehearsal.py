"""B5: agent-rehearsed on a test stand-in (AI agent, not a human).

Run with ``uv run python -m tests.canary.rehearsal``. Never qualifies G3.
Only D4 is supported; no marker objects or workload DDL are created.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from tests.capacity.driver import find_free_port, split_dsn

LABEL = "agent-rehearsed on a test stand-in (AI agent, not a human)"
BUDGET = {"sessions": 2, "elapsed_seconds": 30.0, "buffer_gets": 1_000_000, "executions": 200}
JUSTIFICATION = (
    "A2 D4 schema enumeration took 1.1–1.3 seconds plus about 1.1 seconds connect; "
    "its metadata p95 was 2517 ms and some probes timed out at 30 seconds. "
    "One sequential observer, no worksheet, and a 30-second aggregate SQL budget "
    "per pass deliberately avoid A2's ten-user/saturation load. Two sessions allow "
    "one harness operation plus transient connection turnover; the acting DBA is excluded. "
    "One million buffer gets and 200 executions are conservative provisional ceilings, "
    "not A2 measurements (A2 did not publish these counters)."
)
PRINCIPALS = ["b5-dba-1", "b5-dba-2", "b5-dba-3"]
GATE = Path("/home/kiwi/OracleDBHarness-B4/databases/reports/2026-10-24-b4/capacity/run.log")


class RehearsalFailure(RuntimeError):
    """A safe, locally authored evidence-gate failure message."""


def load_admin_settings(path: Path, repository: Path) -> dict[str, str]:
    """Read shell-style assignments without executing them; expand only the known REPO placeholder."""
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        parts = shlex.split(line, comments=True)
        if parts and "=" in parts[-1]:
            key, value = parts[-1].split("=", 1)
            values[key] = value.replace("${REPO}", str(repository)).replace(
                "$REPO", str(repository)
            )
    return values


def compare_budget(cost: dict[str, float], budget: dict[str, float]) -> list[str]:
    return [
        key
        for key, ceiling in budget.items()
        if key not in cost or not math.isfinite(cost[key]) or cost[key] < 0 or cost[key] > ceiling
    ]


def check_audit(
    records: list[dict[str, Any]],
    previous: set[str],
    principal: str,
    target: str,
    expected_outcome: str | None = None,
) -> int:
    fresh = [r for r in records if r["id"] not in previous]
    matching = [
        r
        for r in fresh
        if r.get("actorSubject") == principal
        and r.get("profileId") == target
        and r.get("outcome")
        and r.get("operationId")
        and (expected_outcome is None or r.get("outcome") == expected_outcome)
    ]
    if not matching:
        raise RehearsalFailure(
            "Request has no fresh audit execution/refusal with principal, target and outcome"
        )
    return len(matching)


def observation_errors(body: Any) -> list[str]:
    """Catch HTTP-200 panels/runbooks that explicitly report unavailable evidence."""
    errors: list[str] = []
    if isinstance(body, dict):
        if body.get("error") or body.get("available") is False or body.get("outcome") == "degraded":
            errors.append("unavailable observation")
        for value in body.values():
            errors.extend(observation_errors(value))
    elif isinstance(body, list):
        for value in body:
            errors.extend(observation_errors(value))
    return errors


def render_report(report: dict[str, Any]) -> str:
    return f"# B5 G3 canary rehearsal\n\n{LABEL}. G3 stays blocked: no production target.\n\n```json\n{json.dumps(report, indent=2)}\n```\n"


def persist(report: dict[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (directory / "report.md").write_text(render_report(report))


def wait_for_d4(timeout: float = 14400) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if GATE.is_file() and "CAPDONE" in GATE.read_text(errors="replace"):
            return
        if time.monotonic() >= deadline:
            raise RehearsalFailure("D4 gate timed out; no D4 connection made")
        print("Waiting for B4 CAPDONE; no D4 connection made", flush=True)
        time.sleep(min(300, max(0, deadline - time.monotonic())))


def run(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "label": LABEL,
        "gate": "G3 blocked (no production target)",
        "acting_dba_release_owner": "Claude (AI agent, not a human)",
        "cost_scope": "All HARNESS_APP parsing-schema cursors on D4; acting DBA queries excluded. Sessions polled with a 250 ms pause plus query latency (brief peaks may escape sampling). Cursor aging/reset fails verification.",
        "deviations": [
            "Local dev authentication instead of deployed OIDC/TLS",
            "Compressed passes instead of two weeks; no human DBA sign-off",
            *(
                ["Legacy grant-based target disable requested"]
                if args.disable_via_read_grants
                else []
            ),
        ],
        "budget": BUDGET,
        "budget_justification": JUSTIFICATION,
        "passes": [],
        "drill": [],
        "incidents": [],
        "phase": "waiting for D4",
        "restored": False,
    }
    if args.drill_only:
        existing = json.loads((args.report_dir / "report.json").read_text())
        if existing.get("budget") != BUDGET or not existing.get("passes"):
            raise RehearsalFailure(
                "Drill-only requires retained passes with the same predeclared budget"
            )
        report["passes"] = existing["passes"]
        report["incidents"] = existing.get("incidents", [])
    if not args.drill_only and (args.report_dir / "report.json").exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        for extension in ("json", "md"):
            previous = args.report_dir / f"report.{extension}"
            if previous.exists():
                shutil.copyfile(previous, args.report_dir / f"previous-{stamp}.{extension}")
    persist(report, args.report_dir)  # Budget retained BEFORE any connection.
    try:
        wait_for_d4()
    except RehearsalFailure as exc:
        report["phase"] = "D4 gate timed out; never connected"
        report["incidents"].append({"note": str(exc)})
        persist(report, args.report_dir)
        return 1
    report["phase"] = "starting disposable harness"
    # Lazy import: pure tests and report generation never need the Oracle driver.
    import oracledb

    config = json.loads(Path("databases/capacity/a2-run.json").read_text())
    target = next(t for t in config["targets"] if t["name"] == "load-db-3")
    host, port, service = split_dsn(target["dsn"], "D4")
    if target["user"].upper() != "HARNESS_APP":
        raise RehearsalFailure("D4 must use HARNESS_APP")
    admin_env = load_admin_settings(Path("databases/qual/r3-d4.env"), Path.cwd())
    if admin_env["HARNESS_QUAL_ADMIN_DSN"] != target["dsn"]:
        raise RehearsalFailure("Acting DBA DSN differs from D4")

    def connect(admin: bool = False) -> Any:
        return oracledb.connect(
            user=admin_env["HARNESS_QUAL_ADMIN_USER"] if admin else target["user"],
            password=Path(
                str(admin_env["HARNESS_QUAL_ADMIN_PASSWORD_FILE"])
                if admin
                else target["passwordFile"]
            )
            .read_text()
            .strip(),
            dsn=target["dsn"],
            expire_time=1,
        )

    def snapshot(db: Any) -> dict[str, Any]:
        with db.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM v$session WHERE username = 'HARNESS_APP'")
            sessions = cursor.fetchone()[0]
            cursor.execute(
                "SELECT sql_id, child_number, child_address, first_load_time, elapsed_time, buffer_gets, executions FROM v$sql WHERE parsing_schema_name = 'HARNESS_APP'"
            )
            counters = {
                f"{r[0]}:{r[1]}:{r[2]}:{r[3]}": [float(v or 0) for v in r[4:]] for r in cursor
            }
        return {"sessions": sessions, "counters": counters}

    def event(name: str, **detail: Any) -> None:
        report["drill"].append({"at": datetime.now(UTC).isoformat(), "step": name, **detail})
        persist(report, args.report_dir)

    container = f"harness-b5-{os.getpid()}"
    process: subprocess.Popen[bytes] | None = None
    db = None
    tmp_root = Path("/home/kiwi/.cache/harness-capacity-tmp")
    tmp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="b5-", dir=tmp_root) as temp:
        tmp = Path(temp)
        pg_password = tmp / "pg-password"
        pg_password.write_text(secrets.token_urlsafe(32))
        pg_password.chmod(0o600)
        secret_dir = tmp / "secrets"
        secret_dir.mkdir(mode=0o700)
        shutil.copyfile(target["passwordFile"], secret_dir / "d4.password")
        (secret_dir / "d4.password").chmod(0o600)
        pg_port, api_port = find_free_port(), find_free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
        env.update(
            {
                "HARNESS_ENV": "production",
                "HARNESS_AUTH_MODE": "dev",
                "HARNESS_ALLOW_DEV_AUTH_OUTSIDE_DEVELOPMENT": "true",
                "HARNESS_METADATA_URL": f"postgresql+psycopg://harness@127.0.0.1:{pg_port}/harness",
                "HARNESS_METADATA_PASSWORD_FILE": str(pg_password),
                "HARNESS_SECRET_DIR": str(secret_dir),
                "HARNESS_ORACLE_BACKEND": "oracledb",
                "HARNESS_ORACLE_DRIVER_MODE": "thin",
                "HARNESS_COPILOT_ENABLED": "false",
                "HARNESS_KIWI_ENABLED": "false",
                "HARNESS_ALLOWED_ENDPOINTS": f"{host}:{port}",
                "HARNESS_WORKER_PROCESSES": "1",
                "HARNESS_MAX_ROWS": "50",
                "HARNESS_STATEMENT_TIMEOUT_SECONDS": "30",
                "HARNESS_ORACLE_EXPIRE_TIME_MINUTES": "1",
                "HARNESS_SCHEMA_PANEL_CACHE_SECONDS": "0",
            }
        )
        manifest = {
            "version": 1,
            "authMode": "dev",
            "users": [
                *[{"subject": p, "roles": ["dba"]} for p in PRINCIPALS],
                {"subject": "b5-admin", "roles": ["administrator"]},
            ],
            "targets": [
                {
                    "name": "b5-d4",
                    "host": host,
                    "port": port,
                    "serviceName": service,
                    "username": "HARNESS_APP",
                    "defaultSchema": "HARNESS_APP",
                    "environment": "production",
                    "secretReference": "d4",
                    "passwordFile": "d4.password",
                    "worksheetsEnabled": False,
                    "mutatingRunbooksEnabled": False,
                }
            ],
        }
        manifest_path = tmp / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        try:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    container,
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
                ],
                check=True,
                capture_output=True,
            )
            for _ in range(60):
                if (
                    subprocess.run(
                        ["docker", "exec", container, "pg_isready", "-U", "harness"],
                        capture_output=True,
                    ).returncode
                    == 0
                ):
                    break
                time.sleep(1)
            subprocess.run(
                [sys.executable, "scripts/bootstrap_targets.py", "--manifest", str(manifest_path)],
                env=env,
                check=True,
                capture_output=True,
            )
            with (tmp / "api.log").open("wb") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "harness_api.app:create_app",
                        "--factory",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(api_port),
                    ],
                    env=env,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=120) as client:
                for _ in range(60):
                    try:
                        if client.get("/healthz").is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
                tokens = {}
                for p in [*PRINCIPALS, "b5-admin"]:
                    response = client.post(
                        "/api/v1/auth/dev-token",
                        json={
                            "subject": p,
                            "roles": ["administrator"] if p == "b5-admin" else ["dba"],
                        },
                    )
                    response.raise_for_status()
                    tokens[p] = response.json()["accessToken"]

                def request(
                    method: str, path: str, principal: str = "b5-admin", payload: Any = None
                ) -> httpx.Response:
                    return client.request(
                        method,
                        "/api/v1" + path,
                        headers={"Authorization": "Bearer " + tokens[principal]},
                        json=payload,
                    )

                profiles = request("GET", "/targets").json()
                profile = next(p for p in profiles if p["name"] == "b5-d4")
                pid = profile["id"]
                report["target_policy"] = {
                    key: profile.get(key)
                    for key in (
                        "environment",
                        "worksheetsEnabled",
                        "mutatingRunbooksEnabled",
                        "permissions",
                    )
                }
                if (
                    profile["environment"] != "production"
                    or profile["worksheetsEnabled"]
                    or profile["mutatingRunbooksEnabled"]
                ):
                    raise RehearsalFailure("Production boundary configuration differs")
                db = connect(True)
                with db.cursor() as cursor:
                    cursor.execute(
                        "SELECT account_status FROM dba_users WHERE username = 'HARNESS_APP'"
                    )
                    if cursor.fetchone()[0] != "OPEN":
                        raise RehearsalFailure("HARNESS_APP was not initially OPEN; refusing drill")
                for index in range(0 if args.drill_only else args.passes):
                    report["phase"] = f"pass {index + 1}"
                    p = PRINCIPALS[index % 3]
                    if index:
                        try:
                            db.close()
                        except Exception:
                            pass
                        db = connect(True)
                    before = snapshot(db)
                    samples = [before["sessions"]]
                    sample_errors: list[str] = []
                    stop_sampling = threading.Event()

                    def sample_sessions(
                        db: Any = db,
                        stop_sampling: threading.Event = stop_sampling,
                        samples: list[int] = samples,
                        sample_errors: list[str] = sample_errors,
                    ) -> None:
                        try:
                            while not stop_sampling.wait(0.25):
                                with db.cursor() as cursor:
                                    cursor.execute(
                                        "SELECT COUNT(*) FROM v$session WHERE username = 'HARNESS_APP'"
                                    )
                                    samples.append(cursor.fetchone()[0])
                        except Exception:
                            sample_errors.append("session sampler failed")

                    sampler = threading.Thread(target=sample_sessions, daemon=True)
                    sampler.start()
                    result: dict[str, Any] = {
                        "pass": index + 1,
                        "principal": p,
                        "at": datetime.now(UTC).isoformat(),
                        "requests": [],
                        "session_sampling_seconds": 0.25,
                        "audit_records": 0,
                    }
                    workflows = [
                        ("GET", f"/targets/{pid}/schemas", None, False),
                        ("GET", f"/targets/{pid}/objects?owner=HARNESS_APP&limit=10", None, False),
                        ("GET", f"/targets/{pid}/dba/overview", None, False),
                        ("GET", f"/tuning/{pid}/cursors?limit=5", None, False),
                        ("POST", "/runbooks/runbook.health_report/run", {"profileId": pid}, False),
                        (
                            "POST",
                            "/tuning/explain",
                            {"profileId": pid, "statement": "SELECT 1 FROM dual"},
                            True,
                        ),
                        ("POST", "/worksheets", {"profileId": pid}, True),
                        (
                            "POST",
                            "/runbooks/runbook.gather_table_stats/run",
                            {
                                "profileId": pid,
                                "parameters": {"owner": "HARNESS_APP", "table_name": "EMPLOYEES"},
                                "confirm": True,
                            },
                            True,
                        ),
                    ]
                    report["passes"].append(result)
                    try:
                        for method, path, payload, refuse in workflows:
                            old = {r["id"] for r in request("GET", "/audit?limit=1000").json()}
                            response = request(method, path, p, payload)
                            body = response.json()
                            refused = (
                                response.status_code >= 400
                                or body.get("state") in {"refused", "failed"}
                                or body.get("outcome") in {"refused", "failed"}
                                or body.get("error") is not None
                            )
                            if path == f"/tuning/{pid}/cursors?limit=5" and body.get("rows"):
                                columns = [c.lower() for c in body["columns"]]
                                first = body["rows"][0]
                                sql_id = first[columns.index("sql_id")]
                                child = first[columns.index("child_number")]
                                workflows.append(
                                    (
                                        "GET",
                                        f"/tuning/{pid}/cursors/{sql_id}?childNumber={child}",
                                        None,
                                        False,
                                    )
                                )
                            if refuse:
                                errors = [body.get("error", {})] + [
                                    step.get("error", {}) for step in body.get("steps", [])
                                ]
                                if not any(e and e.get("code") == "policy_refused" for e in errors):
                                    raise RehearsalFailure(
                                        "Write refusal did not carry policy_refused"
                                    )
                            if not refuse and observation_errors(body):
                                raise RehearsalFailure("Observation contains unavailable evidence")
                            if refuse != refused:
                                raise RehearsalFailure(f"Unexpected workflow outcome: {path}")
                            result["audit_records"] += check_audit(
                                request("GET", "/audit?limit=1000").json(),
                                old,
                                p,
                                pid,
                                "refused" if refuse else "succeeded",
                            )
                            result["requests"].append(
                                {
                                    "path": path,
                                    "status": response.status_code,
                                    "expected_refusal": refuse,
                                    "outcome": "policy_refused" if refuse else "succeeded",
                                }
                            )
                    finally:
                        stop_sampling.set()
                        sampler.join(timeout=10)
                    if sampler.is_alive() or sample_errors:
                        raise RehearsalFailure("Session peak sampling incomplete")
                    after = snapshot(db)
                    elapsed, gets, executions = 0.0, 0.0, 0.0
                    missing = set(before["counters"]) - set(after["counters"])
                    if missing:
                        raise RehearsalFailure("v$sql cursors aged out; cost cannot be verified")
                    for key, counters in after["counters"].items():
                        prior = before["counters"].get(key, [0, 0, 0])
                        delta = [a - b for a, b in zip(counters, prior, strict=True)]
                        if min(delta) < 0:
                            raise RehearsalFailure("v$sql counters reset; cost cannot be verified")
                        elapsed += delta[0] / 1_000_000
                        gets += delta[1]
                        executions += delta[2]
                    cost = {
                        "sessions": max(*samples, after["sessions"]),
                        "elapsed_seconds": elapsed,
                        "buffer_gets": gets,
                        "executions": executions,
                    }
                    result.update(
                        cost=cost,
                        session_samples=len(samples) + 1,
                        budget_failures=compare_budget(cost, BUDGET),
                    )
                    persist(report, args.report_dir)
                    if result["budget_failures"]:
                        raise RehearsalFailure("Database-cost budget exceeded")
                    if index + 1 < args.passes:
                        time.sleep(args.interval)
                if args.observe_only:
                    report["phase"] = (
                        "observation passes complete; failure-gate drill pending coordinator decision"
                    )
                    report["account_changes"] = 0
                    persist(report, args.report_dir)
                    return 0
                report["phase"] = "failure-gate drill"
                report["lock_choice"] = (
                    "ACCOUNT LOCK preserves existing grants; ACCOUNT UNLOCK avoids revoke/re-grant ambiguity."
                )

                def grants_snapshot() -> list[Any]:
                    grants: list[Any] = []
                    with db.cursor() as cursor:
                        for sql in (
                            "SELECT granted_role, admin_option, default_role FROM dba_role_privs WHERE grantee = 'HARNESS_APP' ORDER BY granted_role",
                            "SELECT privilege, admin_option FROM dba_sys_privs WHERE grantee = 'HARNESS_APP' ORDER BY privilege",
                            "SELECT owner, table_name, privilege, grantable FROM dba_tab_privs WHERE grantee = 'HARNESS_APP' ORDER BY owner, table_name, privilege",
                        ):
                            cursor.execute(sql)
                            grants.append([list(row) for row in cursor])
                    return grants

                initial_grants = grants_snapshot()
                lock_attempted = False

                def set_read_access(enabled: bool) -> None:
                    if not args.disable_via_read_grants:
                        response = request(
                            "POST",
                            f"/admin/targets/{pid}/{'enable' if enabled else 'disable'}",
                            payload={
                                "reason": "B7 canary rollback drill restoration"
                                if enabled
                                else "B7 canary failed-gate rollback drill"
                            },
                        )
                        response.raise_for_status()
                        return
                    for principal in [*PRINCIPALS, "b5-admin"]:
                        response = request(
                            "POST",
                            "/admin/grants",
                            payload={
                                "subject": principal,
                                "profileId": pid,
                                "permissions": ["read"],
                            },
                        )
                        response.raise_for_status()
                        if not enabled:
                            revoked = request("DELETE", f"/admin/grants/{response.json()['id']}")
                            revoked.raise_for_status()

                def refused_read() -> dict[str, str]:
                    codes: dict[str, str] = {}
                    # Revocation must also block the connection-test path, which an
                    # existing but empty-permission grant would still allow to probe.
                    for method, path in (
                        ("GET", f"/targets/{pid}/objects?owner=HARNESS_APP&limit=1"),
                        ("POST", f"/targets/{pid}/test"),
                    ):
                        old = {r["id"] for r in request("GET", "/audit?limit=1000").json()}
                        response = request(method, path, PRINCIPALS[0])
                        code = response.json().get("error", {}).get("code")
                        expected = (
                            "not_authorized" if args.disable_via_read_grants else "target_disabled"
                        )
                        if code != expected or response.status_code != 403:
                            raise RehearsalFailure(
                                f"Disabled target did not refuse access with {expected}"
                            )
                        check_audit(
                            request("GET", "/audit?limit=1000").json(),
                            old,
                            PRINCIPALS[0],
                            pid,
                            "refused",
                        )
                        codes[method + " " + path] = code
                    return codes

                try:
                    set_read_access(False)
                    event("disabled harness target", error_code=refused_read())
                    event("locking HARNESS_APP; restoration armed")
                    lock_attempted = True
                    with db.cursor() as cursor:
                        cursor.execute("ALTER USER harness_app ACCOUNT LOCK")
                    event("HARNESS_APP locked", harness_error=refused_read())
                    try:
                        connection = connect()
                    except oracledb.DatabaseError as exc:
                        error = exc.args[0]
                        if getattr(error, "code", None) != 28000:
                            raise RehearsalFailure(
                                "Direct login failed for a reason other than account lock"
                            ) from None
                        event("direct login refused", oracle_code="ORA-28000")
                    else:
                        connection.close()
                        raise RehearsalFailure("Locked account accepted direct login")
                finally:
                    try:
                        if lock_attempted:
                            unlock_error = True
                            for attempt in range(3):
                                try:
                                    with connect(True) as recovery_db:
                                        with recovery_db.cursor() as cursor:
                                            cursor.execute("ALTER USER harness_app ACCOUNT UNLOCK")
                                    unlock_error = False
                                    break
                                except Exception:
                                    event("unlock retry required", attempt=attempt + 1)
                            if unlock_error:
                                raise RehearsalFailure("All unlock attempts failed")
                            event("HARNESS_APP unlocked in finally")
                        set_read_access(True)
                        event(
                            "read grants re-enabled in finally"
                            if args.disable_via_read_grants
                            else "target re-enabled in finally"
                        )
                        with db.cursor() as cursor:
                            cursor.execute(
                                "SELECT account_status FROM dba_users WHERE username = 'HARNESS_APP'"
                            )
                            if cursor.fetchone()[0] != "OPEN":
                                raise RehearsalFailure("Restored account is not OPEN")
                        if grants_snapshot() != initial_grants:
                            raise RehearsalFailure("Oracle grants differ after restoration")
                        with connect() as connection:
                            with connection.cursor() as cursor:
                                cursor.execute("SELECT 1 FROM dual")
                                if cursor.fetchone()[0] != 1:
                                    raise RehearsalFailure("Restored direct read failed")
                        restored_audits = 0
                        for principal in PRINCIPALS:
                            old = {r["id"] for r in request("GET", "/audit?limit=1000").json()}
                            response = request(
                                "GET",
                                f"/targets/{pid}/objects?owner=HARNESS_APP&limit=1",
                                principal,
                            )
                            response.raise_for_status()
                            if observation_errors(response.json()):
                                raise RehearsalFailure("Restored HTTP read failed")
                            restored_audits += check_audit(
                                request("GET", "/audit?limit=1000").json(),
                                old,
                                principal,
                                pid,
                                "succeeded",
                            )
                        report["restored"] = True
                        report["oracle_grants_unchanged"] = True
                        event(
                            "recovery verified",
                            account_status="OPEN",
                            direct_read=True,
                            harness_read=True,
                            oracle_grants_unchanged=True,
                            restored_principals=len(PRINCIPALS),
                            restored_read_audits=restored_audits,
                        )
                    except Exception:
                        event("CRITICAL: restoration failed; acting DBA must restore immediately")
                        print(
                            "CRITICAL: D4 HARNESS_APP restoration could not be verified; acting DBA intervention required",
                            file=sys.stderr,
                            flush=True,
                        )
                        raise RehearsalFailure(
                            "CRITICAL: D4 HARNESS_APP restoration failed"
                        ) from None
                report["phase"] = "completed"
                return 0
        except Exception as exc:
            # Never persist exception text, DSNs, SQL rows, tokens or subprocess output.
            report["incidents"].append(
                {
                    "type": type(exc).__name__,
                    "note": str(exc)
                    if isinstance(exc, RehearsalFailure)
                    else "Rehearsal stopped; secret-bearing exception text omitted",
                }
            )
            return 1
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
            persist(report, args.report_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--disable-via-read-grants",
        action="store_true",
        help="Use the legacy grant-revocation drill instead of the target disable endpoint",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--observe-only",
        action="store_true",
        help="Run authorized observation passes; leave the account-lock drill pending",
    )
    mode.add_argument(
        "--drill-only",
        action="store_true",
        help="Retain prior pass evidence and run only the failure-gate drill",
    )
    parser.add_argument("--passes", type=int, default=6)
    parser.add_argument("--interval", type=float, default=300)
    parser.add_argument(
        "--report-dir", type=Path, default=Path("databases/reports/2026-10-24-b5-canary")
    )
    args = parser.parse_args()
    if args.passes < 1 or args.interval < 0:
        parser.error("passes must be positive and interval nonnegative")

    def interrupted(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt("Rehearsal interrupted; restoring and cleaning up")

    signal.signal(signal.SIGTERM, interrupted)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
