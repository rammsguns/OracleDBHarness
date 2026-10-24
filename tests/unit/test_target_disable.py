"""Target policy checks happen before connections and cached reads."""

from unittest.mock import Mock

import pytest
from sqlalchemy import select

from harness_api.models import AuditEvent
from tests.conftest import audit_events, open_worksheet


@pytest.mark.parametrize("permission,allowed", [([], False), (["read"], True)])
def test_connection_test_requires_read(
    client, administrator, developer, targets, monkeypatch, permission, allowed
):
    pid = targets["development"]["id"]
    assert (
        client.post(
            "/api/v1/admin/grants",
            headers=administrator,
            json={
                "subject": "dev@example.internal",
                "profileId": pid,
                "permissions": permission,
            },
        ).status_code
        == 201
    )
    backend = client.app.state.harness.execution._backend
    connect = Mock(wraps=backend.connect)
    monkeypatch.setattr(backend, "connect", connect)
    before = len(audit_events(client, "target.test"))
    response = client.post(f"/api/v1/targets/{pid}/test", headers=developer)
    if allowed:
        assert response.status_code == 200, response.text
        assert response.json()["connected"]
        connect.assert_called_once()
    else:
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "not_authorized"
        connect.assert_not_called()
        events = audit_events(client, "target.test")
        assert len(events) == before + 1
        assert events[0].actor_subject == "dev@example.internal"
        assert events[0].outcome == "refused"


ROUTES = [
    ("GET", "/targets/{pid}", None),
    ("POST", "/targets/{pid}/test", None),
    ("GET", "/targets/{pid}/schemas", None),
    ("GET", "/targets/{pid}/objects?owner=HARNESS_APP", None),
    (
        "GET",
        "/targets/{pid}/objects/detail?owner=HARNESS_APP&objectName=EMPLOYEES&objectType=TABLE",
        None,
    ),
    ("GET", "/targets/{pid}/dba/overview", None),
    ("GET", "/tuning/{pid}/cursors", None),
    ("GET", "/tuning/{pid}/cursors/sample", None),
    ("POST", "/tuning/explain", {"statement": "select 1 from dual"}),
    ("POST", "/tuning/observations", {"label": "disabled"}),
    ("POST", "/worksheets", {}),
    ("POST", "/plsql/compile", {"source": "CREATE OR REPLACE PROCEDURE p AS BEGIN NULL; END;"}),
    ("POST", "/scripts", {"name": "disabled", "body": "select 1 from dual"}),
    ("POST", "/runbooks/runbook.health_report/preview", {}),
    ("POST", "/runbooks/runbook.health_report/run", {}),
    ("GET", "/worksheets/{sid}", None),
    ("POST", "/worksheets/{sid}/execute", {"statement": "select 1 from dual"}),
    ("POST", "/worksheets/{sid}/commit", None),
    ("POST", "/worksheets/{sid}/rollback", None),
    ("POST", "/worksheets/{sid}/cancel", None),
    ("DELETE", "/worksheets/{sid}", None),
]


@pytest.mark.parametrize("as_admin", [False, True], ids=["developer", "administrator"])
@pytest.mark.parametrize("method,path,payload", ROUTES)
def test_disabled_routes_refuse_once_without_connect(
    client, administrator, developer, targets, monkeypatch, method, path, payload, as_admin
):
    pid = targets["development"]["id"]
    headers = administrator if as_admin else developer
    if as_admin:
        assert (
            client.post(
                "/api/v1/admin/grants",
                headers=administrator,
                json={
                    "subject": "admin@example.internal",
                    "profileId": pid,
                    "permissions": ["read", "worksheet", "compile", "runbook"],
                },
            ).status_code
            == 201
        )
    sid = open_worksheet(client, headers, pid)
    response = client.post(
        f"/api/v1/admin/targets/{pid}/disable",
        headers=administrator,
        json={"reason": "failed G3 gate"},
    )
    assert response.status_code == 200, response.text
    connect = Mock(side_effect=AssertionError("disabled target connected"))
    monkeypatch.setattr(client.app.state.harness.execution._backend, "connect", connect)
    with client.app.state.harness.session_factory() as db:
        previous = set(db.scalars(select(AuditEvent.id)))
    response = client.request(
        method,
        "/api/v1" + path.format(pid=pid, sid=sid),
        headers=headers,
        json=(payload if "{sid}" in path else {**payload, "profileId": pid})
        if payload is not None
        else None,
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "target_disabled"
    connect.assert_not_called()
    with client.app.state.harness.session_factory() as db:
        new = list(db.scalars(select(AuditEvent).where(AuditEvent.id.not_in(previous))))
    assert len(new) == 1
    assert new[0].profile_id == pid and new[0].outcome == "refused"
    assert new[0].actor_subject == (
        "admin@example.internal" if as_admin else "dev@example.internal"
    )


def test_toggle_audits_invalidates_cache_and_resumes(
    client, administrator, developer, targets, monkeypatch
):
    pid = targets["development"]["id"]
    state = client.app.state.harness
    invalidate = Mock(wraps=state.schema_panel_cache.invalidate)
    monkeypatch.setattr(state.schema_panel_cache, "invalidate", invalidate)
    sid = open_worksheet(client, developer, pid)
    for action, enabled in [("disable", False), ("enable", True)]:
        response = client.post(
            f"/api/v1/admin/targets/{pid}/{action}",
            headers=administrator,
            json={"reason": f"gate {action}"},
        )
        assert response.status_code == 200, response.text
        invalidate.assert_called_with(pid)
        target = next(
            t for t in client.get("/api/v1/targets", headers=developer).json() if t["id"] == pid
        )
        assert target["enabled"] is enabled
        (event,) = audit_events(client, f"admin.target.{action}")
        assert event.actor_subject == "admin@example.internal" and event.actor_id
        assert event.detail == {"enabled": enabled, "reason": f"gate {action}"}
        if not enabled:
            assert (
                client.post(f"/api/v1/worksheets/{sid}/commit", headers=developer).status_code
                == 403
            )
    assert invalidate.call_count == 2
    assert client.post(f"/api/v1/targets/{pid}/test", headers=developer).json()["connected"]
    assert (
        client.post(
            f"/api/v1/worksheets/{sid}/execute",
            headers=developer,
            json={"statement": "select 1 from dual"},
        ).status_code
        == 200
    )


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_toggle_requires_admin_and_reason(client, administrator, developer, targets, reason):
    path = f"/api/v1/admin/targets/{targets['development']['id']}/disable"
    payload = {} if reason is None else {"reason": reason}
    assert client.post(path, headers=developer, json={"reason": "gate"}).status_code == 403
    assert client.post(path, headers=administrator, json=payload).status_code in (400, 422)


def test_first_production_probe_still_requires_and_accepts_read(client, dba, targets):
    from harness_api.models import ConnectionProfile

    pid = targets["production"]["id"]
    with client.app.state.harness.session_factory() as db:
        profile = db.get(ConnectionProfile, pid)
        profile.identity_json = None
        profile.identity_checked_at = None
        db.commit()
    response = client.post(f"/api/v1/targets/{pid}/test", headers=dba)
    assert response.status_code == 200, response.text
    assert response.json()["connected"] is True


def test_toggle_drops_warm_schema_cache(client, administrator, developer, targets, monkeypatch):
    from harness_api.schemas import PanelResult

    pid = targets["development"]["id"]
    cache = client.app.state.harness.schema_panel_cache
    panel = PanelResult(
        operationId="schema.list_schemas",
        title="Schemas",
        available=True,
        collectedAt="before-toggle",
    )
    for action in ("disable", "enable"):
        key = (pid, "warm")
        _, generation = cache.get(key)
        cache.put(key, panel, "original", 60, generation)
        assert cache.get(key)[0] is not None
        response = client.post(
            f"/api/v1/admin/targets/{pid}/{action}",
            headers=administrator,
            json={"reason": "cache invalidation"},
        )
        assert response.status_code == 200
        assert cache.get(key)[0] is None
        # Invalidation must also fence an in-flight fill from before the toggle.
        cache.put(key, panel, "late", 60, generation)
        assert cache.get(key)[0] is None
