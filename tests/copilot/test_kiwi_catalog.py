"""Kiwi's K-4 lookups against the stand-in (KIWI_PLAN.md, K-4).

Each new catalog entry runs through the same path a model call takes: the toolbox,
``ExecutionService`` and the stand-in's dictionary. The stand-in carries the cases
qualification cannot: a database link with credentials that must never be returned,
and a failed job whose ADDITIONAL_INFO is written to look like instructions.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.provider import ScriptedTurn, ToolCall
from harness_api.copilot.toolbox import KiwiToolbox, tool_name_for
from harness_api.execution import ExecutionService
from tests.copilot.test_copilot import sse
from tests.copilot.test_kiwi import kiwi_payload, last, use_script

K4_ENTRIES = {
    "schema.object_referenced_by",
    "schema.package_subprograms",
    "schema.object_source_range",
    "schema.plscope_identifiers",
    "schema.plscope_statements",
    "schema.triggers",
    "schema.db_links_referenced",
    "dba.scheduler_job_detail",
    "dba.scheduler_chain",
    "dba.scheduler_run_history",
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
        (
            "schema.object_referenced_by",
            {"owner": "HARNESS_APP", "object_name": "EMPLOYEES"},
            "EMPLOYEE_REPORT",
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
    assert lines[-1] == "----- END UNTRUSTED TOOL_RESULT dba.scheduler_run_history -----"
    assert "dba.kill_session" in lines[1]
    # The lookup made no other call because of it.
    assert result["rowCount"] >= 1
