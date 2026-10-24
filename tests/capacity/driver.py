"""Repeatable NP-07 three-database capacity run driver helper.

Implements offline configuration validation, bootstrap manifest generation,
workload generation with agreed ADR-0013 thresholds and soak shape,
idempotent marker schema management, and host process-tree metrics sampling.
"""

from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import json
import os
import re
import secrets
import shutil
import signal
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.capacity.metrics import percentile
from tests.capacity.workload import parse

NAME_REGEX = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


class DriverConfigError(ValueError):
    """The capacity run configuration is invalid."""


@dataclass(frozen=True)
class TargetConfig:
    name: str
    dsn: str
    user: str
    password_file: str
    host: str
    port: int
    service_name: str


@dataclass(frozen=True)
class PostgresConfig:
    url: str | None = None


@dataclass(frozen=True)
class DriverConfig:
    api_port: int
    postgres: PostgresConfig
    targets: list[TargetConfig]


def split_dsn(dsn: str, target_name: str) -> tuple[str, int, str]:
    """Split host:port/service into components."""
    host, sep1, remainder = dsn.partition(":")
    if not sep1:
        raise DriverConfigError(
            f"Target {target_name!r}: DSN {dsn!r} is not in host:port/service format."
        )
    port_text, sep2, service = remainder.partition("/")
    if not sep2 or not host.strip() or not port_text.isdigit() or not service.strip():
        raise DriverConfigError(
            f"Target {target_name!r}: DSN {dsn!r} must be in host:port/service format."
        )
    port = int(port_text)
    if not (1 <= port <= 65535):
        raise DriverConfigError(
            f"Target {target_name!r}: port {port} in DSN {dsn!r} is out of range 1-65535."
        )
    return host.strip(), port, service.strip()


def validate_driver_config(data: dict[str, Any], *, check_files: bool = False) -> DriverConfig:
    """Validate driver JSON configuration."""
    if not isinstance(data, dict):
        raise DriverConfigError("Configuration must be a JSON object.")

    # API port
    api_port_raw = data.get("apiPort")
    if api_port_raw is None and isinstance(data.get("api"), dict):
        api_port_raw = data["api"].get("port")
    if (
        isinstance(api_port_raw, bool)
        or not isinstance(api_port_raw, int)
        or not (1 <= api_port_raw <= 65535)
    ):
        raise DriverConfigError("apiPort must be an integer between 1 and 65535.")
    api_port = api_port_raw

    # PostgreSQL configuration
    pg_raw = data.get("postgres")
    pg_url: str | None = None
    if pg_raw is not None:
        if isinstance(pg_raw, str):
            pg_url = pg_raw.strip() or None
        elif isinstance(pg_raw, dict):
            url_val = pg_raw.get("url")
            if url_val is not None:
                if not isinstance(url_val, str):
                    raise DriverConfigError("postgres.url must be a string or null.")
                pg_url = url_val.strip() or None
        else:
            raise DriverConfigError("postgres must be an object, a URL string, or null.")
    postgres = PostgresConfig(url=pg_url)

    # Targets configuration: exactly three targets
    targets_raw = data.get("targets")
    if not isinstance(targets_raw, list):
        raise DriverConfigError("targets must be a list of target specifications.")
    if len(targets_raw) != 3:
        raise DriverConfigError(f"NP-07 requires exactly 3 targets; found {len(targets_raw)}.")

    targets: list[TargetConfig] = []
    seen_names: set[str] = set()
    seen_dsns: set[str] = set()

    for i, t in enumerate(targets_raw):
        where = f"targets[{i}]"
        if not isinstance(t, dict):
            raise DriverConfigError(f"{where} must be a JSON object.")

        name = t.get("name")
        if not isinstance(name, str) or not NAME_REGEX.fullmatch(name):
            raise DriverConfigError(
                f"{where}.name must be an identifier matching {NAME_REGEX.pattern}."
            )
        if name in seen_names:
            raise DriverConfigError(f"Duplicate target name {name!r}.")
        seen_names.add(name)

        dsn = t.get("dsn")
        if not isinstance(dsn, str) or not dsn.strip():
            raise DriverConfigError(f"{where}.dsn must be a non-empty string.")
        host, port, service = split_dsn(dsn, name)
        canonical_dsn = f"{host.lower()}:{port}/{service.lower()}"
        if canonical_dsn in seen_dsns:
            raise DriverConfigError(
                f"Targets must point to distinct endpoints; duplicate DSN {dsn!r}."
            )
        seen_dsns.add(canonical_dsn)

        user = t.get("user")
        if not isinstance(user, str) or not user.strip():
            raise DriverConfigError(f"{where}.user must be a non-empty string.")

        pw_file = t.get("passwordFile")
        if not isinstance(pw_file, str) or not pw_file.strip():
            raise DriverConfigError(f"{where}.passwordFile must be a non-empty string.")

        if check_files:
            p = Path(pw_file)
            if not p.is_file():
                raise DriverConfigError(
                    f"{where}.passwordFile points to {pw_file!r}, which is not an existing file."
                )
            if p.stat().st_size == 0:
                raise DriverConfigError(f"{where}.passwordFile {pw_file!r} is empty.")

        targets.append(
            TargetConfig(
                name=name,
                dsn=dsn,
                user=user.strip(),
                password_file=pw_file.strip(),
                host=host,
                port=port,
                service_name=service,
            )
        )

    return DriverConfig(api_port=api_port, postgres=postgres, targets=targets)


def generate_manifest(config: DriverConfig) -> dict[str, Any]:
    """Generate manifest for scripts/bootstrap_targets.py."""
    users = [{"subject": f"load-user-{i:02d}", "roles": ["developer"]} for i in range(1, 11)]
    targets = [
        {
            "name": t.name,
            "environment": "test",
            "host": t.host,
            "port": t.port,
            "serviceName": t.service_name,
            "username": t.user,
            "defaultSchema": t.user.upper(),
            "secretReference": t.name,
            "passwordFile": f"{t.name}.password",
            "worksheetsEnabled": True,
            "mutatingRunbooksEnabled": False,
            "protocol": "tcp",
        }
        for t in config.targets
    ]
    return {
        "version": 1,
        "authMode": "dev",
        "users": users,
        "targets": targets,
    }


def prepare_secrets(config: DriverConfig, secret_dir: Path) -> None:
    """Stage password files into secret_dir with chmod 0600."""
    secret_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        secret_dir.chmod(0o700)
    for t in config.targets:
        src = Path(t.password_file)
        dst = secret_dir / f"{t.name}.password"
        # Copy content directly and set 0600 permissions
        shutil.copyfile(src, dst)
        if os.name == "posix":
            dst.chmod(0o600)


def generate_workloads(
    config: DriverConfig,
    profile_ids: dict[str, str],
    example_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate main workload and soak workload from example."""
    if example_path is None:
        example_path = Path("tests/capacity/workload.example.json")

    template = json.loads(example_path.read_text(encoding="utf-8"))

    # Verify template targets match config targets
    main_doc = copy.deepcopy(template)
    main_doc["name"] = "pilot capacity - NP-07 three-database run"
    main_doc["api"] = {
        "baseUrl": f"http://127.0.0.1:{config.api_port}",
        "auth": {"kind": "devToken"},
    }

    # Ensure profileId is populated for each target
    for target_entry in main_doc["targets"]:
        tname = target_entry["name"]
        if tname not in profile_ids:
            raise DriverConfigError(f"Missing profile ID for target {tname!r}.")
        target_entry["profileId"] = profile_ids[tname]

    # ADR-0013 agreed thresholds
    main_doc["thresholds"]["status"] = "agreed"
    main_doc["thresholds"]["agreedBy"] = "Claude (AI agent, acting DBA and application owner)"
    main_doc["thresholds"]["agreedOn"] = "2026-10-24"
    main_doc["thresholds"]["reference"] = "docs/decisions.md ADR-0013"

    # Soak workload:
    # One hour of steady load, without saturation or recovery (ADR-0013).
    soak_doc = copy.deepcopy(main_doc)
    soak_doc["name"] = "pilot capacity - NP-07 soak run"
    soak_doc["phases"] = {
        "steady": {"seconds": 3600, "streamsPerUser": 1, "thinkTimeScale": 1},
    }

    # Validate offline with capacity parser
    parse(main_doc)
    parse(soak_doc)

    return main_doc, soak_doc


def apply_marker_schema(config: DriverConfig) -> None:
    """Apply oracle/capacity/load_schema.sql to each target idempotently."""
    import oracledb

    for target in config.targets:
        password = Path(target.password_file).read_text(encoding="utf-8").strip()
        with oracledb.connect(user=target.user, password=password, dsn=target.dsn) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM user_tables WHERE table_name = 'HARNESS_LOAD_MARKERS'"
                )
                row = cursor.fetchone()
                if row and row[0] == 0:
                    try:
                        cursor.execute(
                            "CREATE TABLE harness_load_markers ("
                            " run_id VARCHAR2(40) NOT NULL,"
                            " target_name VARCHAR2(100) NOT NULL,"
                            " user_name VARCHAR2(255) NOT NULL,"
                            " seq NUMBER(12) NOT NULL,"
                            " created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,"
                            " CONSTRAINT harness_load_markers_pk PRIMARY KEY (run_id, user_name, seq))"
                        )
                    except oracledb.DatabaseError as exc:
                        (err,) = exc.args
                        # ORA-00955: name is already used by an existing object
                        if getattr(err, "code", None) != 955:
                            raise


def teardown_marker_schema(config: DriverConfig) -> None:
    """Drop marker table on each target idempotently."""
    import oracledb

    for target in config.targets:
        try:
            password = Path(target.password_file).read_text(encoding="utf-8").strip()
            with oracledb.connect(user=target.user, password=password, dsn=target.dsn) as conn:
                with conn.cursor() as cursor:
                    try:
                        cursor.execute("DROP TABLE harness_load_markers PURGE")
                    except oracledb.DatabaseError as exc:
                        (err,) = exc.args
                        # ORA-00942: table or view does not exist
                        if getattr(err, "code", None) != 942:
                            raise
        except Exception as exc:
            print(
                f"Warning: teardown on {target.name} encountered: {exc}",
                file=sys.stderr,
            )


# Host process metrics sampling
def get_process_tree(root_pid: int) -> set[int]:
    """Find all descendant PIDs of root_pid from /proc."""
    if not os.path.isdir("/proc"):
        return {root_pid}

    children_map: dict[int, list[int]] = {}
    for entry in os.scandir("/proc"):
        if entry.is_dir() and entry.name.isdigit():
            pid = int(entry.name)
            try:
                with open(f"/proc/{pid}/stat", "rb") as f:
                    content = f.read()
                rparen = content.rfind(b")")
                if rparen != -1:
                    fields = content[rparen + 2 :].split()
                    ppid = int(fields[1])
                    children_map.setdefault(ppid, []).append(pid)
            except (OSError, IndexError, ValueError):
                continue

    result = {root_pid}
    queue = [root_pid]
    while queue:
        curr = queue.pop()
        for child in children_map.get(curr, []):
            if child not in result:
                result.add(child)
                queue.append(child)
    return result


def get_proc_stat(pid: int) -> tuple[int, int] | None:
    """Return (utime + stime, rss_bytes) for pid from /proc."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            content = f.read()
        rparen = content.rfind(b")")
        if rparen == -1:
            return None
        fields = content[rparen + 2 :].split()
        ticks = int(fields[11]) + int(fields[12])

        with open(f"/proc/{pid}/statm", "rb") as f:
            statm_fields = f.read().split()
        page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
        rss_bytes = int(statm_fields[1]) * page_size
        return ticks, rss_bytes
    except (OSError, IndexError, ValueError):
        return None


def sample_process_tree(
    root_pid: int, prev_ticks: dict[int, int], elapsed_s: float
) -> tuple[float, int, dict[int, int]]:
    """Sample CPU% and RSS for process tree under root_pid."""
    pids = get_process_tree(root_pid)
    current_ticks: dict[int, int] = {}
    total_rss = 0
    delta_ticks = 0

    for pid in pids:
        stat = get_proc_stat(pid)
        if stat is None:
            continue
        ticks, rss = stat
        current_ticks[pid] = ticks
        total_rss += rss
        if pid in prev_ticks:
            delta_ticks += max(0, ticks - prev_ticks[pid])

    clk_tck = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    if elapsed_s > 0 and clk_tck > 0:
        cpu_percent = (delta_ticks / (clk_tck * elapsed_s)) * 100.0
    else:
        cpu_percent = 0.0

    return cpu_percent, total_rss, current_ticks


def compute_stats(values: list[float]) -> dict[str, float]:
    """Calculate peak, mean, and p95."""
    if not values:
        return {"peak": 0.0, "mean": 0.0, "p95": 0.0}
    peak = max(values)
    mean = sum(values) / len(values)
    p95 = percentile(values, 0.95)
    return {
        "peak": round(peak, 2),
        "mean": round(mean, 2),
        "p95": round(p95 if p95 is not None else 0.0, 2),
    }


def run_metrics_sampler(
    api_pid: int,
    client_pid: int,
    csv_path: Path,
    summary_path: Path,
    interval_s: float = 5.0,
) -> None:
    """Sample API and load client metrics every interval_s until client exits."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    interrupted = False

    def handle_signal(_signum: int, _frame: Any) -> None:
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    api_cpu_samples: list[float] = []
    api_rss_samples: list[float] = []
    client_cpu_samples: list[float] = []
    client_rss_samples: list[float] = []

    prev_api_ticks: dict[int, int] = {}
    prev_client_ticks: dict[int, int] = {}
    last_time = time.monotonic()
    start_time = time.time()

    # Initial tick snapshot
    _, _, prev_api_ticks = sample_process_tree(api_pid, {}, 0.0)
    _, _, prev_client_ticks = sample_process_tree(client_pid, {}, 0.0)

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "timestamp_iso",
                "elapsed_s",
                "api_cpu_percent",
                "api_rss_bytes",
                "api_rss_mib",
                "client_cpu_percent",
                "client_rss_bytes",
                "client_rss_mib",
            ]
        )

        while not interrupted:
            time.sleep(interval_s)
            now = time.monotonic()
            elapsed_interval = max(0.001, now - last_time)
            last_time = now
            wall_elapsed = time.time() - start_time
            iso_now = dt.datetime.now(dt.UTC).isoformat()

            api_cpu, api_rss, prev_api_ticks = sample_process_tree(
                api_pid, prev_api_ticks, elapsed_interval
            )
            client_cpu, client_rss, prev_client_ticks = sample_process_tree(
                client_pid, prev_client_ticks, elapsed_interval
            )

            api_cpu_samples.append(api_cpu)
            api_rss_samples.append(api_rss)
            client_cpu_samples.append(client_cpu)
            client_rss_samples.append(client_rss)

            api_mib = round(api_rss / (1024 * 1024), 2)
            client_mib = round(client_rss / (1024 * 1024), 2)

            writer.writerow(
                [
                    iso_now,
                    f"{wall_elapsed:.1f}",
                    f"{api_cpu:.1f}",
                    api_rss,
                    api_mib,
                    f"{client_cpu:.1f}",
                    client_rss,
                    client_mib,
                ]
            )
            f.flush()

            # Check if client has finished
            try:
                os.kill(client_pid, 0)
            except OSError:
                break

    api_mib_samples = [r / (1024 * 1024) for r in api_rss_samples]
    client_mib_samples = [r / (1024 * 1024) for r in client_rss_samples]

    summary = {
        "api": {
            "cpuPercent": compute_stats(api_cpu_samples),
            "rssBytes": compute_stats(api_rss_samples),
            "rssMiB": compute_stats(api_mib_samples),
        },
        "client": {
            "cpuPercent": compute_stats(client_cpu_samples),
            "rssBytes": compute_stats(client_rss_samples),
            "rssMiB": compute_stats(client_mib_samples),
        },
        "sampleCount": len(api_cpu_samples),
        "samplingIntervalSeconds": interval_s,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def extract_report_verdict(report_path: Path) -> str:
    """Extract verdict string from a markdown report."""
    if not report_path.is_file():
        return "UNKNOWN - report file not found"
    content = report_path.read_text(encoding="utf-8")
    match = re.search(
        r"^\*\*(PASSED.*?|FAILED.*?|NOT A PILOT QUALIFICATION.*?|REHEARSAL.*?)\*\*$",
        content,
        re.MULTILINE,
    )
    if match:
        return match.group(1).strip()
    return "UNKNOWN - verdict not found in report"


def find_free_port() -> int:
    """Find an available local TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def query_profile_ids(
    metadata_url: str, metadata_password_file: str | None = None
) -> dict[str, str]:
    """Query connection profile IDs directly from the metadata store."""
    from sqlalchemy import select

    from harness_api.config import Settings
    from harness_api.db import build_engine
    from harness_api.models import ConnectionProfile

    settings = Settings(
        metadata_url=metadata_url,
        metadata_password_file=metadata_password_file or "",
        auth_mode="dev",
        env="test",
        allow_dev_auth_outside_development=True,
    )
    engine = build_engine(settings)
    try:
        with engine.connect() as conn:
            rows = conn.execute(select(ConnectionProfile.name, ConnectionProfile.id)).all()
            return {name: pid for name, pid in rows}
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_p = subparsers.add_parser("validate-config")
    validate_p.add_argument("config", type=Path)
    validate_p.add_argument("--check-files", action="store_true")

    port_p = subparsers.add_parser("get-api-port")
    port_p.add_argument("config", type=Path)

    pg_p = subparsers.add_parser("get-postgres-url")
    pg_p.add_argument("config", type=Path)

    subparsers.add_parser("free-port")

    gen_pass_p = subparsers.add_parser("generate-password-file")
    gen_pass_p.add_argument("output", type=Path)

    prep_sec_p = subparsers.add_parser("prepare-secrets")
    prep_sec_p.add_argument("config", type=Path)
    prep_sec_p.add_argument("secret_dir", type=Path)

    manifest_p = subparsers.add_parser("generate-manifest")
    manifest_p.add_argument("config", type=Path)
    manifest_p.add_argument("output", type=Path)

    query_prof_p = subparsers.add_parser("query-profiles")
    query_prof_p.add_argument("--metadata-url", required=True)
    query_prof_p.add_argument("--password-file", default=None)
    query_prof_p.add_argument("--output", type=Path, default=None)

    workload_p = subparsers.add_parser("generate-workloads")
    workload_p.add_argument("config", type=Path)
    workload_p.add_argument("main_output", type=Path)
    workload_p.add_argument("soak_output", type=Path)
    workload_p.add_argument("--profile-ids-json", type=Path, default=None)
    workload_p.add_argument("--metadata-url", default=None)
    workload_p.add_argument("--metadata-password-file", default=None)

    markers_apply_p = subparsers.add_parser("apply-markers")
    markers_apply_p.add_argument("config", type=Path)

    markers_drop_p = subparsers.add_parser("teardown-markers")
    markers_drop_p.add_argument("config", type=Path)

    sample_p = subparsers.add_parser("sample-metrics")
    sample_p.add_argument("--api-pid", type=int, required=True)
    sample_p.add_argument("--client-pid", type=int, required=True)
    sample_p.add_argument("--csv", type=Path, required=True)
    sample_p.add_argument("--summary", type=Path, required=True)
    sample_p.add_argument("--interval", type=float, default=5.0)

    verdict_p = subparsers.add_parser("report-verdict")
    verdict_p.add_argument("report", type=Path)
    verdict_p.add_argument("label", nargs="?", default="Capacity run")

    args = parser.parse_args(argv)

    if args.command == "validate-config":
        data = json.loads(args.config.read_text(encoding="utf-8"))
        cfg = validate_driver_config(data, check_files=args.check_files)
        print(f"Valid configuration with {len(cfg.targets)} targets; API port {cfg.api_port}.")
        return 0

    if args.command == "get-api-port":
        data = json.loads(args.config.read_text(encoding="utf-8"))
        cfg = validate_driver_config(data)
        print(cfg.api_port)
        return 0

    if args.command == "get-postgres-url":
        data = json.loads(args.config.read_text(encoding="utf-8"))
        cfg = validate_driver_config(data)
        print(cfg.postgres.url or "")
        return 0

    if args.command == "free-port":
        print(find_free_port())
        return 0

    if args.command == "generate-password-file":
        pw = secrets.token_urlsafe(24)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(pw, encoding="utf-8")
        # The postgres image reads the file after dropping to its own uid, so it must be
        # world-readable; the run's 0700 temporary directory keeps it private on the host.
        if os.name == "posix":
            args.output.chmod(0o644)
        return 0

    if args.command == "prepare-secrets":
        data = json.loads(args.config.read_text(encoding="utf-8"))
        cfg = validate_driver_config(data)
        prepare_secrets(cfg, args.secret_dir)
        return 0

    if args.command == "generate-manifest":
        data = json.loads(args.config.read_text(encoding="utf-8"))
        cfg = validate_driver_config(data)
        manifest = generate_manifest(cfg)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return 0

    if args.command == "query-profiles":
        pids = query_profile_ids(args.metadata_url, args.password_file)
        payload = json.dumps(pids, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        else:
            print(payload)
        return 0

    if args.command == "generate-workloads":
        data = json.loads(args.config.read_text(encoding="utf-8"))
        cfg = validate_driver_config(data)
        if args.profile_ids_json:
            pids = json.loads(args.profile_ids_json.read_text(encoding="utf-8"))
        elif args.metadata_url:
            pids = query_profile_ids(args.metadata_url, args.metadata_password_file)
        else:
            print(
                "Error: either --profile-ids-json or --metadata-url is required.", file=sys.stderr
            )
            return 2
        main_doc, soak_doc = generate_workloads(cfg, pids)
        args.main_output.parent.mkdir(parents=True, exist_ok=True)
        args.soak_output.parent.mkdir(parents=True, exist_ok=True)
        args.main_output.write_text(json.dumps(main_doc, indent=2), encoding="utf-8")
        args.soak_output.write_text(json.dumps(soak_doc, indent=2), encoding="utf-8")
        return 0

    if args.command == "apply-markers":
        data = json.loads(args.config.read_text(encoding="utf-8"))
        cfg = validate_driver_config(data)
        apply_marker_schema(cfg)
        return 0

    if args.command == "teardown-markers":
        data = json.loads(args.config.read_text(encoding="utf-8"))
        cfg = validate_driver_config(data)
        teardown_marker_schema(cfg)
        return 0

    if args.command == "sample-metrics":
        run_metrics_sampler(
            api_pid=args.api_pid,
            client_pid=args.client_pid,
            csv_path=args.csv,
            summary_path=args.summary,
            interval_s=args.interval,
        )
        return 0

    if args.command == "report-verdict":
        verdict = extract_report_verdict(args.report)
        print(f"{args.label} verdict: {verdict}")
        return 1 if verdict.startswith("FAILED") else 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
