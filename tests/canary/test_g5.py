"""No Oracle extra or database is needed to exercise G5 gates."""

import json
import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.canary import g5

VERSIONS = {"D1": "19.9.1.0.210119", "P1": "19.9.1.0.210119", "oracledb": "26.0.1"}
RESULTS = {"r1": 0, "r3": 0, "p1": 0}


def test_decision_requires_every_pass_and_stable_probes():
    assert g5.decision(VERSIONS, VERSIONS, RESULTS) == "PROMOTE"
    for name in RESULTS:
        assert g5.decision(VERSIONS, VERSIONS, {**RESULTS, name: 1}) == "BLOCK"
        assert (
            g5.decision(VERSIONS, VERSIONS, {k: v for k, v in RESULTS.items() if k != name})
            == "BLOCK"
        )
    assert g5.decision({}, {}, RESULTS) == "BLOCK"
    assert g5.decision(VERSIONS, {**VERSIONS, "oracledb": "27"}, RESULTS) == "BLOCK"


def test_ru_mismatch_blocks_even_when_suites_pass():
    mismatch = {**VERSIONS, "P1": "19.10.0.0.0"}
    assert g5.decision(mismatch, mismatch, RESULTS) == "BLOCK"


def test_official_path_and_symlink_refused(tmp_path):
    alias = tmp_path / "official"
    alias.symlink_to(g5.OFFICIAL, target_is_directory=True)
    for directory in (g5.OFFICIAL, alias, alias / "child", g5.OFFICIAL.parent):
        with pytest.raises(ValueError, match="official C4"):
            g5.validate_directory(directory)


def test_dry_run_has_no_side_effects(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g5, "probe", lambda: pytest.fail("database touched"))
    directory = tmp_path / "unused"
    assert g5.run("baseline: no change", directory, True) == 0
    assert not directory.exists()
    plan = json.loads(capsys.readouterr().out)
    assert (
        plan["label"]
        == "agent-qualified on a production-shaped stand-in on z4g5 (AI agent, not a human)"
    )
    assert "tests.canary.production" in plan["commands"]["p1"]
    for profile in ("r1", "r3"):
        assert "--d1-only" in plan["commands"][profile]
        assert plan["commands"][profile][-3:] == g5.SUITES


def test_probe_uses_lazy_fake_driver_and_read_only_sql(monkeypatch, tmp_path):
    from tests.database_locks import database_locks

    monkeypatch.setattr(
        g5,
        "database_locks",
        lambda dsns, description: database_locks(dsns, description, repository=tmp_path),
    )
    connection = MagicMock()
    cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = [VERSIONS["D1"]]
    driver = SimpleNamespace(__version__="26.0.1", connect=MagicMock(return_value=connection))
    monkeypatch.setitem(sys.modules, "oracledb", driver)
    monkeypatch.setattr(
        g5,
        "load_admin_settings",
        lambda path, repo: {
            "HARNESS_QUAL_ADMIN_DSN": "localhost:1522/ORCL"
            if path.stem == "r1"
            else "localhost:1523/PRODCDB",
            "HARNESS_QUAL_ADMIN_USER": "system",
            "HARNESS_QUAL_ADMIN_PASSWORD": "fake",
        },
    )
    assert g5.probe() == VERSIONS
    assert cursor.execute.call_count == 2
    cursor.execute.assert_called_with("SELECT version_full FROM v$instance")


@pytest.mark.parametrize("failed", [False, True])
def test_run_records_decision_budget_label_and_approver(tmp_path, monkeypatch, failed):
    official = tmp_path / "official"
    official.mkdir()
    (official / "budget.json").write_text(json.dumps({"ceilings": {"sessions": 2}}))
    monkeypatch.setattr(g5, "OFFICIAL", official)
    monkeypatch.setattr(g5.production, "read_budget", lambda path: {"sessions": 2})
    monkeypatch.setattr(g5, "probe", lambda: VERSIONS)
    monkeypatch.setattr(g5, "canary_allowed", lambda now: True)
    monkeypatch.setattr(
        g5.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=int(failed))
    )
    directory = tmp_path / "run"
    assert g5.run("baseline: no change", directory) == int(failed)
    record = json.loads((directory / "g5.json").read_text())
    assert record["approver"] == "Claude (AI agent, acting DBA), not a human"
    assert record["label"] == g5.LABEL
    assert record["decision"] == ("BLOCK" if failed else "PROMOTE")
    assert json.loads((directory / "budget.json").read_text()) == json.loads(
        (official / "budget.json").read_text()
    )
    assert g5.LABEL in (directory / "g5.md").read_text()
    assert record["before"] == record["after"] == VERSIONS


@pytest.mark.parametrize(
    "hour,minute,allowed", [(8, 49, True), (8, 50, False), (9, 29, False), (9, 30, True)]
)
def test_timer_exclusion(hour, minute, allowed):
    assert g5.canary_allowed(datetime(2026, 10, 25, hour + 6, minute, tzinfo=UTC)) is allowed


def test_launcher_d1_only_cannot_connect_second_target(tmp_path):
    import shutil
    import subprocess

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(g5.REPO / "scripts/qual_run.sh", scripts / "qual_run.sh")
    profiles = tmp_path / "databases/qual"
    profiles.mkdir(parents=True)
    (profiles / "r1.env").write_text(
        "HARNESS_QUAL_ORACLE_DSN=localhost:1522/ORCL\n"
        "HARNESS_QUAL_ADMIN_DSN=localhost:1522/ORCL\n"
        "HARNESS_QUAL_SECOND_DSN=forbidden\n"
        "HARNESS_QUAL_REQUIRE=admin,second_target,restricted_account\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        '#!/bin/bash\n[[ -z "${HARNESS_QUAL_SECOND_DSN:-}" && "$HARNESS_QUAL_REQUIRE" == admin,restricted_account && "$HARNESS_QUAL_REPORT" == "$EXPECTED_REPORT" ]]\n'
    )
    fake_uv.chmod(0o700)
    import os

    output = tmp_path / "separate.md"
    result = subprocess.run(
        [
            "bash",
            str(scripts / "qual_run.sh"),
            "r1",
            "--d1-only",
            "--report-path",
            str(output),
            *g5.SUITES,
        ],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "EXPECTED_REPORT": str(output),
        },
        capture_output=True,
    )
    assert result.returncode == 0


def test_upgrade_requires_actual_old_and_new_probe_evidence(tmp_path):
    upgraded = {**VERSIONS, "oracledb": "27.0.0"}
    change = "python-oracledb 26.0.1 -> 27.0.0"
    _, reasons = g5.version_evidence(change, upgraded, upgraded, None)
    assert reasons == ["oracledb: declared old version lacks matching probe evidence"]
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps({"label": g5.LABEL, "decision": "PROMOTE", "after": VERSIONS}))
    versions, reasons = g5.version_evidence(change, upgraded, upgraded, previous)
    assert not reasons
    assert versions["old"] == VERSIONS
    assert versions["new"] == upgraded
    _, reasons = g5.version_evidence(change, VERSIONS, VERSIONS, previous)
    assert reasons == ["oracledb: installed version does not match declared new version"]


def test_ru_upgrade_checks_both_targets(tmp_path):
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps({"label": g5.LABEL, "decision": "PROMOTE", "after": VERSIONS}))
    upgraded = {**VERSIONS, "D1": "19.10.0.0.0", "P1": "19.10.0.0.0"}
    _, reasons = g5.version_evidence(
        "oracle RU 19.9.1.0.210119 -> 19.10.0.0.0", upgraded, upgraded, previous
    )
    assert not reasons
