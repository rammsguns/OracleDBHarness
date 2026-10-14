"""Diagnostics and tuning against a lab schema with realistic volumes (T-5).

The fixtures prove behaviour on small objects built for the purpose. A lab schema such as
``SH`` holds what users actually point the harness at: tables of close to a million rows,
partitions, materialized views, dimensions, triggers and routines nobody wrote for a test.
The account reading it holds the diagnostics role and ``SELECT`` on the lab owner's
objects, nothing else. Nothing here writes: the lab schema is read-only material.

What is checked on every call: it answers inside the default execution deadline, a page
never exceeds its limit, and anything unavailable says so with ``capability_unavailable``
for a capability the probe really found missing, instead of an empty, healthy-looking
panel.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from harness_worker.types import ExecutionLimits
from tests import oracle_config as qual_config
from tests.qualification.evidence import Evidence
from tests.qualification.requirements import skip_or_fail

SECRET_NAME = "harness-lab"  # noqa: S105 - a reference name
SECRET_FILE = "harness_lab.password"  # noqa: S105 - a file name
PAGE = 25
DEADLINE = ExecutionLimits().deadline_seconds

# A join and an aggregate over the largest tables of each known lab schema. Explain only:
# the optimizer estimates it, nothing executes it.
JOINS = {
    "SH": (
        "select c.cust_state_province, sum(s.amount_sold) from sh.sales s "
        "join sh.customers c on c.cust_id = s.cust_id group by c.cust_state_province"
    ),
}


class _Timed:
    def __init__(self, client: TestClient, headers: dict[str, str]) -> None:
        self.client = client
        self.headers = headers
        self.timings: list[tuple[str, float]] = []

    def get(self, label: str, url: str, **params: Any) -> Any:
        return self._call(label, lambda: self.client.get(url, headers=self.headers, params=params))

    def post(self, label: str, url: str, body: dict[str, Any]) -> Any:
        return self._call(label, lambda: self.client.post(url, headers=self.headers, json=body))

    def _call(self, label: str, send: Any) -> Any:
        started = time.perf_counter()
        response = send()
        self.timings.append((label, time.perf_counter() - started))
        assert response.status_code == 200, f"{label}: {response.status_code} {response.text}"
        return response.json()

    def slow(self) -> list[str]:
        return [f"{label} took {took:.1f}s" for label, took in self.timings if took > DEADLINE]


def _register(client: TestClient, administrator: dict[str, str], workspace: Path, lab: Any) -> str:
    (workspace / "secrets" / SECRET_FILE).write_text(lab.password, encoding="utf-8")
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
            "name": "lab",
            "environment": "development",
            "host": lab.host,
            "port": lab.port,
            "serviceName": lab.service_name,
            "username": lab.username,
            "defaultSchema": lab.schema,
            "secretReference": SECRET_NAME,
            # Explain needs the worksheet permission. The account holds SELECT only, so
            # nothing a worksheet could send would write to the lab schema.
            "worksheetsEnabled": True,
        },
    )
    assert target.status_code == 201, target.text
    profile_id: str = target.json()["id"]
    granted = client.post(
        "/api/v1/admin/grants",
        headers=administrator,
        json={
            "subject": "dba@example.internal",
            "profileId": profile_id,
            "permissions": ["read", "worksheet"],
        },
    )
    assert granted.status_code == 201, granted.text
    return profile_id


def _panel_defect(name: str, panel: dict[str, Any], missing: set[str]) -> str | None:
    """The restricted-account rule: unavailable only for a capability the probe lacked."""

    if panel["available"]:
        return None
    error = panel.get("error") or {}
    named = (error.get("detail") or {}).get("missingCapabilities")
    if error.get("code") != "capability_unavailable" or named is None:
        return f"{name} failed in use: {error.get('code')}: {error.get('message')}"
    if not set(named) <= missing:
        return f"{name} names {named} as missing; the probe found only {sorted(missing)}"
    if panel["rows"]:
        return f"{name} is unavailable but carries rows"
    return None


def test_diagnostics_and_tuning_on_a_lab_schema(
    client: TestClient,
    administrator: dict[str, str],
    dba: dict[str, str],
    workspace: Path,
    oracle_config: qual_config.OracleTestConfig,
    evidence: Evidence,
) -> None:
    lab = oracle_config.lab
    if lab is None:
        skip_or_fail(oracle_config, "lab_schema", qual_config.NO_LAB_SCHEMA_REASON)
    assert lab is not None
    schema = lab.schema
    profile_id = _register(client, administrator, workspace, lab)
    api = _Timed(client, dba)
    defects: list[str] = []
    seen: list[str] = []

    probed = api.post("probe", f"/api/v1/targets/{profile_id}/test", {})
    assert probed["connected"] is True, probed
    missing = {row["capability"] for row in probed["capabilities"] if not row["available"]}
    seen.append(f"probe: missing {sorted(missing) or 'nothing'}")

    # -- DBA overview -------------------------------------------------------------------
    overview = api.get("dba overview", f"/api/v1/targets/{profile_id}/dba/overview")
    for name, panel in overview["panels"].items():
        if (defect := _panel_defect(f"overview.{name}", panel, missing)) is not None:
            defects.append(defect)
        seen.append(
            f"overview.{name}: "
            + (f"{len(panel['rows'])} rows" if panel["available"] else "unavailable")
            + (" (truncated)" if panel.get("truncated") else "")
        )

    # -- schema browser -----------------------------------------------------------------
    schemas = api.get("schemas", f"/api/v1/targets/{profile_id}/schemas")
    if (defect := _panel_defect("schemas", schemas, missing)) is not None:
        defects.append(defect)
    elif schema not in {str(row[0]).upper() for row in schemas["rows"]}:
        defects.append(f"schemas does not list {schema}, which the account can read")

    objects: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = api.get(
            f"objects offset {offset}",
            f"/api/v1/targets/{profile_id}/objects",
            owner=schema,
            offset=offset,
            limit=PAGE,
        )
        assert len(page["rows"]) <= PAGE, f"a page of {PAGE} returned {len(page['rows'])}"
        objects += [dict(zip(page["columns"], row, strict=True)) for row in page["rows"]]
        if not page["hasMore"]:
            break
        assert len(page["rows"]) == PAGE, "hasMore on a short page"
        offset += PAGE
    # Partitions share their table's name; the subobject tells them apart.
    keys = [(o["OBJECT_TYPE"], o["OBJECT_NAME"], o["SUBOBJECT_NAME"]) for o in objects]
    assert objects, f"no objects listed for {schema}; an empty page reads as an empty schema"
    assert len(set(keys)) == len(keys), "paging repeated an object"
    names = sorted({(kind, name) for kind, name, _ in keys})
    seen.append(f"objects: {len(objects)} in {offset // PAGE + 1} pages of {PAGE}")

    browsable = (
        "TABLE",
        "VIEW",
        "MATERIALIZED VIEW",
        "PACKAGE",
        "PROCEDURE",
        "FUNCTION",
        "TRIGGER",
    )
    detailed = 0
    for kind, name in names:
        if kind not in browsable:
            continue
        detail = api.get(
            f"detail {kind} {name}",
            f"/api/v1/targets/{profile_id}/objects/detail",
            owner=schema,
            objectName=name,
            objectType=kind,
        )
        detailed += 1
        for panel_name, panel in detail["panels"].items():
            label = f"detail {kind} {name}.{panel_name}"
            if (defect := _panel_defect(label, panel, missing)) is not None:
                defects.append(defect)
            elif kind == "TABLE" and panel_name == "columns" and not panel["rows"]:
                defects.append(f"{label} is available and empty")
    seen.append(f"object detail: {detailed} objects")

    # -- tuning workbench ---------------------------------------------------------------
    statement = JOINS.get(schema)
    if statement is None:
        tables = [name for kind, name in names if kind == "TABLE"]
        statement = f"select count(*) from {schema}.{tables[0]}" if tables else None
    if statement is not None:
        plan = api.post(
            "explain", "/api/v1/tuning/explain", {"profileId": profile_id, "statement": statement}
        )
        if plan.get("error"):
            defects.append(f"explain failed: {plan['error']}")
        elif not plan["rows"]:
            defects.append("explain succeeded with no plan rows")
        seen.append(f"explain: {len(plan['rows'])} plan rows")

    cursors = api.get(
        "cursor search", f"/api/v1/tuning/{profile_id}/cursors", textFilter=f"{schema}.", limit=25
    )
    if not cursors["available"]:
        code = (cursors.get("error") or {}).get("code")
        if code != "capability_unavailable":
            defects.append(f"cursor search failed in use: {cursors.get('error')}")
        seen.append(f"cursor search: unavailable ({code})")
    else:
        seen.append(f"cursor search: {len(cursors['rows'])} cursors")
        # The harness's own EXPLAIN PLAN statements match the filter too, and Oracle keeps
        # no V$SQL_PLAN rows for them; nor for a child with no plan hash. A query cursor
        # that has a plan hash has plan rows.
        found = [dict(zip(cursors["columns"], row, strict=True)) for row in cursors["rows"]]
        queries = [
            c
            for c in found
            if str(c["SQL_TEXT_PREVIEW"]).lstrip().upper().startswith(("SELECT", "WITH"))
            and c["PLAN_HASH_VALUE"]
        ]
        if queries:
            first = queries[0]
            detail = api.get(
                "cursor detail",
                f"/api/v1/tuning/{profile_id}/cursors/{first['SQL_ID']}",
                childNumber=first["CHILD_NUMBER"],
            )
            plan_state = detail["plan"]
            if not plan_state["available"]:
                code = (plan_state.get("error") or {}).get("code")
                if code != "capability_unavailable":
                    defects.append(f"cursor plan failed in use: {plan_state.get('error')}")
            elif not plan_state["rows"]:
                defects.append(f"cursor {first['SQL_ID']} is a cached query with an empty plan")
            seen.append(
                "cursor detail: plan "
                + (f"{len(plan_state['rows'])} rows" if plan_state["available"] else "unavailable")
            )

    slowest = sorted(api.timings, key=lambda item: item[1], reverse=True)[:5]
    evidence.note(f"Lab workload on {schema}", "<br>".join(seen))
    evidence.note(
        f"Lab workload timings on {schema}",
        f"{len(api.timings)} calls, total {sum(t for _, t in api.timings):.1f}s; slowest: "
        + ", ".join(f"{label} {took:.2f}s" for label, took in slowest),
    )
    defects += api.slow()
    assert not defects, "; ".join(defects)
