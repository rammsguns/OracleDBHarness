"""Guard the switch that points the API suites at Oracle.

None of this code runs unless someone has a database, so without these checks its
first exercise would be during a qualification run, which is the worst moment to
discover that a DSN is parsed wrongly or that a skip marker was lost in a rename.

Nothing here connects to anything. What it asserts is that the configuration is read
correctly, that the demonstration seed can be pointed somewhere else, and that the
tests which cannot run against Oracle are still marked as such.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from sqlalchemy import select

from harness_api.config import Settings
from harness_api.db import build_engine, build_session_factory
from harness_api.models import ConnectionProfile, SecretReference
from harness_api.seed import DEMO_TARGETS, TargetEndpoint, _resolve_endpoints, seed
from harness_worker.errors import ConfigurationError
from tests import oracle_config

# -- configuration ---------------------------------------------------------------------


@pytest.fixture
def password_file(tmp_path: Path) -> Path:
    path = tmp_path / "qual.password"
    path.write_text("a-real-password\n", encoding="utf-8")
    return path


def configure(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name in dir(oracle_config):
        if name.startswith("ENV_") and name != "ENV_PREFIX":
            monkeypatch.delenv(getattr(oracle_config, name), raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_an_unset_dsn_means_the_stand_in(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    assert oracle_config.is_configured() is False
    with pytest.raises(oracle_config.ConfigurationProblem):
        oracle_config.load()


def test_a_configured_target_is_parsed_into_connection_fields(
    monkeypatch: pytest.MonkeyPatch, password_file: Path
) -> None:
    configure(
        monkeypatch,
        HARNESS_QUAL_ORACLE_DSN="dbhost:1521/ORCLPDB1",
        HARNESS_QUAL_ORACLE_USER="harness_app",
        HARNESS_QUAL_ORACLE_PASSWORD_FILE=str(password_file),
    )
    config = oracle_config.load()

    assert config.primary.host == "dbhost"
    assert config.primary.port == 1521
    assert config.primary.service_name == "ORCLPDB1"
    assert config.primary.password == "a-real-password"
    # The suites address the demonstration schema by name, so it is the default.
    assert config.primary.schema == oracle_config.DEMO_SCHEMA
    assert config.second is None
    assert config.admin is None

    spec = config.connection_spec()
    assert spec.dsn() == "dbhost:1521/ORCLPDB1"
    assert spec.default_schema == oracle_config.DEMO_SCHEMA


def test_the_password_never_appears_in_a_repr(
    monkeypatch: pytest.MonkeyPatch, password_file: Path
) -> None:
    """These objects end up in pytest output on a failure."""

    configure(
        monkeypatch,
        HARNESS_QUAL_ORACLE_DSN="dbhost:1521/ORCLPDB1",
        HARNESS_QUAL_ORACLE_USER="harness_app",
        HARNESS_QUAL_ORACLE_PASSWORD_FILE=str(password_file),
    )
    config = oracle_config.load()
    assert "a-real-password" not in repr(config)
    assert "a-real-password" not in repr(config.primary)
    assert "a-real-password" not in repr(config.connection_spec())


@pytest.mark.parametrize(
    ("dsn", "fragment"),
    [
        ("dbhost/ORCLPDB1", "host:port/service"),
        ("dbhost:1521", "host:port/service"),
        ("dbhost:not-a-port/ORCLPDB1", "non-numeric port"),
        ("(DESCRIPTION=(ADDRESS=...))", "host:port/service"),
    ],
)
def test_a_dsn_the_application_could_not_express_is_refused(
    monkeypatch: pytest.MonkeyPatch, password_file: Path, dsn: str, fragment: str
) -> None:
    """A profile has separate host, port and service columns and nothing else.

    Accepting a connect descriptor here would qualify a connection path no profile
    can express, so the run would prove something the application cannot do.
    """

    configure(
        monkeypatch,
        HARNESS_QUAL_ORACLE_DSN=dsn,
        HARNESS_QUAL_ORACLE_USER="harness_app",
        HARNESS_QUAL_ORACLE_PASSWORD_FILE=str(password_file),
    )
    with pytest.raises(oracle_config.ConfigurationProblem, match=fragment):
        oracle_config.load()


def test_a_half_configured_run_fails_rather_than_skipping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Switched on but unusable is an error, not a quiet skip.

    A qualification run that silently did nothing is indistinguishable from a passing
    one in a CI summary, which is the failure worth preventing.
    """

    configure(monkeypatch, HARNESS_QUAL_ORACLE_DSN="dbhost:1521/ORCLPDB1")
    assert oracle_config.is_configured() is True
    with pytest.raises(oracle_config.ConfigurationProblem, match="ORACLE_USER"):
        oracle_config.load()


def test_a_missing_password_file_is_named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configure(
        monkeypatch,
        HARNESS_QUAL_ORACLE_DSN="dbhost:1521/ORCLPDB1",
        HARNESS_QUAL_ORACLE_USER="harness_app",
        HARNESS_QUAL_ORACLE_PASSWORD_FILE=str(tmp_path / "absent"),
    )
    with pytest.raises(oracle_config.ConfigurationProblem, match="not a file"):
        oracle_config.load()


def test_thick_mode_needs_the_client_libraries(
    monkeypatch: pytest.MonkeyPatch, password_file: Path
) -> None:
    configure(
        monkeypatch,
        HARNESS_QUAL_ORACLE_DSN="dbhost:1521/ORCLPDB1",
        HARNESS_QUAL_ORACLE_USER="harness_app",
        HARNESS_QUAL_ORACLE_PASSWORD_FILE=str(password_file),
        HARNESS_QUAL_DRIVER_MODE="thick",
    )
    with pytest.raises(oracle_config.ConfigurationProblem, match="CLIENT_LIB_DIR"):
        oracle_config.load()


def test_a_second_target_on_the_same_database_is_refused(
    monkeypatch: pytest.MonkeyPatch, password_file: Path
) -> None:
    """The isolation checks compare two targets; one database would pass trivially."""

    configure(
        monkeypatch,
        HARNESS_QUAL_ORACLE_DSN="dbhost:1521/ORCLPDB1",
        HARNESS_QUAL_ORACLE_USER="harness_app",
        HARNESS_QUAL_ORACLE_PASSWORD_FILE=str(password_file),
        HARNESS_QUAL_SECOND_DSN="dbhost:1521/ORCLPDB1",
        HARNESS_QUAL_SECOND_USER="harness_app",
        HARNESS_QUAL_SECOND_PASSWORD_FILE=str(password_file),
    )
    with pytest.raises(oracle_config.ConfigurationProblem, match="same database"):
        oracle_config.load()


def test_a_second_target_is_a_separate_endpoint(
    monkeypatch: pytest.MonkeyPatch, password_file: Path
) -> None:
    configure(
        monkeypatch,
        HARNESS_QUAL_ORACLE_DSN="dbhost:1521/ORCLPDB1",
        HARNESS_QUAL_ORACLE_USER="harness_app",
        HARNESS_QUAL_ORACLE_PASSWORD_FILE=str(password_file),
        HARNESS_QUAL_SECOND_DSN="dbhost:1521/ORCLPDB2",
        HARNESS_QUAL_SECOND_USER="harness_app",
        HARNESS_QUAL_SECOND_PASSWORD_FILE=str(password_file),
    )
    config = oracle_config.load()
    assert oracle_config.has_second_target() is True
    assert config.second is not None
    assert config.second.service_name == "ORCLPDB2"
    second = config.second_spec()
    assert second is not None
    assert second.dsn() == "dbhost:1521/ORCLPDB2"


def test_the_privileged_connection_does_not_enter_the_fixture_schema(
    monkeypatch: pytest.MonkeyPatch, password_file: Path
) -> None:
    """It kills sessions; it has no business having its schema switched."""

    configure(
        monkeypatch,
        HARNESS_QUAL_ORACLE_DSN="dbhost:1521/ORCLPDB1",
        HARNESS_QUAL_ORACLE_USER="harness_app",
        HARNESS_QUAL_ORACLE_PASSWORD_FILE=str(password_file),
        HARNESS_QUAL_ADMIN_DSN="dbhost:1521/ORCLPDB1",
        HARNESS_QUAL_ADMIN_USER="system",
        HARNESS_QUAL_ADMIN_PASSWORD_FILE=str(password_file),
    )
    spec = oracle_config.load().admin_spec()
    assert spec is not None
    assert spec.username == "system"
    assert spec.default_schema is None


# -- pointing the demonstration seed somewhere else ------------------------------------


def test_endpoints_default_to_the_stand_ins() -> None:
    resolved = _resolve_endpoints(None)
    assert set(resolved) == {str(spec["name"]) for spec in DEMO_TARGETS}
    assert resolved["development"].host == "localhost"
    assert resolved["development"].service_name == "DEVPDB1"


def test_an_override_for_an_unknown_target_is_refused() -> None:
    """Silently ignoring it would mean waiting for a connection error to find out."""

    with pytest.raises(ConfigurationError, match="staging"):
        _resolve_endpoints({"staging": TargetEndpoint(service_name="X")})


def test_an_unoverridden_target_keeps_its_default() -> None:
    resolved = _resolve_endpoints(
        {"development": TargetEndpoint(host="db1", port=1522, service_name="PDB_A")}
    )
    assert resolved["development"].host == "db1"
    assert resolved["production"].host == "localhost"
    assert resolved["production"].service_name == "PRODPDB1"


def test_one_credential_reference_over_two_files_is_refused() -> None:
    """Keeping the first file would give the second target the first's password."""

    with pytest.raises(ConfigurationError, match="different files"):
        _resolve_endpoints(
            {
                "development": TargetEndpoint(service_name="PDB_A"),
                "test": TargetEndpoint(
                    service_name="PDB_B",
                    secret_locator="second.password",  # noqa: S106 - a file name
                ),
            }
        )


def test_targets_may_share_a_credential_reference_that_means_one_file() -> None:
    shared = TargetEndpoint(host="db1", service_name="PDB_A")
    resolved = _resolve_endpoints({"development": shared, "production": shared})
    assert resolved["production"].secret_locator == resolved["development"].secret_locator


def _seed_settings(tmp_path: Path) -> Settings:
    # The autouse workspace fixture has already made these.
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "fake").mkdir(exist_ok=True)
    return Settings(
        env="development",
        metadata_url=f"sqlite+pysqlite:///{(tmp_path / 'metadata.sqlite3').as_posix()}",
        oracle_backend="fake",
        oracle_fake_data_dir=str(tmp_path / "fake"),
        secret_dir=str(tmp_path / "secrets"),
        auth_mode="dev",
        dev_token_secret="test-secret",
    )


def test_reseeding_a_stored_credential_reference_onto_another_file_is_refused(
    tmp_path: Path,
) -> None:
    """The stored reference would win, and the new file would never be read."""

    settings = _seed_settings(tmp_path)
    seed(settings, probe=False)

    moved = TargetEndpoint(
        service_name="PDB_A",
        secret_locator="moved.password",  # noqa: S106 - a file name
    )
    with pytest.raises(ConfigurationError, match="already exists"):
        seed(
            settings,
            probe=False,
            endpoints={"development": moved, "test": moved, "production": moved},
        )


def test_seeding_against_overridden_endpoints_keeps_their_credentials_apart(
    tmp_path: Path,
) -> None:
    """Two targets on two databases need two credential references, not one.

    Sharing one would mean the second target authenticating with the first's
    password, which is exactly the credential mixing the isolation criteria forbid.
    """

    settings = _seed_settings(tmp_path)
    (tmp_path / "secrets" / "second.password").write_text("second-password", encoding="utf-8")

    seed(
        settings,
        probe=False,
        endpoints={
            "development": TargetEndpoint(host="db1", port=1522, service_name="PDB_A"),
            "test": TargetEndpoint(
                host="db2",
                port=1523,
                service_name="PDB_B",
                secret_name="second",  # noqa: S106 - a reference name
                secret_locator="second.password",  # noqa: S106 - a file name
            ),
        },
    )

    factory = build_session_factory(build_engine(settings))
    with factory() as db:
        profiles = {p.name: p for p in db.scalars(select(ConnectionProfile))}
        assert (profiles["development"].host, profiles["development"].port) == ("db1", 1522)
        assert (profiles["test"].host, profiles["test"].port) == ("db2", 1523)
        assert profiles["production"].host == "localhost"

        secrets = {
            name: db.get(SecretReference, profiles[name].secret_reference_id)
            for name in ("development", "test")
        }
        assert secrets["development"] is not None and secrets["test"] is not None
        assert secrets["development"].id != secrets["test"].id
        assert secrets["test"].locator == "second.password"


# -- the skips ------------------------------------------------------------------------

STAND_IN_ONLY = {
    "tests.integration.test_commit_outcomes": [
        "test_a_refused_commit_leaves_the_work_pending_on_a_usable_session",
        "test_a_commit_whose_answer_is_lost_is_reported_as_outcome_unknown",
        "test_a_lost_commit_is_recorded_in_the_audit_trail",
        "test_a_session_that_lost_its_commit_is_retired_not_reused",
        "test_a_lost_one_shot_commit_is_not_recorded_as_a_failed_execution",
    ],
    "tests.integration.test_tuning": [
        "test_a_plan_query_that_failed_is_not_reported_as_an_available_empty_plan",
        "test_a_cursor_search_that_failed_is_not_reported_as_no_cursors",
        "test_cursor_statistics_that_failed_are_not_reported_as_measured",
    ],
}


@pytest.mark.parametrize(
    ("module_name", "test_name"),
    [(module, name) for module, names in STAND_IN_ONLY.items() for name in names],
)
def test_the_stand_in_only_tests_are_still_marked(module_name: str, test_name: str) -> None:
    """A rename that drops the marker would fail confusingly on a qualification run.

    Each of these either injects a fault the stand-in exposes on purpose, or patches
    its class. Against Oracle they cannot run at all; the equivalent evidence comes
    from tests/qualification.
    """

    module = importlib.import_module(module_name)
    function = getattr(module, test_name, None)
    assert function is not None, f"{module_name}.{test_name} no longer exists"
    markers = {mark.name for mark in getattr(function, "pytestmark", [])}
    assert "stand_in_only" in markers, (
        f"{module_name}.{test_name} lost its stand_in_only marker. It patches or "
        "fault-injects the stand-in, so against Oracle it would fail rather than skip."
    )


def test_every_fault_injecting_test_is_accounted_for() -> None:
    """Catch a new fault-injection test that nobody marked.

    ``fail_next_commit`` and patching ``FakeOracleConnection`` are the two ways the
    suite reaches into the stand-in. Both are stand-in only by construction.
    """

    from tests.conftest import _repository_root  # noqa: PLC0415 - local to this check

    marked = {name for names in STAND_IN_ONLY.values() for name in names}
    offenders: list[str] = []
    for path in sorted((_repository_root() / "tests" / "integration").glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        blocks = source.split("\ndef test_")
        for block in blocks[1:]:
            name = "test_" + block.split("(", 1)[0].strip()
            uses_fault = "fail_next_commit" in block or "FakeOracleConnection" in block
            if uses_fault and name not in marked:
                offenders.append(f"{path.name}::{name}")
    assert not offenders, (
        "These reach into the stand-in but are not listed as stand_in_only, so they "
        f"would fail on a qualification run: {offenders}"
    )
