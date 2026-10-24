"""Offline unit tests for DataForge real runner logic."""

from __future__ import annotations

import argparse
from pathlib import Path

from tests.dataforge_live.__main__ import run_dataforge_command
from tests.dataforge_live.real_runner import (
    REPORT_LABEL,
    CheckResult,
    RealRunReport,
    parse_sse_events,
)


def test_parse_sse_events() -> None:
    raw = (
        'event: start\ndata: {"requestId": "r1"}\n\n'
        'event: delta\ndata: {"text": "hello "}\n\n'
        'event: delta\ndata: {"text": "world"}\n\n'
        'event: done\ndata: {"outcome": "ok"}\n\n'
    )
    events = parse_sse_events(raw)
    assert len(events) == 4
    assert events[0] == ("start", {"requestId": "r1"})
    assert events[1] == ("delta", {"text": "hello "})
    assert events[2] == ("delta", {"text": "world"})
    assert events[3] == ("done", {"outcome": "ok"})


def test_real_run_report_serialization() -> None:
    checks = [
        CheckResult("check_a", True, 12.3, "detail a"),
        CheckResult("check_b", False, 45.6, "detail b"),
    ]
    report = RealRunReport(
        label=REPORT_LABEL,
        harness_build="abc1234",
        dataforge_commit="def5678",
        adapter_version="0.1.0",
        protocol_version="1.1",
        checks=checks,
        ok=False,
        summary="1/2 checks passed",
    )
    md = report.to_markdown()
    assert REPORT_LABEL in md
    assert "abc1234" in md
    assert "def5678" in md
    assert "0.1.0" in md
    assert "1.1" in md
    assert "FAILED" in md
    assert "check_a" in md
    assert "check_b" in md

    js = report.to_json()
    assert REPORT_LABEL in js
    assert '"harnessBuild": "abc1234"' in js
    assert '"dataforgeCommit": "def5678"' in js
    assert '"adapterVersion": "0.1.0"' in js
    assert '"protocolVersion": "1.1"' in js
    assert '"ok": false' in js


def test_missing_dataforge_dir_returns_2(tmp_path: Path) -> None:
    missing_dir = tmp_path / "nonexistent"
    args = argparse.Namespace(
        dataforge_dir=missing_dir,
        harness_url=None,
        token=None,
        token_file=None,
        report=None,
        report_json=None,
        workdir=None,
    )
    code = run_dataforge_command(args)
    assert code == 2


def test_b1_target_loader_selects_only_d1_d2(tmp_path: Path) -> None:
    import json

    from tests.dataforge_live.oracle_stack import load_b1_targets

    path = tmp_path / "targets.json"
    path.write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "name": "load-db-2",
                        "dsn": "localhost:1522/PDB",
                        "user": "harness_app",
                        "passwordFile": "/not/read/d2",
                    },
                    {
                        "name": "load-db-1",
                        "dsn": "localhost:1521/ORCL",
                        "user": "harness_app",
                        "passwordFile": "/not/read/d1",
                    },
                    {"name": "load-db-3", "dsn": "ignored", "user": "ignored"},
                ],
                "apiPort": "not used",
                "postgres": "not used",
            }
        )
    )
    config = load_b1_targets(path)
    assert [t.name for t in config.targets] == ["load-db-1", "load-db-2"]
    assert config.targets[1].service_name == "PDB"
    assert config.targets[0].password_file == "/not/read/d1"
    assert config.postgres.url is None


def test_b1_target_loader_refuses_wrong_user_or_duplicate(tmp_path: Path) -> None:
    import json

    import pytest

    from tests.dataforge_live.oracle_stack import load_b1_targets

    targets = [
        {
            "name": name,
            "dsn": "localhost:1521/ORCL",
            "user": "harness_app",
            "passwordFile": "unused",
        }
        for name in ("load-db-1", "load-db-2")
    ]
    path = tmp_path / "targets.json"
    for bad in (
        [targets[0]],
        [*targets, targets[0]],
        [dict(targets[0], user="system"), targets[1]],
    ):
        path.write_text(json.dumps({"targets": bad}))
        with pytest.raises(ValueError, match="B1 requires"):
            load_b1_targets(path)


def test_b1_assist_context_cannot_select_another_profile() -> None:
    from tests.dataforge_live.oracle_runner import assist_body

    body = assist_body()
    assert body["connectionId"] == "load-db-1"
    assert "profileId" not in body
    assert "attachments" not in body


def test_real_report_includes_environment() -> None:
    import json

    report = RealRunReport(
        REPORT_LABEL, "h", "d", "0.1.0", "1.1", [], True, "ok", {"setupSeconds": 1.25}
    )
    assert json.loads(report.to_json())["environment"]["setupSeconds"] == 1.25
    assert '"setupSeconds": 1.25' in report.to_markdown()
