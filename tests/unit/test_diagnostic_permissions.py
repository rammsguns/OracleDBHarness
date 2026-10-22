"""Missing diagnostic grants stay explicit through panels and Kiwi lookups."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from harness_api.copilot.provider import ToolCall
from harness_api.copilot.toolbox import KiwiToolbox, tool_name_for
from harness_api.models import AppRole, ConnectionProfile, Execution, User, UserTargetGrant
from harness_api.routers.targets import _panel
from harness_api.security import Principal
from harness_worker.backend.fake import FakeOracleConnection
from harness_worker.catalog import load_catalog
from harness_worker.errors import OracleError
from harness_worker.types import RiskClass

CATALOG = load_catalog(Path(__file__).resolve().parents[2] / "oracle")
PANELS = [entry for entry in CATALOG.list() if entry.risk == RiskClass.READ]


@pytest.fixture(scope="session")
def oracle_target():
    return None


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"oracle_backend": "fake", "kiwi_enabled": True})


def parameters(entry):
    values = {
        "source_filter": "%permission%",
        "owner": "HARNESS_APP",
        "object_name": "DEMO_PKG",
        "object_type": "PACKAGE",
        "table_name": "DEMO_ITEMS",
        "package_name": "DEMO_PKG",
        "job_name": "DEMO_JOB",
        "chain_name": "DEMO_CHAIN",
        "program_name": "DEMO_PROGRAM",
        "sql_id": "sample",
        "statement_id": "permission_probe",
        "child_number": 0,
        "row_limit": 10,
        "row_offset": 0,
        "start_line": 1,
        "end_line": 10,
    }
    assert set(entry.required_parameters) <= values.keys()
    return {name: values[name] for name in entry.parameters if name in values}


@pytest.mark.parametrize("code", ["ORA-00942", "ORA-01031"])
@pytest.mark.parametrize("entry", PANELS, ids=lambda entry: entry.operation_id)
def test_missing_grant_is_unavailable_for_every_catalog_panel(
    client, targets, monkeypatch, code, entry
):
    """Inject after capability probing: cached grants cannot hide runtime revocation."""
    execute = Mock(
        side_effect=OracleError("Diagnostic view or grant unavailable", oracle_code=code)
    )
    monkeypatch.setattr(FakeOracleConnection, "execute", execute)
    state = client.app.state.harness
    with state.session_factory() as db:
        user = db.scalar(select(User).where(User.subject == "dba@example.internal"))
        actor = Principal(subject=user.subject, user_id=user.id, roles={AppRole.DBA})
        profile = db.get(ConnectionProfile, targets["development"]["id"])
        grant = db.scalar(
            select(UserTargetGrant).where(
                UserTargetGrant.user_id == user.id,
                UserTargetGrant.profile_id == profile.id,
            )
        )
        panel = _panel(state, db, actor, profile, grant, entry.operation_id, parameters(entry))
        execute.assert_called_once()
        assert panel.available is False
        assert panel.rows == []
        assert panel.error["oracleCode"] == code
        assert panel.error["code"] == "oracle_error"
        assert panel.collected_at
        record = db.scalar(select(Execution).where(Execution.operation_id == entry.operation_id))
        assert record.state == "failed"
        assert record.error_code == "oracle_error"
        assert record.rows_returned is None

        if entry.kiwi:
            execute.reset_mock()
            toolbox = KiwiToolbox(state.execution, state.settings)
            result = toolbox.run(
                db,
                actor,
                profile.id,
                ToolCall(
                    id="permission-probe",
                    name=tool_name_for(entry.operation_id),
                    input={"why": "Check permission failure", **parameters(entry)},
                ),
            )
            execute.assert_called_once()
            assert result.is_error
            assert result.status == "failed"
            assert result.error_code == "oracle_error"
            assert result.rows == []
            assert code in result.content
            assert "No rows: nothing matched" not in result.content
            assert json.dumps({"error": panel.error}) in result.content


@pytest.mark.parametrize("code", ["ORA-00942", "ORA-01031"])
def test_overview_reports_unavailable_panels_after_grant_revocation(
    client, dba, targets, monkeypatch, code
):
    execute = Mock(side_effect=OracleError("Diagnostic permission unavailable", oracle_code=code))
    monkeypatch.setattr(FakeOracleConnection, "execute", execute)
    target_id = targets["development"]["id"]
    response = client.get(f"/api/v1/targets/{target_id}/dba/overview", headers=dba)
    assert response.status_code == 200
    body = response.json()
    assert set(body["unavailablePanels"]) == set(body["panels"])
    assert execute.call_count == len(body["panels"])
    for panel in body["panels"].values():
        assert panel["available"] is False
        assert panel["rows"] == []
        assert panel["error"]["oracleCode"] == code
