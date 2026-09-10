"""Apply and remove the Oracle qualification fixtures.

The DDL lives in ``oracle/qualification/*.sql`` so a DBA can review it before it runs
anywhere. This module is only the applier: it splits those files on their ``--#``
markers and runs the statements in order through the same python-oracledb connection
the harness uses, so a fixture that the driver cannot execute fails here rather than
being papered over by a separate client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from harness_worker.backend.base import OracleConnection
from harness_worker.statement import classify, normalize
from harness_worker.types import ExecutionLimits

_SEPARATOR = re.compile(r"^--#\s*(\S+)\s*$", re.MULTILINE)

# Building the slow-query fixture inserts 400,000 rows and gathers statistics, which
# is well past an interactive budget. The fixtures are setup, not a measurement.
_SETUP_LIMITS = ExecutionLimits(
    maxRows=1,
    maxResponseBytes=1 << 20,
    deadlineSeconds=900.0,
    maxDbmsOutputBytes=0,
    lobPreviewBytes=0,
)


def qualification_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "oracle" / "qualification"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("Could not locate oracle/qualification relative to the tests.")


@dataclass(frozen=True)
class Step:
    name: str
    sql: str


def parse_script(path: Path) -> list[Step]:
    """Split one reviewed script into its named statements.

    Anything before the first marker is the file's header comment and is dropped.
    """

    text = path.read_text(encoding="utf-8")
    pieces = _SEPARATOR.split(text)
    if len(pieces) < 3:
        raise ValueError(f"{path} contains no '--#' statement markers.")
    steps: list[Step] = []
    # pieces alternates: [header, name, body, name, body, ...]
    for name, body in zip(pieces[1::2], pieces[2::2], strict=True):
        sql = _strip_comments(body)
        if sql:
            steps.append(Step(name=name, sql=sql))
    return steps


def _strip_comments(body: str) -> str:
    """Drop whole-line ``--`` comments and normalise the trailing terminator.

    Only lines that are entirely a comment are removed. A ``--`` inside a statement
    is left alone, because removing one would change what the database is asked to
    run, and what the database is asked to run is the thing under test.

    ``normalize`` is the harness's own terminator handling, so these scripts are
    accepted on exactly the terms a worksheet statement is. In particular it strips a
    trailing ``/`` and never strips a ``;`` - a PL/SQL block whose closing ``END;``
    lost its semicolon would not compile.
    """

    lines = [line for line in body.splitlines() if not line.lstrip().startswith("--")]
    return normalize("\n".join(lines))


def run_script(connection: OracleConnection, path: Path) -> list[str]:
    """Run every statement in one script, in order. Returns the step names run."""

    executed: list[str] = []
    for step in parse_script(path):
        try:
            connection.execute(step.sql, {}, classify(step.sql), _SETUP_LIMITS)
        except Exception as exc:  # noqa: BLE001 - the step name is what makes this fixable
            raise RuntimeError(
                f"Qualification fixture step {step.name!r} in {path.name} failed: {exc}"
            ) from exc
        executed.append(step.name)
    connection.commit()
    return executed


def apply_fixtures(connection: OracleConnection) -> list[str]:
    return run_script(connection, qualification_dir() / "01_fixtures.sql")


def drop_fixtures(connection: OracleConnection) -> list[str]:
    return run_script(connection, qualification_dir() / "02_teardown.sql")


#: The statement the tuning suite looks for a cached cursor of. Kept identical to
#: SLOW_QUERY in tests/integration/test_tuning.py: Oracle keys a cursor on the exact
#: text, so a difference in whitespace would leave nothing to find.
SLOW_QUERY = (
    "SELECT order_id, SUM(quantity * unit_price) FROM order_lines "
    "WHERE product_id = 3 GROUP BY order_id"
)


def warm_cursor_cache(connection: OracleConnection) -> None:
    """Execute the slow-query fixture once, so a cursor for it exists in ``v$sql``.

    The tuning workbench reads cursors Oracle already has. ``EXPLAIN PLAN`` does not
    execute anything, so nothing else in the suite would put this statement in the
    shared pool, and the cursor checks would fail for a reason that has nothing to do
    with the harness.

    A failure here is not fatal. The cursor checks will then fail on their own and
    say what is missing, which is more useful than the whole session erroring out
    during setup.
    """

    try:
        connection.execute(SLOW_QUERY, {}, classify(SLOW_QUERY), _SETUP_LIMITS)
    except Exception:  # noqa: BLE001,S110 - reported by the tests that need it
        pass
