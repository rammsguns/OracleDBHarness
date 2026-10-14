"""BOOLEAN and VECTOR through a worksheet, the path a user takes.

Before ``_shape`` handled VECTOR, the worker returned an ``array.array`` that the API
could not serialise, so a query returning a vector failed in the API rather than
arriving in the grid. Oracle only: the stand-in has neither type.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import execute, open_worksheet

pytestmark = pytest.mark.oracle_only


@pytest.fixture
def session_id(client: TestClient, developer, targets) -> str:
    development = targets["development"]["id"]
    identity = client.post(f"/api/v1/targets/{development}/test", headers=developer).json()
    version = identity["identity"]["version"]
    if int(version.split(".", 1)[0]) < 23:
        pytest.skip(f"The target is {version}; BOOLEAN and VECTOR arrived in 23ai.")
    return open_worksheet(client, developer, development)


def test_a_vector_and_a_boolean_reach_the_grid(
    client: TestClient, developer, session_id: str
) -> None:
    body = execute(
        client,
        developer,
        session_id,
        "SELECT TO_VECTOR('[1.5, 2]', 2, FLOAT32) AS embedding, TRUE AS flag FROM dual",
    )

    outcome = body["outcome"]
    assert outcome["state"] == "succeeded", body
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
