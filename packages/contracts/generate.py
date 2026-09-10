"""Generate the published contract documents.

Writes:

* ``openapi.json`` - the whole API, produced from the FastAPI application, so it
  cannot drift from the code that serves it.
* ``copilot-protocol.schema.json`` - the request and stream-event shapes an IDE
  adapter has to implement. These are the versioned pieces of the integration
  contract described in ORACLEDATAFORGE_INTEGRATION.md.

Run it with::

    uv run python packages/contracts/generate.py

The TypeScript types in ``src/`` are written against these documents; the contract
test in ``tests/integration/test_contracts.py`` fails if they diverge.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness_api.app import create_app
from harness_api.config import Settings
from harness_api.copilot.context import ACTIONS, FORBIDDEN_CATEGORIES, ContextCategory
from harness_api.copilot.service import PROTOCOL_VERSION, SUPPORTED_PROTOCOL_MAJOR

HERE = Path(__file__).parent

STREAM_EVENTS = {
    "start": {
        "description": "Sent once, before any answer text. Carries the context preview.",
        "required": ["requestId", "protocolVersion", "action", "provider", "model"],
    },
    "delta": {
        "description": "A chunk of answer text.",
        "required": ["text"],
    },
    "proposal": {
        "description": (
            "A reviewable editor diff, pinned to the document revision and hash it was "
            "generated from. Applying it changes the editor buffer only."
        ),
        "required": [
            "proposalId",
            "editorId",
            "baseRevision",
            "baseHash",
            "targetReference",
            "proposedText",
            "appliesToEditorOnly",
        ],
    },
    "usage": {
        "description": "Provider, model and token usage for the completed request.",
        "required": ["provider", "model"],
    },
    "done": {
        "description": "Terminal event. Always the last event of a successful stream.",
        "required": ["requestId", "outcome", "latencyMs"],
    },
    "error": {
        "description": (
            "Typed failure. The code is stable: disabled, unauthorized, "
            "unsupported-version, context-too-large, rate-limit, timeout and "
            "provider-failure outcomes all arrive here."
        ),
        "required": ["code", "message"],
    },
}


def copilot_protocol_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://oracledbharness.invalid/schemas/copilot-protocol.json",
        "title": "OracleDBHarness copilot protocol",
        "protocolVersion": PROTOCOL_VERSION,
        "supportedProtocolMajors": [SUPPORTED_PROTOCOL_MAJOR],
        "description": (
            "The contract between an IDE adapter and the harness copilot API. A major "
            "version mismatch is rejected; unknown optional fields are ignored."
        ),
        "definitions": {
            "action": {"enum": list(ACTIONS)},
            "contextCategory": {"enum": [c.value for c in ContextCategory]},
            "forbiddenContextCategory": {
                "enum": list(FORBIDDEN_CATEGORIES),
                "description": (
                    "Never accepted, whatever a caller sends. Credentials, wallets, "
                    "result rows and bind values do not enter model context."
                ),
            },
            "streamEvent": {
                "oneOf": [
                    {
                        "title": name,
                        "type": "object",
                        "description": spec["description"],
                        "required": spec["required"],
                    }
                    for name, spec in STREAM_EVENTS.items()
                ]
            },
        },
        "guarantees": [
            "Applying a proposal updates an editor buffer. It never runs, compiles, "
            "commits, or pushes anything.",
            "A proposal is rejected if the document, its revision, the editor, or the "
            "target changed since it was generated.",
            "An actor reference asserted by an adapter is namespaced by integration "
            "instance and is never treated as a role.",
            "A partially delivered request is never replayed automatically.",
        ],
    }


def main() -> None:
    settings = Settings(metadata_url="sqlite+pysqlite:///:memory:", oracle_backend="fake")
    app = create_app(settings)
    document = app.openapi()

    (HERE / "openapi.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (HERE / "copilot-protocol.schema.json").write_text(
        json.dumps(copilot_protocol_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"openapi.json: {len(document['paths'])} paths")
    print(f"copilot-protocol.schema.json: protocol {PROTOCOL_VERSION}")


if __name__ == "__main__":
    main()
