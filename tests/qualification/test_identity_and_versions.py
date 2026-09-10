"""Prove what we are connected to, and write it down.

This module runs first by name and is the one that makes every other result in the
suite citable. A pass here means the recorded version in docs/compatibility.md came
from the database rather than from whoever filled in the table.
"""

from __future__ import annotations

from typing import Any

from harness_worker.backend import OracleConnection
from harness_worker.types import Capability, ExecutionLimits, StatementKind
from tests.oracle_config import OracleTestConfig
from tests.qualification.evidence import Evidence, driver_versions


def _scalar(connection: OracleConnection, sql: str, limits: ExecutionLimits) -> Any:
    result = connection.execute(sql, {}, StatementKind.QUERY, limits)
    assert result.result_set is not None, sql
    assert result.result_set.rows, f"{sql} returned no rows"
    return result.result_set.rows[0][0]


def test_the_target_reports_its_own_identity(
    connection: OracleConnection,
    evidence: Evidence,
    oracle_config: OracleTestConfig,
) -> None:
    """Identity is queried, never derived from the profile."""

    identity = connection.identity()

    assert identity.version_full, "The database did not report a version."
    assert identity.current_user.upper() == oracle_config.username.upper()
    assert identity.session_id is not None

    evidence.record_database(
        {
            "version": identity.version,
            "versionFull": identity.version_full,
            "databaseName": identity.database_name,
            "instanceName": identity.instance_name,
            "hostName": identity.host_name,
            "isCdb": identity.is_cdb,
            "containerName": identity.container_name,
            "currentUser": identity.current_user,
            "currentSchema": identity.current_schema,
        }
    )


def test_the_default_schema_was_actually_set(
    connection: OracleConnection,
    oracle_config: OracleTestConfig,
) -> None:
    """The stand-in reports default_schema without ever setting it. Oracle cannot.

    ``ALTER SESSION SET CURRENT_SCHEMA`` is what makes an unqualified object name
    resolve where the profile says it should. If it silently did not run, every
    other test in this suite would be reading someone else's tables.
    """

    identity = connection.identity()
    assert identity.current_schema.upper() == oracle_config.schema.upper()


def test_the_recorded_version_is_the_compatibility_target(
    connection: OracleConnection,
    evidence: Evidence,
) -> None:
    """19c is the stated target. Anything else is recorded, not silently accepted."""

    identity = connection.identity()
    if identity.major_version != 19:
        evidence.note(
            "Database major version",
            f"**{identity.major_version}**, not 19. This run does not qualify "
            f"Oracle 19c. Full version: `{identity.version_full}`.",
        )
    else:
        evidence.note("Database major version", f"19c (`{identity.version_full}`)")


def test_character_set_and_national_character_set_are_recorded(
    connection: OracleConnection,
    evidence: Evidence,
    limits: ExecutionLimits,
) -> None:
    """The Unicode and LOB results below mean nothing without these two values."""

    charset = _scalar(
        connection,
        "SELECT value FROM nls_database_parameters WHERE parameter = 'NLS_CHARACTERSET'",
        limits,
    )
    ncharset = _scalar(
        connection,
        "SELECT value FROM nls_database_parameters WHERE parameter = 'NLS_NCHAR_CHARACTERSET'",
        limits,
    )
    evidence.record_database({"characterSet": charset, "nationalCharacterSet": ncharset})

    if str(charset).upper() != "AL32UTF8":
        evidence.note(
            "Database character set",
            f"`{charset}`, not AL32UTF8. Treat the Unicode bind results as specific "
            "to this character set.",
        )


def test_the_driver_version_is_recorded(
    connection: OracleConnection,
    evidence: Evidence,
    oracle_config: OracleTestConfig,
) -> None:
    """Driver mode is process wide, so it belongs on the record with the version."""

    versions = driver_versions(oracle_config.driver_mode)
    assert versions["pythonOracledb"] != "not installed"
    evidence.record_database({f"driver.{k}": v for k, v in versions.items()})


def test_capabilities_are_probed_rather_than_assumed(
    connection: OracleConnection,
    evidence: Evidence,
) -> None:
    """Each capability is answered by a real query against this account's grants.

    A capability that is unavailable is a legitimate outcome and is recorded: it
    means a panel degrades, not that the harness is broken. What would be a defect
    is a capability reported available that then fails in use.
    """

    lines = []
    for capability in Capability:
        report = connection.probe_capability(capability)
        state = "available" if report.available else f"unavailable ({report.detail})"
        lines.append(f"{capability.value}: {state}")
    evidence.note("Capabilities on this account", "<br>".join(lines))
