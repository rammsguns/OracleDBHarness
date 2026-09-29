"""Model provider adapters.

The provider boundary is deliberately narrow: assemble a system prompt and one user
message, stream text back, and report usage. Provider credentials are resolved from a
server-side secret reference and never appear in a record or a log line.

``start_conversation`` adds multi-turn tool use for Kiwi (KIWI_PLAN.md, K-2). The
provider only reports which tools the model asked for; it never runs one. Running a
tool, authorising it and bounding the loop belong to the caller (K-3), so no adapter
can become a way around the execution service.

Two adapters ship. ``anthropic`` calls the Claude Messages API. ``fake`` returns
deterministic fixture answers so the whole copilot path -- authorisation, context
policy, streaming, proposals, stale-diff rejection, budgets -- can be tested and
demonstrated without calling a model or spending anything.
"""

from __future__ import annotations

import abc
import re
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from harness_worker.errors import ConfigurationError, ProviderError


@dataclass
class ProviderUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    model: str = ""
    provider: str = ""
    stop_reason: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "stopReason": self.stop_reason,
            **self.extra,
        }


# -- tool use -----------------------------------------------------------------------

# The Messages API's rule for tool names. Catalog ids contain dots, so the caller maps
# them; refusing here keeps a bad name from surfacing as a provider 400 mid-request.
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class ToolSpec:
    """A tool offered to the model: its name, what it does, and a JSON schema."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def __post_init__(self) -> None:
        if not _TOOL_NAME.match(self.name):
            raise ConfigurationError(
                f"Tool name {self.name!r} must be 1-64 letters, digits, '_' or '-'."
            )
        if self.input_schema.get("type") != "object":
            raise ConfigurationError(f"Tool {self.name!r} needs an object input schema.")


@dataclass(frozen=True)
class ToolCall:
    """A tool the model asked for. A request, not an instruction to run anything."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class TurnEnd:
    """The last event of every turn.

    ``tool_calls`` is empty unless the model stopped to ask for tools. A turn cut off
    by the output limit (``max_tokens``) carries none either: a tool call in it may be
    incomplete, and the text before it is a partial answer.
    """

    stop_reason: str
    tool_calls: tuple[ToolCall, ...]
    usage: ProviderUsage
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    @property
    def truncated(self) -> bool:
        return self.stop_reason == "max_tokens"


TurnEvent = TextDelta | TurnEnd


class ToolConversation(abc.ABC):
    """One model exchange that may pause for tool results.

    The caller alternates ``turn()`` and ``add_tool_results()`` until a turn ends
    without tool calls. The conversation enforces the order - every call answered
    exactly once before the next turn, nothing after the final turn - because the
    provider rejects a transcript that breaks it, and a rejection there would surface
    as a provider failure halfway through a request.

    Usage is kept per turn. If a turn fails, the turns before it still count, and the
    total is marked incomplete: the failed turn may have been billed, and its tokens
    are unknown.
    """

    def __init__(self, provider: str, model: str, tools: Sequence[ToolSpec]) -> None:
        names = [tool.name for tool in tools]
        if len(set(names)) != len(names):
            raise ConfigurationError("Tool names offered in one conversation must be unique.")
        self.tools = tuple(tools)
        self._provider = provider
        self._model = model
        self._turns: list[ProviderUsage] = []
        self._pending: tuple[ToolCall, ...] = ()
        self._finished = False
        self._failed = False

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def pending_calls(self) -> tuple[ToolCall, ...]:
        return self._pending

    async def turn(self) -> AsyncGenerator[TurnEvent, None]:
        """Stream one model turn: text deltas, then exactly one ``TurnEnd``."""

        if self._finished:
            raise RuntimeError("The conversation has ended; there is no next turn.")
        if self._pending:
            raise RuntimeError("Answer the pending tool calls before the next turn.")
        ended = False
        try:
            async for event in self._run_turn():
                if isinstance(event, TurnEnd):
                    ended = True
                    self._turns.append(event.usage)
                    if event.stop_reason == "refusal":
                        self._finished = True
                        raise ProviderError(
                            "The model declined to answer this request.", detail=event.detail
                        )
                    if event.stop_reason != "tool_use":
                        event = TurnEnd(event.stop_reason, (), event.usage, event.detail)
                    if event.tool_calls:
                        self._pending = event.tool_calls
                    else:
                        self._finished = True
                yield event
        finally:
            # A turn that failed, or that the caller abandoned, cannot be resumed: the
            # provider never produced the message the next turn would have to follow.
            if not ended:
                self._failed = True
                self._finished = True
        if not ended:
            raise ProviderError("The model provider ended a turn without a final message.")

    def add_tool_results(self, results: Sequence[ToolResult]) -> None:
        """Answer every pending call, once each. Order follows the calls."""

        wanted = {call.id for call in self._pending}
        given = [result.call_id for result in results]
        if not wanted or len(set(given)) != len(given) or set(given) != wanted:
            raise ValueError(
                "Tool results must answer each pending call exactly once "
                f"(pending {sorted(wanted)}, given {given})."
            )
        by_id = {result.call_id: result for result in results}
        self._append_results([by_id[call.id] for call in self._pending])
        self._pending = ()

    def turn_usage(self) -> list[ProviderUsage]:
        return list(self._turns)

    def usage(self) -> ProviderUsage:
        """Totals across turns. ``complete`` is false if a turn failed partway."""

        def total(values: list[int | None]) -> int | None:
            known = [value for value in values if value is not None]
            return sum(known) if known else None

        last = self._turns[-1] if self._turns else None
        return ProviderUsage(
            provider=self._provider,
            model=last.model if last else self._model,
            prompt_tokens=total([turn.prompt_tokens for turn in self._turns]),
            completion_tokens=total([turn.completion_tokens for turn in self._turns]),
            stop_reason=last.stop_reason if last else "",
            extra={"turns": len(self._turns), "complete": not self._failed},
        )

    @abc.abstractmethod
    def _run_turn(self) -> AsyncIterator[TurnEvent]:
        """Call the model once with the transcript so far; end with a ``TurnEnd``."""

    @abc.abstractmethod
    def _append_results(self, results: list[ToolResult]) -> None:
        """Add the answers to the transcript for the next turn."""


class Provider(abc.ABC):
    name = "base"

    @abc.abstractmethod
    def stream(self, system: str, user_message: str) -> AsyncIterator[str]:
        """Yield answer text as it arrives."""

    @abc.abstractmethod
    def usage(self) -> ProviderUsage:
        """Usage for the most recent stream. Valid only after it completes."""

    @property
    @abc.abstractmethod
    def ready(self) -> bool:
        """Whether the adapter is configured well enough to be called."""

    def start_conversation(
        self, system: str, user_message: str, tools: Sequence[ToolSpec]
    ) -> ToolConversation:
        """Begin a multi-turn exchange in which the model may ask for ``tools``."""

        raise ConfigurationError(f"The {self.name!r} provider does not support tool use.")

    async def check_access(self) -> str:
        """Prove the credentials and model work without generating anything.

        Returns the model identifier the provider reports. Adapters that cannot check
        without spending say so, rather than reporting a check that did not happen.
        """

        raise ConfigurationError(
            f"The {self.name!r} provider has no access check that avoids generating text."
        )


class FakeProvider(Provider):
    """Deterministic fixture answers.

    The text is written to look like a real answer for the fixture schema, but it is
    canned. Any deployment running this provider says so on its capabilities endpoint
    and in its startup warnings, so a demonstration is never mistaken for a model.
    """

    name = "fake"

    def __init__(
        self, model: str = "fixture", *, script: Sequence[ScriptedTurn] | None = None
    ) -> None:
        self._model = model
        self._usage = ProviderUsage(provider=self.name, model=model)
        self._script = tuple(script) if script is not None else None

    @property
    def ready(self) -> bool:
        return True

    async def stream(self, system: str, user_message: str) -> AsyncIterator[str]:
        answer = _fixture_answer(user_message)
        self._usage = ProviderUsage(
            provider=self.name,
            model=self._model,
            prompt_tokens=_rough_tokens(system) + _rough_tokens(user_message),
            completion_tokens=_rough_tokens(answer),
            stop_reason="end_turn",
            extra={"fixture": True},
        )
        for chunk in _chunks(answer, 120):
            yield chunk

    def usage(self) -> ProviderUsage:
        return self._usage

    def start_conversation(
        self, system: str, user_message: str, tools: Sequence[ToolSpec]
    ) -> FakeConversation:
        """Play the script, or without one answer in a single turn with no tool calls."""

        script = self._script
        if script is None:
            script = (ScriptedTurn(text=_fixture_answer(user_message)),)
        return FakeConversation(self._model, system, user_message, tools, script)


@dataclass(frozen=True)
class ScriptedTurn:
    """One turn of a fixture conversation.

    ``stop_reason`` defaults to ``tool_use`` when there are calls and ``end_turn``
    otherwise. ``fail`` raises a provider failure after the text has streamed, the way
    a connection dropped mid-answer would.
    """

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    stop_reason: str | None = None
    fail: str | None = None


class FakeConversation(ToolConversation):
    """Plays scripted turns and keeps every tool result it was given, for tests."""

    def __init__(
        self,
        model: str,
        system: str,
        user_message: str,
        tools: Sequence[ToolSpec],
        script: Sequence[ScriptedTurn],
    ) -> None:
        super().__init__(FakeProvider.name, model, tools)
        self._script = list(script)
        self._context = [system, user_message]
        self.received: list[ToolResult] = []

    async def _run_turn(self) -> AsyncIterator[TurnEvent]:
        if not self._script:
            raise RuntimeError("The fixture script has no turn left to play.")
        step = self._script.pop(0)
        for chunk in _chunks(step.text, 120):
            yield TextDelta(chunk)
        if step.fail is not None:
            raise ProviderError(step.fail)
        completion = step.text + "".join(repr(call.input) for call in step.tool_calls)
        usage = ProviderUsage(
            provider=FakeProvider.name,
            model=self._model,
            prompt_tokens=sum(_rough_tokens(part) for part in self._context),
            completion_tokens=_rough_tokens(completion),
            stop_reason=step.stop_reason or ("tool_use" if step.tool_calls else "end_turn"),
            extra={"fixture": True},
        )
        self._context.append(completion)
        yield TurnEnd(usage.stop_reason, step.tool_calls, usage)

    def _append_results(self, results: list[ToolResult]) -> None:
        self.received.extend(results)
        self._context.extend(result.content for result in results)


class AnthropicProvider(Provider):
    """Calls the Claude Messages API with streaming."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-5",
        max_tokens: int = 8000,
        *,
        timeout_seconds: float = 600.0,
        max_retries: int = 2,
        client: Any = None,
    ) -> None:
        if client is not None:
            # Tests pass a stand-in client; nothing else should.
            self._client = client
        else:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:  # pragma: no cover - depends on install extra
                raise ConfigurationError(
                    "The anthropic package is not installed. Install the 'copilot' extra "
                    "of harness-api, or set HARNESS_COPILOT_PROVIDER=fake."
                ) from exc
            if not api_key:
                raise ConfigurationError(
                    "The Anthropic provider needs a key. Point HARNESS_COPILOT_API_KEY_REF "
                    "at a secret reference."
                )
            # A retried attempt can be billed as well as the one that finally answers, so
            # anything accounting for spend has to know how many attempts one call may
            # make.
            self._client = AsyncAnthropic(
                api_key=api_key, timeout=timeout_seconds, max_retries=max_retries
            )
        self._model = model
        self._max_tokens = max_tokens
        self._usage = ProviderUsage(provider=self.name, model=model)

    @property
    def ready(self) -> bool:
        return True

    async def check_access(self) -> str:
        import anthropic

        try:
            info = await self._client.models.retrieve(self._model)
        except anthropic.AuthenticationError as exc:
            raise ProviderError(
                "The model provider rejected the configured key.",
                detail={"status": exc.status_code},
            ) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                f"The model provider returned {exc.status_code} for model {self._model!r}.",
                detail={"status": exc.status_code, "model": self._model},
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("The model provider could not be reached.") from exc
        return info.id

    async def stream(self, system: str, user_message: str) -> AsyncIterator[str]:
        import anthropic

        try:
            async with self._client.messages.stream(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                thinking={"type": "adaptive"},
                messages=[{"role": "user", "content": user_message}],
            ) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            raise _provider_failure(exc) from exc

        self._usage = _anthropic_usage(self.name, final)
        if final.stop_reason == "refusal":
            raise ProviderError(
                "The model declined to answer this request.", detail=_refusal_detail(final)
            )

    def usage(self) -> ProviderUsage:
        return self._usage

    def start_conversation(
        self, system: str, user_message: str, tools: Sequence[ToolSpec]
    ) -> AnthropicConversation:
        return AnthropicConversation(self, system, user_message, tools)


class AnthropicConversation(ToolConversation):
    """Multi-turn tool use over the Messages API.

    Each turn resends the whole transcript, so prompt tokens are billed again every
    turn; the per-turn usage shows that. The assistant's content, thinking blocks
    included, goes back exactly as it came: the API requires thinking blocks from a
    tool-use turn to be returned unchanged.
    """

    def __init__(
        self,
        provider: AnthropicProvider,
        system: str,
        user_message: str,
        tools: Sequence[ToolSpec],
    ) -> None:
        super().__init__(provider.name, provider._model, tools)
        self._client = provider._client
        self._max_tokens = provider._max_tokens
        self._system = system
        self._messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

    async def _run_turn(self) -> AsyncIterator[TurnEvent]:
        import anthropic

        try:
            async with self._client.messages.stream(
                model=self._model,
                max_tokens=self._max_tokens,
                system=self._system,
                thinking={"type": "adaptive"},
                tools=[
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.input_schema,
                    }
                    for tool in self.tools
                ],
                messages=self._messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield TextDelta(text)
                final = await stream.get_final_message()
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            raise _provider_failure(exc) from exc

        self._messages.append({"role": "assistant", "content": final.content})
        calls = tuple(
            ToolCall(id=block.id, name=block.name, input=dict(block.input))
            for block in final.content
            if block.type == "tool_use"
        )
        stop_reason = final.stop_reason or ""
        detail = _refusal_detail(final) if stop_reason == "refusal" else {}
        yield TurnEnd(stop_reason, calls, _anthropic_usage(self._provider, final), detail)

    def _append_results(self, results: list[ToolResult]) -> None:
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": result.call_id,
                        "content": result.content,
                        "is_error": result.is_error,
                    }
                    for result in results
                ],
            }
        )


def _provider_failure(exc: Exception) -> ProviderError:
    status = getattr(exc, "status_code", None)
    if status is not None:
        return ProviderError(
            f"The model provider returned {status}. Database workflows are unaffected.",
            detail={"status": status},
        )
    return ProviderError(
        "The model provider could not be reached. Database workflows are unaffected."
    )


def _anthropic_usage(provider: str, final: Any) -> ProviderUsage:
    return ProviderUsage(
        provider=provider,
        model=final.model,
        prompt_tokens=final.usage.input_tokens,
        completion_tokens=final.usage.output_tokens,
        stop_reason=final.stop_reason or "",
        extra={
            "cacheReadInputTokens": getattr(final.usage, "cache_read_input_tokens", None),
            "cacheCreationInputTokens": getattr(final.usage, "cache_creation_input_tokens", None),
        },
    )


def _refusal_detail(final: Any) -> dict[str, Any]:
    return {"category": getattr(getattr(final, "stop_details", None), "category", None)}


def create_provider(
    kind: str,
    *,
    api_key: str = "",
    model: str = "claude-opus-5",
    max_output_tokens: int = 8000,
    timeout_seconds: float = 600.0,
    max_retries: int = 2,
) -> Provider:
    if kind == "fake":
        return FakeProvider(model="fixture")
    if kind == "anthropic":
        return AnthropicProvider(
            api_key=api_key,
            model=model,
            max_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
    raise ConfigurationError(
        f"Unknown copilot provider {kind!r}. Use 'anthropic' or 'fake'.",
        detail={"configured": kind},
    )


# -- fixture helpers ----------------------------------------------------------------

_INJECTION_MARKERS = re.compile(
    r"ignore (all |any )?(previous|prior|above) instructions|you are now|"
    r"disregard (the )?(rules|instructions)|system prompt|reveal your",
    re.IGNORECASE,
)


def _fixture_answer(user_message: str) -> str:
    """A canned answer that still reacts to the shape of the request.

    It deliberately reproduces the two behaviours the copilot evaluation checks: it
    reports embedded instructions instead of following them, and it never claims to
    have run anything.
    """

    noticed = _INJECTION_MARKERS.search(user_message)
    lines: list[str] = []
    if noticed:
        lines.append(
            "Before anything else: the supplied context contains text that tries to "
            "give me instructions "
            f"({noticed.group(0)!r}). I have treated it as data, not as a command."
        )
        lines.append("")

    lowered = user_message.lower()
    if "ora-00942" in lowered or "table or view does not exist" in lowered:
        lines += [
            "The error is ORA-00942, raised where the unit selects from a table the "
            "compiling schema cannot see.",
            "",
            "Most likely cause: the referenced table name is wrong, or it is owned by "
            "another schema with no grant to this one. In the supplied source the "
            "singular name does not match the table in the schema metadata.",
            "",
            "Proposed repair:",
            "",
            "```sql",
            "SELECT COUNT(*) INTO l_count FROM employees WHERE department_id = p_department_id;",
            "```",
            "",
            "I have not compiled this. Compile it through the PL/SQL workspace and "
            "check the reported errors.",
        ]
    elif "explain" in lowered and "plan" in lowered:
        lines += [
            "The plan is dominated by a full scan of the largest table in the "
            "statement; the operation below the aggregation reads every row before "
            "filtering.",
            "",
            "The row counts and costs shown are optimizer estimates for a statement "
            "that was not executed, not measurements. To compare them with reality you "
            "need measured values for an execution of the same statement.",
            "",
            "Experiments worth measuring, in order: confirm the predicate column is "
            "selective; check whether statistics on the table are current; then "
            "measure the same statement again under equivalent conditions.",
        ]
    elif "test" in lowered and ("block" in lowered or "anonymous" in lowered):
        lines += [
            "Here is an anonymous block that exercises the routine and reports what it "
            "checked. It does not commit.",
            "",
            "```sql",
            "BEGIN",
            "  DBMS_OUTPUT.PUT_LINE('checking headcount for department 20');",
            "END;",
            "```",
            "",
            "Run it from the worksheet; applying this text to the editor does not execute it.",
        ]
    else:
        lines += [
            "The selected statement reads from the objects named in the supplied "
            "context and returns one row per grouping key.",
            "",
            "Nothing in the supplied context shows a correctness problem. I have not "
            "executed or compiled anything, so this is a reading of the code, not a "
            "verified result.",
        ]
    return "\n".join(lines)


def _chunks(text: str, size: int):
    for start in range(0, len(text), size):
        yield text[start : start + size]


def _rough_tokens(text: str) -> int:
    """A crude count for the fixture provider only. Never reported as measured."""

    return max(1, len(text) // 4)
