"""Kiwi's K-4 lookups against a real dictionary.

The stand-in answers these from seeded rows, so a column that does not exist on 19c,
or a PL/Scope view that behaves differently, would pass there and fail here. Each
check runs the shipped catalog SQL, not a copy of it.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness_worker.backend import OracleConnection
from harness_worker.catalog import load_catalog
from harness_worker.types import ExecutionLimits, StatementKind
from tests import oracle_config as qual_config
from tests.oracle_fixtures import qualification_dir
from tests.qualification.evidence import Evidence


def _lookup(
    connection: OracleConnection,
    limits: ExecutionLimits,
    operation_id: str,
    **parameters: Any,
) -> tuple[list[str], list[list[Any]]]:
    entry = load_catalog(qualification_dir().parent).get(operation_id)
    binds = {name: parameters.get(name) for name in entry.parameters}
    if "row_limit" in binds and binds["row_limit"] is None:
        binds["row_limit"] = 100
    if "row_offset" in binds and binds["row_offset"] is None:
        binds["row_offset"] = 0
    result = connection.execute(entry.sql, binds, StatementKind.QUERY, limits)
    assert result.result_set is not None
    columns = [column.name for column in result.result_set.columns]
    return columns, [list(row) for row in result.result_set.rows]


def _column(columns: list[str], rows: list[list[Any]], name: str) -> list[Any]:
    index = columns.index(name)
    return [row[index] for row in rows]


@pytest.fixture
def owner(oracle_config: qual_config.OracleTestConfig) -> str:
    return oracle_config.schema.upper()


def test_references_subprograms_and_source_ranges(
    connection: OracleConnection, limits: ExecutionLimits, owner: str, evidence: Evidence
) -> None:
    columns, rows = _lookup(
        connection, limits, "schema.object_referenced_by", owner=owner, object_name="EMPLOYEES"
    )
    names = set(_column(columns, rows, "NAME"))
    assert {"HARNESS_EMP_EMAIL_TRG", "HARNESS_DEPT_HEADCOUNT"} <= names

    columns, rows = _lookup(
        connection,
        limits,
        "schema.package_subprograms",
        owner=owner,
        package_name="EMPLOYEE_REPORT",
    )
    assert {"HEADCOUNT", "REPORT_DEPARTMENT", "EMIT_LINES"} <= set(
        _column(columns, rows, "SUBPROGRAM_NAME")
    )

    columns, rows = _lookup(
        connection,
        limits,
        "schema.object_source_range",
        owner=owner,
        object_name="HARNESS_DEPT_HEADCOUNT",
        object_type="FUNCTION",
        start_line=3,
        end_line=4,
    )
    assert _column(columns, rows, "LINE") == [3, 4]
    evidence.note("K-4 dependency, subprogram and source lookups", "rows as on the stand-in")


def test_plscope_absence_is_reported_not_guessed(
    connection: OracleConnection, limits: ExecutionLimits, owner: str, evidence: Evidence
) -> None:
    columns, rows = _lookup(
        connection,
        limits,
        "schema.plscope_identifiers",
        owner=owner,
        object_name="EMPLOYEE_REPORT",
        object_type="PACKAGE BODY",
    )
    statuses = set(_column(columns, rows, "PLSCOPE_STATUS"))
    assert len(statuses) == 1
    assert next(iter(statuses)).startswith("NOT COLLECTED")

    columns, rows = _lookup(
        connection,
        limits,
        "schema.plscope_identifiers",
        owner=owner,
        object_name="HARNESS_DEPT_HEADCOUNT",
        object_type="FUNCTION",
    )
    assert set(_column(columns, rows, "PLSCOPE_STATUS")) == {"COLLECTED"}
    assert "L_COUNT" in _column(columns, rows, "NAME")

    columns, rows = _lookup(
        connection,
        limits,
        "schema.plscope_statements",
        owner=owner,
        object_name="HARNESS_DEPT_HEADCOUNT",
        object_type="FUNCTION",
    )
    texts = [str(text) for text in _column(columns, rows, "SQL_TEXT") if text]
    assert any(text.upper().startswith("SELECT COUNT(*) FROM EMPLOYEES") for text in texts)
    evidence.note("PL/Scope absence", "EMPLOYEE_REPORT reports NOT COLLECTED; the function is read")


def test_triggers_links_and_scheduler(
    connection: OracleConnection, limits: ExecutionLimits, owner: str, evidence: Evidence
) -> None:
    columns, rows = _lookup(
        connection, limits, "schema.triggers", owner=owner, table_name="EMPLOYEES"
    )
    assert "HARNESS_EMP_EMAIL_TRG" in _column(columns, rows, "TRIGGER_NAME")

    # No link exists in the qualification schema: the shape is what is checked here.
    columns, rows = _lookup(
        connection,
        limits,
        "schema.db_links_referenced",
        owner=owner,
        object_name="HARNESS_DEPT_HEADCOUNT",
    )
    assert "USERNAME" not in columns and "HOST" not in columns
    assert rows == []

    columns, rows = _lookup(
        connection, limits, "dba.scheduler_job_detail", owner=owner, job_name="HARNESS_NOOP_JOB"
    )
    assert len(rows) == 1

    columns, rows = _lookup(
        connection, limits, "dba.scheduler_chain", owner=owner, chain_name="HARNESS_CHAIN"
    )
    assert rows

    columns, rows = _lookup(
        connection, limits, "dba.scheduler_run_history", owner=owner, job_name="HARNESS_NOOP_JOB"
    )
    assert rows
    evidence.note("K-4 trigger, link and scheduler lookups", "rows as on the stand-in; no link")
