"""The published contract must keep matching the code that serves it.

`packages/contracts/src/index.ts` is written by hand against the generated OpenAPI
document. These tests fail when an endpoint or field the TypeScript client depends on
moves, so the console and the IDE adapters cannot drift silently.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

CONTRACTS = Path(__file__).resolve().parents[2] / "packages" / "contracts"

# Endpoints the shipped TypeScript client calls.
CLIENT_ENDPOINTS = [
    ("get", "/api/v1/system/info"),
    ("get", "/api/v1/targets"),
    ("post", "/api/v1/targets/{profile_id}/test"),
    ("post", "/api/v1/worksheets"),
    ("post", "/api/v1/worksheets/{session_id}/execute"),
    ("post", "/api/v1/worksheets/{session_id}/commit"),
    ("post", "/api/v1/worksheets/{session_id}/rollback"),
    ("post", "/api/v1/worksheets/{session_id}/cancel"),
    ("post", "/api/v1/copilot/requests"),
    ("post", "/api/v1/copilot/proposals/{proposal_id}/apply-check"),
    ("get", "/api/v1/integrations/capabilities"),
]


def test_every_endpoint_the_client_uses_exists(client: TestClient) -> None:
    document = client.app.openapi()
    for method, path in CLIENT_ENDPOINTS:
        assert path in document["paths"], path
        assert method in document["paths"][path], f"{method.upper()} {path}"


def test_the_generated_openapi_document_is_current(client: TestClient) -> None:
    """Regenerate with: uv run python packages/contracts/generate.py"""

    published = json.loads((CONTRACTS / "openapi.json").read_text(encoding="utf-8"))
    live = client.app.openapi()
    assert sorted(published["paths"]) == sorted(live["paths"]), (
        "packages/contracts/openapi.json is out of date; regenerate it."
    )


def test_the_copilot_protocol_schema_matches_the_implementation(client: TestClient) -> None:
    from harness_api.copilot.context import ACTIONS, FORBIDDEN_CATEGORIES
    from harness_api.copilot.service import PROTOCOL_VERSION

    schema = json.loads((CONTRACTS / "copilot-protocol.schema.json").read_text(encoding="utf-8"))
    assert schema["protocolVersion"] == PROTOCOL_VERSION
    assert schema["definitions"]["action"]["enum"] == list(ACTIONS)
    assert schema["definitions"]["forbiddenContextCategory"]["enum"] == list(FORBIDDEN_CATEGORIES)
    events = {entry["title"] for entry in schema["definitions"]["streamEvent"]["oneOf"]}
    assert events == {"start", "delta", "proposal", "usage", "done", "error"}


def test_the_typescript_client_declares_the_same_protocol_version() -> None:
    source = (CONTRACTS / "src" / "index.ts").read_text(encoding="utf-8")
    from harness_api.copilot.service import PROTOCOL_VERSION, SUPPORTED_PROTOCOL_MAJOR

    assert f'PROTOCOL_VERSION = "{PROTOCOL_VERSION}"' in source
    assert f"SUPPORTED_PROTOCOL_MAJOR = {SUPPORTED_PROTOCOL_MAJOR}" in source


def test_the_typescript_client_lists_the_same_actions_and_exclusions() -> None:
    from harness_api.copilot.context import ACTIONS, FORBIDDEN_CATEGORIES

    source = (CONTRACTS / "src" / "index.ts").read_text(encoding="utf-8")
    declared = set(re.findall(r'\|\s*"([a-z_]+)"', source))
    assert set(ACTIONS) <= declared
    for category in FORBIDDEN_CATEGORIES:
        assert f'"{category}"' in source, category


def test_the_openapi_description_states_the_standing_constraints(client: TestClient) -> None:
    description = " ".join(client.app.openapi()["info"]["description"].split())
    assert "keyword filter is not a read-only boundary" in description
    assert "Production targets are observation only" in description
    assert "never runs, compiles or commits anything" in description
