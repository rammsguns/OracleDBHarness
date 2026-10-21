"""The published contract must keep matching the code that serves it.

`packages/contracts/src/index.ts` is written by hand against the generated OpenAPI
document. These tests fail when an endpoint or field the TypeScript client depends on
moves, so the console and the IDE adapters cannot drift silently.
"""

from __future__ import annotations

import importlib.util
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
    assert events == {
        "start",
        "delta",
        "plan_step",
        "tool_call",
        "tool_result",
        "budget",
        "proposal",
        "lineage",
        "claim_check",
        "revision",
        "edit_contract",
        "lookup_record",
        "code_check",
        "usage",
        "done",
        "error",
    }


def _generator():
    spec = importlib.util.spec_from_file_location("contracts_generate", CONTRACTS / "generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_published_protocol_schema_is_what_the_generator_writes() -> None:
    """Regeneration must not drop an event or a required field (C26)."""

    published = json.loads((CONTRACTS / "copilot-protocol.schema.json").read_text(encoding="utf-8"))
    generated = json.loads(json.dumps(_generator().copilot_protocol_schema()))
    assert generated == published, "copilot-protocol.schema.json is out of date; regenerate it."
    required = {
        entry["title"]: entry.get("required", [])
        for entry in published["definitions"]["streamEvent"]["oneOf"]
    }
    assert required["claim_check"] == ["stage", "findings", "action"]
    assert required["revision"] == ["draft", "findings"]
    assert required["edit_contract"] == ["contract", "delivered", "repair"]
    assert required["lookup_record"] == ["lookups", "notRun"]


def test_the_typescript_client_declares_every_published_event() -> None:
    source = (CONTRACTS / "src" / "index.ts").read_text(encoding="utf-8")
    schema = json.loads((CONTRACTS / "copilot-protocol.schema.json").read_text(encoding="utf-8"))
    declared = set(re.findall(r'event:\s*"([a-z_]+)"', source))
    events = {entry["title"] for entry in schema["definitions"]["streamEvent"]["oneOf"]}
    assert events <= declared, sorted(events - declared)


def test_the_finding_kinds_are_published_and_declared() -> None:
    """Every kind the checks report is in the schema and the client's union (C26)."""

    from harness_api.copilot.claim_checks import FINDING_KINDS

    schema = json.loads((CONTRACTS / "copilot-protocol.schema.json").read_text(encoding="utf-8"))
    assert schema["definitions"]["claimFindingKind"]["enum"] == list(FINDING_KINDS)
    source = (CONTRACTS / "src" / "index.ts").read_text(encoding="utf-8")
    union = source.split("export type KiwiClaimFindingKind =", 1)[1].split(";", 1)[0]
    assert re.findall(r'"([a-z_]+)"', union) == list(FINDING_KINDS)
    assert 'rendered?: "next_checks" | "bounded_fallback"' in source


def test_every_reported_finding_kind_is_listed() -> None:
    from harness_api.copilot.claim_checks import FINDING_KINDS

    package = CONTRACTS.parents[1] / "services" / "api" / "harness_api" / "copilot"
    used = set()
    for path in package.glob("*.py"):
        used |= set(re.findall(r'ClaimFinding\(\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))
    assert used and used <= set(FINDING_KINDS), sorted(used - set(FINDING_KINDS))


def test_the_typescript_client_declares_the_same_protocol_version() -> None:
    source = (CONTRACTS / "src" / "index.ts").read_text(encoding="utf-8")
    from harness_api.copilot.service import PROTOCOL_VERSION, SUPPORTED_PROTOCOL_MAJOR

    assert f'PROTOCOL_VERSION = "{PROTOCOL_VERSION}"' in source
    assert f"SUPPORTED_PROTOCOL_MAJOR = {SUPPORTED_PROTOCOL_MAJOR}" in source


def test_the_typescript_client_lists_the_same_actions_and_exclusions() -> None:
    from harness_api.copilot.context import ACTIONS, FORBIDDEN_CATEGORIES

    source = (CONTRACTS / "src" / "index.ts").read_text(encoding="utf-8")
    declared = set(re.findall(r'\|\s*"([a-z_.]+)"', source))
    assert set(ACTIONS) <= declared
    for category in FORBIDDEN_CATEGORIES:
        assert f'"{category}"' in source, category


def test_the_openapi_description_states_the_standing_constraints(client: TestClient) -> None:
    description = " ".join(client.app.openapi()["info"]["description"].split())
    assert "keyword filter is not a read-only boundary" in description
    assert "Production targets are observation only" in description
    assert "never runs, compiles or commits anything" in description
