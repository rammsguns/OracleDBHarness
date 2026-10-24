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
from tests.capacity.runner import CapacityRun, Response, UserClient
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

    # Soak workload: exactly one hour of steady load
    assert soak_doc["phases"]["steady"]["seconds"] == 3600
    assert soak_doc["phases"]["steady"]["streamsPerUser"] == 1
    assert soak_doc["phases"]["steady"]["thinkTimeScale"] == 1

    assert set(soak_doc["phases"]) == {"steady"}

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
    closed = []
    monkeypatch.setattr(
        peer, "request", lambda method, path: closed.append(path) or Response(404, {}, 0)
    )
    run._probe(owner, target, 1)
    assert calls == ["stale", "fresh"]
    assert "/api/v1/worksheets/stale" in closed
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
    closed = []
    monkeypatch.setattr(
        user, "request", lambda method, path: closed.append(path) or Response(200, {}, 0)
    )
    monkeypatch.setattr(user, "_open", lambda target: "fresh")
    assert user.session(target) == "fresh"
    assert user.sessions_replaced == 1
    assert user.session(target) == "fresh"
    assert user.sessions_replaced == 1
    assert closed == ["/api/v1/worksheets/idle"]
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


@pytest.mark.parametrize("missing", ["saturation", "recovery", "steady"])
def test_capacity_main_requires_all_phases(missing):
    from tests.capacity.workload import WorkloadProblem

    document = json.loads(EXAMPLE_WORKLOAD.read_text(encoding="utf-8"))
    del document["phases"][missing]
    with pytest.raises(WorkloadProblem, match=f"phases.{missing} is required"):
        parse(document)


@pytest.mark.parametrize("steady_only", [True, False])
def test_capacity_declared_phases_run_and_soak_can_pass(monkeypatch, steady_only):
    from tests.capacity import runner

    config = validate_driver_config(_valid_config_dict())
    main, soak = generate_workloads(
        config, {target.name: target.name for target in config.targets}, EXAMPLE_WORKLOAD
    )
    workload = parse(soak if steady_only else main)
    seen = []
    checked = []

    class FakeRun:
        def __init__(self, workload, report):
            self.report = report
            self.samples = []
            self.users = {}
            self.verification = metrics.Verification()
            self.gate = type("Gate", (), {"peak": 1})()

        def prepare(self):
            self.report.environment = {"oracleBackend": "oracledb"}
            self.report.identities = {
                target.name: {"hostName": target.name, "databaseName": target.name}
                for target in workload.targets
            }

        def offset(self):
            return 0

        def run_phase(self, phase):
            seen.append((phase.name, phase.seconds))
            self.samples.extend(
                metrics.Sample(phase.name, kind, "u", "t", 0, 10, True, db_ms=5)
                for kind in workload.thresholds.latency_ms
            )
            return True

        def check_ownership(self):
            checked.append("ownership")

        def verify(self):
            checked.append("verification")

        def close_and_check_leaks(self):
            checked.append("leaks")

    monkeypatch.setattr(runner, "CapacityRun", FakeRun)
    report = runner.execute(workload)
    assert seen == [(phase.name, phase.seconds) for phase in workload.phases]
    assert checked == ["ownership", "verification", "leaks"]
    assert report.completed_phases == [phase.name for phase in workload.phases]
    phase_criteria = [c for c in report.criteria if c.name.startswith(("saturation ", "recovery "))]
    assert len(phase_criteria) == 3
    if steady_only:
        assert seen == [("steady", 3600)]
        assert all(c.passed is None for c in phase_criteria)
        assert all(c.measured == "not applicable (steady-only soak)" for c in phase_criteria)
        assert all(c.passed is True for c in report.criteria if c not in phase_criteria)
        assert report.qualified
        assert report.verdict.startswith("PASSED")
        assert report.as_markdown().count("| N/A |") == 3
        assert "Recovery: not applicable (steady-only soak)" in report.as_markdown()
        assert sum(c["passed"] is None for c in report.as_dict()["criteria"]) == 3
        for name in report.CORRECTNESS:
            criterion = next(c for c in report.criteria if c.name == name)
            criterion.passed = False
            assert report.failed
            assert report.verdict.startswith("FAILED")
            criterion.passed = True
    else:
        assert all(c.passed is not None for c in phase_criteria)
        assert not report.qualified  # Short fake recovery has no complete window.
        assert phase_criteria[-1].passed is False


@pytest.mark.parametrize("commit", [True, False])
@pytest.mark.parametrize("stage", ["insert", "end"])
@pytest.mark.parametrize(
    "status,code,unknown",
    [
        (0, "transport_ReadTimeout", True),
        (500, "internal_error", True),
        (502, "outcome_unknown", True),
        (409, "session_busy", False),
        (400, "unrecognized_error", True),
        (200, "outcome_unknown", True),
    ],
)
def test_capacity_transaction_outcomes(monkeypatch, commit, stage, status, code, unknown):
    run = _measurement_run()
    user = run.users[run.order[0]]
    target = run.workload.targets[0]
    user.sessions[target.name] = "transaction"
    error = Response(status, {"error": {"code": code, "message": "API detail"}}, 2)
    calls = []

    def request(method, path):
        calls.append((method, path))
        if path.endswith("/commit" if commit else "/rollback") and len(calls) == 1:
            return error
        return Response(200, {}, 1)

    monkeypatch.setattr(
        user, "execute", lambda *args: error if stage == "insert" else Response(200, {}, 1)
    )
    monkeypatch.setattr(user, "request", request)
    sample = run._transaction("steady", user, target, 0, commit)
    key = (target.name, user.subject)
    assert run.ledger.unknown.get(key, set()) == ({1} if unknown else set())
    assert run.ledger.rolled_back.get(key, set()) == (set() if unknown else {1})
    assert not run.ledger.committed
    assert sample.code == code
    assert sample.note == "API detail"
    assert not sample.ok
    assert target.name not in user.sessions
    assert calls[-1] == ("DELETE", "/api/v1/worksheets/transaction")

    # Simulate a server-side commit despite the lost response. Unknown markers are
    # allowed present or absent, even if an older ledger also called one rolled back.
    if unknown:
        run.ledger.add(run.ledger.rolled_back, target.name, user.subject, 1)
    monkeypatch.setattr(user, "_open", lambda t: t.name)

    def verify_execute(session, statement, binds=None, **limits):
        rows = [[0]]
        if statement == target.sql["listMarkers"]:
            rows = (
                [[1]]
                if unknown and session == target.name and binds["user_name"] == user.subject
                else []
            )
        return Response(200, {"outcome": {"resultSet": {"rows": rows}}}, 0)

    monkeypatch.setattr(user, "execute", verify_execute)
    run.verify()
    assert run.verification.unknown == int(unknown)
    assert not run.verification.contaminations
    assert not run.verification.lost_commits
    for client in run.users.values():
        client.http.close()


def test_capacity_refusal_with_failed_rollback_remains_unknown(monkeypatch):
    run = _measurement_run()
    user = run.users[run.order[0]]
    target = run.workload.targets[0]
    user.sessions[target.name] = "transaction"
    monkeypatch.setattr(user, "execute", lambda *args: Response(200, {}, 0))
    monkeypatch.setattr(
        user,
        "request",
        lambda method, path: Response(
            409 if path.endswith("/commit") else 0,
            {
                "error": {
                    "code": "session_busy" if path.endswith("/commit") else "transport_ReadTimeout"
                }
            },
            0,
        ),
    )
    run._transaction("steady", user, target, 0, True)
    assert run.ledger.unknown[(target.name, user.subject)] == {1}
    assert not run.ledger.rolled_back
    assert "transaction" in user.retired_sessions
    for client in run.users.values():
        client.http.close()


def test_capacity_close_checks_live_sessions_and_retries_failed_closes(monkeypatch):
    run = _measurement_run()
    user = run.users[run.order[0]]
    target = run.workload.targets[0]
    user.sessions[target.name] = "uncertain"
    calls = []
    attempts = 0

    def request(self, method, path, body=None):
        nonlocal attempts
        calls.append((self.subject, method, path))
        if method == "DELETE":
            attempts += 1
            return Response(0 if attempts == 1 else 200, {}, 0)
        if path == "/api/v1/worksheets":
            sessions = [
                {"sessionId": sid} for sid in ("gone", "quarantined", "live", "unreachable")
            ]
            return Response(200, {"sessions": sessions if self.subject == user.subject else []}, 0)
        if path.endswith("/gone"):
            return Response(404, {}, 0)
        if path.endswith("/quarantined"):
            return Response(409, {"error": {"code": "session_quarantined"}}, 0)
        return Response(500 if path.endswith("/unreachable") else 200, {}, 0)

    monkeypatch.setattr(UserClient, "request", request)
    user.drop_session(target)
    assert "uncertain" in user.retired_sessions
    run.close_and_check_leaks()
    assert attempts == 2
    assert run.verification.leaked_sessions == [f"{user.subject}: live"]
    assert len(run.verification.incomplete) == 1


def test_capacity_report_lists_every_unknown_result():
    run = _measurement_run()
    run.report.samples = [
        metrics.Sample(
            "saturation",
            "transaction",
            "u",
            target.name,
            1,
            2,
            False,
            "outcome_unknown",
            note=f"API message {i}",
        )
        for i, target in enumerate(run.workload.targets)
    ]
    results = run.report.as_dict()["outcomeUnknownResults"]
    assert len(results) == 3
    markdown = run.report.as_markdown()
    for i, result in enumerate(results):
        assert result == {
            "target": run.workload.targets[i].name,
            "phase": "saturation",
            "kind": "transaction",
            "message": f"API message {i}",
        }
        assert (
            f"Target `{result['target']}`, phase `saturation`, kind `transaction`: API message {i}"
            in markdown
        )
    for client in run.users.values():
        client.http.close()


@pytest.mark.parametrize(
    "status,code", [(404, "not_found"), (403, "not_authorized"), (409, "session_quarantined")]
)
def test_capacity_forget_closes_before_removing(monkeypatch, status, code):
    run = _measurement_run()
    user = run.users[run.order[0]]
    target = run.workload.targets[0]
    user.sessions[target.name] = "stale"
    closed = []

    def request(method, path):
        assert user.sessions[target.name] == "stale"
        closed.append((method, path))
        return Response(status, {"error": {"code": code}}, 0)

    monkeypatch.setattr(user, "request", request)
    user.forget(target, Response(status, {"error": {"code": code}}, 0))
    assert closed == [("DELETE", "/api/v1/worksheets/stale")]
    assert target.name not in user.sessions
    for client in run.users.values():
        client.http.close()
