"""The local provider and the rule that keeps prompts on this host.

``HARNESS_COPILOT_LOCAL_ONLY`` admits only the fixture and a model served over
loopback. These tests cover where that is enforced -- settings, the provider factory,
the endpoint check and the transport under the client -- and the Chat Completions
dialect against a stand-in server. Nothing is sent over a network.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError as SettingsError

from harness_api.config import Settings
from harness_api.copilot.local import LocalProvider, LoopbackEndpoint
from harness_api.copilot.provider import (
    TextDelta,
    ToolResult,
    ToolSpec,
    TurnEnd,
    create_provider,
)
from harness_worker.errors import ConfigurationError, PolicyError, ProviderError

MODEL = "local-model"
STATUS = ToolSpec(
    name="object_status",
    description="Status of one object.",
    input_schema={
        "type": "object",
        "properties": {"owner": {"type": "string"}, "name": {"type": "string"}},
        "required": ["owner", "name"],
    },
)


def resolves_to(*addresses: str):
    def resolver(host: str, port: int) -> list[str]:
        return list(addresses)

    return resolver


def sse(*chunks: dict[str, Any]) -> bytes:
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def delta(content: Any = None, *, finish: str | None = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"content": content} if content is not None else {}
    body.update(extra)
    return {"model": MODEL, "choices": [{"index": 0, "delta": body, "finish_reason": finish}]}


def usage(prompt: int, completion: int) -> dict[str, Any]:
    return {
        "model": MODEL,
        "choices": [],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


class StandInServer:
    """Answers each request with the next canned response, and keeps what it was sent."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.content:
            self.bodies.append(json.loads(request.content))
        return self.responses.pop(0)


def provider(server: StandInServer, base_url: str = "http://127.0.0.1:8080/v1") -> LocalProvider:
    return LocalProvider(
        base_url,
        api_key="test-key",
        model=MODEL,
        transport=httpx.MockTransport(server),
    )


# -- where the rule is enforced --------------------------------------------------------


def test_settings_refuse_a_cloud_provider_while_local_only() -> None:
    with pytest.raises(SettingsError, match="HARNESS_COPILOT_LOCAL_ONLY is on"):
        Settings(copilot_provider="anthropic")


def test_local_only_is_the_default_and_admits_local_and_fake() -> None:
    assert Settings().copilot_local_only is True
    Settings(copilot_provider="local", copilot_base_url="http://127.0.0.1:8080/v1")
    Settings(copilot_provider="fake")


def test_turning_local_only_off_is_warned_about() -> None:
    settings = Settings(copilot_provider="anthropic", copilot_local_only=False)
    assert any("LOCAL_ONLY is off" in warning for warning in settings.startup_warnings())


def test_an_unknown_provider_is_refused() -> None:
    with pytest.raises(SettingsError, match="must be 'local', 'fake' or 'anthropic'"):
        Settings(copilot_provider="openai")


def test_the_factory_refuses_anthropic_unless_told_otherwise() -> None:
    with pytest.raises(PolicyError, match="LOCAL_ONLY is on"):
        create_provider("anthropic", api_key="k", model="m")


def test_startup_refuses_a_local_endpoint_off_this_host() -> None:
    settings = Settings(
        copilot_enabled=True,
        copilot_provider="local",
        copilot_base_url="http://192.0.2.10:8080/v1",
    )
    with pytest.raises(PolicyError, match="not on this machine"):
        settings.check_copilot_endpoint()


@pytest.mark.parametrize(
    "url",
    [
        "http://192.0.2.10:8080/v1",
        "http://0.0.0.0:8080/v1",
        "http://[2001:db8::1]:8080/v1",
        "http://[::ffff:192.0.2.10]:8080/v1",
        "http://10.0.0.5/v1",
    ],
)
def test_addresses_off_this_host_are_refused(url: str) -> None:
    with pytest.raises(PolicyError, match="not on this machine"):
        LoopbackEndpoint.resolve(url)


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:8080/v1", "http://127.8.9.10:8080/v1", "http://[::1]:8080/v1"]
)
def test_loopback_addresses_are_accepted(url: str) -> None:
    endpoint = LoopbackEndpoint.resolve(url)
    assert endpoint.url.port == 8080
    assert endpoint.url.path == "/v1/"


def test_a_name_must_resolve_only_to_loopback() -> None:
    with pytest.raises(PolicyError, match="203.0.113.7"):
        LoopbackEndpoint.resolve(
            "http://model.example:8080/v1", resolves_to("127.0.0.1", "203.0.113.7")
        )
    with pytest.raises(PolicyError, match="not on this machine"):
        LoopbackEndpoint.resolve("http://model.example:8080/v1", resolves_to())


def test_a_name_is_pinned_to_its_loopback_address() -> None:
    endpoint = LoopbackEndpoint.resolve("http://localhost:8080/v1", resolves_to("127.0.0.1"))
    assert endpoint.url.host == "127.0.0.1"
    assert endpoint.host_header == "localhost:8080"


@pytest.mark.parametrize(
    ("url", "error", "match"),
    [
        ("https://127.0.0.1:8080/v1", PolicyError, "plain http"),
        ("ftp://127.0.0.1/v1", PolicyError, "plain http"),
        ("http://user:pw@127.0.0.1:8080/v1", ConfigurationError, "no credentials"),
        ("http://127.0.0.1:8080/v1?x=1", ConfigurationError, "no credentials"),
        ("", ConfigurationError, "HARNESS_COPILOT_BASE_URL"),
    ],
)
def test_other_endpoint_shapes_are_refused(url: str, error: type, match: str) -> None:
    with pytest.raises(error, match=match):
        LoopbackEndpoint.resolve(url)


def test_the_local_provider_needs_a_model() -> None:
    with pytest.raises(ConfigurationError, match="HARNESS_COPILOT_MODEL"):
        LocalProvider("http://127.0.0.1:8080/v1", model="")


async def test_a_redirect_is_refused_not_followed() -> None:
    server = StandInServer(
        httpx.Response(307, headers={"Location": "http://203.0.113.7/v1/models"})
    )
    with pytest.raises(PolicyError, match="redirect"):
        await provider(server).check_access()
    assert len(server.requests) == 1


async def test_the_transport_refuses_a_request_to_another_host() -> None:
    server = StandInServer(httpx.Response(200, json={"data": []}))
    local = provider(server)
    with pytest.raises(PolicyError, match="other than the configured loopback"):
        await local._client.get("http://203.0.113.7:8080/v1/models")
    with pytest.raises(PolicyError, match="other than the configured loopback"):
        await local._client.get("http://127.0.0.1:9999/v1/models")
    assert server.requests == []


async def test_proxy_settings_in_the_environment_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://203.0.113.7:3128")
    server = StandInServer(httpx.Response(200, json={"data": [{"id": MODEL}]}))
    assert await provider(server).check_access() == MODEL
    assert server.requests[0].url.host == "127.0.0.1"


# -- the dialect -----------------------------------------------------------------------


async def test_access_check_sends_the_key_and_finds_the_model() -> None:
    server = StandInServer(
        httpx.Response(200, json={"data": [{"id": "other", "aliases": [MODEL]}]})
    )
    assert await provider(server).check_access() == "other"
    request = server.requests[0]
    assert str(request.url) == "http://127.0.0.1:8080/v1/models"
    assert request.headers["Authorization"] == "Bearer test-key"


async def test_access_check_reports_a_missing_model_and_a_rejected_key() -> None:
    with pytest.raises(ProviderError, match="lists other"):
        await provider(
            StandInServer(httpx.Response(200, json={"data": [{"id": "other"}]}))
        ).check_access()
    with pytest.raises(ProviderError, match="rejected the configured key"):
        await provider(StandInServer(httpx.Response(401, json={"error": {}}))).check_access()


async def test_an_unreachable_server_is_a_typed_provider_failure() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    local = LocalProvider(
        "http://127.0.0.1:8080/v1", model=MODEL, transport=httpx.MockTransport(refuse)
    )
    with pytest.raises(ProviderError, match="could not be reached"):
        await local.check_access()


async def test_a_single_turn_streams_text_and_reports_usage() -> None:
    server = StandInServer(
        httpx.Response(
            200,
            content=sse(
                delta("", role="assistant"),
                delta(None, reasoning_content="thinking it over"),
                delta("Hello, "),
                delta("world."),
                delta(None, finish="stop"),
                usage(40, 6),
            ),
        )
    )
    local = provider(server)
    text = [piece async for piece in local.stream("system text", "question")]
    assert "".join(text) == "Hello, world."
    used = local.usage()
    assert (used.provider, used.model, used.prompt_tokens, used.completion_tokens) == (
        "local",
        MODEL,
        40,
        6,
    )
    assert used.stop_reason == "end_turn"
    body = server.bodies[0]
    assert body["messages"] == [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": "question"},
    ]
    assert body["stream"] is True and "tools" not in body


async def test_a_tool_turn_merges_streamed_calls_and_carries_results_back() -> None:
    first = sse(
        delta(
            "Let me look.",
            tool_calls=[
                {
                    "index": 0,
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "object_status", "arguments": ""},
                }
            ],
        ),
        delta(None, tool_calls=[{"index": 0, "function": {"arguments": '{"owner": "HR",'}}]),
        delta(None, tool_calls=[{"index": 0, "function": {"arguments": ' "name": "EMP"}'}}]),
        delta(
            None,
            tool_calls=[
                {"index": 1, "function": {"name": "object_status", "arguments": "not json"}}
            ],
        ),
        delta(None, finish="tool_calls"),
        usage(100, 20),
    )
    second = sse(delta("It is valid."), delta(None, finish="stop"), usage(150, 5))
    server = StandInServer(httpx.Response(200, content=first), httpx.Response(200, content=second))
    conversation = provider(server).start_conversation("sys", "why?", [STATUS])

    events = [event async for event in conversation.turn()]
    end = events[-1]
    assert isinstance(end, TurnEnd) and end.stop_reason == "tool_use"
    assert [(call.id, call.input) for call in end.tool_calls] == [
        ("c1", {"owner": "HR", "name": "EMP"}),
        ("call_1", {}),
    ]
    assert server.bodies[0]["tools"][0]["function"]["parameters"] == STATUS.input_schema

    conversation.add_tool_results(
        [ToolResult("c1", "VALID"), ToolResult("call_1", "bad input", is_error=True)]
    )
    events = [event async for event in conversation.turn()]
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["It is valid."]
    assert conversation.finished

    sent = server.bodies[1]["messages"]
    assert sent[2]["role"] == "assistant"
    assert sent[2]["tool_calls"][0]["function"]["arguments"] == '{"owner": "HR", "name": "EMP"}'
    assert sent[3:] == [
        {"role": "tool", "tool_call_id": "c1", "content": "VALID"},
        {"role": "tool", "tool_call_id": "call_1", "content": "ERROR: bad input"},
    ]
    total = conversation.usage()
    assert (total.prompt_tokens, total.completion_tokens) == (250, 25)


async def test_a_turn_that_must_answer_chooses_no_tool() -> None:
    first = sse(
        delta(
            None,
            tool_calls=[
                {
                    "index": 0,
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "object_status", "arguments": "{}"},
                }
            ],
        ),
        delta(None, finish="tool_calls"),
    )
    second = sse(delta("It is valid."), delta(None, finish="stop"))
    server = StandInServer(httpx.Response(200, content=first), httpx.Response(200, content=second))
    conversation = provider(server).start_conversation("sys", "why?", [STATUS])
    [event async for event in conversation.turn()]
    conversation.add_tool_results([ToolResult("c1", "VALID")])
    conversation.require_answer()
    [event async for event in conversation.turn()]
    assert "tool_choice" not in server.bodies[0]
    # The transcript holds tool calls, so the tools stay described.
    assert server.bodies[1]["tools"] == server.bodies[0]["tools"]
    assert server.bodies[1]["tool_choice"] == "none"
    assert conversation.finished


async def test_a_truncated_turn_drops_its_tool_calls() -> None:
    body = sse(
        delta(
            None,
            tool_calls=[
                {"index": 0, "id": "c1", "function": {"name": "object_status", "arguments": "{"}}
            ],
        ),
        delta(None, finish="length"),
    )
    conversation = provider(StandInServer(httpx.Response(200, content=body))).start_conversation(
        "s", "u", [STATUS]
    )
    end = [event async for event in conversation.turn()][-1]
    assert isinstance(end, TurnEnd)
    assert end.truncated and not end.tool_calls and conversation.finished


async def test_server_errors_do_not_quote_the_server_back() -> None:
    echo = {"error": {"message": "prompt was: SECRET SOURCE TEXT"}}
    for response in (
        httpx.Response(500, json=echo),
        httpx.Response(200, content=sse(echo)),
    ):
        conversation = provider(StandInServer(response)).start_conversation("s", "u", ())
        with pytest.raises(ProviderError) as caught:
            [event async for event in conversation.turn()]
        assert "SECRET" not in caught.value.message
        assert "SECRET" not in json.dumps(caught.value.detail)
