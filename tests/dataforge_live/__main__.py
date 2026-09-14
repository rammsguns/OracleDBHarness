"""``python -m tests.dataforge_live`` - real-harness checks for the DataForge adapter.

    uv run python -m tests.dataforge_live run [--report PATH]

Starts a disposable harness API (fake Oracle backend, fixture copilot provider),
issues it a real ``dataforge`` integration credential, and runs the adapter and
route-registration code in ``integrations/dataforge/src`` against that live process,
including through a proxy. See ``tests/dataforge_live/__init__.py`` for what this
does and does not establish, and ``integrations/dataforge/COMPATIBILITY.md`` for
what remains owed for NP-05.

Exit codes: 0 when every check passed; 1 when a check failed; 2 when a prerequisite
(node, `npm install`) is missing and nothing could be checked.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from tests.dataforge_live.runner import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.dataforge_live", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser(
        "run", help="start a disposable harness and run the adapter against it"
    )
    runner.add_argument("--report", type=Path, help="write a summary here")
    runner.add_argument(
        "--workdir", type=Path, help="where the disposable harness keeps its store and logs"
    )
    arguments = parser.parse_args(argv)

    # node's default reporter uses non-ASCII glyphs (checkmarks, "ℹ"); a Windows
    # console in its legacy code page cannot print them, and that must not crash a
    # run that otherwise succeeded. Not every stdout replacement (e.g. in a test
    # harness) has `reconfigure`, so this is best-effort.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")

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
