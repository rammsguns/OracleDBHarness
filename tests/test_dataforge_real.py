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
