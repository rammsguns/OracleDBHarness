"""The NP-01/03 qualification wiring, checked without a database.

The restricted-account, second-target, PDB and process-death checks only run against
Oracle, so their first real exercise would be the qualification run itself. These check
the parts that decide whether such a run can be trusted: that a missing environment is
reported or refused rather than quietly skipped, that two targets are compared on what
the databases say, and that a process-death run on the stand-in cannot pass for one on
Oracle.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from tests import oracle_config
from tests.process_death.child import Barrier
from tests.process_death.harness import confirm_backend, start_api
from tests.qualification.databases import SAME_CONTAINER, DatabaseIdentity, relation
from tests.qualification.evidence import Evidence
from tests.unit.test_oracle_wiring import configure


@pytest.fixture
def password_file(tmp_path: Path) -> Path:
    path = tmp_path / "qual.password"
    path.write_text("a-real-password\n", encoding="utf-8")
    return path


def _primary(monkeypatch: pytest.MonkeyPatch, password_file: Path, **extra: str) -> None:
    configure(
        monkeypatch,
        HARNESS_QUAL_ORACLE_DSN="dbhost:1521/ORCLPDB1",
        HARNESS_QUAL_ORACLE_USER="harness_app",
        HARNESS_QUAL_ORACLE_PASSWORD_FILE=str(password_file),
        **extra,
    )


# -- the restricted account ------------------------------------------------------------


def test_the_restricted_account_defaults_to_the_primary_database(
    monkeypatch: pytest.MonkeyPatch, password_file: Path
) -> None:
    _primary(
        monkeypatch,
        password_file,
        HARNESS_QUAL_RESTRICTED_USER="harness_ro",
        HARNESS_QUAL_RESTRICTED_PASSWORD_FILE=str(password_file),
    )
    config = oracle_config.load()
    spec = config.restricted_spec()
    assert spec is not None
    assert spec.dsn() == "dbhost:1521/ORCLPDB1"
    assert spec.username == "harness_ro"
    # It addresses the fixture schema: what it cannot see there is the point.
    assert spec.default_schema == oracle_config.DEMO_SCHEMA
    assert "a-real-password" not in repr(config)


def test_the_primary_account_cannot_stand_in_for_a_restricted_one(
    monkeypatch: pytest.MonkeyPatch, password_file: Path
) -> None:
    _primary(
        monkeypatch,
        password_file,
        HARNESS_QUAL_RESTRICTED_USER="HARNESS_APP",
        HARNESS_QUAL_RESTRICTED_PASSWORD_FILE=str(password_file),
    )
    with pytest.raises(oracle_config.ConfigurationProblem, match="different account"):
        oracle_config.load()


def test_a_restricted_dsn_without_an_account_is_refused(
    monkeypatch: pytest.MonkeyPatch, password_file: Path
) -> None:
    _primary(monkeypatch, password_file, HARNESS_QUAL_RESTRICTED_DSN="dbhost:1521/ORCLPDB1")
    with pytest.raises(oracle_config.ConfigurationProblem, match="RESTRICTED_USER"):
        oracle_config.load()


# -- required environments -------------------------------------------------------------


@pytest.mark.parametrize(
    ("area", "variable"),
    [
        ("admin", "HARNESS_QUAL_ADMIN_DSN"),
        ("second_target", "HARNESS_QUAL_SECOND_DSN"),
        ("restricted_account", "HARNESS_QUAL_RESTRICTED_USER"),
    ],
)
def test_a_required_environment_that_is_not_configured_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, password_file: Path, area: str, variable: str
) -> None:
    """Otherwise the run meant to close the gate skips those checks and looks green."""

    _primary(monkeypatch, password_file, HARNESS_QUAL_REQUIRE=f"pdb, {area}")
    with pytest.raises(oracle_config.ConfigurationProblem, match=variable):
        oracle_config.load()


def test_an_unknown_requirement_is_refused(
    monkeypatch: pytest.MonkeyPatch, password_file: Path
) -> None:
    _primary(monkeypatch, password_file, HARNESS_QUAL_REQUIRE="pdb,rac")
    with pytest.raises(oracle_config.ConfigurationProblem, match="rac"):
        oracle_config.load()


def test_requirements_are_available_to_the_checks(
    monkeypatch: pytest.MonkeyPatch, password_file: Path
) -> None:
    _primary(monkeypatch, password_file, HARNESS_QUAL_REQUIRE="PDB")
    config = oracle_config.load()
    assert config.requires("pdb")
    assert not config.requires("admin")


# -- two targets -----------------------------------------------------------------------


def _identity(**overrides: str) -> DatabaseIdentity:
    values = {
        "dbid": "1111",
        "con_dbid": "2222",
        "container": "PDB1",
        "unique_name": "ORCLCDB",
        "instance": "ORCLCDB",
        "server_host": "db1",
        "session_user": "HARNESS_APP",
    }
    values.update(overrides)
    return DatabaseIdentity(**values)


def test_two_spellings_of_one_container_are_not_two_targets() -> None:
    assert relation(_identity(), _identity(server_host="DB1")) == SAME_CONTAINER


def test_two_pdbs_of_one_cdb_are_named_as_such() -> None:
    found = relation(_identity(), _identity(con_dbid="3333", container="PDB2"))
    assert found != SAME_CONTAINER
    assert "one instance" in found


def test_clones_on_separate_hosts_are_separate_targets() -> None:
    """Databases built from one prebuilt image share a DBID and are still separate."""

    found = relation(_identity(), _identity(server_host="db2"))
    assert found != SAME_CONTAINER
    assert "clones" in found


# -- evidence --------------------------------------------------------------------------


def test_the_report_names_what_was_not_exercised(
    monkeypatch: pytest.MonkeyPatch, password_file: Path, tmp_path: Path
) -> None:
    _primary(monkeypatch, password_file)
    config = oracle_config.load()
    path = tmp_path / "report-process-death.md"
    record = Evidence(config=config, title="Process-death run", path=path)
    record.note("Process death before dispatch", "cancelled")
    record.gap("PDB identity", "the target is a non-CDB")
    record.write()

    text = path.read_text(encoding="utf-8")
    assert text.startswith("### Process-death run ")
    assert "Not exercised by this run:" in text
    assert "- PDB identity: the target is a non-CDB" in text
    assert "a-real-password" not in text
    assert path.with_suffix(".json").exists()


# -- process death ---------------------------------------------------------------------


def test_a_stand_in_child_cannot_be_recorded_as_an_oracle_run() -> None:
    stand_in = {"versionFull": "Oracle Database stand-in 19.0 (harness fake backend)"}
    with pytest.raises(AssertionError, match="not 'oracledb'"):
        confirm_backend({"backend": "fake"}, stand_in, on_oracle=True)
    with pytest.raises(AssertionError, match="does not match"):
        confirm_backend({"backend": "oracledb"}, stand_in, on_oracle=True)
    confirm_backend({"backend": "fake"}, stand_in, on_oracle=False)
    confirm_backend(
        {"backend": "oracledb"},
        {"versionFull": "Oracle Database 19c Enterprise Edition Release 19.0.0.0.0"},
        on_oracle=True,
    )


def test_an_unknown_barrier_is_refused_before_anything_starts(tmp_path: Path) -> None:
    directory = tmp_path / "child"
    with pytest.raises(SystemExit, match="Unknown barrier"):
        Barrier("during_lunch", "", directory)
    with pytest.raises(ValueError, match="Unknown barrier"):
        start_api(None, directory, barrier="during_lunch")  # type: ignore[arg-type]
    assert not directory.exists(), "a process was configured for an unknown barrier"


def test_the_mid_statement_scenario_is_still_marked_oracle_only() -> None:
    """Against the stand-in it has no row lock to wait on, and would fail, not skip."""

    module = importlib.import_module("tests.integration.test_process_death")
    function = module.test_death_while_a_write_waits_inside_the_database
    assert "oracle_only" in {mark.name for mark in getattr(function, "pytestmark", [])}
