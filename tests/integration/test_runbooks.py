"""Controlled runbooks: preview, confirmation, and verification evidence."""

from __future__ import annotations

from fastapi.testclient import TestClient

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


def test_recompile_records_verification_evidence(client: TestClient, dba, targets) -> None:
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
    verification = body["verification"]
    assert verification["operationId"] == "schema.object_status"
    assert verification["observed"] is True
    assert verification["collectedAt"]
    # The seeded body is genuinely broken, so recompiling it leaves it INVALID and
    # the evidence says so rather than reporting a clean success.
    statuses = {row[3] for row in verification["rows"]}
    assert "INVALID" in statuses


def test_gather_statistics_verification_shows_the_recorded_values(
    client: TestClient, dba, targets
) -> None:
    profile_id = targets["development"]["id"]
    before = client.get(
        f"/api/v1/targets/{profile_id}/objects/detail",
        headers=dba,
        params={"owner": "HARNESS_APP", "objectName": "ORDER_LINES", "objectType": "TABLE"},
    ).json()["panels"]["statistics"]["rows"]
    assert before[0][2] is None, "the fixture starts with no statistics"

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
    rows = body["verification"]["rows"]
    assert rows[0][2] == 4000
    assert rows[0][3], "last_analyzed is recorded"


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
