"""Provider-neutral streaming contracts and remote provider Beings."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from threading import Event, RLock
from types import MappingProxyType

import httpx

from cor_being import Being, Life, World
from cor_beings.settings import SettingsBeing


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    text: bool = True
    image_input: bool = False
    tools: bool = True
    reasoning_summaries: bool = False
    image_generation: bool = False
    video_generation: bool = False
    speech_generation: bool = False
    transcription: bool = False
    model_discovery: bool = True
    context_window: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    provider: str
    model: str
    messages: tuple[Mapping[str, object], ...]
    system: str = ""
    tools: tuple[Mapping[str, object], ...] = ()
    attachments: tuple[Mapping[str, object], ...] = ()
    settings: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    metadata: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    kind: str
    data: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def make(cls, kind: str, **data: object) -> "ProviderEvent":
        return cls(kind, MappingProxyType(data))


class ProviderError(RuntimeError):
    """One normalized remote-provider failure safe for product boundaries."""

    def __init__(self, provider: str, kind: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.provider = provider
        self.kind = kind
        self.retryable = retryable


def _iter_sse(response: httpx.Response, cancel: Event) -> Iterator[tuple[str, dict[str, object]]]:
    event_name = "message"
    data_lines: list[str] = []
    for line in response.iter_lines():
        if cancel.is_set():
            return
        if not line:
            if data_lines:
                raw = "\n".join(data_lines)
                data_lines.clear()
                if raw == "[DONE]":
                    return
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as error:
                    raise ProviderError("remote", "malformed_stream", "provider sent malformed streaming JSON") from error
                if isinstance(payload, dict):
                    yield event_name, payload
            event_name = "message"
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError as error:
            raise ProviderError("remote", "malformed_stream", "provider ended with malformed streaming JSON") from error
        if isinstance(payload, dict):
            yield event_name, payload


class RemoteProviderBeing(Being):
    """Shared lifecycle and safe request behavior for provider adapters."""

    needs = (SettingsBeing,)
    provider_name = "remote"
    base_url = ""
    capabilities = ProviderCapabilities()

    def __init__(self, *, base_url: str | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self._configured_base_url = (base_url or self.base_url).rstrip("/")
        self._transport = transport
        self._settings: SettingsBeing | None = None
        self._client: httpx.Client | None = None

    def birth(self, world: World, life: Life) -> None:
        self._settings = world.need(SettingsBeing)
        client = httpx.Client(
            base_url=self._configured_base_url,
            timeout=httpx.Timeout(120.0, connect=15.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            transport=self._transport,
        )
        self._client = client
        life.on_death(self._close)
        # TODO: Add opt-in provider-specific beta features behind capability flags.

    def _close(self) -> None:
        client = self._client
        self._client = None
        self._settings = None
        if client is not None:
            client.close()

    def _key(self) -> str:
        if self._settings is None:
            raise RuntimeError(f"{self.provider_name} provider is not alive")
        key = self._settings.provider_key(self.provider_name)
        if not key:
            raise ProviderError(self.provider_name, "not_configured", f"{self.provider_name} API key is not configured")
        return key

    def _require_client(self) -> httpx.Client:
        if self._client is None:
            raise RuntimeError(f"{self.provider_name} provider is not alive")
        return self._client

    def _raise_http(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        status = response.status_code
        if status in (401, 403):
            kind, retryable = "authentication", False
        elif status == 429:
            kind, retryable = "rate_limit", True
        elif status >= 500:
            kind, retryable = "provider_unavailable", True
        else:
            kind, retryable = "bad_request", False
        raise ProviderError(self.provider_name, kind, f"{self.provider_name} request failed with HTTP {status}", retryable=retryable)

    def list_models(self) -> tuple[str, ...]:
        return ()

    def stream(self, request: ProviderRequest, cancel: Event) -> Iterator[ProviderEvent]:
        raise NotImplementedError


class OpenAIProviderBeing(RemoteProviderBeing):
    name = "provider_openai"
    provider_name = "openai"
    base_url = "https://api.openai.com/v1"
    capabilities = ProviderCapabilities(
        image_input=True,
        reasoning_summaries=True,
        image_generation=True,
        video_generation=True,
        speech_generation=True,
        transcription=True,
    )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key()}", "Accept": "text/event-stream"}

    def list_models(self) -> tuple[str, ...]:
        response = self._require_client().get("/models", headers=self._headers())
        self._raise_http(response)
        payload = response.json()
        return tuple(sorted(item["id"] for item in payload.get("data", ()) if isinstance(item, dict) and isinstance(item.get("id"), str)))

    def stream(self, request: ProviderRequest, cancel: Event) -> Iterator[ProviderEvent]:
        body: dict[str, object] = {
            "model": request.model,
            "instructions": request.system or None,
            "input": list(request.messages),
            "stream": True,
            "store": False,
        }
        if request.tools:
            body["tools"] = [
                {"type": "function", **dict(tool)} for tool in request.tools
            ]
        yield ProviderEvent.make("start", provider=self.provider_name, model=request.model)
        try:
            with self._require_client().stream("POST", "/responses", headers=self._headers(), json=body) as response:
                self._raise_http(response)
                for _name, payload in _iter_sse(response, cancel):
                    kind = payload.get("type")
                    if kind == "response.output_text.delta" and isinstance(payload.get("delta"), str):
                        yield ProviderEvent.make("text_delta", text=payload["delta"])
                    elif kind in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta") and isinstance(payload.get("delta"), str):
                        yield ProviderEvent.make("reasoning_delta", text=payload["delta"])
                    elif kind == "response.output_item.done":
                        item = payload.get("item")
                        if isinstance(item, dict) and item.get("type") == "function_call":
                            yield ProviderEvent.make(
                                "tool_call",
                                id=str(item.get("call_id") or item.get("id") or ""),
                                name=str(item.get("name") or ""),
                                arguments=str(item.get("arguments") or "{}"),
                            )
                    elif kind == "response.completed":
                        response_data = payload.get("response")
                        usage = response_data.get("usage", {}) if isinstance(response_data, dict) else {}
                        yield ProviderEvent.make("usage", **(usage if isinstance(usage, dict) else {}))
        except httpx.HTTPError as error:
            raise ProviderError(self.provider_name, "network", "OpenAI network request failed", retryable=True) from error
        if cancel.is_set():
            yield ProviderEvent.make("cancelled")
        else:
            yield ProviderEvent.make("completed")


class AnthropicProviderBeing(RemoteProviderBeing):
    name = "provider_anthropic"
    provider_name = "anthropic"
    base_url = "https://api.anthropic.com"
    capabilities = ProviderCapabilities(image_input=True, reasoning_summaries=True)

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._key(), "anthropic-version": "2023-06-01", "Accept": "text/event-stream"}

    def list_models(self) -> tuple[str, ...]:
        response = self._require_client().get("/v1/models", headers=self._headers())
        self._raise_http(response)
        payload = response.json()
        return tuple(sorted(item["id"] for item in payload.get("data", ()) if isinstance(item, dict) and isinstance(item.get("id"), str)))

    def stream(self, request: ProviderRequest, cancel: Event) -> Iterator[ProviderEvent]:
        tools = [
            {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "input_schema": tool.get("parameters", {"type": "object", "properties": {}}),
            }
            for tool in request.tools
        ]
        body: dict[str, object] = {
            "model": request.model,
            "system": request.system,
            "messages": list(request.messages),
            "max_tokens": int(request.settings.get("max_output_tokens", 4096)),
            "stream": True,
        }
        if tools:
            body["tools"] = tools
        tool_blocks: dict[int, dict[str, str]] = {}
        yield ProviderEvent.make("start", provider=self.provider_name, model=request.model)
        try:
            with self._require_client().stream("POST", "/v1/messages", headers=self._headers(), json=body) as response:
                self._raise_http(response)
                for _name, payload in _iter_sse(response, cancel):
                    kind = payload.get("type")
                    index = payload.get("index")
                    if kind == "content_block_start" and isinstance(index, int):
                        content = payload.get("content_block")
                        if isinstance(content, dict) and content.get("type") == "tool_use":
                            tool_blocks[index] = {"id": str(content.get("id", "")), "name": str(content.get("name", "")), "arguments": ""}
                    elif kind == "content_block_delta":
                        delta = payload.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                            yield ProviderEvent.make("text_delta", text=delta["text"])
                        elif delta.get("type") == "thinking_delta" and isinstance(delta.get("thinking"), str):
                            yield ProviderEvent.make("reasoning_delta", text=delta["thinking"])
                        elif delta.get("type") == "input_json_delta" and isinstance(index, int) and index in tool_blocks:
                            tool_blocks[index]["arguments"] += str(delta.get("partial_json", ""))
                    elif kind == "content_block_stop" and isinstance(index, int) and index in tool_blocks:
                        tool = tool_blocks.pop(index)
                        yield ProviderEvent.make("tool_call", **tool)
                    elif kind == "message_delta":
                        usage = payload.get("usage")
                        if isinstance(usage, dict):
                            yield ProviderEvent.make("usage", **usage)
        except httpx.HTTPError as error:
            raise ProviderError(self.provider_name, "network", "Anthropic network request failed", retryable=True) from error
        yield ProviderEvent.make("cancelled" if cancel.is_set() else "completed")


class GeminiProviderBeing(RemoteProviderBeing):
    name = "provider_gemini"
    provider_name = "gemini"
    base_url = "https://generativelanguage.googleapis.com"
    capabilities = ProviderCapabilities(
        image_input=True,
        reasoning_summaries=True,
        image_generation=True,
        video_generation=True,
        speech_generation=True,
        transcription=True,
    )

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._key(), "Accept": "text/event-stream"}

    def list_models(self) -> tuple[str, ...]:
        response = self._require_client().get("/v1beta/models", headers=self._headers())
        self._raise_http(response)
        payload = response.json()
        return tuple(sorted(str(item["name"]).removeprefix("models/") for item in payload.get("models", ()) if isinstance(item, dict) and isinstance(item.get("name"), str)))

    def stream(self, request: ProviderRequest, cancel: Event) -> Iterator[ProviderEvent]:
        body: dict[str, object] = {
            "model": request.model,
            "input": list(request.messages),
            "stream": True,
            "system_instruction": request.system,
        }
        if request.tools:
            body["tools"] = [{"type": "function", **dict(tool)} for tool in request.tools]
        tool_blocks: dict[int, dict[str, str]] = {}
        yield ProviderEvent.make("start", provider=self.provider_name, model=request.model)
        try:
            with self._require_client().stream("POST", "/v1beta/interactions", headers=self._headers(), json=body) as response:
                self._raise_http(response)
                for _name, payload in _iter_sse(response, cancel):
                    kind = payload.get("event_type") or payload.get("type")
                    index = payload.get("index")
                    if kind == "step.start" and isinstance(index, int):
                        step = payload.get("step")
                        if isinstance(step, dict) and step.get("type") == "function_call":
                            tool_blocks[index] = {"id": str(step.get("id", "")), "name": str(step.get("name", "")), "arguments": ""}
                    elif kind == "step.delta":
                        delta = payload.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        if delta.get("type") == "text" and isinstance(delta.get("text"), str):
                            yield ProviderEvent.make("text_delta", text=delta["text"])
                        elif delta.get("type") == "thought_summary":
                            content = delta.get("content")
                            if isinstance(content, dict) and isinstance(content.get("text"), str):
                                yield ProviderEvent.make("reasoning_delta", text=content["text"])
                        elif delta.get("type") == "arguments_delta" and isinstance(index, int) and index in tool_blocks:
                            tool_blocks[index]["arguments"] += str(delta.get("arguments", ""))
                    elif kind == "step.stop" and isinstance(index, int) and index in tool_blocks:
                        yield ProviderEvent.make("tool_call", **tool_blocks.pop(index))
                    elif kind == "interaction.completed":
                        interaction = payload.get("interaction")
                        usage = interaction.get("usage", {}) if isinstance(interaction, dict) else {}
                        if isinstance(usage, dict):
                            yield ProviderEvent.make("usage", **usage)
        except httpx.HTTPError as error:
            raise ProviderError(self.provider_name, "network", "Gemini network request failed", retryable=True) from error
        yield ProviderEvent.make("cancelled" if cancel.is_set() else "completed")


ProviderBeing = OpenAIProviderBeing | AnthropicProviderBeing | GeminiProviderBeing


class ProviderRegistryBeing(Being):
    """Life-owned O(1) provider selection and short-lived model catalogs."""

    name = "provider_registry"
    needs = (OpenAIProviderBeing, AnthropicProviderBeing, GeminiProviderBeing)

    def __init__(self, *, cache_seconds: int = 300) -> None:
        self._providers: dict[str, ProviderBeing] = {}
        self._models: dict[str, tuple[float, tuple[str, ...]]] = {}
        self._cache_seconds = cache_seconds
        self._lock = RLock()

    def birth(self, world: World, life: Life) -> None:
        providers = tuple(world.need(kind) for kind in self.needs)
        self._providers = {provider.provider_name: provider for provider in providers}
        if len(self._providers) != len(providers):
            raise ValueError("provider names must be unique")
        life.on_death(self._clear)
        # TODO: Add owner-installed remote providers through lifecycle-owned registration.

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def get(self, name: str) -> ProviderBeing:
        try:
            return self._providers[name]
        except KeyError as error:
            raise LookupError(f"unknown provider: {name}") from error

    def list_models(self, name: str, *, refresh: bool = False) -> tuple[str, ...]:
        now = time.monotonic()
        with self._lock:
            cached = self._models.get(name)
            if not refresh and cached and now - cached[0] < self._cache_seconds:
                return cached[1]
        models = self.get(name).list_models()
        with self._lock:
            self._models[name] = (now, models)
        return models

    def _clear(self) -> None:
        self._providers.clear()
        self._models.clear()


__all__ = [
    "AnthropicProviderBeing",
    "GeminiProviderBeing",
    "OpenAIProviderBeing",
    "ProviderCapabilities",
    "ProviderError",
    "ProviderEvent",
    "ProviderRegistryBeing",
    "ProviderRequest",
]
