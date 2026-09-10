"""The tuning workbench: estimates stay labelled as estimates."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

SLOW_QUERY = (
    "SELECT order_id, SUM(quantity * unit_price) FROM order_lines "
    "WHERE product_id = 3 GROUP BY order_id"
)


def test_explain_returns_an_estimated_plan_and_says_so(
    client: TestClient, developer, targets
) -> None:
    response = client.post(
        "/api/v1/tuning/explain",
        headers=developer,
        json={"profileId": targets["development"]["id"], "statement": SLOW_QUERY},
    )
    body = response.json()
    assert response.status_code == 200, body
    assert body["kind"] == "estimated"
    assert body["rows"], body
    operations = {row[3] for row in body["rows"]}
    assert "TABLE ACCESS" in operations
    assert "not measurements" in body["note"]


def test_explain_reads_the_plan_back_on_the_connection_that_wrote_it(
    client: TestClient, developer, targets, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Oracle the plan is only visible to the session that produced it.

    PLAN_TABLE is a synonym for a global temporary table, so every session sees only
    its own rows. The stand-in backend keeps one shared table and cannot show that,
    so what is asserted here is the property that makes the read work on Oracle: the
    EXPLAIN and the read of PLAN_TABLE run on the same connection.
    """

    backend = client.app.state.harness.execution._engine._backend
    original_connect = backend.connect
    statements: list[tuple[int, str]] = []
    handles = iter(range(1, 1000))

    def recording_connect(spec: Any) -> Any:
        connection = original_connect(spec)
        handle = next(handles)
        inner = connection.execute

        def execute(statement: str, *args: Any, **kwargs: Any) -> Any:
            statements.append((handle, statement))
            return inner(statement, *args, **kwargs)

        connection.execute = execute  # type: ignore[method-assign]
        return connection

    monkeypatch.setattr(backend, "connect", recording_connect)

    response = client.post(
        "/api/v1/tuning/explain",
        headers=developer,
        json={"profileId": targets["development"]["id"], "statement": SLOW_QUERY},
    )
    body = response.json()
    assert response.status_code == 200, body
    assert body["rows"], body

    wrote = [handle for handle, sql in statements if sql.upper().lstrip().startswith("EXPLAIN")]
    read = [handle for handle, sql in statements if "from plan_table" in sql.lower()]
    assert len(wrote) == 1, statements
    assert len(read) == 1, statements
    assert wrote == read, "the plan was read on a different connection than the one that wrote it"


def test_explain_refuses_statements_that_are_not_queries_or_dml(
    client: TestClient, developer, targets
) -> None:
    response = client.post(
        "/api/v1/tuning/explain",
        headers=developer,
        json={
            "profileId": targets["development"]["id"],
            "statement": "BEGIN NULL; END;",
        },
    )
    assert response.status_code == 400


def test_cursor_search_and_detail_are_labelled_as_measured(
    client: TestClient, developer, targets
) -> None:
    profile_id = targets["development"]["id"]
    search = client.get(
        f"/api/v1/tuning/{profile_id}/cursors",
        headers=developer,
        params={"textFilter": "order_lines"},
    ).json()
    assert search["kind"] == "measured"
    assert search["rows"], search
    sql_id = search["rows"][0][0]

    detail = client.get(f"/api/v1/tuning/{profile_id}/cursors/{sql_id}", headers=developer).json()
    assert detail["statistics"]["kind"] == "measured"
    assert detail["plan"]["available"] is True
    assert detail["plan"]["rowSourceStatistics"] is False
    assert "not collected by default" in detail["plan"]["note"]


def test_a_missing_display_cursor_capability_reports_instead_of_pretending(
    client: TestClient, developer, targets
) -> None:
    from harness_api.models import ConnectionProfile

    profile_id = targets["development"]["id"]
    state = client.app.state.harness
    with state.session_factory() as db:
        profile = db.get(ConnectionProfile, profile_id)
        row = next(c for c in profile.capabilities if c.capability == "display_cursor")
        row.available = False
        row.detail = "ORA-01031: insufficient privileges (V$SQL_PLAN)"
        db.commit()

    detail = client.get(
        f"/api/v1/tuning/{profile_id}/cursors/c1n5xa9k4h2rd", headers=developer
    ).json()
    assert detail["plan"]["available"] is False
    assert detail["plan"]["error"]["code"] == "capability_unavailable"
    assert detail["statistics"]["rows"], "statistics still work without V$SQL_PLAN"


def test_comparison_refuses_to_conclude_without_measured_values(
    client: TestClient, developer, targets
) -> None:
    profile_id = targets["development"]["id"]
    before = client.post(
        "/api/v1/tuning/observations",
        headers=developer,
        json={
            "profileId": profile_id,
            "label": "before",
            "sqlId": "c1n5xa9k4h2rd",
            "estimated": {"cost": 806},
            "context": {"dataSetLabel": "fixture-v1"},
        },
    ).json()
    after = client.post(
        "/api/v1/tuning/observations",
        headers=developer,
        json={
            "profileId": profile_id,
            "label": "after",
            "sqlId": "c1n5xa9k4h2rd",
            "estimated": {"cost": 4},
            "context": {"dataSetLabel": "fixture-v1"},
        },
    ).json()

    comparison = client.get(
        "/api/v1/tuning/observations/compare",
        headers=developer,
        params={"beforeId": before["id"], "afterId": after["id"]},
    ).json()
    assert comparison["conclusionPermitted"] is False
    assert any("no measured values" in c for c in comparison["caveats"])
    assert comparison["measuredDeltas"] == {}


def test_comparison_of_measured_values_reports_a_delta(
    client: TestClient, developer, targets
) -> None:
    profile_id = targets["development"]["id"]

    def observe(label: str, elapsed: int) -> str:
        return client.post(
            "/api/v1/tuning/observations",
            headers=developer,
            json={
                "profileId": profile_id,
                "label": label,
                "sqlId": "c1n5xa9k4h2rd",
                "measured": {"elapsedMs": elapsed, "bufferGets": elapsed * 10},
                "context": {"dataSetLabel": "fixture-v1"},
            },
        ).json()["id"]

    comparison = client.get(
        "/api/v1/tuning/observations/compare",
        headers=developer,
        params={"beforeId": observe("before", 880), "afterId": observe("after", 120)},
    ).json()
    assert comparison["conclusionPermitted"] is True
    assert comparison["measuredDeltas"]["elapsedMs"]["delta"] == -760
    assert comparison["measuredDeltas"]["elapsedMs"]["kind"] == "measured"


def test_comparing_different_targets_is_flagged(client: TestClient, developer, targets) -> None:
    def observe(profile_id: str) -> str:
        return client.post(
            "/api/v1/tuning/observations",
            headers=developer,
            json={
                "profileId": profile_id,
                "label": "obs",
                "sqlId": "c1n5xa9k4h2rd",
                "measured": {"elapsedMs": 10},
                "context": {"dataSetLabel": "fixture-v1"},
            },
        ).json()["id"]

    comparison = client.get(
        "/api/v1/tuning/observations/compare",
        headers=developer,
        params={
            "beforeId": observe(targets["development"]["id"]),
            "afterId": observe(targets["test"]["id"]),
        },
    ).json()
    assert comparison["conclusionPermitted"] is False
    assert any("different targets" in c for c in comparison["caveats"])


def test_another_user_cannot_read_your_observations(
    client: TestClient, developer, second_developer, targets
) -> None:
    created = client.post(
        "/api/v1/tuning/observations",
        headers=developer,
        json={
            "profileId": targets["development"]["id"],
            "label": "mine",
            "measured": {"elapsedMs": 1},
        },
    ).json()
    assert (
        client.get("/api/v1/tuning/observations", headers=second_developer).json()["observations"]
        == []
    )
    response = client.get(
        "/api/v1/tuning/observations/compare",
        headers=second_developer,
        params={"beforeId": created["id"], "afterId": created["id"]},
    )
    assert response.status_code == 404


def test_a_plan_query_that_failed_is_not_reported_as_an_available_empty_plan(
    client: TestClient, developer, targets, monkeypatch
) -> None:
    """No rows because the query failed is not the same as no rows in the plan.

    A privilege that is missing at runtime rather than declared missing up front
    comes back as a failed execution, not as a raised refusal. Presented as an
    available plan with no rows, it reads as a finding about the cursor.
    """

    from harness_worker.backend.fake import FakeOracleConnection
    from harness_worker.errors import OracleError

    original = FakeOracleConnection.execute

    def execute(self, statement, binds, kind, limits, **kwargs):
        if "v$sql_plan" in statement.lower():
            raise OracleError("ORA-01031: insufficient privileges", oracle_code="ORA-01031")
        return original(self, statement, binds, kind, limits, **kwargs)

    monkeypatch.setattr(FakeOracleConnection, "execute", execute)

    profile_id = targets["development"]["id"]
    detail = client.get(
        f"/api/v1/tuning/{profile_id}/cursors/c1n5xa9k4h2rd", headers=developer
    ).json()

    assert detail["plan"]["available"] is False
    assert detail["plan"]["error"]["oracleCode"] == "ORA-01031"
    assert "rows" not in detail["plan"]
    # The half that did work is still reported.
    assert detail["statistics"]["rows"], detail


def test_a_cursor_search_that_failed_is_not_reported_as_no_cursors(
    client: TestClient, developer, targets, monkeypatch
) -> None:
    """An empty grid means Oracle has nothing cached. A failure means we do not know.

    Presenting the rows a failed query did not return as measured data turns a lost
    connection or an exhausted deadline into a finding about the target.
    """

    from harness_worker.backend.fake import FakeOracleConnection
    from harness_worker.errors import OracleError

    original = FakeOracleConnection.execute

    def execute(self, statement, binds, kind, limits, **kwargs):
        if "sql_text_preview" in statement.lower():
            raise OracleError("ORA-00942: table or view does not exist", oracle_code="ORA-00942")
        return original(self, statement, binds, kind, limits, **kwargs)

    monkeypatch.setattr(FakeOracleConnection, "execute", execute)

    profile_id = targets["development"]["id"]
    search = client.get(
        f"/api/v1/tuning/{profile_id}/cursors",
        headers=developer,
        params={"textFilter": "order_lines"},
    ).json()

    assert search["available"] is False
    assert search["error"]["oracleCode"] == "ORA-00942"
    assert "rows" not in search


def test_cursor_statistics_that_failed_are_not_reported_as_measured(
    client: TestClient, developer, targets, monkeypatch
) -> None:
    """The half that failed says so; the half that worked is still reported."""

    from harness_worker.backend.fake import FakeOracleConnection
    from harness_worker.errors import OracleError

    original = FakeOracleConnection.execute

    def execute(self, statement, binds, kind, limits, **kwargs):
        if "avg_elapsed_ms" in statement.lower():
            raise OracleError("ORA-00942: table or view does not exist", oracle_code="ORA-00942")
        return original(self, statement, binds, kind, limits, **kwargs)

    monkeypatch.setattr(FakeOracleConnection, "execute", execute)

    profile_id = targets["development"]["id"]
    detail = client.get(
        f"/api/v1/tuning/{profile_id}/cursors/c1n5xa9k4h2rd", headers=developer
    ).json()

    assert detail["statistics"]["available"] is False
    assert detail["statistics"]["error"]["oracleCode"] == "ORA-00942"
    assert "rows" not in detail["statistics"]
    assert detail["plan"]["available"] is True, detail["plan"]
