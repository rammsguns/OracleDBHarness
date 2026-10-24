"""Offline unit tests for the NP-07 three-database capacity run driver."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.bootstrap_targets import validate_manifest
from tests.capacity import metrics
from tests.capacity.driver import (
    DriverConfigError,
    compute_stats,
    extract_report_verdict,
    generate_manifest,
    generate_workloads,
    prepare_secrets,
    split_dsn,
    validate_driver_config,
)
from tests.capacity.report import CapacityReport
from tests.capacity.runner import CapacityRun, Response
from tests.capacity.workload import load, parse

EXAMPLE_CONFIG = Path(__file__).resolve().parents[1] / "capacity" / "capacity-run.example.json"
EXAMPLE_WORKLOAD = Path(__file__).resolve().parents[1] / "capacity" / "workload.example.json"


def _valid_config_dict() -> dict[str, Any]:
    return json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))


# --- Configuration Validation Tests ---


def test_example_config_file_is_valid() -> None:
    data = _valid_config_dict()
    config = validate_driver_config(data)
    assert config.api_port == 8000
    assert config.postgres.url is None
    assert len(config.targets) == 3
    assert config.targets[0].name == "load-db-1"
    assert config.targets[0].host == "dbhost1.example.internal"
    assert config.targets[0].port == 1521
    assert config.targets[0].service_name == "LOADPDB1"
    assert config.targets[0].user == "harness_app"


@pytest.mark.parametrize("bad_port", [0, -1, 65536, "8000", True, None])
def test_invalid_api_port_rejected(bad_port: Any) -> None:
    data = _valid_config_dict()
    data["apiPort"] = bad_port
    with pytest.raises(DriverConfigError, match="apiPort"):
        validate_driver_config(data)


def test_api_port_nested_object_supported() -> None:
    data = _valid_config_dict()
    del data["apiPort"]
    data["api"] = {"port": 9000}
    config = validate_driver_config(data)
    assert config.api_port == 9000


@pytest.mark.parametrize("target_count", [0, 1, 2, 4, 5])
def test_target_count_must_be_three(target_count: int) -> None:
    data = _valid_config_dict()
    base_target = data["targets"][0]
    data["targets"] = [
        {
            **base_target,
            "name": f"load-db-{i}",
            "dsn": f"dbhost{i}.example.internal:1521/PDB{i}",
        }
        for i in range(target_count)
    ]
    with pytest.raises(DriverConfigError, match="NP-07 requires exactly 3 targets"):
        validate_driver_config(data)


def test_duplicate_target_names_rejected() -> None:
    data = _valid_config_dict()
    data["targets"][1]["name"] = data["targets"][0]["name"]
    with pytest.raises(DriverConfigError, match="Duplicate target name"):
        validate_driver_config(data)


def test_duplicate_target_dsns_rejected() -> None:
    data = _valid_config_dict()
    data["targets"][1]["dsn"] = data["targets"][0]["dsn"]
    with pytest.raises(DriverConfigError, match="duplicate DSN"):
        validate_driver_config(data)


@pytest.mark.parametrize(
    "bad_dsn",
    [
        "nohostportservice",
        "host:notaport/service",
        "host:1521",
        ":1521/service",
        "host:1521/",
        "host:70000/service",
    ],
)
def test_invalid_dsn_formats_rejected(bad_dsn: str) -> None:
    data = _valid_config_dict()
    data["targets"][0]["dsn"] = bad_dsn
    with pytest.raises(DriverConfigError, match="DSN"):
        validate_driver_config(data)


def test_split_dsn_valid() -> None:
    host, port, service = split_dsn("db.example.internal:1521/MYPDB", "test-db")
    assert host == "db.example.internal"
    assert port == 1521
    assert service == "MYPDB"


@pytest.mark.parametrize(
    ("pg_input", "expected_url"),
    [
        (None, None),
        ({"url": None}, None),
        (
            {"url": "postgresql+psycopg://harness:pw@127.0.0.1:5432/harness"},
            "postgresql+psycopg://harness:pw@127.0.0.1:5432/harness",
        ),
        (
            "postgresql+psycopg://harness:pw@127.0.0.1:5432/harness",
            "postgresql+psycopg://harness:pw@127.0.0.1:5432/harness",
        ),
    ],
)
def test_postgres_options(pg_input: Any, expected_url: str | None) -> None:
    data = _valid_config_dict()
    data["postgres"] = pg_input
    config = validate_driver_config(data)
    assert config.postgres.url == expected_url


def test_check_files_enforces_existing_password_files(tmp_path: Path) -> None:
    data = _valid_config_dict()
    with pytest.raises(DriverConfigError, match="not an existing file"):
        validate_driver_config(data, check_files=True)

    # Create real files
    for i, t in enumerate(data["targets"]):
        pw = tmp_path / f"pwd_{i}.txt"
        pw.write_text("secret_password\n", encoding="utf-8")
        t["passwordFile"] = str(pw)

    config = validate_driver_config(data, check_files=True)
    assert len(config.targets) == 3


def test_prepare_secrets_stages_files_with_mode_600(tmp_path: Path) -> None:
    data = _valid_config_dict()
    for i, t in enumerate(data["targets"]):
        pw = tmp_path / f"src_pwd_{i}.txt"
        pw.write_text(f"pass_{i}", encoding="utf-8")
        t["passwordFile"] = str(pw)

    config = validate_driver_config(data, check_files=True)
    secret_dir = tmp_path / "staged_secrets"
    prepare_secrets(config, secret_dir)

    for t in config.targets:
        staged = secret_dir / f"{t.name}.password"
        assert staged.is_file()
        assert staged.read_text(encoding="utf-8") == Path(t.password_file).read_text(
            encoding="utf-8"
        )
        assert (staged.stat().st_mode & 0o777) == 0o600


# --- Manifest Generation Tests ---


def test_generate_manifest_matches_bootstrap_format() -> None:
    config = validate_driver_config(_valid_config_dict())
    manifest = generate_manifest(config)

    assert manifest["version"] == 1
    assert manifest["authMode"] == "dev"
    assert len(manifest["users"]) == 10
    expected_subjects = [f"load-user-{i:02d}" for i in range(1, 11)]
    assert [u["subject"] for u in manifest["users"]] == expected_subjects
    for u in manifest["users"]:
        assert u["roles"] == ["developer"]

    assert len(manifest["targets"]) == 3
    for target_spec, config_target in zip(manifest["targets"], config.targets, strict=True):
        assert target_spec["name"] == config_target.name
        assert target_spec["environment"] == "test"
        assert target_spec["host"] == config_target.host
        assert target_spec["port"] == config_target.port
        assert target_spec["serviceName"] == config_target.service_name
        assert target_spec["username"] == config_target.user
        assert target_spec["defaultSchema"] == config_target.user.upper()
        assert target_spec["secretReference"] == config_target.name
        assert target_spec["passwordFile"] == f"{config_target.name}.password"
        assert target_spec["worksheetsEnabled"] is True
        assert target_spec["mutatingRunbooksEnabled"] is False
        assert target_spec["protocol"] == "tcp"

    # Validate against bootstrap_targets.py validator
    validated = validate_manifest(copy.deepcopy(manifest))
    assert validated["version"] == 1


# --- Workload Generation Tests ---


def test_generate_workloads_thresholds_phases_and_soak_shape() -> None:
    config = validate_driver_config(_valid_config_dict())
    example_raw = json.loads(EXAMPLE_WORKLOAD.read_text(encoding="utf-8"))
    profile_ids = {
        "load-db-1": "tgt_profile_01",
        "load-db-2": "tgt_profile_02",
        "load-db-3": "tgt_profile_03",
    }

    main_doc, soak_doc = generate_workloads(config, profile_ids, EXAMPLE_WORKLOAD)

    # API configuration
    assert main_doc["api"]["baseUrl"] == f"http://127.0.0.1:{config.api_port}"
    assert main_doc["api"]["auth"] == {"kind": "devToken"}
    assert soak_doc["api"]["baseUrl"] == f"http://127.0.0.1:{config.api_port}"
    assert soak_doc["api"]["auth"] == {"kind": "devToken"}

    # Target profileIds assigned
    for t in main_doc["targets"]:
        assert t["profileId"] == profile_ids[t["name"]]
    for t in soak_doc["targets"]:
        assert t["profileId"] == profile_ids[t["name"]]

    # Thresholds: agreed according to ADR-0013
    for doc in (main_doc, soak_doc):
        t = doc["thresholds"]
        assert t["status"] == "agreed"
        assert t["agreedBy"] == "Claude (AI agent, acting DBA and application owner)"
        assert t["agreedOn"] == "2026-10-24"
        assert t["reference"] == "docs/decisions.md ADR-0013"

        # Every threshold value unchanged from example
        assert t["latencyMs"] == example_raw["thresholds"]["latencyMs"]
        assert (
            t["applicationOverheadP95Ms"] == example_raw["thresholds"]["applicationOverheadP95Ms"]
        )
        assert t["steadyMaxErrorRate"] == example_raw["thresholds"]["steadyMaxErrorRate"]
        assert t["saturation"] == example_raw["thresholds"]["saturation"]
        assert t["recoveryWithinSeconds"] == example_raw["thresholds"]["recoveryWithinSeconds"]
        assert t["maxOutcomeUnknown"] == example_raw["thresholds"]["maxOutcomeUnknown"]
        assert t["maxContaminations"] == example_raw["thresholds"]["maxContaminations"]

    # Main workload phases unchanged
    assert main_doc["phases"] == example_raw["phases"]
    assert main_doc["phases"]["steady"]["seconds"] == 1800
    assert main_doc["phases"]["saturation"]["seconds"] == 300
    assert main_doc["phases"]["recovery"]["seconds"] == 600

    # Soak workload phases: 3600s steady, 1s saturation and recovery
    assert soak_doc["phases"]["steady"]["seconds"] == 3600
    assert soak_doc["phases"]["steady"]["streamsPerUser"] == 1
    assert soak_doc["phases"]["steady"]["thinkTimeScale"] == 1

    assert soak_doc["phases"]["saturation"]["seconds"] == 1
    assert soak_doc["phases"]["saturation"]["streamsPerUser"] == 1
    assert soak_doc["phases"]["saturation"]["thinkTimeScale"] == 0

    assert soak_doc["phases"]["recovery"]["seconds"] == 1
    assert soak_doc["phases"]["recovery"]["streamsPerUser"] == 1
    assert soak_doc["phases"]["recovery"]["thinkTimeScale"] == 1

    # Ensure capacity parser accepts both generated workloads without error
    parsed_main = parse(main_doc)
    parsed_soak = parse(soak_doc)
    assert parsed_main.thresholds.agreed is True
    assert parsed_soak.thresholds.agreed is True
    assert parsed_soak.phase("steady").seconds == 3600.0


# --- Metrics Summary Tests ---


def test_compute_stats() -> None:
    assert compute_stats([]) == {"peak": 0.0, "mean": 0.0, "p95": 0.0}

    single = compute_stats([42.0])
    assert single["peak"] == 42.0
    assert single["mean"] == 42.0
    assert single["p95"] == 42.0

    # 100 values from 1 to 100
    hundred = [float(i) for i in range(1, 101)]
    stats = compute_stats(hundred)
    assert stats["peak"] == 100.0
    assert stats["mean"] == 50.5
    assert stats["p95"] == 95.0


def test_extract_report_verdict(tmp_path: Path) -> None:
    rep = tmp_path / "test_report.md"
    rep.write_text(
        "### Capacity run 2026-10-24 10:00 UTC\n\n"
        "**PASSED - pilot capacity, thresholds agreed by Claude (AI agent, acting DBA and application owner) on 2026-10-24 (docs/decisions.md ADR-0013)**\n\n"
        "- Some details...",
        encoding="utf-8",
    )
    verdict = extract_report_verdict(rep)
    assert verdict.startswith("PASSED - pilot capacity")

    rep.write_text(
        "### Capacity run 2026-10-24 10:00 UTC\n\n**FAILED - 1 criterion(s) not met: latency**\n",
        encoding="utf-8",
    )
    assert extract_report_verdict(rep).startswith("FAILED")

    rep.write_text(
        "### Capacity run 2026-10-24 10:00 UTC\n\n**NOT A PILOT QUALIFICATION - only 2 targets**\n",
        encoding="utf-8",
    )
    assert extract_report_verdict(rep).startswith("NOT A PILOT QUALIFICATION")

    assert extract_report_verdict(tmp_path / "nonexistent.md").startswith("UNKNOWN")


# Measurement regressions use only fake HTTP responses, never a database.


def _measurement_run():
    workload = load(EXAMPLE_WORKLOAD)
    return CapacityRun(workload, CapacityReport(workload, rehearsal=True))


def test_capacity_verification_pages_under_api_cap(monkeypatch):
    run = _measurement_run()
    user = run.users[run.order[0]]
    target = run.workload.targets[0]
    for _ in range(150):
        seq = run.sequence.take(user.subject)
        run.ledger.add(run.ledger.committed, target.name, user.subject, seq)
    monkeypatch.setattr(user, "_open", lambda target: target.name)
    monkeypatch.setattr(user, "request", lambda *args: Response(200, {}, 0))
    pages = []

    def execute(session, statement, binds=None, **limits):
        rows = [[0]]
        truncated = False
        if statement == target.sql["listMarkers"]:
            assert limits["max_rows"] <= 50
            pages.append((binds["lo"], binds["hi"]))
            rows = (
                [[n] for n in range(binds["lo"], min(binds["hi"], 150) + 1)]
                if session == target.name and binds["user_name"] == user.subject
                else []
            )
            truncated = len(rows) > min(50, limits["max_rows"])
            rows = rows[: min(50, limits["max_rows"])]
        return Response(200, {"outcome": {"resultSet": {"rows": rows, "truncated": truncated}}}, 0)

    monkeypatch.setattr(user, "execute", execute)
    run.verify()
    assert pages
    assert not run.verification.lost_commits
    assert not run.verification.incomplete
    for client in run.users.values():
        client.http.close()


@pytest.mark.parametrize(
    "status,code",
    [(404, ""), (403, "not_authorized"), (410, "session_expired"), (409, "session_quarantined")],
)
@pytest.mark.parametrize("retry_ok", [True, False])
def test_capacity_probe_replaces_stale_session_once(monkeypatch, status, code, retry_ok):
    run = _measurement_run()
    owner, peer = (run.users[name] for name in run.order[:2])
    target = run.workload.targets[0]
    peer.probe_sessions[target.name] = "stale"
    monkeypatch.setattr(peer, "_open", lambda target: "fresh")
    calls = []

    def execute(session, *args):
        calls.append(session)
        if session == "stale" or not retry_ok:
            return Response(status, {"error": {"code": code}}, 0)
        return Response(200, {"outcome": {"resultSet": {"rows": [[0]]}}}, 0)

    monkeypatch.setattr(peer, "execute", execute)
    run._probe(owner, target, 1)
    assert calls == ["stale", "fresh"]
    assert bool(run.verification.incomplete) is not retry_ok
    assert run.verification.probes == int(retry_ok)
    for client in run.users.values():
        client.http.close()


def test_capacity_known_idle_session_replaced_before_use(monkeypatch):
    run = _measurement_run()
    user = run.users[run.order[0]]
    target = run.workload.targets[0]
    monkeypatch.setattr("tests.capacity.runner.time.monotonic", lambda: 1000)
    user.sessions[target.name] = "idle"
    user.session_used["idle"] = 0
    monkeypatch.setattr(user, "request", lambda *args: Response(200, {}, 0))
    monkeypatch.setattr(user, "_open", lambda target: "fresh")
    assert user.session(target) == "fresh"
    assert user.sessions_replaced == 1
    assert user.session(target) == "fresh"
    assert user.sessions_replaced == 1
    for client in run.users.values():
        client.http.close()


def test_capacity_sparse_recovery_and_slow_tail():
    workload = load(EXAMPLE_WORKLOAD)
    kinds = list(workload.thresholds.latency_ms)
    samples = [
        metrics.Sample("recovery", kind, "u", "t", second, 10, True)
        for kind in kinds
        for second in range(0, 180, 5 if kind != "cancellation" else 70)
    ]
    assert metrics.recovered_after(samples, workload, 0, 180) == 0
    samples += [
        metrics.Sample("recovery", kinds[0], "u", "t", second, 999999, True)
        for second in range(150, 180)
    ]
    assert metrics.recovered_after(samples, workload, 0, 180) is None
    assert metrics.recovered_after([], workload, 0, 180) is None


def test_capacity_report_breaks_down_targets():
    workload = load(EXAMPLE_WORKLOAD)
    report = CapacityReport(workload, rehearsal=True)
    report.samples = [
        metrics.Sample("steady", "metadata", "u", target.name, 0, value, True)
        for target, value in zip(workload.targets, [100, 200, 1500], strict=True)
    ]
    for target, value in zip(workload.targets, [100, 200, 1500], strict=True):
        summary = report.as_dict()["phases"]["steady"]["targets"][target.name]["metadata"]
        assert summary["client_p50"] == summary["client_p95"] == value
        assert f"| {target.name} | metadata | {value} / {value} |" in report.as_markdown()


def test_capacity_recovery_error_breach_and_missing_kind_fail():
    workload = load(EXAMPLE_WORKLOAD)
    kinds = list(workload.thresholds.latency_ms)
    samples = [
        metrics.Sample("recovery", kind, "u", "t", second, 10, True)
        for kind in kinds
        for second in range(180)
    ]
    samples += [
        metrics.Sample("recovery", kinds[0], "u", "t", second, 10, False, "execution_timeout")
        for second in range(180)
    ]
    assert metrics.recovered_after(samples, workload, 0, 180) is None
    missing = [sample for sample in samples if sample.ok and sample.kind != kinds[0]]
    assert metrics.recovered_after(missing, workload, 0, 180) is None


def test_capacity_active_session_expiration_is_not_retried(monkeypatch):
    run = _measurement_run()
    user = run.users[run.order[0]]
    target = run.workload.targets[0]
    user.sessions[target.name] = "active"
    monkeypatch.setattr("tests.capacity.runner.time.monotonic", lambda: 1000)
    user.session_used["active"] = 999
    calls = []

    def execute(session, *args):
        calls.append(session)
        return Response(404, {"error": {"code": "session_expired"}}, 1)

    monkeypatch.setattr(user, "execute", execute)
    sample = run._read("saturation", user, target, 0)
    assert not sample.ok
    assert sample.code == "session_expired"
    assert calls == ["active"]
    for client in run.users.values():
        client.http.close()
