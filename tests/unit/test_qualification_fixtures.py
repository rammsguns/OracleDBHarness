"""Guard the Oracle qualification fixtures without an Oracle.

The scripts in ``oracle/qualification/`` only run when someone has a database, which
means a typo in one of them would otherwise be found by the person who can least
afford the delay: whoever finally has a 19c instance in front of them. These checks
run in ordinary CI and catch the structural mistakes.

They assert nothing about whether Oracle accepts the SQL. Only Oracle can say that.
"""

from __future__ import annotations

import re

import pytest

from harness_worker.catalog import load_catalog
from harness_worker.statement import classify
from harness_worker.types import StatementKind
from tests.oracle_fixtures import Step, parse_script, qualification_dir

SCRIPTS = ["01_fixtures.sql", "02_teardown.sql"]


def _object_names(steps: list[Step], prefix_pattern: str) -> set[str]:
    """Object names appearing after a CREATE or DROP, however they are quoted.

    The teardown wraps its drops in ``EXECUTE IMMEDIATE`` string literals, so this
    reads the whole statement text rather than only its first keyword.
    """

    pattern = re.compile(prefix_pattern + r'("[^"]+"|[A-Za-z_][A-Za-z0-9_$#]*)', re.IGNORECASE)
    names: set[str] = set()
    for step in steps:
        for match in pattern.finditer(step.sql):
            names.add(match.group(1).strip('"').upper())
    return names


@pytest.mark.parametrize("name", SCRIPTS)
def test_every_script_parses_into_named_statements(name: str) -> None:
    steps = parse_script(qualification_dir() / name)
    assert steps, f"{name} produced no statements"
    names = [step.name for step in steps]
    assert len(names) == len(set(names)), f"Duplicate step names in {name}: {names}"
    for step in steps:
        assert step.sql.strip(), f"Step {step.name!r} in {name} is empty"


@pytest.mark.parametrize("name", SCRIPTS)
def test_a_plsql_block_keeps_its_terminating_semicolon(name: str) -> None:
    """``END`` without its semicolon does not compile.

    The parser normalises a trailing ``/`` and must never take the ``;`` with it.
    """

    for step in parse_script(qualification_dir() / name):
        kind = classify(step.sql)
        if kind in (StatementKind.PLSQL_BLOCK, StatementKind.PLSQL_SOURCE):
            assert step.sql.rstrip().endswith(";"), (
                f"Step {step.name!r} in {name} is {kind.value} but does not end in "
                f"';'. It ends: {step.sql[-40:]!r}"
            )


def test_the_fixtures_and_the_teardown_agree_on_what_exists() -> None:
    """Everything the setup creates has a matching drop.

    A fixture left behind changes the result of the next run, and the tests that
    assert on row counts would start failing for a reason that has nothing to do
    with Oracle.
    """

    created = _object_names(
        parse_script(qualification_dir() / "01_fixtures.sql"),
        r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|PACKAGE\s+BODY|PACKAGE|PROCEDURE|FUNCTION)\s+",
    )
    dropped = _object_names(
        parse_script(qualification_dir() / "02_teardown.sql"),
        r"DROP\s+(?:TABLE|PACKAGE\s+BODY|PACKAGE|PROCEDURE|FUNCTION)\s+",
    )
    assert created, "No CREATE statements were found; the pattern is wrong."
    missing = created - dropped
    assert not missing, (
        f"01_fixtures.sql creates {sorted(missing)} but 02_teardown.sql never drops "
        "them. They would survive into the next run."
    )


def test_the_qualification_scripts_are_not_loaded_as_operations() -> None:
    """The catalog scans oracle/ recursively. These are scripts, not operations.

    Without the directory exclusion, ``load_catalog`` would try to parse the fixture
    DDL as a reviewed diagnostic query and either fail at startup or publish an
    operation that drops tables.
    """

    catalog = load_catalog(qualification_dir().parent)
    assert len(catalog) > 0, "The catalog loaded nothing at all; the root is wrong."
    for entry in catalog.list():
        assert entry.source_path is not None
        assert "qualification" not in entry.source_path.parts, (
            f"{entry.operation_id} was loaded from the qualification fixtures."
        )
