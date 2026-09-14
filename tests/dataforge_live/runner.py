"""Starts a disposable harness API and runs the real adapter's Node tests against it."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tests.dataforge_live.stack import LiveHarness, StackProblem

REPOSITORY = Path(__file__).resolve().parents[2]
ADAPTER_DIR = REPOSITORY / "integrations" / "dataforge"
NODE_MODULES = ADAPTER_DIR / "node_modules"

# Only the checks that need a real (or, for the streaming test, a simulated) network
# path. adapter.test.ts's stubbed-fetch checks already run in the `adapter` CI job.
LIVE_TEST_FILES = ["test/proxy-streaming.test.ts", "test/live-harness.test.ts"]


class PrerequisiteError(RuntimeError):
    """Nothing ran: a local tool or dependency is missing."""


@dataclass
class NodeResult:
    command: list[str]
    returncode: int
    output: str
    tests: int | None
    passed: int | None
    failed: int | None
    skipped: int | None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.failed in (None, 0)


@dataclass
class Outcome:
    ok: bool
    prerequisite_failure: bool
    summary: str
    node: NodeResult | None


def preflight() -> list[str]:
    problems = []
    if shutil.which("node") is None:
        problems.append("`node` is not on PATH; the adapter checks run under it.")
    if not NODE_MODULES.exists():
        problems.append(
            "The adapter's dependencies are not installed. Run "
            "`npm --prefix integrations/dataforge install`."
        )
    return problems


def _extract(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def _run_node_tests(env: dict[str, str]) -> NodeResult:
    command = ["node", "--test", *LIVE_TEST_FILES]
    process = subprocess.run(  # noqa: S603 - fixed command, fixed file list
        command,
        cwd=ADAPTER_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = process.stdout + process.stderr
    return NodeResult(
        command=command,
        returncode=process.returncode,
        output=output,
        # The default "spec" reporter prefixes its summary with "ℹ", not the TAP "#".
        tests=_extract(output, r"[#ℹ]\s*tests (\d+)"),
        passed=_extract(output, r"[#ℹ]\s*pass (\d+)"),
        failed=_extract(output, r"[#ℹ]\s*fail (\d+)"),
        skipped=_extract(output, r"[#ℹ]\s*skip(?:ped)? (\d+)"),
    )


def run(workdir: Path) -> Outcome:
    """Start a disposable harness, provision it, and run the live Node checks.

    Returns an ``Outcome`` rather than raising: a missing prerequisite and a failed
    check are both reportable outcomes, distinguished by ``prerequisite_failure``.
    """

    problems = preflight()
    if problems:
        return Outcome(ok=False, prerequisite_failure=True, summary="\n".join(problems), node=None)

    harness = LiveHarness(workdir=workdir)
    token = ""
    try:
        harness.start()
        token = harness.issue_dataforge_credential()
    except StackProblem as problem:
        harness.stop()
        return Outcome(ok=False, prerequisite_failure=True, summary=str(problem), node=None)

    try:
        env = {key: value for key, value in os.environ.items()}
        env["DATAFORGE_LIVE_HARNESS_URL"] = harness.base_url
        env["DATAFORGE_LIVE_HARNESS_TOKEN"] = token
        result = _run_node_tests(env)
    finally:
        harness.stop()

    if token:
        result.output = result.output.replace(token, "<redacted>")

    counts = (
        f"{result.passed or 0} passed, {result.failed or 0} failed, {result.skipped or 0} skipped"
    )
    summary = (
        f"node --test {' '.join(LIVE_TEST_FILES)} against a live harness at {harness.base_url}: "
        f"{counts}"
    )
    return Outcome(ok=result.ok, prerequisite_failure=False, summary=summary, node=result)
