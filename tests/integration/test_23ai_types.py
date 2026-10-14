"""BOOLEAN and VECTOR through a worksheet, the path a user takes.

Before ``_shape`` handled VECTOR, the worker returned an ``array.array`` that the API
could not serialise, so a query returning a vector failed in the API rather than
arriving in the grid. Oracle only: the stand-in has neither type.

In thick mode an Oracle Client older than 23 gets both converted by the server: the
vector arrives as a plain list of unknown format, the BOOLEAN as 1, and the worksheet
says which client caused it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests import oracle_config
from tests.conftest import execute, open_worksheet

pytestmark = pytest.mark.oracle_only


def _thick_client_version(target: oracle_config.OracleTestConfig) -> tuple[int, ...] | None:
    if target.driver_mode != "thick":
        return None
    import oracledb  # thick mode means the driver is installed and initialised

    return tuple(oracledb.clientversion())


@pytest.fixture
def session_id(client: TestClient, developer, targets) -> str:
    development = targets["development"]["id"]
    identity = client.post(f"/api/v1/targets/{development}/test", headers=developer).json()
    version = identity["identity"]["version"]
    if int(version.split(".", 1)[0]) < 23:
        pytest.skip(f"The target is {version}; BOOLEAN and VECTOR arrived in 23ai.")
    return open_worksheet(client, developer, development)


def test_a_vector_and_a_boolean_reach_the_grid(
    client: TestClient,
    developer,
    session_id: str,
    oracle_target: oracle_config.OracleTestConfig | None,
) -> None:
    body = execute(
        client,
        developer,
        session_id,
        "SELECT TO_VECTOR('[1.5, 2]', 2, FLOAT32) AS embedding, TRUE AS flag FROM dual",
    )

    outcome = body["outcome"]
    assert outcome["state"] == "succeeded", body
    assert oracle_target is not None
    thick_client = _thick_client_version(oracle_target)
    if thick_client is not None and thick_client[0] < 23:
        client_version = ".".join(map(str, thick_client))
        assert outcome["resultSet"]["rows"] == [
            [
                {
                    "kind": "vector",
                    "format": None,
                    "length": 2,
                    "values": [1.5, 2.0],
                    "truncated": False,
                },
                1,
            ]
        ]
        assert any(client_version in warning for warning in outcome["warnings"]), outcome
        return
    assert outcome["resultSet"]["rows"] == [
        [
            {
                "kind": "vector",
                "format": "FLOAT32",
                "dimensions": 2,
                "values": [1.5, 2.0],
                "truncated": False,
            },
            True,
        ]
    ]


def test_a_json_array_binds_as_a_vector_when_hinted(
    client: TestClient, developer, session_id: str
) -> None:
    """Unhinted, the array reaches the driver as a list, which binds as a PL/SQL array."""

    statement = "SELECT ROUND(VECTOR_DISTANCE(TO_VECTOR('[0, 1, 0]'), :q, COSINE), 4) FROM dual"
    hinted = execute(
        client,
        developer,
        session_id,
        statement,
        binds=[{"name": "q", "value": [0, 1, 0], "typeHint": "vector"}],
    )
    unhinted = execute(
        client, developer, session_id, statement, binds=[{"name": "q", "value": [0, 1, 0]}]
    )

    assert hinted["outcome"]["state"] == "succeeded", hinted
    assert hinted["outcome"]["resultSet"]["rows"] == [[0]]
    assert unhinted["outcome"]["state"] == "failed"
    assert "ORA-01484" in unhinted["outcome"]["error"]["message"]
