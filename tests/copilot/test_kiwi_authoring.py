"""Kiwi's authoring features (KIWI_PLAN.md, K-5).

Multi-part proposals, the team standards file and the playbook actions. The
exit criterion for K-5 is the multi-part apply-check: every part applies together,
and one stale, missing or extra part refuses the whole proposal with nothing applied.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness_api.config import Settings
from harness_api.copilot.context import ACTIONS
from harness_api.copilot.provider import Provider, ProviderUsage
from harness_api.copilot.standards import STANDARD_KEYS, parse_standards
from harness_worker.errors import ConfigurationError
from tests.copilot.test_copilot import base_payload, integration_headers, sse

SPEC = """\
CREATE OR REPLACE PACKAGE payroll AS
  PROCEDURE run_month(p_month IN DATE);
END payroll;
"""

BODY = """\
CREATE OR REPLACE PACKAGE BODY payroll AS
  PROCEDURE run_month(p_month IN DATE) IS
  BEGIN
    NULL;
  END run_month;
END payroll;
"""

TARGET = base_payload()["targetReference"]


def multi_part_payload(**overrides) -> dict:
    payload = base_payload(
        action="propose",
        userMessage="Add a p_dry_run flag to run_month, in the spec and the body.",
        attachments=[
            {
                "category": "selected_source",
                "name": "part:spec",
                "content": SPEC,
                "provenance": "editor buffer",
            },
            {
                "category": "selected_source",
                "name": "part:body",
                "content": BODY,
                "provenance": "editor buffer",
            },
        ],
        parts=[
            {"part": "spec", "editorId": "payroll-spec", "revision": "3", "text": SPEC},
            {"part": "body", "editorId": "payroll-body", "revision": "5", "text": BODY},
        ],
    )
    payload.pop("editor")
    payload.update(overrides)
    return payload


def current_parts(**changes: dict) -> list[dict]:
    parts = {
        "spec": {"part": "spec", "editorId": "payroll-spec", "revision": "3", "currentText": SPEC},
        "body": {"part": "body", "editorId": "payroll-body", "revision": "5", "currentText": BODY},
    }
    for name, change in changes.items():
        parts[name] = {**parts[name], **change}
    return list(parts.values())


def multi_part_proposal(client: TestClient, developer) -> dict:
    events = sse(client, developer, multi_part_payload())
    return next(data for name, data in events if name == "proposal")


def apply(client: TestClient, headers: dict, proposal_id: str, parts: list[dict], **extra):
    return client.post(
        f"/api/v1/copilot/proposals/{proposal_id}/apply-check",
        headers=headers,
        json={"targetReference": TARGET, "parts": parts, **extra},
    )


class FixedAnswer(Provider):
    """A provider that always gives the same answer, to shape the proposal."""

    name = "fixed"

    def __init__(self, answer: str) -> None:
        self._answer = answer

    @property
    def ready(self) -> bool:
        return True

    async def stream(self, system: str, user_message: str) -> AsyncIterator[str]:
        self.system = system
        yield self._answer

    def usage(self) -> ProviderUsage:
        return ProviderUsage(provider=self.name)


@pytest.fixture
def fixed(client: TestClient):
    harness = client.app.state.harness  # type: ignore[attr-defined]
    original = harness.copilot._provider  # noqa: SLF001

    def install(answer: str) -> FixedAnswer:
        provider = FixedAnswer(answer)
        harness.copilot._provider = provider  # noqa: SLF001
        return provider

    yield install
    harness.copilot._provider = original  # noqa: SLF001


# -- multi-part proposals ------------------------------------------------------------


def test_a_multi_part_proposal_pins_every_part(client: TestClient, developer) -> None:
    proposal = multi_part_proposal(client, developer)
    assert proposal["multiPart"] is True
    assert proposal["appliesToEditorOnly"] is True
    assert [part["part"] for part in proposal["parts"]] == ["spec", "body"]
    for part in proposal["parts"]:
        assert len(part["baseHash"]) == 64
        assert part["changed"] is True
        assert part["proposedText"].strip()
    assert "or none of them" in proposal["note"]


def test_every_part_applies_together(client: TestClient, developer) -> None:
    proposal = multi_part_proposal(client, developer)
    response = apply(client, developer, proposal["proposalId"], current_parts())
    assert response.status_code == 200
    body = response.json()
    assert body["canApply"] is True
    assert body["executesDatabaseOperations"] is False
    assert [(p["part"], p["editorId"]) for p in body["parts"]] == [
        ("spec", "payroll-spec"),
        ("body", "payroll-body"),
    ]
    assert all(p["proposedText"].strip() for p in body["parts"])


def test_one_stale_part_refuses_the_whole_proposal(client: TestClient, developer) -> None:
    proposal = multi_part_proposal(client, developer)
    stale = current_parts(body={"revision": "6", "currentText": BODY + "-- edited\n"})
    refused = apply(client, developer, proposal["proposalId"], stale).json()
    assert refused["canApply"] is False
    assert "parts" not in refused  # no part's text is handed back to apply
    assert refused["partReasons"]["spec"] == []
    assert refused["partReasons"]["body"] == [
        "The document changed since the proposal was generated."
    ]
    assert refused["reasons"] == ["body: The document changed since the proposal was generated."]
    assert "Nothing was applied" in refused["note"]

    # Nothing was marked applied, so once the buffer is back as it was the whole
    # proposal still applies, exactly once.
    first = apply(client, developer, proposal["proposalId"], current_parts()).json()
    second = apply(client, developer, proposal["proposalId"], current_parts()).json()
    assert first["canApply"] is True
    assert second["canApply"] is False
    assert "already been applied" in second["reasons"][0]


def test_a_changed_revision_on_one_part_refuses_the_whole(client: TestClient, developer) -> None:
    proposal = multi_part_proposal(client, developer)
    body = apply(
        client, developer, proposal["proposalId"], current_parts(spec={"revision": "4"})
    ).json()
    assert body["canApply"] is False
    assert body["partReasons"]["spec"] == [
        "The document revision changed since the proposal was generated."
    ]


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (current_parts()[:1], "body: This part's current buffer was not sent."),
        (
            current_parts()
            + [{"part": "extra", "editorId": "x", "revision": "1", "currentText": ""}],
            "The proposal has no part named 'extra'.",
        ),
        (current_parts() + current_parts()[:1], "Part 'spec' was sent more than once."),
        (
            current_parts(spec={"editorId": "somewhere-else"}),
            "spec: The part was generated for a different editor buffer.",
        ),
    ],
    ids=["missing", "extra", "duplicate", "other-editor"],
)
def test_a_missing_extra_or_misdirected_part_refuses_the_whole(
    client: TestClient, developer, parts: list[dict], expected: str
) -> None:
    proposal = multi_part_proposal(client, developer)
    body = apply(client, developer, proposal["proposalId"], parts).json()
    assert body["canApply"] is False
    assert expected in body["reasons"]
    assert "parts" not in body


def test_a_changed_target_refuses_the_whole(client: TestClient, developer) -> None:
    proposal = multi_part_proposal(client, developer)
    body = client.post(
        f"/api/v1/copilot/proposals/{proposal['proposalId']}/apply-check",
        headers=developer,
        json={"targetReference": "harness:other:OTHER", "parts": current_parts()},
    ).json()
    assert body["canApply"] is False
    assert "The selected target has changed" in body["reasons"][0]


def test_parts_the_model_left_alone_are_still_pinned(client: TestClient, developer, fixed) -> None:
    fixed("Only the body needs it.\n\n```plsql part=body\n-- new body\n```\n")
    events = sse(client, developer, multi_part_payload())
    proposal = next(data for name, data in events if name == "proposal")
    by_part = {part["part"]: part for part in proposal["parts"]}
    assert by_part["spec"]["changed"] is False
    assert by_part["spec"]["proposedText"] == SPEC
    assert by_part["body"]["changed"] is True
    assert by_part["body"]["proposedText"] == "-- new body"
    assert proposal["rationale"] == "Only the body needs it."

    # The unchanged part is still checked: editing the spec refuses the whole.
    body = apply(
        client, developer, proposal["proposalId"], current_parts(spec={"currentText": "x"})
    ).json()
    assert body["canApply"] is False
    assert body["partReasons"]["spec"]


def test_labels_that_name_no_part_are_ignored(client: TestClient, developer, fixed) -> None:
    fixed("```plsql part=trigger\n-- not asked for\n```\n")
    events = sse(client, developer, multi_part_payload())
    assert not any(name == "proposal" for name, _ in events)
    assert events[-1][1]["outcome"] == "succeeded"


def test_the_request_names_every_part_for_the_model(client: TestClient, developer, fixed) -> None:
    provider = fixed("No change needed.")
    captured: list[str] = []
    original = provider.stream

    async def spy(system: str, user_message: str) -> AsyncIterator[str]:
        captured.append(user_message)
        async for chunk in original(system, user_message):
            yield chunk

    provider.stream = spy  # type: ignore[method-assign]
    sse(client, developer, multi_part_payload())
    assert "Parts, in order: spec, body." in captured[0]
    assert "part=" in captured[0]


@pytest.mark.parametrize(
    ("parts", "message"),
    [
        (
            [
                {"part": "spec", "editorId": "a", "revision": "1", "text": SPEC},
                {"part": "spec", "editorId": "b", "revision": "1", "text": BODY},
            ],
            "its own name",
        ),
        (
            [
                {"part": "spec", "editorId": "a", "revision": "1", "text": SPEC},
                {"part": "body", "editorId": "a", "revision": "1", "text": BODY},
            ],
            "its own editor buffer",
        ),
    ],
    ids=["duplicate-name", "duplicate-editor"],
)
def test_ambiguous_parts_are_refused_before_anything_is_spent(
    client: TestClient, developer, parts: list[dict], message: str
) -> None:
    events = sse(client, developer, multi_part_payload(parts=parts))
    assert [name for name, _ in events] == ["error"]
    assert events[0][1]["code"] == "invalid_request"
    assert message in events[0][1]["message"]


@pytest.mark.parametrize(
    "part",
    [
        {"part": "has space", "editorId": "a", "revision": "1", "text": ""},
        {"part": "spec", "editorId": "", "revision": "1", "text": ""},
    ],
    ids=["bad-name", "no-editor"],
)
def test_malformed_parts_are_rejected_by_the_schema(
    client: TestClient, developer, part: dict
) -> None:
    response = client.post(
        "/api/v1/copilot/requests", headers=developer, json=multi_part_payload(parts=[part])
    )
    assert response.status_code == 422


def test_at_most_eight_parts(client: TestClient, developer) -> None:
    parts = [{"part": f"p{i}", "editorId": f"e{i}", "revision": "1", "text": ""} for i in range(9)]
    response = client.post(
        "/api/v1/copilot/requests", headers=developer, json=multi_part_payload(parts=parts)
    )
    assert response.status_code == 422


def test_parts_cannot_be_sent_to_a_single_part_proposal(client: TestClient, developer) -> None:
    events = sse(client, developer, base_payload())
    proposal = next(data for name, data in events if name == "proposal")
    assert "multiPart" not in proposal
    response = apply(client, developer, proposal["proposalId"], current_parts())
    assert response.status_code == 400
    assert "single part" in response.json()["error"]["message"]


def test_another_actor_cannot_apply_a_multi_part_proposal(
    client: TestClient, developer, second_developer
) -> None:
    proposal = multi_part_proposal(client, developer)
    response = apply(client, second_developer, proposal["proposalId"], current_parts())
    assert response.status_code == 404


# -- playbook actions ------------------------------------------------------------------


@pytest.mark.parametrize("action", ["kiwi.diagnose", "kiwi.create", "kiwi.test_block"])
def test_the_playbook_actions_are_accepted(client: TestClient, developer, action: str) -> None:
    assert action in ACTIONS
    events = sse(client, developer, base_payload(action=action))
    assert events[0][0] == "start"
    assert events[0][1]["action"] == action
    assert events[-1][0] == "done"


def test_the_playbooks_name_real_lookups(client: TestClient) -> None:
    from harness_api.copilot.context import ACTION_INSTRUCTIONS

    execution = client.app.state.harness.execution  # type: ignore[attr-defined]
    known = {entry.operation_id for entry in execution.catalog.list()}
    for action in ("kiwi.diagnose", "kiwi.create", "kiwi.test_block"):
        cited = set(re.findall(r"\b((?:schema|dba|tuning)\.[a-z_]+)", ACTION_INSTRUCTIONS[action]))
        assert cited <= known, (action, cited - known)
    assert "schema.object_errors" in ACTION_INSTRUCTIONS["kiwi.diagnose"]
    assert "ROLLBACK" in ACTION_INSTRUCTIONS["kiwi.create"]


# -- standards ---------------------------------------------------------------------------

STANDARDS = {
    "namingPrefixes": {"package": "pkg_", "parameter": "p_"},
    "errorLoggingPackage": "app_log.error",
    "bulkCollectLimit": 500,
    "exceptionPolicy": "Never swallow WHEN OTHERS; log and re-raise.",
}


def test_standards_render_every_key_with_the_citation_rule() -> None:
    standards = parse_standards(json.dumps(STANDARDS))
    assert standards.keys == [k for k in STANDARD_KEYS if k in STANDARDS]
    rendered = standards.render()
    assert "(standard: <key>)" in rendered
    assert "- namingPrefixes: package: pkg_, parameter: p_" in rendered
    assert "- bulkCollectLimit: 500" in rendered


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("not json", "not valid JSON"),
        ("[]", "must be a JSON object"),
        ('{"namingPrefix": {}}', "unknown keys: namingPrefix"),
        ('{"namingPrefixes": {"package": 1}}', "namingPrefixes"),
        ('{"bulkCollectLimit": 0}', "bulkCollectLimit"),
        ('{"bulkCollectLimit": true}', "bulkCollectLimit"),
        ('{"exceptionPolicy": ["x"]}', "exceptionPolicy must be a string"),
        ('{"headerTemplate": "' + "x" * 17_000 + '"}', "larger than"),
    ],
)
def test_a_bad_standards_file_fails_loudly(raw: str, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        parse_standards(raw)


@pytest.fixture
def standards_file(tmp_path: Path) -> Path:
    path = tmp_path / "standards.json"
    path.write_text(json.dumps(STANDARDS), encoding="utf-8")
    return path


def _with_standards(client: TestClient, settings: Settings, path: Path) -> None:
    copilot = client.app.state.harness.copilot  # type: ignore[attr-defined]
    copilot._settings = settings.model_copy(update={"kiwi_standards_file": str(path)})  # noqa: SLF001
    copilot._standards = None  # noqa: SLF001


def test_configured_standards_reach_the_system_prompt(
    client: TestClient, developer, settings: Settings, standards_file: Path, fixed
) -> None:
    _with_standards(client, settings, standards_file)
    provider = fixed("Fine as it is.")
    sse(client, developer, base_payload(action="kiwi.create"))
    assert "Team standards." in provider.system
    assert "- errorLoggingPackage: app_log.error" in provider.system


def test_capabilities_report_the_standards(
    client: TestClient, administrator, settings: Settings, standards_file: Path
) -> None:
    headers = integration_headers(client, administrator)
    body = client.get("/api/v1/integrations/capabilities", headers=headers).json()
    assert body["kiwi"]["standards"] == {"configured": False, "keys": []}
    assert body["kiwi"]["multiPartProposals"] is True

    _with_standards(client, settings, standards_file)
    body = client.get("/api/v1/integrations/capabilities", headers=headers).json()
    assert body["kiwi"]["standards"]["configured"] is True
    assert body["kiwi"]["standards"]["keys"] == list(STANDARDS)


def test_a_bad_standards_file_refuses_requests_with_a_typed_error(
    client: TestClient, developer, administrator, settings: Settings, tmp_path: Path
) -> None:
    bad = tmp_path / "standards.json"
    bad.write_text('{"namingPrefix": {}}', encoding="utf-8")
    _with_standards(client, settings, bad)

    events = sse(client, developer, base_payload())
    assert [name for name, _ in events] == ["error"]
    assert events[0][1]["code"] == "configuration_error"

    headers = integration_headers(client, administrator)
    body = client.get("/api/v1/integrations/capabilities", headers=headers).json()
    assert body["kiwi"]["standards"]["configured"] is True
    assert "unknown keys" in body["kiwi"]["standards"]["error"]


def test_a_missing_standards_file_is_reported(
    client: TestClient, developer, settings: Settings, tmp_path: Path
) -> None:
    _with_standards(client, settings, tmp_path / "nowhere.json")
    events = sse(client, developer, base_payload())
    assert events[0][1]["code"] == "configuration_error"
    assert "could not be read" in events[0][1]["message"]
