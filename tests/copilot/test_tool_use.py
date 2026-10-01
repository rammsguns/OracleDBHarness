"""Provider tool use (KIWI_PLAN.md, K-2).

The provider reports which tools the model asked for and carries the answers back; it
never runs a tool. These tests cover the turn protocol, stop reasons, usage per turn
and failure partway through a loop, for the fixture provider and for the Anthropic
adapter against a stand-in client. No model is called.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

from harness_api.copilot.provider import (
    AnthropicProvider,
    FakeProvider,
    ScriptedTurn,
    TextDelta,
    ToolCall,
    ToolConversation,
    ToolResult,
    ToolSpec,
    TurnEnd,
)
from harness_worker.errors import ConfigurationError, ProviderError

STATUS = ToolSpec(
    name="object_status",
    description="Status of one object.",
    input_schema={
        "type": "object",
        "properties": {"owner": {"type": "string"}, "name": {"type": "string"}},
        "required": ["owner", "name"],
    },
)
ERRORS = ToolSpec(
    name="object_errors",
    description="Compilation errors for one object.",
    input_schema={"type": "object", "properties": {"name": {"type": "string"}}},
)


async def run_turn(conversation: ToolConversation) -> tuple[str, TurnEnd]:
    text: list[str] = []
    end: TurnEnd | None = None
    async for event in conversation.turn():
        if isinstance(event, TextDelta):
            text.append(event.text)
        else:
            end = event
    assert end is not None
    return "".join(text), end


# -- tool specs ---------------------------------------------------------------------


@pytest.mark.parametrize("name", ["schema.object_status", "", "x" * 65, "has space"])
def test_tool_names_the_api_would_reject_are_refused_early(name: str) -> None:
    with pytest.raises(ConfigurationError):
        ToolSpec(name=name, description="", input_schema={"type": "object"})


def test_a_tool_needs_an_object_schema() -> None:
    with pytest.raises(ConfigurationError):
        ToolSpec(name="t", description="", input_schema={"type": "string"})


def test_duplicate_tool_names_are_refused() -> None:
    with pytest.raises(ConfigurationError):
        FakeProvider().start_conversation("s", "u", [STATUS, STATUS])


def test_a_provider_without_tool_use_says_so() -> None:
    from harness_api.copilot.provider import Provider, ProviderUsage

    class TextOnly(Provider):
        name = "text-only"

        async def stream(self, system, user_message):
            yield ""

        def usage(self) -> ProviderUsage:
            return ProviderUsage()

        @property
        def ready(self) -> bool:
            return True

    with pytest.raises(ConfigurationError, match="does not support tool use"):
        TextOnly().start_conversation("s", "u", [STATUS])


# -- fixture provider ---------------------------------------------------------------


async def test_without_a_script_the_fixture_answers_in_one_turn() -> None:
    conversation = FakeProvider().start_conversation(
        "system", "Explain this plan: full scan", [STATUS]
    )
    text, end = await run_turn(conversation)
    assert "optimizer estimates" in text
    assert end.stop_reason == "end_turn"
    assert not end.wants_tools
    assert conversation.finished


async def test_a_scripted_tool_loop_carries_results_back() -> None:
    call = ToolCall(id="c1", name="object_status", input={"owner": "HR", "name": "PKG"})
    provider = FakeProvider(
        script=[
            ScriptedTurn(text="Checking the status first.", tool_calls=(call,)),
            ScriptedTurn(text="PKG is INVALID."),
        ]
    )
    conversation = provider.start_conversation("system", "why is PKG broken?", [STATUS])

    text, end = await run_turn(conversation)
    assert text == "Checking the status first."
    assert end.stop_reason == "tool_use"
    assert end.tool_calls == (call,)
    assert conversation.pending_calls == (call,)
    assert not conversation.finished

    conversation.add_tool_results([ToolResult(call_id="c1", content="INVALID")])
    assert conversation.received == [ToolResult(call_id="c1", content="INVALID")]

    text, end = await run_turn(conversation)
    assert text == "PKG is INVALID."
    assert end.stop_reason == "end_turn"
    assert conversation.finished


async def test_several_calls_in_one_turn_are_answered_in_call_order() -> None:
    first = ToolCall(id="a", name="object_status", input={"owner": "HR", "name": "P"})
    second = ToolCall(id="b", name="object_errors", input={"name": "P"})
    conversation = FakeProvider(
        script=[ScriptedTurn(tool_calls=(first, second)), ScriptedTurn(text="done")]
    ).start_conversation("s", "u", [STATUS, ERRORS])
    await run_turn(conversation)

    conversation.add_tool_results(
        [ToolResult(call_id="b", content="PLS-00201", is_error=False), ToolResult("a", "VALID")]
    )
    assert [result.call_id for result in conversation.received] == ["a", "b"]


@pytest.mark.parametrize(
    "results",
    [
        [],
        [ToolResult("a", "x")],
        [ToolResult("a", "x"), ToolResult("a", "y"), ToolResult("b", "z")],
        [ToolResult("a", "x"), ToolResult("b", "y"), ToolResult("c", "z")],
    ],
    ids=["none", "missing", "duplicate", "unknown"],
)
async def test_results_must_answer_each_pending_call_exactly_once(
    results: list[ToolResult],
) -> None:
    calls = (ToolCall("a", "object_status", {}), ToolCall("b", "object_errors", {}))
    conversation = FakeProvider(script=[ScriptedTurn(tool_calls=calls)]).start_conversation(
        "s", "u", [STATUS, ERRORS]
    )
    await run_turn(conversation)
    with pytest.raises(ValueError):
        conversation.add_tool_results(results)
    assert conversation.pending_calls == calls


async def test_the_next_turn_waits_for_pending_results() -> None:
    call = ToolCall("a", "object_status", {})
    conversation = FakeProvider(
        script=[ScriptedTurn(tool_calls=(call,)), ScriptedTurn(text="x")]
    ).start_conversation("s", "u", [STATUS])
    await run_turn(conversation)
    with pytest.raises(RuntimeError, match="pending tool calls"):
        await run_turn(conversation)


async def test_results_are_refused_when_nothing_is_pending() -> None:
    conversation = FakeProvider(script=[ScriptedTurn(text="x")]).start_conversation(
        "s", "u", [STATUS]
    )
    await run_turn(conversation)
    with pytest.raises(ValueError):
        conversation.add_tool_results([ToolResult("a", "x")])


async def test_there_is_no_turn_after_the_final_one() -> None:
    conversation = FakeProvider(script=[ScriptedTurn(text="x")]).start_conversation(
        "s", "u", [STATUS]
    )
    await run_turn(conversation)
    with pytest.raises(RuntimeError, match="ended"):
        await run_turn(conversation)


# -- stop reasons -------------------------------------------------------------------


async def test_a_truncated_turn_drops_its_tool_calls() -> None:
    """A call cut off by the output limit may be incomplete; it is never offered."""

    call = ToolCall("a", "object_status", {"owner": "HR"})
    conversation = FakeProvider(
        script=[ScriptedTurn(text="partial", tool_calls=(call,), stop_reason="max_tokens")]
    ).start_conversation("s", "u", [STATUS])
    _, end = await run_turn(conversation)
    assert end.truncated
    assert end.tool_calls == ()
    assert conversation.finished


async def test_a_refusal_is_a_provider_failure_that_still_counts_usage() -> None:
    conversation = FakeProvider(
        script=[ScriptedTurn(text="", stop_reason="refusal")]
    ).start_conversation("s", "u", [STATUS])
    with pytest.raises(ProviderError, match="declined"):
        await run_turn(conversation)
    assert conversation.finished
    usage = conversation.usage()
    assert usage.extra == {"turns": 1, "complete": True}
    assert usage.stop_reason == "refusal"


# -- usage --------------------------------------------------------------------------


async def test_usage_is_kept_per_turn_and_summed() -> None:
    call = ToolCall("a", "object_status", {})
    conversation = FakeProvider(
        script=[ScriptedTurn(text="look", tool_calls=(call,)), ScriptedTurn(text="answer")]
    ).start_conversation("system prompt", "question", [STATUS])
    await run_turn(conversation)
    conversation.add_tool_results([ToolResult("a", "VALID " * 40)])
    await run_turn(conversation)

    turns = conversation.turn_usage()
    assert [turn.stop_reason for turn in turns] == ["tool_use", "end_turn"]
    # The second turn resends everything, including the tool result.
    assert turns[1].prompt_tokens > turns[0].prompt_tokens
    total = conversation.usage()
    assert total.prompt_tokens == sum(turn.prompt_tokens for turn in turns)
    assert total.completion_tokens == sum(turn.completion_tokens for turn in turns)
    assert total.extra == {"turns": 2, "complete": True}
    assert total.stop_reason == "end_turn"


async def test_a_failure_mid_loop_keeps_earlier_usage_and_marks_it_incomplete() -> None:
    call = ToolCall("a", "object_status", {})
    conversation = FakeProvider(
        script=[
            ScriptedTurn(text="look", tool_calls=(call,)),
            ScriptedTurn(text="half an ans", fail="connection dropped"),
        ]
    ).start_conversation("s", "u", [STATUS])
    _, first = await run_turn(conversation)
    conversation.add_tool_results([ToolResult("a", "VALID")])

    with pytest.raises(ProviderError, match="connection dropped"):
        await run_turn(conversation)

    assert conversation.finished
    total = conversation.usage()
    assert total.prompt_tokens == first.usage.prompt_tokens
    assert total.extra == {"turns": 1, "complete": False}
    with pytest.raises(RuntimeError):
        await run_turn(conversation)


async def test_an_abandoned_turn_ends_the_conversation() -> None:
    conversation = FakeProvider(script=[ScriptedTurn(text="x" * 500)]).start_conversation(
        "s", "u", [STATUS]
    )
    events = conversation.turn()
    assert isinstance(await anext(events), TextDelta)
    await events.aclose()
    assert conversation.finished
    assert conversation.usage().extra == {"turns": 0, "complete": False}


# -- Anthropic adapter, against a stand-in client -----------------------------------


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(status_code)
        self.status_code = status_code


class _ConnectionError(Exception):
    pass


@pytest.fixture
def anthropic_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter names the SDK's error classes; the default suite has no SDK."""

    module = types.ModuleType("anthropic")
    module.APIStatusError = _StatusError  # type: ignore[attr-defined]
    module.APIConnectionError = _ConnectionError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", module)


def block(kind: str, **fields: Any) -> types.SimpleNamespace:
    return types.SimpleNamespace(type=kind, **fields)


def message(content: list, stop_reason: str, input_tokens: int, output_tokens: int):
    return types.SimpleNamespace(
        model="claude-test",
        content=content,
        stop_reason=stop_reason,
        stop_details=None,
        usage=types.SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


@dataclass
class StandInClient:
    """Records each request and replays one prepared response per call."""

    responses: list[Any]
    requests: list[dict] = field(default_factory=list)

    @property
    def messages(self) -> StandInClient:
        return self

    def stream(self, **request: Any) -> _Stream:
        # Snapshot the transcript: the adapter keeps appending to the same list.
        self.requests.append({**request, "messages": list(request["messages"])})
        return _Stream(self.responses.pop(0))


class _Stream:
    def __init__(self, response: Any) -> None:
        self._response = response

    async def __aenter__(self) -> _Stream:
        if isinstance(self._response, Exception):
            raise self._response
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    @property
    async def text_stream(self):
        for item in self._response.content:
            if item.type == "text":
                yield item.text

    async def get_final_message(self):
        return self._response


def anthropic(client: StandInClient) -> AnthropicProvider:
    return AnthropicProvider(api_key="", model="claude-test", max_tokens=1000, client=client)


async def test_anthropic_offers_tools_and_resends_the_assistant_turn_unchanged(
    anthropic_errors: None,
) -> None:
    thinking = block("thinking", thinking="...", signature="sig")
    use = block("tool_use", id="toolu_1", name="object_status", input={"owner": "HR"})
    client = StandInClient(
        responses=[
            message([thinking, block("text", text="Checking."), use], "tool_use", 100, 20),
            message([block("text", text="It is INVALID.")], "end_turn", 180, 10),
        ]
    )
    conversation = anthropic(client).start_conversation("sys", "why?", [STATUS])

    text, end = await run_turn(conversation)
    assert text == "Checking."
    assert end.tool_calls == (ToolCall("toolu_1", "object_status", {"owner": "HR"}),)

    first = client.requests[0]
    assert first["system"] == "sys"
    assert first["thinking"] == {"type": "adaptive"}
    assert first["tools"] == [
        {
            "name": "object_status",
            "description": STATUS.description,
            "input_schema": STATUS.input_schema,
        }
    ]
    assert first["messages"] == [{"role": "user", "content": "why?"}]

    conversation.add_tool_results([ToolResult("toolu_1", "INVALID", is_error=False)])
    text, end = await run_turn(conversation)
    assert text == "It is INVALID."
    assert end.stop_reason == "end_turn"

    second = client.requests[1]["messages"]
    assert second[1]["role"] == "assistant"
    # The same objects, thinking block and signature included.
    assert second[1]["content"][0] is thinking
    assert second[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": "INVALID",
                "is_error": False,
            }
        ],
    }

    total = conversation.usage()
    assert (total.prompt_tokens, total.completion_tokens) == (280, 30)
    assert total.model == "claude-test"
    assert total.extra == {"turns": 2, "complete": True}


async def test_anthropic_failure_on_a_later_turn_is_a_typed_provider_failure(
    anthropic_errors: None,
) -> None:
    use = block("tool_use", id="t", name="object_status", input={})
    client = StandInClient(responses=[message([use], "tool_use", 50, 5), _StatusError(529)])
    conversation = anthropic(client).start_conversation("s", "u", [STATUS])
    await run_turn(conversation)
    conversation.add_tool_results([ToolResult("t", "VALID")])

    with pytest.raises(ProviderError) as raised:
        await run_turn(conversation)
    assert raised.value.detail == {"status": 529}
    assert "Database workflows are unaffected" in raised.value.message
    assert conversation.usage().extra == {"turns": 1, "complete": False}
    assert conversation.usage().prompt_tokens == 50


async def test_anthropic_unreachable_is_a_typed_provider_failure(
    anthropic_errors: None,
) -> None:
    conversation = anthropic(StandInClient(responses=[_ConnectionError()])).start_conversation(
        "s", "u", [STATUS]
    )
    with pytest.raises(ProviderError, match="could not be reached"):
        await run_turn(conversation)


async def test_anthropic_refusal_ends_the_conversation(anthropic_errors: None) -> None:
    conversation = anthropic(
        StandInClient(responses=[message([], "refusal", 10, 0)])
    ).start_conversation("s", "u", [STATUS])
    with pytest.raises(ProviderError, match="declined"):
        await run_turn(conversation)
    assert conversation.finished


async def test_anthropic_tool_calls_in_a_truncated_turn_are_dropped(
    anthropic_errors: None,
) -> None:
    use = block("tool_use", id="t", name="object_status", input={"owner": "H"})
    conversation = anthropic(
        StandInClient(responses=[message([use], "max_tokens", 10, 1000)])
    ).start_conversation("s", "u", [STATUS])
    _, end = await run_turn(conversation)
    assert end.truncated and end.tool_calls == ()
    assert conversation.finished


async def test_the_single_turn_stream_is_unchanged(anthropic_errors: None) -> None:
    client = StandInClient(responses=[message([block("text", text="hi")], "end_turn", 3, 1)])
    provider = anthropic(client)
    chunks = [chunk async for chunk in provider.stream("s", "u")]
    assert chunks == ["hi"]
    assert "tools" not in client.requests[0]
    assert provider.usage().prompt_tokens == 3
