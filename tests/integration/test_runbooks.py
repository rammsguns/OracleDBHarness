"""Controlled runbooks: preview, confirmation, and verification evidence."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_plsql import REPAIRED_BODY

RECOMPILE_PARAMS = {
    "owner": "HARNESS_APP",
    "object_name": "EMPLOYEE_REPORT",
    "object_kind": "PACKAGE BODY",
}


def test_the_catalog_lists_three_runbooks_with_their_risk(client: TestClient, dba, targets) -> None:
    body = client.get("/api/v1/runbooks", headers=dba).json()
    ids = {r["id"] for r in body["runbooks"]}
    assert ids == {
        "runbook.health_report",
        "runbook.recompile_object",
        "runbook.gather_table_stats",
    }
    mutating = {r["id"] for r in body["runbooks"] if r["mutating"]}
    assert mutating == {"runbook.recompile_object", "runbook.gather_table_stats"}
    for runbook in body["runbooks"]:
        assert runbook["requiresConfirmation"] == runbook["mutating"]


def test_health_report_collects_every_panel_with_its_time(client: TestClient, dba, targets) -> None:
    body = client.post(
        "/api/v1/runbooks/runbook.health_report/run",
        headers=dba,
        json={"profileId": targets["development"]["id"]},
    ).json()
    assert body["outcome"] == "succeeded"
    assert len(body["steps"]) == 5
    for step in body["steps"]:
        assert step["collectedAt"]
        assert step["available"] is True


def test_health_report_reports_degradation_rather_than_hiding_it(
    client: TestClient, dba, targets
) -> None:
    from harness_api.models import ConnectionProfile

    state = client.app.state.harness
    with state.session_factory() as db:
        profile = db.get(ConnectionProfile, targets["development"]["id"])
        for row in profile.capabilities:
            if row.capability == "dba_scheduler_jobs":
                row.available = False
                row.detail = "ORA-00942 (DBA_SCHEDULER_JOBS)"
        db.commit()

    body = client.post(
        "/api/v1/runbooks/runbook.health_report/run",
        headers=dba,
        json={"profileId": targets["development"]["id"]},
    ).json()
    assert body["outcome"] == "degraded"
    failed = [s for s in body["steps"] if not s["available"]]
    assert [s["operationId"] for s in failed] == ["dba.scheduler_failures"]
    assert failed[0]["error"]["code"] == "capability_unavailable"


def test_preview_shows_the_exact_target_and_parameters(client: TestClient, dba, targets) -> None:
    body = client.post(
        "/api/v1/runbooks/runbook.recompile_object/preview",
        headers=dba,
        json={"profileId": targets["development"]["id"], "parameters": RECOMPILE_PARAMS},
    ).json()
    assert body["ready"] is True
    assert body["willChangeDatabase"] is True
    assert body["requiresConfirmation"] is True
    assert body["parameters"] == RECOMPILE_PARAMS
    assert body["target"]["name"] == "development"


def test_preview_names_missing_parameters(client: TestClient, dba, targets) -> None:
    body = client.post(
        "/api/v1/runbooks/runbook.recompile_object/preview",
        headers=dba,
        json={"profileId": targets["development"]["id"], "parameters": {"owner": "HARNESS_APP"}},
    ).json()
    assert body["ready"] is False
    assert set(body["missingParameters"]) == {"object_name", "object_kind"}


def test_a_mutating_runbook_needs_an_explicit_confirmation(
    client: TestClient, dba, targets
) -> None:
    response = client.post(
        "/api/v1/runbooks/runbook.recompile_object/run",
        headers=dba,
        json={"profileId": targets["development"]["id"], "parameters": RECOMPILE_PARAMS},
    )
    assert response.status_code == 400
    assert response.json()["error"]["detail"]["requiresConfirmation"] is True


def test_a_recompile_that_leaves_the_object_invalid_is_not_verified(
    client: TestClient, dba, targets
) -> None:
    """The statement succeeding is not the same as the object being usable.

    ALTER PACKAGE ... COMPILE over broken source is a statement Oracle accepts; the
    body stays INVALID. The evidence has always shown that. What the runbook now does
    is read it: the run is reported as unverified rather than succeeded.
    """

    body = client.post(
        "/api/v1/runbooks/runbook.recompile_object/run",
        headers=dba,
        json={
            "profileId": targets["development"]["id"],
            "parameters": RECOMPILE_PARAMS,
            "confirm": True,
        },
    ).json()
    assert body["steps"][0]["state"] == "succeeded"
    assert body["outcome"] == "unverified"

    verification = body["verification"]
    assert verification["operationId"] == "schema.object_status"
    assert verification["collectedAt"]
    # The query answered, so the evidence was observed; what it says falls short.
    assert verification["observed"] is True
    assert verification["verified"] is False
    assert verification["requirement"] == (
        "The named object is VALID in the dictionary after the recompile."
    )
    assert "HARNESS_APP.EMPLOYEE_REPORT is INVALID" in verification["note"]
    statuses = {row[3] for row in verification["rows"]}
    assert "INVALID" in statuses


@pytest.mark.usefixtures("restore_seeded_package")
def test_a_recompile_that_makes_the_object_valid_is_verified(
    client: TestClient, developer, dba, targets
) -> None:
    """The other side of the same check: a real repair reports a verified success."""

    profile_id = targets["development"]["id"]
    compiled = client.post(
        "/api/v1/plsql/compile",
        headers=developer,
        json={"profileId": profile_id, "source": REPAIRED_BODY},
    ).json()
    assert compiled["compiled"] is True, compiled

    body = client.post(
        "/api/v1/runbooks/runbook.recompile_object/run",
        headers=dba,
        json={"profileId": profile_id, "parameters": RECOMPILE_PARAMS, "confirm": True},
    ).json()
    assert body["outcome"] == "succeeded"
    assert body["verification"]["verified"] is True
    assert "note" not in body["verification"]
    assert {row[3] for row in body["verification"]["rows"]} == {"VALID"}


def test_gather_statistics_verification_shows_the_recorded_values(
    client: TestClient, dba, targets
) -> None:
    profile_id = targets["development"]["id"]
    before = client.get(
        f"/api/v1/targets/{profile_id}/objects/detail",
        headers=dba,
        params={"owner": "HARNESS_APP", "objectName": "ORDER_LINES", "objectType": "TABLE"},
    ).json()["panels"]["statistics"]["rows"]
    # The stand-in seeds no statistics. The Oracle fixture gathers them on purpose,
    # for the tuning checks, and 12c onwards also gathers them during a bulk load.
    # Either way the runbook has to leave a fresh, recorded result behind.
    analyzed_before = before[0][3]

    body = client.post(
        "/api/v1/runbooks/runbook.gather_table_stats/run",
        headers=dba,
        json={
            "profileId": profile_id,
            "parameters": {"owner": "HARNESS_APP", "table_name": "ORDER_LINES"},
            "confirm": True,
        },
    ).json()
    assert body["outcome"] == "succeeded"
    assert body["verification"]["verified"] is True
    assert body["verification"]["requirement"] == (
        "The named table has a recorded row count and collection time after the gather."
    )
    rows = body["verification"]["rows"]
    # 4,000 rows in the stand-in, 400,000 in the Oracle fixture.
    assert rows[0][2] in (4000, 400000)
    assert rows[0][3], "last_analyzed is recorded"
    if analyzed_before is not None:
        assert str(rows[0][3]) >= str(analyzed_before), "the gather left an older result"


def test_identifier_parameters_are_validated_not_interpolated(
    client: TestClient, dba, targets
) -> None:
    response = client.post(
        "/api/v1/runbooks/runbook.recompile_object/run",
        headers=dba,
        json={
            "profileId": targets["development"]["id"],
            "parameters": {
                "owner": "HARNESS_APP",
                "object_name": 'EMPLOYEE_REPORT" COMPILE; DROP TABLE employees --',
                "object_kind": "PACKAGE BODY",
            },
            "confirm": True,
        },
    )
    assert response.status_code == 400
    assert "double quote" in response.json()["error"]["message"]


def test_object_kind_comes_from_an_allowlist(client: TestClient, dba, targets) -> None:
    response = client.post(
        "/api/v1/runbooks/runbook.recompile_object/run",
        headers=dba,
        json={
            "profileId": targets["development"]["id"],
            "parameters": {**RECOMPILE_PARAMS, "object_kind": "DATABASE"},
            "confirm": True,
        },
    )
    assert response.status_code == 400
    assert "object_kind must be one of" in response.json()["error"]["message"]


def test_a_developer_cannot_run_a_mutating_runbook(client: TestClient, developer, targets) -> None:
    response = client.post(
        "/api/v1/runbooks/runbook.recompile_object/run",
        headers=developer,
        json={
            "profileId": targets["development"]["id"],
            "parameters": RECOMPILE_PARAMS,
            "confirm": True,
        },
    )
    assert response.status_code == 403


def test_a_panel_that_failed_without_raising_still_degrades_the_report(
    client: TestClient, dba, targets, monkeypatch
) -> None:
    """A diagnostic can come back as a settled failure rather than an exception.

    The engine turns a timeout, a cancellation or a driver error into an outcome
    whose state is not ``succeeded`` and returns it; only the errors raised before a
    statement runs arrive as exceptions. The report has to treat both the same way,
    or a panel reads as unavailable while the report says the collection succeeded.
    """

    from harness_worker.types import ExecutionState

    harness = client.app.state.harness  # type: ignore[attr-defined]
    collected = harness.execution.run_catalog_operation

    def settle_blocking_as_failed(*args, **kwargs):
        result = collected(*args, **kwargs)
        if result.entry.operation_id == "dba.blocking":
            result.outcome.state = ExecutionState.FAILED
            result.outcome.result_set = None
            result.outcome.error = {
                "code": "statement_timeout",
                "message": "The statement exceeded its execution budget.",
            }
        return result

    monkeypatch.setattr(harness.execution, "run_catalog_operation", settle_blocking_as_failed)

    body = client.post(
        "/api/v1/runbooks/runbook.health_report/run",
        headers=dba,
        json={"profileId": targets["development"]["id"]},
    ).json()

    assert body["outcome"] == "degraded"
    failed = [s for s in body["steps"] if not s["available"]]
    assert [s["operationId"] for s in failed] == ["dba.blocking"]
    assert failed[0]["error"]["code"] == "statement_timeout"
    assert failed[0]["state"] == "failed"
