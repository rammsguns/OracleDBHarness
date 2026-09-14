"""``python -m tests.browser`` - the browser sign-in qualification entry point.

    uv run --group browser python -m tests.browser run [--report PATH]

Exit codes: 0 when the run did what its mode asks (in pilot mode, that it qualified, or
completed with ``HARNESS_BROWSER_ALLOW_PARTIAL`` set); 1 when a check failed or a pilot
run is not qualified; 2 when the run is not configured or a prerequisite is missing
before anything could be checked.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tests.browser import config as browser_config
from tests.browser.report import Report
from tests.browser.runner import run


def _print(report: Report) -> None:
    text = report.as_markdown()
    report.assert_clean(text)
    print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.browser", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser("run", help="run the browser sign-in checks")
    runner.add_argument("--report", type=Path, help="append the report here (and a .json)")
    runner.add_argument("--workdir", type=Path, help="where local runs keep their store and logs")
    commands.add_parser("config", help="validate the configuration and print it, then stop")
    arguments = parser.parse_args(argv)

    try:
        config = browser_config.load()
    except browser_config.ConfigurationProblem as problem:
        print(f"The browser qualification cannot run as configured:\n{problem}", file=sys.stderr)
        return 2

    if arguments.command == "config":
        print(
            f"mode {config.mode}, origin {config.origin}, issuer {config.issuer or '(started by the run)'}"
        )
        print(f"client {config.client_id}, login {config.login}, browser {config.channel}")
        print(
            f"expiry wait {config.expiry_wait_seconds:.0f}s, unregistered check {config.check_unregistered}"
        )
        return 0

    report = run(config, arguments.workdir)
    _print(report)
    path = arguments.report or config.report_path
    if path is not None:
        report.write(path)
        print(f"Report appended to {path}", file=sys.stderr)
    if any(check.id.startswith("prerequisite.") for check in report.failures):
        return 2
    return 0 if report.succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
