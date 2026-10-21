"""Kiwi's K-4 lookups against the stand-in (KIWI_PLAN.md, K-4).

Each new catalog entry runs through the same path a model call takes: the toolbox,
``ExecutionService`` and the stand-in's dictionary. The stand-in carries the cases
qualification cannot: a database link with credentials that must never be returned,
and a failed job whose ADDITIONAL_INFO is written to look like instructions.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.provider import ScriptedTurn, ToolCall
from harness_api.copilot.toolbox import KiwiToolbox, tool_name_for
from harness_api.execution import ExecutionService
from harness_worker.backend import etl_fixture as fx
from harness_worker.catalog import parse_entry
from harness_worker.errors import ConfigurationError, ValidationError
from tests.copilot.test_copilot import sse
from tests.copilot.test_kiwi import kiwi_payload, last, use_script

K4_ENTRIES = {
    "schema.object_referenced_by",
    "schema.package_subprograms",
    "schema.object_source_range",
    "schema.object_source_search",
    "schema.plscope_identifiers",
    "schema.plscope_statements",
    "schema.triggers",
    "schema.db_links_referenced",
    "dba.scheduler_job_detail",
    "dba.scheduler_chain",
    "dba.scheduler_run_history",
    "dba.scheduler_program",
}


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"kiwi_enabled": True})


def lookup(
    client: TestClient, headers, profile_id: str, operation_id: str, **parameters: Any
) -> tuple[dict, str]:
    """Run one tool call as a model would; return the stream result and the content."""

    provider = use_script(
        client,
        ScriptedTurn(
            tool_calls=(
                ToolCall(
                    id="c1",
                    name=tool_name_for(operation_id),
                    input={"why": "K-4 check", **parameters},
                ),
            )
        ),
        ScriptedTurn(text="Done."),
    )
    events = sse(client, headers, kiwi_payload(profile_id))
    return last(events, "tool_result"), provider.conversations[0].received[0].content


def rows(content: str) -> tuple[list[str], list[dict]]:
    body = content.splitlines()[1]
    data = json.loads(body)
    return data["columns"], [dict(zip(data["columns"], row, strict=True)) for row in data["rows"]]


def test_every_k4_entry_is_offered_with_privileges_and_a_version(
    execution: ExecutionService, settings
) -> None:
    toolbox = KiwiToolbox(execution, settings)
    offered = {entry.operation_id: entry for entry in toolbox.entries()}
    assert K4_ENTRIES <= set(offered)
    for operation_id in K4_ENTRIES:
        entry = offered[operation_id]
        assert entry.privileges, operation_id
        assert entry.min_version, operation_id


def test_line_bounds_are_offered_as_integers(execution: ExecutionService, settings) -> None:
    toolbox = KiwiToolbox(execution, settings)
    spec = next(s for s in toolbox.specs() if s.name == tool_name_for("schema.object_source_range"))
    assert spec.input_schema["properties"]["start_line"]["type"] == "integer"
    assert spec.input_schema["properties"]["end_line"]["type"] == "integer"


@pytest.mark.parametrize(
    ("operation_id", "parameters", "expect"),
    [
        pytest.param(
            "schema.object_referenced_by",
            {"owner": "HARNESS_APP", "object_name": "EMPLOYEES"},
            # The view is the stand-in's dependent of EMPLOYEES; EMPLOYEE_REPORT's
            # invalid body reads EMPLOYEE and depends on nothing by that name (C16).
            # tests/qualification checks the references Oracle records.
            "V_ACTIVE_EMPLOYEES",
            marks=pytest.mark.stand_in_only,
        ),
        (
            "schema.package_subprograms",
            {"owner": "HARNESS_APP", "package_name": "EMPLOYEE_REPORT"},
            "P_WIDTH",
        ),
        (
            "schema.triggers",
            {"owner": "HARNESS_APP", "table_name": "EMPLOYEES"},
            "HARNESS_EMP_EMAIL_TRG",
        ),
        (
            "dba.scheduler_job_detail",
            {"owner": "HARNESS_APP", "job_name": "HARNESS_NOOP_JOB"},
            "HARNESS_NOOP_PROG",
        ),
        (
            "dba.scheduler_chain",
            {"owner": "HARNESS_APP", "chain_name": "HARNESS_CHAIN"},
            "STEP_ONE COMPLETED",
        ),
        (
            "dba.scheduler_run_history",
            {"owner": "HARNESS_APP", "job_name": "HARNESS_NOOP_JOB"},
            "SUCCEEDED",
        ),
        (
            "dba.scheduler_program",
            {"owner": "HARNESS_APP", "program_name": "ETL_PUBLISH_PROG"},
            "etl_orders.publish",
        ),
    ],
)
def test_each_lookup_runs_on_the_stand_in(
    client: TestClient, developer, targets, operation_id: str, parameters: dict, expect: str
) -> None:
    result, content = lookup(
        client, developer, targets["development"]["id"], operation_id, **parameters
    )
    assert result["status"] == "succeeded", (result, content)
    assert result["rowCount"]
    assert expect in content


def test_a_source_range_returns_only_the_lines_asked_for(
    client: TestClient, developer, targets
) -> None:
    result, content = lookup(
        client,
        developer,
        targets["development"]["id"],
        "schema.object_source_range",
        owner="HARNESS_APP",
        object_name="HARNESS_DEPT_HEADCOUNT",
        object_type="FUNCTION",
        start_line=3,
        end_line=4,
    )
    assert result["status"] == "succeeded", (result, content)
    _, found = rows(content)
    assert [row["LINE"] for row in found] == [3, 4]


def test_a_source_search_finds_a_rule_and_its_caller_in_a_long_body(
    client: TestClient, developer, targets
) -> None:
    body = fx.body_lines()
    result, content = lookup(
        client,
        developer,
        targets["development"]["id"],
        "schema.object_source_search",
        owner=fx.OWNER,
        object_name=fx.PACKAGE,
        object_type="PACKAGE BODY",
        source_filter="%check_rule_042%",
    )
    assert result["status"] == "succeeded", (result, content)
    _, found = rows(content)
    lines = [row["LINE"] for row in found]
    assert lines == [n for n, text in enumerate(body, 1) if "check_rule_042" in text]
    # The definition and the call from the dispatcher: two lookups' worth of reading.
    assert len(lines) >= 2
    assert {row["NAME"] for row in found} == {fx.PACKAGE}


def test_a_source_search_without_an_object_searches_the_owner(
    client: TestClient, developer, targets
) -> None:
    result, content = lookup(
        client,
        developer,
        targets["development"]["id"],
        "schema.object_source_search",
        owner=fx.OWNER,
        source_filter="%check_rule_042%",
    )
    assert result["status"] == "succeeded", (result, content)
    _, found = rows(content)
    assert {(row["NAME"], row["TYPE"]) for row in found} >= {(fx.PACKAGE, "PACKAGE BODY")}


def test_a_source_search_without_a_pattern_is_refused_before_it_runs(
    client: TestClient, developer, targets
) -> None:
    # C19: UPPER(text) LIKE UPPER(NULL) matches nothing, which read as an empty answer.
    result, content = lookup(
        client,
        developer,
        targets["development"]["id"],
        "schema.object_source_search",
        owner=fx.OWNER,
        object_name=fx.PACKAGE,
    )
    assert result["status"] == "invalid", (result, content)
    assert result["executionId"] is None and result["rowCount"] is None
    assert "requires source_filter" in content and "did not run" in content


# -- C19 (round 15): required selectors -------------------------------------------------

# Parameters the harness always fills (row_limit) or that Oracle treats as 0 when NULL
# (row_offset), so they are never required.
_FILLED = {"row_limit", "row_offset"}


def _guarded(sql: str, name: str) -> bool:
    """True when the query lets ``name`` be NULL: ``:name IS NULL OR`` or ``NVL(:name``."""

    return bool(
        re.search(rf":{name}\s+IS\s+NULL", sql, re.I) or re.search(rf"NVL\(\s*:{name}\b", sql, re.I)
    )


def test_every_kiwi_entry_requires_exactly_the_selectors_its_query_compares(
    execution: ExecutionService, settings
) -> None:
    # The @required header is reviewed by hand; this keeps it in step with the SQL.
    for entry in KiwiToolbox(execution, settings).entries():
        used = {p for p in entry.parameters if re.search(rf":{p}\b", entry.sql)}
        derived = {p for p in used if p not in _FILLED and not _guarded(entry.sql, p)}
        assert set(entry.required_parameters) == derived, entry.operation_id


def test_required_selectors_are_in_each_tool_schema(execution: ExecutionService, settings) -> None:
    specs = {spec.name: spec for spec in KiwiToolbox(execution, settings).specs()}
    listing = specs[tool_name_for("schema.list_objects")]
    assert listing.input_schema["required"] == ["owner"]
    assert "Requires owner." in listing.description
    source = specs[tool_name_for("schema.object_source_range")]
    assert source.input_schema["required"] == ["owner", "object_name", "object_type"]
    # Nothing is required where the query guards every parameter.
    assert "required" not in specs[tool_name_for("dba.invalid_objects")].input_schema
    assert "required" not in specs[tool_name_for("tuning.cursor_search")].input_schema


def test_a_required_parameter_the_entry_does_not_take_is_a_configuration_error(
    tmp_path,
) -> None:
    sql = tmp_path / "bad.sql"
    sql.write_text(
        "-- @id: schema.bad\n-- @title: Bad\n-- @parameters: owner\n-- @required: owner, name\n"
        "SELECT 1 FROM dual WHERE :owner IS NOT NULL\n"
    )
    with pytest.raises(ConfigurationError, match="requires parameter"):
        parse_entry(sql)


@pytest.mark.parametrize("owner", [None, "", "   "])
def test_a_listing_without_an_owner_is_refused_not_run_across_schemas(
    client: TestClient, developer, targets, owner
) -> None:
    extra = {} if owner is None else {"owner": owner}
    result, content = lookup(
        client,
        developer,
        targets["development"]["id"],
        "schema.list_objects",
        object_type="TABLE",
        name_filter="%EMPLOY%",
        **extra,
    )
    assert result["status"] == "invalid", (result, content)
    assert result["errorCode"] == "invalid_request"
    assert result["executionId"] is None
    assert "requires owner" in content
    assert "not a search of every schema" in content


def test_the_corrected_listing_finds_the_table(client: TestClient, developer, targets) -> None:
    # DIAG-01 trial 15: lookup 7 (no owner) returned nothing; with the owner it is found.
    result, content = lookup(
        client,
        developer,
        targets["development"]["id"],
        "schema.list_objects",
        owner="HARNESS_APP",
        object_type="TABLE",
        name_filter="%EMPLOY%",
    )
    assert result["status"] == "succeeded", (result, content)
    _, found = rows(content)
    assert {row["OBJECT_NAME"] for row in found} == {"EMPLOYEES"}


def test_a_source_range_without_an_owner_is_refused(client: TestClient, developer, targets) -> None:
    # KINJ-01 trial 15: lookups 6-8 sent no owner and read as unreadable lines.
    result, content = lookup(
        client,
        developer,
        targets["development"]["id"],
        "schema.object_source_range",
        object_name=fx.PACKAGE,
        object_type="PACKAGE BODY",
        start_line=2540,
        end_line=2560,
    )
    assert result["status"] == "invalid", (result, content)
    assert result["executionId"] is None


def test_the_corrected_source_range_returns_the_lines(
    client: TestClient, developer, targets
) -> None:
    result, content = lookup(
        client,
        developer,
        targets["development"]["id"],
        "schema.object_source_range",
        owner=fx.OWNER,
        object_name=fx.PACKAGE,
        object_type="PACKAGE BODY",
        start_line=2540,
        end_line=2560,
    )
    assert result["status"] == "succeeded", (result, content)
    _, found = rows(content)
    assert [row["LINE"] for row in found] == list(range(2540, 2561))


# -- C28: object types the source view can hold ----------------------------------------

_SOURCE_ENTRIES = (
    "schema.object_source",
    "schema.object_source_range",
    "schema.object_source_search",
)


@pytest.mark.parametrize("operation_id", _SOURCE_ENTRIES)
@pytest.mark.parametrize("object_type", ["VIEW", "view", "TABLE", "SYNONYM"])
def test_a_source_lookup_refuses_a_type_all_source_cannot_hold(
    client: TestClient, developer, targets, operation_id, object_type
) -> None:
    # Trial 19 DIAG-01 proposed object_source(type VIEW) to read a view's query.
    # ALL_SOURCE holds no view text, so the call would bind and come back empty.
    extra = {"source_filter": "%"} if operation_id.endswith("search") else {}
    result, content = lookup(
        client,
        developer,
        targets["development"]["id"],
        operation_id,
        owner="HARNESS_APP",
        object_name="V_ACTIVE_EMPLOYEES",
        object_type=object_type,
        **extra,
    )
    assert result["status"] == "invalid", (result, content)
    assert result["errorCode"] == "invalid_request"
    assert result["executionId"] is None and result["rowCount"] is None
    assert "did not run" in content and "ALL_SOURCE" in content
    assert "no lookup here returns a view's query" in content


def test_a_refused_view_source_lookup_is_shown_as_not_run(
    client: TestClient, developer, targets
) -> None:
    # The refusal reaches the user as well as the model: the stream marks the call
    # invalid, and the lookup record keeps what was asked for with no rows.
    use_script(
        client,
        ScriptedTurn(
            tool_calls=(
                ToolCall(
                    id="c1",
                    name=tool_name_for("schema.object_source"),
                    input={
                        "why": "read the view's filter",
                        "owner": "HARNESS_APP",
                        "object_name": "V_ACTIVE_EMPLOYEES",
                        "object_type": "VIEW",
                    },
                ),
            )
        ),
        ScriptedTurn(text="The view's query could not be read here."),
    )
    events = sse(client, developer, kiwi_payload(targets["development"]["id"]))
    result = last(events, "tool_result")
    assert (result["status"], result["errorCode"]) == ("invalid", "invalid_request")
    [entry] = last(events, "lookup_record")["lookups"]
    assert entry["operationId"] == "schema.object_source"
    assert entry["parameters"]["object_type"] == "VIEW"
    assert entry["coverage"] == "failed" and entry["rows"] == 0


@pytest.mark.parametrize("object_type", ["PACKAGE BODY", "package body", " PACKAGE BODY "])
def test_a_stored_source_type_still_runs(
    client: TestClient, developer, targets, object_type
) -> None:
    result, content = lookup(
        client,
        developer,
        targets["development"]["id"],
        "schema.object_source_range",
        owner=fx.OWNER,
        object_name=fx.PACKAGE,
        object_type=object_type,
        start_line=1,
        end_line=3,
    )
    assert result["status"] == "succeeded", (result, content)
    _, found = rows(content)
    assert [row["LINE"] for row in found] == [1, 2, 3]
    function, content = lookup(
        client,
        developer,
        targets["development"]["id"],
        "schema.object_source",
        owner="HARNESS_APP",
        object_name="HARNESS_DEPT_HEADCOUNT",
        object_type="FUNCTION",
    )
    assert function["status"] == "succeeded", (function, content)
    assert function["rowCount"] > 0


def test_a_padded_valid_type_is_not_refused(execution: ExecutionService, settings) -> None:
    # The check compares the trimmed, upper-cased value; the SQL upper-cases it too.
    toolbox = KiwiToolbox(execution, settings)
    entry = next(e for e in toolbox.entries() if e.operation_id == "schema.object_source")
    toolbox._check_object_type(entry, " package body ")
    toolbox._check_object_type(entry, None)
    toolbox._check_object_type(entry, "")


def test_every_source_lookup_lists_the_types_all_source_holds(
    execution: ExecutionService, settings
) -> None:
    toolbox = KiwiToolbox(execution, settings)
    specs = {spec.name: spec for spec in toolbox.specs()}
    for operation_id in _SOURCE_ENTRIES:
        entry = toolbox.entry_for(tool_name_for(operation_id))
        assert entry is not None and entry.privileges == ("SELECT on ALL_SOURCE",)
        assert "PACKAGE BODY" in entry.object_types and "VIEW" not in entry.object_types
        described = specs[tool_name_for(operation_id)].input_schema["properties"]["object_type"]
        assert "cannot return a view's query" in described["description"]
        assert "VIEW, PACKAGE BODY" not in described["description"]
    search = specs[tool_name_for("schema.object_source_search")]
    assert "Leave it out" in search.input_schema["properties"]["object_type"]["description"]
    source = specs[tool_name_for("schema.object_source")]
    assert "Leave it out" not in source.input_schema["properties"]["object_type"]["description"]
    # Lookups on other views keep the general description, VIEW included.
    listing = specs[tool_name_for("schema.list_objects")]
    assert "VIEW" in listing.input_schema["properties"]["object_type"]["description"]


# The TYPE values ALL_SOURCE can hold, from INT$DBA_SOURCE's predicate read live on D1
# 19.9 and D2 23.26 (docs/compatibility.md, 2026-10-19); MLE MODULE is D2's only.
LIVE_SOURCE_TYPES = {
    "FUNCTION",
    "JAVA SOURCE",
    "LIBRARY",
    "MLE MODULE",
    "PACKAGE",
    "PACKAGE BODY",
    "PROCEDURE",
    "TRIGGER",
    "TYPE",
    "TYPE BODY",
}


@pytest.mark.parametrize("operation_id", _SOURCE_ENTRIES)
def test_source_lookups_advertise_the_live_dictionary_types(
    execution: ExecutionService, settings, operation_id
) -> None:
    toolbox = KiwiToolbox(execution, settings)
    entry = toolbox.entry_for(tool_name_for(operation_id))
    assert entry is not None and set(entry.object_types) == LIVE_SOURCE_TYPES
    for listed in ("JAVA SOURCE", " java source ", "LIBRARY", "library", "MLE MODULE"):
        toolbox._check_object_type(entry, listed)
    # ASSEMBLY is in the view's DECODE but not its predicate; the rest are not source.
    for unlisted in ("ASSEMBLY", "VIEW", "TABLE", "JAVA CLASS", "JAVA RESOURCE", "MLE"):
        with pytest.raises(ValidationError) as refused:
            toolbox._check_object_type(entry, unlisted)
        assert refused.value.detail["allowed"] == list(entry.object_types)
        assert "did not run" in refused.value.message
    described = {spec.name: spec for spec in toolbox.specs()}[tool_name_for(operation_id)]
    text = described.input_schema["properties"]["object_type"]["description"]
    assert "ASSEMBLY" not in text
    assert "LIBRARY" in text and "JAVA SOURCE" in text
    assert "MLE MODULE exists from Oracle 23ai only" in text


def test_object_types_on_an_entry_without_object_type_is_a_configuration_error(
    tmp_path,
) -> None:
    sql = tmp_path / "bad.sql"
    sql.write_text(
        "-- @id: schema.bad\n-- @title: Bad\n-- @parameters: owner\n"
        "-- @object_types: PACKAGE\nSELECT 1 FROM dual WHERE :owner IS NOT NULL\n"
    )
    with pytest.raises(ConfigurationError, match="takes no object_type"):
        parse_entry(sql)


def test_optional_filters_may_still_be_left_out(client: TestClient, developer, targets) -> None:
    target = targets["development"]["id"]
    listing, content = lookup(client, developer, target, "schema.list_objects", owner=fx.OWNER)
    assert listing["status"] == "succeeded", (listing, content)
    assert listing["rowCount"] > 0
    # dba.invalid_objects guards its owner, so leaving it out lists every schema it sees.
    invalid, content = lookup(client, developer, target, "dba.invalid_objects")
    assert invalid["status"] == "succeeded", (invalid, content)
    search, content = lookup(client, developer, target, "tuning.cursor_search", text_filter="%")
    assert search["status"] == "succeeded", (search, content)


def test_a_source_search_is_capped_like_every_lookup(
    client: TestClient, developer, targets, settings
) -> None:
    result, content = lookup(
        client,
        developer,
        targets["development"]["id"],
        "schema.object_source_search",
        owner=fx.OWNER,
        object_name=fx.PACKAGE,
        source_filter="%",
        row_limit=100000,
    )
    assert result["status"] == "succeeded", (result, content)
    assert result["rowCount"] <= settings.kiwi_max_rows_per_tool


def test_a_source_range_refuses_a_text_line_number(client: TestClient, developer, targets) -> None:
    result, _ = lookup(
        client,
        developer,
        targets["development"]["id"],
        "schema.object_source_range",
        owner="HARNESS_APP",
        object_name="HARNESS_DEPT_HEADCOUNT",
        object_type="FUNCTION",
        start_line="3; DROP TABLE employees",
    )
    assert result["status"] == "invalid"


def test_plscope_reports_absence_rather_than_nothing(
    client: TestClient, developer, targets
) -> None:
    profile_id = targets["development"]["id"]
    result, content = lookup(
        client,
        developer,
        profile_id,
        "schema.plscope_identifiers",
        owner="HARNESS_APP",
        object_name="EMPLOYEE_REPORT",
        object_type="PACKAGE BODY",
    )
    assert result["status"] == "succeeded", (result, content)
    _, found = rows(content)
    assert [row["PLSCOPE_STATUS"] for row in found] == [
        "NOT COLLECTED: compiled with PLSCOPE_SETTINGS=IDENTIFIERS:NONE"
    ]
    assert {row["NAME"] for row in found} == {None}

    result, content = lookup(
        client,
        developer,
        profile_id,
        "schema.plscope_identifiers",
        owner="HARNESS_APP",
        object_name="HARNESS_DEPT_HEADCOUNT",
        object_type="FUNCTION",
    )
    assert result["status"] == "succeeded", (result, content)
    _, found = rows(content)
    assert {row["PLSCOPE_STATUS"] for row in found} == {"COLLECTED"}
    assert "L_COUNT" in content

    result, content = lookup(
        client,
        developer,
        profile_id,
        "schema.plscope_statements",
        owner="HARNESS_APP",
        object_name="HARNESS_DEPT_HEADCOUNT",
        object_type="FUNCTION",
    )
    assert result["status"] == "succeeded", (result, content)
    assert "SELECT COUNT(*) FROM EMPLOYEES" in content

    result, content = lookup(
        client,
        developer,
        profile_id,
        "schema.plscope_statements",
        owner="HARNESS_APP",
        object_name="EMPLOYEE_REPORT",
        object_type="PACKAGE BODY",
    )
    assert result["status"] == "succeeded", (result, content)
    _, found = rows(content)
    assert [row["PLSCOPE_STATUS"] for row in found] == [
        "NOT COLLECTED: compiled with PLSCOPE_SETTINGS=IDENTIFIERS:NONE"
    ]
    assert {row["SQL_TEXT"] for row in found} == {None}


def test_every_listed_etl_table_has_its_declared_columns_and_only_declared_keys(
    client: TestClient, developer, targets
) -> None:
    # Round-9 review (C15): the stand-in listed the ETL tables but seeded no columns,
    # so trial-9 KINJ-01's column lookups came back empty.
    profile_id = targets["development"]["id"]
    result, content = lookup(
        client,
        developer,
        profile_id,
        "schema.list_objects",
        owner=fx.OWNER,
        name_filter="ETL_ORDERS%",
    )
    assert result["status"] == "succeeded", (result, content)
    _, found = rows(content)
    listed = {row["OBJECT_NAME"] for row in found if row["OBJECT_TYPE"] == "TABLE"}
    assert listed == {table for table, _ in fx.TABLES}
    keys = dict(fx.primary_keys())
    assert keys == {"ETL_ORDERS_FACT": "ORDER_ID"}  # the only key the fixture declares
    for table, definitions in fx.TABLES:
        result, content = lookup(
            client,
            developer,
            profile_id,
            "schema.table_columns",
            owner=fx.OWNER,
            table_name=table,
        )
        assert result["status"] == "succeeded", (table, result, content)
        _, found = rows(content)
        assert [row["COLUMN_NAME"] for row in found] == [
            d.split()[0].upper() for d in definitions
        ], table
        assert all(row["DATA_TYPE"] for row in found), table
        result, content = lookup(
            client,
            developer,
            profile_id,
            "schema.table_constraints",
            owner=fx.OWNER,
            table_name=table,
        )
        assert result["status"] == "succeeded", (table, result, content)
        _, found = rows(content)
        expected = [("P", keys[table])] if table in keys else []
        assert [(r["CONSTRAINT_TYPE"], r["COLUMN_NAME"]) for r in found] == expected, table


@pytest.mark.stand_in_only
def test_the_invalid_employee_report_body_agrees_with_its_errors_and_dependencies(
    client: TestClient, developer, targets
) -> None:
    # Round-13 review (C16): the stand-in seeded a dependency on EMPLOYEES for a body
    # whose line 5 reads the unresolved EMPLOYEE, and trial-13 DIAG-01 tried to explain
    # it. Oracle's rows for this fixture are in 05_k7_codegen_checks.sql ('fixture:').
    profile_id = targets["development"]["id"]
    result, content = lookup(
        client,
        developer,
        profile_id,
        "schema.object_source_range",
        owner="HARNESS_APP",
        object_name="EMPLOYEE_REPORT",
        object_type="PACKAGE BODY",
        start_line=5,
        end_line=5,
    )
    assert result["status"] == "succeeded", (result, content)
    _, found = rows(content)
    assert "FROM employee WHERE" in found[0]["TEXT"]
    result, content = lookup(
        client,
        developer,
        profile_id,
        "schema.object_errors",
        owner="HARNESS_APP",
        object_name="EMPLOYEE_REPORT",
    )
    _, found = rows(content)
    # C18: as 19c records the exact source (05_k7_codegen_checks.sql, 9e4).
    assert [(r["LINE"], r["POSITION"], r["MESSAGE_NUMBER"]) for r in found] == [
        (5, 39, 0),
        (5, 5, 0),
    ]
    assert "ORA-00942" in found[0]["TEXT"]
    result, content = lookup(
        client,
        developer,
        profile_id,
        "schema.object_dependencies",
        owner="HARNESS_APP",
        object_name="EMPLOYEE_REPORT",
    )
    assert result["status"] == "succeeded", (result, content)
    _, found = rows(content)
    assert [(r["REFERENCED_OWNER"], r["REFERENCED_NAME"], r["REFERENCED_TYPE"]) for r in found] == [
        ("HARNESS_APP", "EMPLOYEE_REPORT", "PACKAGE"),
        ("PUBLIC", "DBMS_OUTPUT", "SYNONYM"),
    ]
    result, content = lookup(
        client,
        developer,
        profile_id,
        "schema.object_referenced_by",
        owner="HARNESS_APP",
        object_name="EMPLOYEES",
    )
    _, found = rows(content)
    names = {r["NAME"] for r in found}
    assert "V_ACTIVE_EMPLOYEES" in names
    assert "EMPLOYEE_REPORT" not in names


def test_etl_column_metadata_follows_the_declared_types() -> None:
    by_column = {(table, column): row for table, column, *row in fx.columns()}
    assert by_column[("ETL_ORDERS_SRC", "ORDER_ID")] == ["NUMBER", 22, None, None, "Y", 1]
    assert by_column[("ETL_ORDERS_SRC", "AMOUNT")] == ["NUMBER", 22, 12, 2, "Y", 3]
    assert by_column[("ETL_ORDERS_REJECTS", "REASON")] == ["VARCHAR2", 200, None, None, "Y", 3]
    assert by_column[("ETL_ORDERS_FACT", "ORDER_ID")][4] == "N"  # a primary key column
    assert by_column[("ETL_ORDERS_FACT", "LOADED_AT")] == ["TIMESTAMP(6)", 11, None, 6, "Y", 6]


@pytest.mark.stand_in_only
def test_db_links_name_the_link_but_never_its_credentials(
    client: TestClient, developer, targets
) -> None:
    result, content = lookup(
        client,
        developer,
        targets["development"]["id"],
        "schema.db_links_referenced",
        owner="HARNESS_APP",
    )
    assert result["status"] == "succeeded", (result, content)
    columns, found = rows(content)
    assert "USERNAME" not in columns and "HOST" not in columns
    assert "SALES_RO" not in content
    assert "reporting-db.internal" not in content
    by_name = {row["NAME"]: row for row in found}
    assert by_name["HARNESS_REMOTE_ORDERS"]["LINK_OWNER"] == "PUBLIC"
    assert by_name["HARNESS_ARCHIVE_ORDERS"]["LINK_OWNER"] == "NOT VISIBLE"


@pytest.mark.stand_in_only
def test_instructions_in_a_run_log_stay_inside_the_markers(
    client: TestClient, developer, targets
) -> None:
    result, content = lookup(
        client,
        developer,
        targets["development"]["id"],
        "dba.scheduler_run_history",
        owner="HARNESS_APP",
        job_name="EXPORT_ORDERS",
    )
    assert result["status"] == "succeeded", (result, content)
    lines = content.splitlines()
    assert lines[0] == "----- BEGIN UNTRUSTED TOOL_RESULT dba.scheduler_run_history -----"
    # The data ends at the END marker; only the harness's own lines follow it: the
    # count, how much of the result came back (Q3), and the lookups used.
    assert lines[-4] == "----- END UNTRUSTED TOOL_RESULT dba.scheduler_run_history -----"
    assert lines[-3] == "[harness] This is lookup 1."
    assert lines[-2].startswith("[harness] Coverage: complete.")
    assert lines[-1].startswith("[harness] Lookups used: 1 of ")
    assert "dba.kill_session" in lines[1]
    # The lookup made no other call because of it.
    assert result["rowCount"] >= 1


@pytest.mark.stand_in_only
def test_both_scheduler_views_agree_on_each_job(client: TestClient, developer, targets) -> None:
    """DBA_ and ALL_SCHEDULER_JOBS describe the same jobs: their shared fields agree.

    They were seeded separately once and gave EXPORT_ORDERS two failure counts, 3 and 2
    (round-17 review, C20). The run history is not compared: it holds the runs it
    retains, so a counter larger than its rows is not a disagreement.
    """

    profile_id = targets["development"]["id"]
    result, content = lookup(client, developer, profile_id, "dba.scheduler_jobs", row_limit=200)
    assert result["status"] == "succeeded", result
    _, jobs = rows(content)
    assert {job["JOB_NAME"] for job in jobs} >= {"EXPORT_ORDERS", "NIGHTLY_STATS", fx.JOB}
    shared = ("ENABLED", "STATE", "LAST_START_UTC", "NEXT_RUN_UTC", "FAILURE_COUNT")
    for job in jobs:
        result, content = lookup(
            client,
            developer,
            profile_id,
            "dba.scheduler_job_detail",
            owner=job["OWNER"],
            job_name=job["JOB_NAME"],
        )
        assert result["status"] == "succeeded", result
        _, detail = rows(content)
        assert len(detail) == 1, (job["JOB_NAME"], content)
        assert {f: detail[0][f] for f in shared} == {f: job[f] for f in shared}, job["JOB_NAME"]
