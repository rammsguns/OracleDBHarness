"""A model served on this machine, over the OpenAI-style Chat Completions API.

``HARNESS_COPILOT_LOCAL_ONLY`` (on by default) makes this the only real provider the
copilot may use: prompts carry database source, plans and errors, and the setting
keeps all of it on the host. The rule is enforced here, not trusted to configuration:

* The endpoint is plain ``http`` to a loopback address (127.0.0.0/8 or ::1). A name
  is resolved once, at construction, and every address it resolves to must be
  loopback. The client then connects to that address, so a name that later resolves
  elsewhere (DNS rebinding) changes nothing.
* Every request is checked again in the transport, below the client, so no code path
  that builds a URL of its own can reach another host.
* Proxy settings from the environment are ignored, and a redirect is refused rather
  than followed: either could hand a prompt to another host.

TLS is not offered. Loopback traffic never leaves the machine, and a certificate for
an address the client pins would have to be issued for that address.

The adapter speaks the dialect most local servers share (llama.cpp's server, Ollama,
vLLM, the strata server): streamed ``chat/completions`` with ``tools``, and
``GET models`` for the access check. Reasoning text a server streams separately is
not shown or stored. A local server is not billed, so failed calls are not retried
and nothing here accounts for spend.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from harness_api.copilot.provider import (
    Provider,
    ProviderUsage,
    TextDelta,
    ToolCall,
    ToolConversation,
    ToolResult,
    ToolSpec,
    TurnEnd,
    TurnEvent,
)
from harness_worker.errors import ConfigurationError, PolicyError, ProviderError

Resolver = Callable[[str, int], list[str]]

# How the server's finish reasons map onto the stop reasons the rest of the copilot uses
# (the Messages API's names). Anything else passes through unchanged.
_STOP_REASONS = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "refusal",
}


def _system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ConfigurationError(
            f"The local model host {host!r} does not resolve.", detail={"host": host}
        ) from exc
    return [str(info[4][0]) for info in infos]


def _is_loopback(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    # An IPv4-mapped IPv6 address (::ffff:127.0.0.1) is loopback only through its IPv4 half.
    mapped = getattr(ip, "ipv4_mapped", None)
    return (mapped or ip).is_loopback


@dataclass(frozen=True)
class LoopbackEndpoint:
    """The endpoint as configured, and the loopback address the client is pinned to."""

    configured: str
    url: httpx.URL
    host_header: str
    address: str

    @classmethod
    def resolve(cls, base_url: str, resolver: Resolver | None = None) -> LoopbackEndpoint:
        """Refuse anything but plain http to an address on this machine."""

        if not base_url.strip():
            raise ConfigurationError(
                "The local provider needs HARNESS_COPILOT_BASE_URL, for example "
                "http://127.0.0.1:8080/v1."
            )
        try:
            url = httpx.URL(base_url.strip())
        except httpx.InvalidURL as exc:
            raise ConfigurationError(
                f"HARNESS_COPILOT_BASE_URL is not a URL: {exc}",
                detail={"configured": base_url},
            ) from exc
        if url.scheme != "http":
            raise PolicyError(
                "HARNESS_COPILOT_BASE_URL must be plain http to a loopback address; "
                f"{url.scheme or 'no'} scheme given.",
                detail={"configured": base_url},
            )
        if url.userinfo or url.query or url.fragment:
            raise ConfigurationError(
                "HARNESS_COPILOT_BASE_URL takes no credentials, query or fragment. The key "
                "comes from HARNESS_COPILOT_API_KEY_REF.",
            )
        host = url.host
        if not host:
            raise ConfigurationError("HARNESS_COPILOT_BASE_URL names no host.")
        port = url.port or 80
        addresses = [host] if _is_ip(host) else (resolver or _system_resolver)(host, port)
        outside = sorted({address for address in addresses if not _is_loopback(address)})
        if not addresses or outside:
            raise PolicyError(
                f"The local model endpoint {host!r} is not on this machine: it resolves to "
                f"{', '.join(outside) or 'nothing'}. Only loopback addresses (127.0.0.0/8, "
                "::1) are allowed while HARNESS_COPILOT_LOCAL_ONLY is on.",
                detail={"host": host, "outside": outside},
            )
        address = addresses[0].split("%", 1)[0]
        path = url.path if url.path.endswith("/") else url.path + "/"
        pinned = url.copy_with(host=address, path=path)
        host_header = host if url.port is None else f"{_bracketed(host)}:{url.port}"
        return cls(configured=base_url, url=pinned, host_header=host_header, address=address)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


def _bracketed(host: str) -> str:
    return f"[{host}]" if ":" in host else host


class LoopbackTransport(httpx.AsyncBaseTransport):
    """Sends a request only to the pinned loopback address, and never past a redirect."""

    def __init__(self, endpoint: LoopbackEndpoint, inner: httpx.AsyncBaseTransport) -> None:
        self._endpoint = endpoint
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if (
            url.scheme != "http"
            or url.host != self._endpoint.address
            or url.port != self._endpoint.url.port
            or not _is_loopback(url.host)
        ):
            raise PolicyError(
                "Refused a model request to an address other than the configured "
                "loopback endpoint.",
                detail={"host": url.host},
            )
        response = await self._inner.handle_async_request(request)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise PolicyError(
                f"The local model server answered with a redirect ({response.status_code}); "
                "redirects are not followed, so a prompt cannot be sent elsewhere.",
                detail={"status": response.status_code},
            )
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


class LocalProvider(Provider):
    """A model on this machine. See the module docstring for what is enforced."""

    name = "local"

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "",
        max_tokens: int = 8000,
        *,
        timeout_seconds: float = 600.0,
        resolver: Resolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not model.strip():
            raise ConfigurationError(
                "The local provider needs HARNESS_COPILOT_MODEL, the model id the local "
                "server lists under /models."
            )
        self.endpoint = LoopbackEndpoint.resolve(base_url, resolver)
        headers = {"Host": self.endpoint.host_header}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        # Tests pass a stand-in inner transport; the loopback check wraps it either way.
        inner = transport or httpx.AsyncHTTPTransport(retries=0)
        self._client = httpx.AsyncClient(
            base_url=self.endpoint.url,
            headers=headers,
            transport=LoopbackTransport(self.endpoint, inner),
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            follow_redirects=False,
            trust_env=False,
        )
        self._model = model
        self._max_tokens = max_tokens
        self._usage = ProviderUsage(provider=self.name, model=model)

    @property
    def ready(self) -> bool:
        return True

    async def check_access(self) -> str:
        """List the server's models; the configured one has to be among them."""

        try:
            response = await self._client.get("models")
        except httpx.HTTPError as exc:
            raise _unreachable(exc) from exc
        if response.status_code in (401, 403):
            raise ProviderError(
                "The local model server rejected the configured key.",
                detail={"status": response.status_code},
            )
        if response.status_code != 200:
            raise _status_failure(response.status_code)
        try:
            listed = response.json().get("data") or []
        except (ValueError, AttributeError) as exc:
            raise ProviderError("The local model server's model list is not JSON.") from exc
        for entry in listed:
            if not isinstance(entry, dict):
                continue
            names = [entry.get("id"), *(entry.get("aliases") or [])]
            if self._model in names:
                return str(entry.get("id"))
        offered = sorted(str(entry.get("id")) for entry in listed if isinstance(entry, dict))
        raise ProviderError(
            f"The local model server does not list model {self._model!r}; it lists "
            f"{', '.join(offered) or 'none'}.",
            detail={"model": self._model, "listed": offered},
        )

    async def stream(self, system: str, user_message: str) -> AsyncIterator[str]:
        conversation = LocalConversation(self, system, user_message, ())
        async for event in conversation.turn():
            if isinstance(event, TextDelta):
                yield event.text
            else:
                self._usage = event.usage

    def usage(self) -> ProviderUsage:
        return self._usage

    def start_conversation(
        self, system: str, user_message: str, tools: Sequence[ToolSpec]
    ) -> LocalConversation:
        return LocalConversation(self, system, user_message, tools)

    async def aclose(self) -> None:
        await self._client.aclose()


class LocalConversation(ToolConversation):
    """Multi-turn tool use over Chat Completions. Each turn resends the transcript."""

    def __init__(
        self,
        provider: LocalProvider,
        system: str,
        user_message: str,
        tools: Sequence[ToolSpec],
    ) -> None:
        super().__init__(provider.name, provider._model, tools)
        self._client = provider._client
        self._max_tokens = provider._max_tokens
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ]

    async def _run_turn(self) -> AsyncIterator[TurnEvent]:
        request: dict[str, Any] = {
            "model": self._model,
            "messages": self._messages,
            "max_tokens": self._max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # A turn that has to be the answer offers no tools at all: with tools described
        # and tool_choice "none", a local model can still write a call and no answer
        # (trial 21, DIAG-02). The transcript's earlier calls and results stay.
        if self.tools and not self.answer_only:
            request["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in self.tools
            ]
        text: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish = ""
        usage: dict[str, Any] = {}
        model = self._model
        try:
            async with self._client.stream("POST", "chat/completions", json=request) as response:
                if response.status_code != 200:
                    raise _status_failure(response.status_code)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = _chunk(data)
                    model = str(chunk.get("model") or model)
                    usage = chunk.get("usage") or usage
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            text.append(delta["content"])
                            yield TextDelta(delta["content"])
                        for piece in delta.get("tool_calls") or []:
                            _merge_call(calls, piece)
                        finish = choice.get("finish_reason") or finish
        except httpx.HTTPError as exc:
            raise _unreachable(exc) from exc

        stop_reason = _STOP_REASONS.get(finish, finish)
        ordered = [calls[index] for index in sorted(calls)]
        assistant: dict[str, Any] = {"role": "assistant", "content": "".join(text) or None}
        if ordered:
            assistant["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": call["arguments"]},
                }
                for call in ordered
            ]
        self._messages.append(assistant)
        tool_calls = tuple(
            ToolCall(id=call["id"], name=call["name"], input=_arguments(call["arguments"]))
            for call in ordered
        )
        yield TurnEnd(
            stop_reason,
            tool_calls,
            ProviderUsage(
                provider=self._provider,
                model=model,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                stop_reason=stop_reason,
                extra={
                    "cachedPromptTokens": (usage.get("prompt_tokens_details") or {}).get(
                        "cached_tokens"
                    ),
                },
            ),
        )

    def _append_results(self, results: list[ToolResult]) -> None:
        # Chat Completions has no error flag on a tool message; say so in the text.
        for result in results:
            content = f"ERROR: {result.content}" if result.is_error else result.content
            self._messages.append(
                {"role": "tool", "tool_call_id": result.call_id, "content": content}
            )

    def _append_note(self, note: str) -> None:
        self._messages.append({"role": "user", "content": note})


def _chunk(data: str) -> dict[str, Any]:
    try:
        chunk = json.loads(data)
    except ValueError as exc:
        raise ProviderError("The local model server sent a stream line that is not JSON.") from exc
    if not isinstance(chunk, dict):
        raise ProviderError("The local model server sent a stream line that is not an object.")
    if chunk.get("error"):
        # Its text is left out, as for a status failure: it can quote the prompt back.
        raise ProviderError(
            "The local model server failed mid-answer. Database workflows are unaffected."
        )
    return chunk


def _merge_call(calls: dict[int, dict[str, str]], piece: dict[str, Any]) -> None:
    """Streamed tool calls arrive in pieces, keyed by index."""

    index = int(piece.get("index", len(calls)))
    call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
    if piece.get("id"):
        call["id"] = str(piece["id"])
    function = piece.get("function") or {}
    if function.get("name"):
        call["name"] = str(function["name"])
    call["arguments"] += str(function.get("arguments") or "")
    if not call["id"]:
        call["id"] = f"call_{index}"


def _arguments(raw: str) -> dict[str, Any]:
    """The call's arguments, or nothing when the model wrote something other than an object.

    An empty input fails the toolbox's parameter check, which gives the model a typed
    refusal to correct, rather than ending the request here.
    """

    try:
        value = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _status_failure(status: int) -> ProviderError:
    # The body is left out: a server's error text can quote the prompt back.
    return ProviderError(
        f"The local model server returned {status}. Database workflows are unaffected.",
        detail={"status": status},
    )


def _unreachable(exc: Exception) -> ProviderError:
    return ProviderError(
        "The local model server could not be reached. Database workflows are unaffected.",
        detail={"error": type(exc).__name__},
    )
