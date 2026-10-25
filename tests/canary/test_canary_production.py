"""Offline evidence gates for the persistent production-shaped canary."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.canary.production import (
    BUDGET,
    BUDGET_SCOPE,
    LABEL,
    RehearsalFailure,
    evaluate,
    execute,
    measure_cost,
    read_budget,
    record_budget,
    render_units,
    write_json,
)


def seed(directory: Path, offsets=range(14)) -> None:
    record_budget(directory)
    for index, offset in enumerate(offsets):
        write_json(
            directory / "passes" / f"pass-{index}.json",
            {
                "at": (datetime(2026, 10, 25, 9, tzinfo=UTC) + timedelta(days=offset)).isoformat(),
                "label": LABEL,
                "success": True,
                "cost": dict(BUDGET),
                "budget_scope": BUDGET_SCOPE,
            },
        )
    write_json(
        directory / "drills/drill.json",
        {"label": LABEL, "success": True, "restored": True, "account_status": "OPEN"},
    )


def test_budget_is_exclusive_and_required(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_budget(tmp_path)
    record_budget(tmp_path)
    original = (tmp_path / "budget.json").read_bytes()
    with pytest.raises(FileExistsError):
        record_budget(tmp_path)
    assert (tmp_path / "budget.json").read_bytes() == original
    assert read_budget(tmp_path) == BUDGET
    assert b"recorded by Claude (AI agent, acting DBA), not a human" in original
    assert b"105000" in original


def test_fourteen_days_and_signoff(tmp_path):
    seed(tmp_path)
    result = evaluate(tmp_path)
    assert result["pass"]
    assert result["days"] == 14
    assert result["label"] == LABEL
    assert result["sign_off"] == "acting DBA: Claude (AI agent, not a human)"


@pytest.mark.parametrize("offsets", [[0, 0], [0, 2], [*range(13), 14]])
def test_distinct_days_and_gap_detection(tmp_path, offsets):
    seed(tmp_path, offsets)
    result = evaluate(tmp_path)
    assert not result["pass"]
    assert result["sign_off"] is None
    if 2 in offsets and 1 not in offsets or 14 in offsets:
        assert any("Gap" in reason for reason in result["reasons"])


def test_resolution_requires_acting_dba_record(tmp_path):
    seed(tmp_path)
    write_json(tmp_path / "incidents/problem.json", {"failure": "access defect"})
    assert not evaluate(tmp_path)["pass"]
    write_json(tmp_path / "resolutions/problem.json", {"resolved": True})
    assert not evaluate(tmp_path)["pass"]
    write_json(
        tmp_path / "resolutions/problem.json",
        {
            "incident": "problem",
            "resolved_by": "Claude (AI agent, acting DBA)",
            "at": "2026-11-08T09:00:00Z",
            "note": "Acting DBA verified restoration",
        },
    )
    assert evaluate(tmp_path)["pass"]


def test_every_pass_budget_and_label_are_checked(tmp_path):
    seed(tmp_path)
    path = tmp_path / "passes/pass-0.json"
    record = json.loads(path.read_text())
    record["cost"]["sessions"] = 3
    write_json(path, record)
    assert any("budget breach" in reason for reason in evaluate(tmp_path)["reasons"])
    record["cost"] = dict(BUDGET)
    record["label"] = "human production approved"
    write_json(path, record)
    assert any("label" in reason for reason in evaluate(tmp_path)["reasons"])


def test_cursor_measurement_fails_closed():
    before = {"sessions": 1, "counters": {"cursor": [1000000, 100, 1]}}
    after = {"sessions": 1, "counters": {"cursor": [2000000, 200, 2]}}
    assert measure_cost(before, after, [2])["elapsed_seconds"] == 1
    assert measure_cost(before, after, [2])["sessions"] == 2
    with pytest.raises(RehearsalFailure, match="aged out"):
        measure_cost(before, {"sessions": 0, "counters": {}}, [])
    with pytest.raises(RehearsalFailure, match="reset"):
        measure_cost(after, before, [])


def test_budget_breach_retains_pass_and_incident(tmp_path, monkeypatch):
    import argparse

    from tests.canary import production

    class FakeDeployment:
        pid = "p1"
        client = type("Client", (), {"close": lambda self: None})()

        def __init__(self, state):
            pass

    def over_budget(deployment, report, budget, directory):
        report.update(cost={**BUDGET, "sessions": 3}, budget_failures=["sessions"])
        raise RehearsalFailure("Database-cost budget exceeded")

    monkeypatch.setattr(production, "Deployment", FakeDeployment)
    monkeypatch.setattr(production, "run_pass", over_budget)
    assert (
        execute(
            argparse.Namespace(state_dir=tmp_path, report_dir=tmp_path, subcommand="pass"), BUDGET
        )
        == 1
    )
    passes = list((tmp_path / "passes").glob("*.json"))
    incidents = list((tmp_path / "incidents").glob("*.json"))
    assert len(passes) == len(incidents) == 1
    report = json.loads(passes[0].read_text())
    assert not report["success"]
    assert report["label"] == LABEL
    assert report["budget_failures"] == ["sessions"]
    assert passes[0].with_suffix(".md").exists()


def test_units_have_paths_and_no_secrets(tmp_path):
    units = render_units(tmp_path / "repo with spaces", tmp_path / "state", tmp_path / "reports")
    assert set(units) == {
        "harness-canary-api.service",
        "harness-canary-pass.service",
        "harness-canary-pass.timer",
    }
    for body in units.values():
        assert "PASSWORD" not in body
        assert "@" not in body
    assert (
        f"WorkingDirectory={tmp_path / 'repo with spaces'}" in units["harness-canary-api.service"]
    )
    assert "ExecStartPre=docker start harness-canary-pg" in units["harness-canary-api.service"]
    assert "09:00:00" in units["harness-canary-pass.timer"]
    assert "Persistent=true" in units["harness-canary-pass.timer"]
    assert "--report-dir" in units["harness-canary-pass.service"]


def test_deploy_reuses_persistent_store_and_api(tmp_path, monkeypatch):
    import argparse
    import subprocess

    from tests.canary import production

    repository = tmp_path / "repository"
    source = repository / "databases/secrets/p1_harness_ro.pw"
    source.parent.mkdir(parents=True)
    source.write_text("offline-test-password")
    state = tmp_path / "state"
    state.mkdir()
    commands = []
    created = False

    def run(arguments, **kwargs):
        if arguments[:2] == ["docker", "inspect"]:
            mounts = [{"Name": production.VOLUME}, {"Source": str(state / "secrets/pg.password")}]
            return subprocess.CompletedProcess(
                arguments, 0 if created else 1, stdout=json.dumps([{"Mounts": mounts}]).encode()
            )
        return subprocess.CompletedProcess(arguments, 0)

    def command(arguments, **kwargs):
        nonlocal created
        commands.append(arguments)
        if arguments[:2] == ["docker", "run"]:
            created = True
        return subprocess.CompletedProcess(arguments, 0)

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, path):
            return type("Response", (), {"is_success": True})()

    monkeypatch.setattr(production, "REPO", repository)
    monkeypatch.setattr(production.subprocess, "run", run)
    monkeypatch.setattr(production, "command", command)
    monkeypatch.setattr(
        production.subprocess,
        "Popen",
        lambda *args, **kwargs: type("Process", (), {"pid": 12345})(),
    )
    monkeypatch.setattr(production, "api_alive", lambda state: (state / "api.pid").exists())
    monkeypatch.setattr(production.httpx, "Client", Client)
    args = argparse.Namespace(
        state_dir=state,
        report_dir=tmp_path / "reports",
        port=8640,
        pg_port=8641,
        install_units=False,
    )
    production.deploy(args)
    password = (state / "secrets/pg.password").read_bytes()
    production.deploy(args)
    assert sum(args[:2] == ["docker", "run"] for args in commands) == 1
    assert sum("scripts/bootstrap_targets.py" in args for args in commands) == 1
    assert (state / "secrets/pg.password").read_bytes() == password
    assert (state / "secrets/p1.password").stat().st_mode & 0o777 == 0o600
    assert (state / "secrets/pg.password").stat().st_mode & 0o777 == 0o600
    manifest = json.loads((state / "manifest.json").read_text())
    assert manifest["targets"][0]["environment"] == "production"
    assert not manifest["targets"][0]["worksheetsEnabled"]
    assert "offline-test-password" not in (state / "manifest.json").read_text()


def test_parsing_user_cost_is_enforced_even_when_schema_cost_is_zero(tmp_path, monkeypatch):
    from tests.canary import production

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Deployment:
        pid = "p1"

        def workflow(self, method, path, *args, **kwargs):
            return {"body": {"identity": {"version": "19.9"}, "connected": True}, "path": path}

    snapshots = iter(
        [
            {"sessions": 1, "counters": {"user": [0, 0, 0]}, "schema_counters": {}},
            {"sessions": 1, "counters": {"user": [1000000, 100, 201]}, "schema_counters": {}},
        ]
    )
    monkeypatch.setattr(production, "connect", lambda: Connection())
    monkeypatch.setattr(production, "snapshot", lambda db: next(snapshots))
    report = {}
    with pytest.raises(RehearsalFailure, match="budget exceeded"):
        production.run_pass(Deployment(), report, BUDGET, tmp_path)
    assert report["cost"]["executions"] == 201
    assert report["parsing_schema_cost"]["executions"] == 0
    assert report["budget_scope"] == BUDGET_SCOPE
    assert report["budget_failures"] == ["executions"]


def test_evaluator_rejects_imported_schema_only_pass(tmp_path):
    seed(tmp_path)
    path = tmp_path / "passes/pass-0.json"
    record = json.loads(path.read_text())
    record.pop("budget_scope")
    write_json(path, record)
    assert any("budget scope" in reason for reason in evaluate(tmp_path)["reasons"])


@pytest.mark.parametrize("contents", ["", "\n", "not-a-pid"])
def test_api_alive_rejects_malformed_pid(tmp_path, contents):
    from tests.canary import production

    (tmp_path / "api.pid").write_text(contents)
    assert not production.api_alive(tmp_path)


@pytest.mark.parametrize("operation", ["read_text", "read_bytes"])
@pytest.mark.parametrize("error", [PermissionError, ProcessLookupError, OSError])
def test_api_alive_handles_io_errors(tmp_path, monkeypatch, operation, error):
    from tests.canary import production

    (tmp_path / "api.pid").write_text("12345")

    def fail(*args, **kwargs):
        raise error("offline test")

    monkeypatch.setattr(Path, operation, fail)
    assert not production.api_alive(tmp_path)


@pytest.mark.parametrize("api_only", [False, True])
def test_stop_preserves_store_only_when_requested(tmp_path, monkeypatch, api_only):
    from tests.canary import production

    (tmp_path / "api.pid").write_text("12345")
    alive = iter([True, False])
    signals = []
    commands = []
    monkeypatch.setattr(production, "api_alive", lambda state: next(alive))
    monkeypatch.setattr(production.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(production, "command", lambda args: commands.append(args))
    production.stop(tmp_path, api_only=api_only)
    assert signals == [(12345, production.signal.SIGTERM)]
    assert not (tmp_path / "api.pid").exists()
    assert commands == ([] if api_only else [["docker", "stop", production.CONTAINER]])


def test_stop_cli_forwards_api_only(tmp_path, monkeypatch):
    from tests.canary import production

    record_budget(tmp_path)
    calls = []
    monkeypatch.setattr(
        production.sys,
        "argv",
        [
            "canary",
            "stop",
            "--api-only",
            "--state-dir",
            str(tmp_path),
            "--report-dir",
            str(tmp_path),
        ],
    )
    monkeypatch.setattr(production.signal, "signal", lambda *args: None)
    monkeypatch.setattr(production, "stop", lambda state, **kwargs: calls.append((state, kwargs)))
    assert production.main() == 0
    assert calls == [(tmp_path, {"api_only": True})]


def test_drill_restores_account_and_target_after_assertion_failure(monkeypatch):
    from tests.canary import production

    class DatabaseError(Exception):
        pass

    monkeypatch.setitem(sys.modules, "oracledb", SimpleNamespace(DatabaseError=DatabaseError))
    statements = []
    toggles = []
    observations = []
    locked = False

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def cursor(self):
            return self

        def execute(self, sql):
            nonlocal locked
            statements.append(sql)
            if sql.endswith(" ACCOUNT LOCK"):
                locked = True
            elif sql.endswith(" ACCOUNT UNLOCK"):
                locked = False

        def fetchone(self):
            return ("LOCKED" if locked else "OPEN",)

        def close(self):
            pass

    class Deployment:
        pid = "p1"
        profile = {"enabled": True}

        def request(self, method, path, **kwargs):
            toggles.append(path.rsplit("/", 1)[-1])
            return type("Response", (), {"raise_for_status": lambda self: None})()

        def workflow(self, method, path, principal, **kwargs):
            observations.append(kwargs)

    # A successful login while locked triggers a drill assertion after ACCOUNT LOCK.
    monkeypatch.setattr(production, "connect", lambda *args: Connection())
    report = {}
    with pytest.raises(RehearsalFailure, match="Locked account accepted login"):
        production.drill(Deployment(), report)
    assert "ALTER USER HARNESS_RO ACCOUNT LOCK" in statements
    assert "ALTER USER HARNESS_RO ACCOUNT UNLOCK" in statements
    assert not locked
    assert toggles == ["disable", "enable"]
    assert observations[-1] == {}
    assert report["account_status"] == "OPEN"
    assert report["restored"] is True


def test_review_clean_single_pass_is_offline(tmp_path, monkeypatch):
    from tests.canary import production

    seed(tmp_path, [0])

    def forbidden(*args, **kwargs):
        raise AssertionError("review attempted online operation or write")

    for name in ("connect", "Deployment", "state_lock", "database_locks", "write_json"):
        monkeypatch.setattr(production, name, forbidden)
    result = production.review(tmp_path, datetime(2026, 10, 25).date())
    assert result["pass"]
    assert result["window_start"] == "2026-10-25"
    assert result["days_covered"] == result["days_expected_so_far"] == 1
    assert result["missing_days"] == []
    assert result["latest_pass"]["cost"]["sessions"] == {
        "value": 2,
        "ceiling": 2,
        "remaining_headroom_percent": 0,
    }
    assert result["provisional_result"]["reasons"] == [
        "Need 14 daily passes; have 1 distinct UTC days"
    ]
    assert not (tmp_path / "exit.json").exists()


@pytest.mark.parametrize("problem", ["budget", "gap", "incident", "request"])
def test_review_failures_so_far(tmp_path, problem):
    from tests.canary.production import review

    seed(tmp_path, [0])
    path = tmp_path / "passes/pass-0.json"
    record = json.loads(path.read_text())
    if problem == "budget":
        record["cost"]["sessions"] = 3
    elif problem == "incident":
        write_json(tmp_path / "incidents/open.json", {"failure": "offline fixture"})
    elif problem == "request":
        record["requests"] = [
            {"expected_refusal": "policy_refused", "outcome": "succeeded"},
            {"path": "/objects", "audit_checked": False},
            {"path": "/targets/p1/test", "audit_checked": False},
        ]
    write_json(path, record)
    today = datetime(2026, 10, 27 if problem == "gap" else 25).date()
    result = review(tmp_path, today)
    assert not result["pass"]
    if problem == "gap":
        assert result["missing_days"] == ["2026-10-26"]
    if problem == "incident":
        assert result["unresolved_incidents"] == ["open"]
    if problem == "request":
        assert len(result["latest_pass"]["request_failures"]) == 2
    assert not (tmp_path / "exit.json").exists()


def test_review_cli_record_and_evaluate_compatibility(tmp_path, monkeypatch, capsys):
    from tests.canary import production

    seed(tmp_path, [0])
    result = production.review(tmp_path)
    monkeypatch.setattr(production, "review", lambda directory: result)
    argv = ["canary", "review", "--report-dir", str(tmp_path), "--record"]
    monkeypatch.setattr(production.sys, "argv", argv)
    expected_code = 0 if result["pass"] else 1
    assert production.main() == expected_code
    capsys.readouterr()
    path = tmp_path / "reviews" / f"{datetime.now(UTC).date()}.json"
    original = path.read_bytes()
    assert json.loads(original)["reviewed_by"] == "Claude (AI agent, acting DBA), not a human"
    assert production.main() == 1
    assert path.read_bytes() == original
    monkeypatch.setattr(production.sys, "argv", [*argv, "--force"])
    assert production.main() == expected_code
    assert not (tmp_path / "exit.json").exists()
    capsys.readouterr()
    evaluated = production.evaluate(tmp_path)
    monkeypatch.setattr(production, "evaluate", lambda directory, min_days=14: evaluated)
    monkeypatch.setattr(
        production.sys, "argv", ["canary", "evaluate", "--report-dir", str(tmp_path)]
    )
    assert production.main() == 1
    assert capsys.readouterr().out == json.dumps(evaluated, indent=2) + "\n"
    assert (tmp_path / "exit.json").read_text() == json.dumps(evaluated, indent=2) + "\n"
    assert evaluated["reasons"] == ["Need 14 daily passes; have 1 distinct UTC days"]


def test_review_before_first_pass_and_at_window_end(tmp_path):
    from tests.canary.production import review

    seed(tmp_path, [])
    empty = review(tmp_path, datetime(2026, 10, 25).date())
    assert empty["pass"]
    assert empty["window_start"] is None
    seed(tmp_path / "started", [0])
    final = review(tmp_path / "started", datetime(2026, 11, 8).date())
    assert not final["pass"]
    assert any(reason.startswith("Need 14") for reason in final["failures_so_far"])


def test_review_before_todays_pass(tmp_path):
    from tests.canary.production import review

    seed(tmp_path, [0])
    result = review(tmp_path, datetime(2026, 10, 26, 8, tzinfo=UTC).date())
    assert result["pass"]
    assert result["days_expected_so_far"] == 1
    assert result["missing_days"] == []


@pytest.mark.parametrize("failed_index", [None, 0, 1, 2, 3, 4])
def test_review_real_shaped_refusal_records(tmp_path, failed_index):
    from tests.canary.production import review

    seed(tmp_path, [0])
    path = tmp_path / "passes/pass-0.json"
    record = json.loads(path.read_text())
    paths = [
        "/tuning/explain",
        "/worksheets",
        "/runbooks/runbook.gather_table_stats/run",
        "/copilot/requests",
        "/copilot/requests",
    ]
    record["requests"] = [
        {
            "path": path,
            "status": 200 if index == failed_index else 403,
            "outcome": "succeeded" if index == failed_index else "refused",
            "audit_checked": True,
        }
        for index, path in enumerate(paths)
    ]
    record["requests"].append(
        {"path": "/targets/p1/test", "status": 200, "outcome": "succeeded", "audit_checked": False}
    )
    write_json(path, record)
    result = review(tmp_path, datetime(2026, 10, 25).date())
    if failed_index is None:
        assert result["pass"]
        assert result["request_failures"] == []
    else:
        assert not result["pass"]
        assert result["request_failures"] == [
            {"pass": "pass-0", "request": record["requests"][failed_index]}
        ]
