"""Model provider adapters.

The provider boundary is deliberately narrow: assemble a system prompt and one user
message, stream text back, and report usage. Provider credentials are resolved from a
server-side secret reference and never appear in a record or a log line.

Two adapters ship. ``anthropic`` calls the Claude Messages API. ``fake`` returns
deterministic fixture answers so the whole copilot path -- authorisation, context
policy, streaming, proposals, stale-diff rejection, budgets -- can be tested and
demonstrated without calling a model or spending anything.
"""

from __future__ import annotations

import abc
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

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

    def __init__(self, model: str = "fixture") -> None:
        self._model = model
        self._usage = ProviderUsage(provider=self.name, model=model)

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
    ) -> None:
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
        # anything accounting for spend has to know how many attempts one call may make.
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
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                f"The model provider returned {exc.status_code}. Database workflows are "
                "unaffected.",
                detail={"status": exc.status_code},
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(
                "The model provider could not be reached. Database workflows are unaffected."
            ) from exc

        self._usage = ProviderUsage(
            provider=self.name,
            model=final.model,
            prompt_tokens=final.usage.input_tokens,
            completion_tokens=final.usage.output_tokens,
            stop_reason=final.stop_reason or "",
            extra={
                "cacheReadInputTokens": getattr(final.usage, "cache_read_input_tokens", None),
                "cacheCreationInputTokens": getattr(
                    final.usage, "cache_creation_input_tokens", None
                ),
            },
        )
        if final.stop_reason == "refusal":
            raise ProviderError(
                "The model declined to answer this request.",
                detail={
                    "category": getattr(getattr(final, "stop_details", None), "category", None)
                },
            )

    def usage(self) -> ProviderUsage:
        return self._usage


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
