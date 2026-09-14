"""An account missing grants degrades panel by panel, and says why.

``tests/integration/test_targets_and_access.py`` checks the degraded panel by editing a
stored capability row, because the fixture account has every grant. That shows the API
reads the row correctly; it does not show the probe against a real account reaches the
same answer as the diagnostics that then run under it. Here nothing is edited: an account
really is missing grants, and the question is whether every panel's outcome agrees with
what was probed.

The two failure modes that matter are a panel that fails with an Oracle error although
its capability was probed as available (a probe that does not prove what the panel needs),
and a panel reported unavailable for a capability the probe found. Either is a defect.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from harness_worker.backend import OracleConnection
from harness_worker.types import Capability
from tests import oracle_config as qual_config
from tests.qualification.evidence import Evidence
from tests.qualification.requirements import skip_or_fail

SECRET_NAME = "harness-restricted"  # noqa: S105 - a reference name
SECRET_FILE = "harness_restricted.password"  # noqa: S105 - a file name


def test_the_restricted_account_is_really_restricted(
    restricted_connection: OracleConnection,
    evidence: Evidence,
) -> None:
    """Probe every capability. At least one must be missing, and say so with an ORA code."""

    reports = {
        capability: restricted_connection.probe_capability(capability) for capability in Capability
    }
    evidence.note(
        "Capabilities on the restricted account",
        "<br>".join(
            f"{capability.value}: "
            + ("available" if report.available else f"unavailable ({report.detail})")
            for capability, report in reports.items()
        ),
    )
    assert reports[Capability.CONNECT].available
    missing = {
        capability.value: report.detail
        for capability, report in reports.items()
        if not report.available
    }
    assert missing, (
        f"{qual_config.ENV_RESTRICTED_USER} has every capability, so it cannot show a panel "
        "degrading. Use an account without some of the SELECT grants in "
        "oracle/grants/harness_roles.sql."
    )
    unexplained = {name: detail for name, detail in missing.items() if "ORA-" not in detail}
    assert not unexplained, f"unavailable without an Oracle reason: {unexplained}"


def test_dba_panels_degrade_to_match_the_probe(
    client: TestClient,
    administrator: dict[str, str],
    dba: dict[str, str],
    workspace: Path,
    oracle_config: qual_config.OracleTestConfig,
    evidence: Evidence,
) -> None:
    restricted = oracle_config.restricted
    if restricted is None:
        skip_or_fail(oracle_config, "restricted_account", qual_config.NO_RESTRICTED_ACCOUNT_REASON)
    assert restricted is not None

    (workspace / "secrets" / SECRET_FILE).write_text(restricted.password, encoding="utf-8")
    created = client.post(
        "/api/v1/admin/secrets",
        headers=administrator,
        json={"name": SECRET_NAME, "locator": SECRET_FILE},
    )
    assert created.status_code == 201, created.text
    target = client.post(
        "/api/v1/admin/targets",
        headers=administrator,
        json={
            "name": "restricted",
            "environment": "development",
            "host": restricted.host,
            "port": restricted.port,
            "serviceName": restricted.service_name,
            "username": restricted.username,
            "defaultSchema": restricted.schema,
            "secretReference": SECRET_NAME,
        },
    )
    assert target.status_code == 201, target.text
    profile_id = target.json()["id"]
    granted = client.post(
        "/api/v1/admin/grants",
        headers=administrator,
        json={"subject": "dba@example.internal", "profileId": profile_id, "permissions": ["read"]},
    )
    assert granted.status_code == 201, granted.text

    probed = client.post(f"/api/v1/targets/{profile_id}/test", headers=dba).json()
    assert probed["connected"] is True, probed
    missing = {row["capability"] for row in probed["capabilities"] if not row["available"]}
    assert missing, "the restricted account probed with every capability available"

    overview = client.get(f"/api/v1/targets/{profile_id}/dba/overview", headers=dba).json()
    panels = overview["panels"]
    outcomes: list[str] = []
    defects: list[str] = []
    for name, panel in panels.items():
        if panel["available"]:
            outcomes.append(f"{name}: available")
            continue
        error = panel.get("error") or {}
        named = (error.get("detail") or {}).get("missingCapabilities")
        outcomes.append(f"{name}: unavailable, `{error.get('code')}`, missing {named}")
        if error.get("code") != "capability_unavailable" or named is None:
            defects.append(
                f"{name} failed in use although the probe allowed it: "
                f"{error.get('code')}: {error.get('message')}"
            )
        elif not set(named) <= missing:
            defects.append(
                f"{name} names {named} as missing; the probe found only {sorted(missing)}"
            )
        elif panel["rows"]:
            defects.append(f"{name} is unavailable but carries rows")

    evidence.note(
        "DBA panels on the restricted account",
        f"Probe found missing: {sorted(missing)}<br>" + "<br>".join(outcomes),
    )
    assert set(overview["unavailablePanels"]) == {
        name for name, panel in panels.items() if not panel["available"]
    }
    assert not defects, "; ".join(defects)
