"""``python -m tests.dataforge_live`` - real-harness checks for the DataForge adapter.

    uv run python -m tests.dataforge_live run [--report PATH]
    uv run python -m tests.dataforge_live dataforge [--dataforge-dir DIR] [--harness-url URL] [--report PATH]

Commands:
- `run`: starts a disposable harness API and runs the adapter's Node tests against it.
- `dataforge`: starts the real OracleDataForge Express server and runs full integration checks.

Exit codes: 0 when every check passed; 1 when a check failed; 2 when a prerequisite
(node, `npm install`, dataforge checkout) is missing and nothing could be checked.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from tests.dataforge_live.real_runner import run_dataforge_integration
from tests.dataforge_live.runner import run


def run_dataforge_command(arguments: argparse.Namespace) -> int:
    df_dir: Path = arguments.dataforge_dir.resolve()
    if not df_dir.exists() or not (df_dir / "package.json").exists():
        print(f"DataForge directory not found at {df_dir}", file=sys.stderr)
        return 2

    if not (df_dir / "node_modules").exists():
        print(
            f"DataForge dependencies not installed in {df_dir}. Run `npm install` there.",
            file=sys.stderr,
        )
        return 2

    if shutil.which("node") is None:
        print("`node` is not on PATH.", file=sys.stderr)
        return 2

    # The credential comes from a file only, so it never appears on a command line.
    token = None
    if arguments.token_file:
        token_file = Path(arguments.token_file)
        if not token_file.exists():
            print(f"Token file {token_file} does not exist.", file=sys.stderr)
            return 2
        token = token_file.read_text(encoding="utf-8").strip()

    harness_url = arguments.harness_url

    def execute(workdir: Path):
        if getattr(arguments, "backend", "fake") == "oracledb":
            from tests.dataforge_live.oracle_runner import run_oracle_integration

            if not arguments.targets or harness_url or token:
                raise ValueError("Oracle mode requires --targets and a disposable harness")
            return run_oracle_integration(df_dir, arguments.targets, workdir)
        return run_dataforge_integration(
            dataforge_dir=df_dir, harness_url=harness_url, token=token, workdir=workdir
        )

    if arguments.workdir is not None:
        report = execute(arguments.workdir)
    else:
        with tempfile.TemporaryDirectory(
            prefix="dataforge-real-", ignore_cleanup_errors=True
        ) as tmp:
            report = execute(Path(tmp))

    print(report.to_markdown())

    if arguments.report:
        report_path = Path(arguments.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report.to_markdown() + "\n", encoding="utf-8")
        print(f"Markdown report written to {report_path}", file=sys.stderr)
        if not arguments.report_json:
            json_path = report_path.with_suffix(".json")
            json_path.write_text(report.to_json() + "\n", encoding="utf-8")
            print(f"JSON report written to {json_path}", file=sys.stderr)

    if arguments.report_json:
        json_path = Path(arguments.report_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(report.to_json() + "\n", encoding="utf-8")
        print(f"JSON report written to {json_path}", file=sys.stderr)

    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.dataforge_live", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    # 1. run command (Node adapter checks against live harness)
    runner = commands.add_parser(
        "run", help="start a disposable harness and run the adapter against it"
    )
    runner.add_argument("--report", type=Path, help="write a summary here")
    runner.add_argument(
        "--workdir", type=Path, help="where the disposable harness keeps its store and logs"
    )

    # 2. dataforge command (checks against real DataForge Express server)
    df_runner = commands.add_parser(
        "dataforge", help="start real DataForge server and run full integration checks"
    )
    df_runner.add_argument(
        "--dataforge-dir",
        type=Path,
        required=True,
        help="path to the OracleDataForge clone with the adapter patch applied",
    )
    df_runner.add_argument(
        "--harness-url",
        type=str,
        help="base URL of running harness API or proxy (e.g. http://127.0.0.1:18081). If omitted, starts disposable harness.",
    )
    df_runner.add_argument(
        "--token-file",
        type=Path,
        help="path to file containing integration credential token",
    )
    df_runner.add_argument(
        "--report",
        type=Path,
        help="path to write Markdown report",
    )
    df_runner.add_argument(
        "--report-json",
        type=Path,
        help="path to write JSON report",
    )
    df_runner.add_argument(
        "--workdir",
        type=Path,
        help="where the disposable harness keeps its store and logs",
    )

    df_runner.add_argument("--backend", choices=("fake", "oracledb"), default="fake")
    df_runner.add_argument(
        "--targets", type=Path, help="capacity JSON; Oracle mode selects D1/D2 only"
    )

    arguments = parser.parse_args(argv)

    # node's default reporter uses non-ASCII glyphs (checkmarks, "ℹ"); a Windows
    # console in its legacy code page cannot print them, and that must not crash a
    # run that otherwise succeeded. Not every stdout replacement (e.g. in a test
    # harness) has `reconfigure`, so this is best-effort.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")

    if arguments.command == "dataforge":
        return run_dataforge_command(arguments)

    if arguments.workdir is not None:
        outcome = run(arguments.workdir)
    else:
        # ignore_cleanup_errors: on Windows, a just-killed uvicorn subprocess can hold
        # its sqlite file open for a moment after Popen.wait() returns.
        with tempfile.TemporaryDirectory(
            prefix="dataforge-live-", ignore_cleanup_errors=True
        ) as tmp:
            outcome = run(Path(tmp))

    print(outcome.summary)
    if outcome.node is not None and outcome.node.output:
        print(outcome.node.output)
    if arguments.report:
        arguments.report.write_text(outcome.summary + "\n", encoding="utf-8")
        print(f"Report written to {arguments.report}", file=sys.stderr)

    if outcome.prerequisite_failure:
        return 2
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.exit(main())
