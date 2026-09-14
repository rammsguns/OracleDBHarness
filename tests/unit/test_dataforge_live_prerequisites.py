"""``tests/dataforge_live`` checked without starting a real harness or a real Node.

The end-to-end path - a real harness process, a real dataforge credential, the real
adapter code driven through `node --test` - is exercised by `python -m
tests.dataforge_live run` itself, not by pytest; see that package's docstring. These
checks cover the parts that decide whether such a run can be trusted: a missing local
tool is reported rather than silently skipped, a stack that cannot start does not
leave a process running, and a credential is never written into a report unredacted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.dataforge_live import runner
from tests.dataforge_live.stack import LiveHarness, StackProblem


def test_a_missing_node_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    problems = runner.preflight()
    assert any("node" in problem for problem in problems)


def test_missing_adapter_dependencies_are_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(runner, "NODE_MODULES", tmp_path / "does-not-exist")
    problems = runner.preflight()
    assert any("npm --prefix integrations/dataforge install" in problem for problem in problems)


@pytest.mark.parametrize(
    "line",
    ["# pass 3", "ℹ pass 3", "ℹ  pass 3"],
)
def test_pass_counts_are_read_from_either_reporter_style(line: str) -> None:
    # node's TAP reporter uses "#"; its default "spec" reporter uses "ℹ". A run started
    # from a different shell or CI image can produce either, and a miscount here would
    # silently turn a real failure into a reported pass.
    assert runner._extract(f"...\n{line}\n", r"[#ℹ]\s*pass (\d+)") == 3


def test_a_line_without_the_expected_word_extracts_nothing() -> None:
    assert runner._extract("no summary here", r"[#ℹ]\s*pass (\d+)") is None


def test_a_missing_prerequisite_stops_before_any_process_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runner, "preflight", lambda: ["node is not on PATH"])
    started = []
    monkeypatch.setattr(LiveHarness, "start", lambda self: started.append(True))

    outcome = runner.run(tmp_path)

    assert outcome.prerequisite_failure is True
    assert outcome.ok is False
    assert outcome.node is None
    assert started == []


def test_a_stack_that_cannot_start_is_reported_and_left_with_nothing_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runner, "preflight", lambda: [])
    stopped = []
    monkeypatch.setattr(
        LiveHarness,
        "start",
        lambda self: (_ for _ in ()).throw(StackProblem("the harness process exited")),
    )
    monkeypatch.setattr(LiveHarness, "stop", lambda self: stopped.append(True))

    outcome = runner.run(tmp_path)

    assert outcome.prerequisite_failure is True
    assert "exited" in outcome.summary
    # Nothing was left running even though start() failed partway through.
    assert stopped == [True]


def test_the_credential_never_appears_in_the_captured_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runner, "preflight", lambda: [])
    monkeypatch.setattr(LiveHarness, "start", lambda self: None)
    monkeypatch.setattr(LiveHarness, "stop", lambda self: None)
    monkeypatch.setattr(LiveHarness, "base_url", property(lambda self: "http://127.0.0.1:1"))
    monkeypatch.setattr(
        LiveHarness, "issue_dataforge_credential", lambda self, name="x": "odbh_super-secret"
    )
    monkeypatch.setattr(
        runner,
        "_run_node_tests",
        lambda env: runner.NodeResult(
            command=["node"],
            returncode=1,
            output="failure near token odbh_super-secret in the request log",
            tests=1,
            passed=0,
            failed=1,
            skipped=0,
        ),
    )

    outcome = runner.run(tmp_path)

    assert outcome.ok is False
    assert outcome.prerequisite_failure is False
    assert outcome.node is not None
    assert "odbh_super-secret" not in outcome.node.output
    assert "<redacted>" in outcome.node.output
