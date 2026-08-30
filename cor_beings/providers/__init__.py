"""Provider-neutral streaming contracts and remote provider Beings."""

from __future__ import annotations

import json
import ipaddress
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from threading import Event, RLock
from types import MappingProxyType
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from cor_being import Being, Life, World
from cor_beings.settings import SettingsBeing
from cor_beings.storage import StorageBeing


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
    def make(cls, kind: str, **data: object) -> ProviderEvent:
        return cls(kind, MappingProxyType(data))


class ProviderError(RuntimeError):
    """One normalized remote-provider failure safe for product boundaries."""

    def __init__(
        self, provider: str, kind: str, message: str, *, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.kind = kind
        self.retryable = retryable


def _iter_sse(
    response: httpx.Response, cancel: Event
) -> Iterator[tuple[str, dict[str, object]]]:
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
                    raise ProviderError(
                        "remote",
                        "malformed_stream",
                        "provider sent malformed streaming JSON",
                    ) from error
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
            raise ProviderError(
                "remote",
                "malformed_stream",
                "provider ended with malformed streaming JSON",
            ) from error
        if isinstance(payload, dict):
            yield event_name, payload


class RemoteProviderBeing(Being):
    """Shared lifecycle and safe request behavior for provider adapters."""

    needs = (SettingsBeing,)
    provider_name = "remote"
    base_url = ""
    capabilities = ProviderCapabilities()

    def __init__(
        self,
        *,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._configured_base_url = (base_url or self.base_url).rstrip("/")
        self._transport = transport
        self._settings: SettingsBeing | None = None
        self._client: httpx.Client | None = None

    def birth(self, world: World, life: Life) -> None:
        self._start(world.need(SettingsBeing))
        life.on_death(self._close)
        # TODO: Add opt-in provider-specific beta features behind capability flags.

    def _start(self, settings: SettingsBeing) -> None:
        if self._client is not None:
            raise RuntimeError(f"{self.provider_name} provider is already alive")
        self._settings = settings
        client = httpx.Client(
            base_url=self._configured_base_url,
            timeout=httpx.Timeout(120.0, connect=15.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            transport=self._transport,
        )
        self._client = client

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
            raise ProviderError(
                self.provider_name,
                "not_configured",
                f"{self.provider_name} API key is not configured",
            )
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
        raise ProviderError(
            self.provider_name,
            kind,
            f"{self.provider_name} request failed with HTTP {status}",
            retryable=retryable,
        )

    def list_models(self) -> tuple[str, ...]:
        return ()

    def stream(
        self, request: ProviderRequest, cancel: Event
    ) -> Iterator[ProviderEvent]:
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
        return tuple(
            sorted(
                item["id"]
                for item in payload.get("data", ())
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            )
        )

    def stream(
        self, request: ProviderRequest, cancel: Event
    ) -> Iterator[ProviderEvent]:
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
        yield ProviderEvent.make(
            "start", provider=self.provider_name, model=request.model
        )
        try:
            with self._require_client().stream(
                "POST", "/responses", headers=self._headers(), json=body
            ) as response:
                self._raise_http(response)
                for _name, payload in _iter_sse(response, cancel):
                    kind = payload.get("type")
                    if kind == "response.output_text.delta" and isinstance(
                        payload.get("delta"), str
                    ):
                        yield ProviderEvent.make("text_delta", text=payload["delta"])
                    elif kind in (
                        "response.reasoning_summary_text.delta",
                        "response.reasoning_text.delta",
                    ) and isinstance(payload.get("delta"), str):
                        yield ProviderEvent.make(
                            "reasoning_delta", text=payload["delta"]
                        )
                    elif kind == "response.output_item.done":
                        item = payload.get("item")
                        if (
                            isinstance(item, dict)
                            and item.get("type") == "function_call"
                        ):
                            yield ProviderEvent.make(
                                "tool_call",
                                id=str(item.get("call_id") or item.get("id") or ""),
                                name=str(item.get("name") or ""),
                                arguments=str(item.get("arguments") or "{}"),
                            )
                    elif kind == "response.completed":
                        response_data = payload.get("response")
                        usage = (
                            response_data.get("usage", {})
                            if isinstance(response_data, dict)
                            else {}
                        )
                        yield ProviderEvent.make(
                            "usage", **(usage if isinstance(usage, dict) else {})
                        )
        except httpx.HTTPError as error:
            raise ProviderError(
                self.provider_name,
                "network",
                "OpenAI network request failed",
                retryable=True,
            ) from error
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
        return {
            "x-api-key": self._key(),
            "anthropic-version": "2023-06-01",
            "Accept": "text/event-stream",
        }

    def list_models(self) -> tuple[str, ...]:
        response = self._require_client().get("/v1/models", headers=self._headers())
        self._raise_http(response)
        payload = response.json()
        return tuple(
            sorted(
                item["id"]
                for item in payload.get("data", ())
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            )
        )

    def stream(
        self, request: ProviderRequest, cancel: Event
    ) -> Iterator[ProviderEvent]:
        tools = [
            {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "input_schema": tool.get(
                    "parameters", {"type": "object", "properties": {}}
                ),
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
        yield ProviderEvent.make(
            "start", provider=self.provider_name, model=request.model
        )
        try:
            with self._require_client().stream(
                "POST", "/v1/messages", headers=self._headers(), json=body
            ) as response:
                self._raise_http(response)
                for _name, payload in _iter_sse(response, cancel):
                    kind = payload.get("type")
                    index = payload.get("index")
                    if kind == "content_block_start" and isinstance(index, int):
                        content = payload.get("content_block")
                        if (
                            isinstance(content, dict)
                            and content.get("type") == "tool_use"
                        ):
                            tool_blocks[index] = {
                                "id": str(content.get("id", "")),
                                "name": str(content.get("name", "")),
                                "arguments": "",
                            }
                    elif kind == "content_block_delta":
                        delta = payload.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        if delta.get("type") == "text_delta" and isinstance(
                            delta.get("text"), str
                        ):
                            yield ProviderEvent.make("text_delta", text=delta["text"])
                        elif delta.get("type") == "thinking_delta" and isinstance(
                            delta.get("thinking"), str
                        ):
                            yield ProviderEvent.make(
                                "reasoning_delta", text=delta["thinking"]
                            )
                        elif (
                            delta.get("type") == "input_json_delta"
                            and isinstance(index, int)
                            and index in tool_blocks
                        ):
                            tool_blocks[index]["arguments"] += str(
                                delta.get("partial_json", "")
                            )
                    elif (
                        kind == "content_block_stop"
                        and isinstance(index, int)
                        and index in tool_blocks
                    ):
                        tool = tool_blocks.pop(index)
                        yield ProviderEvent.make("tool_call", **tool)
                    elif kind == "message_delta":
                        usage = payload.get("usage")
                        if isinstance(usage, dict):
                            yield ProviderEvent.make("usage", **usage)
        except httpx.HTTPError as error:
            raise ProviderError(
                self.provider_name,
                "network",
                "Anthropic network request failed",
                retryable=True,
            ) from error
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
        return tuple(
            sorted(
                str(item["name"]).removeprefix("models/")
                for item in payload.get("models", ())
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            )
        )

    def stream(
        self, request: ProviderRequest, cancel: Event
    ) -> Iterator[ProviderEvent]:
        body: dict[str, object] = {
            "model": request.model,
            "input": list(request.messages),
            "stream": True,
            "system_instruction": request.system,
        }
        if request.tools:
            body["tools"] = [
                {"type": "function", **dict(tool)} for tool in request.tools
            ]
        tool_blocks: dict[int, dict[str, str]] = {}
        yield ProviderEvent.make(
            "start", provider=self.provider_name, model=request.model
        )
        try:
            with self._require_client().stream(
                "POST", "/v1beta/interactions", headers=self._headers(), json=body
            ) as response:
                self._raise_http(response)
                for _name, payload in _iter_sse(response, cancel):
                    kind = payload.get("event_type") or payload.get("type")
                    index = payload.get("index")
                    if kind == "step.start" and isinstance(index, int):
                        step = payload.get("step")
                        if (
                            isinstance(step, dict)
                            and step.get("type") == "function_call"
                        ):
                            tool_blocks[index] = {
                                "id": str(step.get("id", "")),
                                "name": str(step.get("name", "")),
                                "arguments": "",
                            }
                    elif kind == "step.delta":
                        delta = payload.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        if delta.get("type") == "text" and isinstance(
                            delta.get("text"), str
                        ):
                            yield ProviderEvent.make("text_delta", text=delta["text"])
                        elif delta.get("type") == "thought_summary":
                            content = delta.get("content")
                            if isinstance(content, dict) and isinstance(
                                content.get("text"), str
                            ):
                                yield ProviderEvent.make(
                                    "reasoning_delta", text=content["text"]
                                )
                        elif (
                            delta.get("type") == "arguments_delta"
                            and isinstance(index, int)
                            and index in tool_blocks
                        ):
                            tool_blocks[index]["arguments"] += str(
                                delta.get("arguments", "")
                            )
                    elif (
                        kind == "step.stop"
                        and isinstance(index, int)
                        and index in tool_blocks
                    ):
                        yield ProviderEvent.make("tool_call", **tool_blocks.pop(index))
                    elif kind == "interaction.completed":
                        interaction = payload.get("interaction")
                        usage = (
                            interaction.get("usage", {})
                            if isinstance(interaction, dict)
                            else {}
                        )
                        if isinstance(usage, dict):
                            yield ProviderEvent.make("usage", **usage)
        except httpx.HTTPError as error:
            raise ProviderError(
                self.provider_name,
                "network",
                "Gemini network request failed",
                retryable=True,
            ) from error
        yield ProviderEvent.make("cancelled" if cancel.is_set() else "completed")


class OpenAICompatibleProviderBeing(RemoteProviderBeing):
    """One saved, owner-configured OpenAI-compatible chat connection."""

    capabilities = ProviderCapabilities(image_input=True, reasoning_summaries=True)

    def __init__(self, provider_id: str, base_url: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self.provider_name = provider_id
        self.name = f"provider_{provider_id}"
        super().__init__(base_url=base_url, transport=transport)

    def _headers(self) -> dict[str, str]:
        if self._settings is None:
            raise RuntimeError(f"{self.provider_name} provider is not alive")
        key = self._settings.provider_key(self.provider_name)
        headers = {"Accept": "text/event-stream"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def list_models(self) -> tuple[str, ...]:
        response = self._require_client().get("/models", headers=self._headers())
        self._raise_http(response)
        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderError(self.provider_name, "malformed_response", "provider returned malformed model data") from error
        return tuple(sorted({str(item["id"]) for item in payload.get("data", ()) if isinstance(item, dict) and isinstance(item.get("id"), str)}))

    def stream(self, request: ProviderRequest, cancel: Event) -> Iterator[ProviderEvent]:
        messages = list(request.messages)
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        body: dict[str, object] = {"model": request.model, "messages": messages, "stream": True}
        if request.tools:
            body["tools"] = [{"type": "function", "function": dict(tool)} for tool in request.tools]
        if "max_output_tokens" in request.settings:
            body["max_tokens"] = int(request.settings["max_output_tokens"])
        tool_calls: dict[int, dict[str, str]] = {}
        yield ProviderEvent.make("start", provider=self.provider_name, model=request.model)
        try:
            with self._require_client().stream("POST", "/chat/completions", headers=self._headers(), json=body) as response:
                self._raise_http(response)
                for _name, payload in _iter_sse(response, cancel):
                    usage = payload.get("usage")
                    if isinstance(usage, dict):
                        yield ProviderEvent.make("usage", **usage)
                    choices = payload.get("choices")
                    if not isinstance(choices, list):
                        continue
                    for choice in choices:
                        delta = choice.get("delta") if isinstance(choice, dict) else None
                        if not isinstance(delta, dict):
                            continue
                        content = delta.get("content")
                        if isinstance(content, str):
                            yield ProviderEvent.make("text_delta", text=content)
                        reasoning = delta.get("reasoning_content")
                        if isinstance(reasoning, str):
                            yield ProviderEvent.make("reasoning_delta", text=reasoning)
                        chunks = delta.get("tool_calls")
                        if not isinstance(chunks, list):
                            continue
                        for chunk in chunks:
                            if not isinstance(chunk, dict) or not isinstance(chunk.get("index"), int):
                                continue
                            index = int(chunk["index"])
                            current = tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            if isinstance(chunk.get("id"), str):
                                current["id"] += chunk["id"]
                            function = chunk.get("function")
                            if isinstance(function, dict):
                                if isinstance(function.get("name"), str):
                                    current["name"] += function["name"]
                                if isinstance(function.get("arguments"), str):
                                    current["arguments"] += function["arguments"]
        except httpx.HTTPError as error:
            raise ProviderError(self.provider_name, "network", "provider network request failed", retryable=True) from error
        if cancel.is_set():
            yield ProviderEvent.make("cancelled")
            return
        for index in sorted(tool_calls):
            yield ProviderEvent.make("tool_call", **tool_calls[index])
        yield ProviderEvent.make("completed")


ProviderBeing = OpenAIProviderBeing | AnthropicProviderBeing | GeminiProviderBeing | OpenAICompatibleProviderBeing


class ProviderRegistryBeing(Being):
    """Life-owned O(1) provider selection and short-lived model catalogs."""

    name = "provider_registry"
    needs = (OpenAIProviderBeing, AnthropicProviderBeing, GeminiProviderBeing, StorageBeing, SettingsBeing)

    def __init__(self, *, cache_seconds: int = 300) -> None:
        self._providers: dict[str, ProviderBeing] = {}
        self._models: dict[str, tuple[float, tuple[str, ...]]] = {}
        self._cache_seconds = cache_seconds
        self._lock = RLock()
        self._storage: StorageBeing | None = None
        self._settings: SettingsBeing | None = None

    def birth(self, world: World, life: Life) -> None:
        providers = tuple(world.need(kind) for kind in self.needs[:3])
        self._providers = {provider.provider_name: provider for provider in providers}
        if len(self._providers) != len(providers):
            raise ValueError("provider names must be unique")
        self._storage = world.need(StorageBeing)
        self._settings = world.need(SettingsBeing)
        for row in self._storage.fetchall("SELECT id,base_url FROM provider_connections WHERE enabled=1 ORDER BY created_at,id"):
            self._activate(str(row["id"]), str(row["base_url"]))
        life.on_death(self._clear)
        # TODO: Add more generic wire protocols only as separate audited adapters.

    @property
    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._providers)

    def get(self, name: str) -> ProviderBeing:
        with self._lock:
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

    def list_connections(self) -> tuple[dict[str, object], ...]:
        storage = self._require_storage()
        configured = {str(row["provider"]) for row in storage.fetchall("SELECT provider FROM provider_secrets")}
        builtins = (
            ("openai", "OpenAI", "responses"),
            ("anthropic", "Anthropic", "messages"),
            ("gemini", "Gemini", "interactions"),
        )
        result: list[dict[str, object]] = []
        for provider_id, display_name, protocol in builtins:
            result.append({"id": provider_id, "display_name": display_name, "protocol": protocol, "base_url": "", "models": [], "enabled": True, "built_in": True, "revision": 1, "configured": provider_id in configured})
        for row in storage.fetchall("SELECT id,protocol,display_name,base_url,models_json,enabled,revision FROM provider_connections ORDER BY created_at,id"):
            provider_id = str(row["id"])
            result.append({"id": provider_id, "display_name": str(row["display_name"]), "protocol": str(row["protocol"]), "base_url": str(row["base_url"]), "models": json.loads(row["models_json"]), "enabled": bool(row["enabled"]), "built_in": False, "revision": int(row["revision"]), "configured": provider_id in configured})
        return tuple(result)

    def create_connection(self, display_name: str, base_url: str, models: object, secret: str | None = None) -> dict[str, object]:
        clean_name = _connection_name(display_name)
        clean_url = _connection_url(base_url)
        clean_models = _connection_models(models)
        provider_id = f"custom_{uuid4().hex[:24]}"
        storage = self._require_storage()
        now = int(time.time())
        try:
            storage.execute("INSERT INTO provider_connections(id,protocol,display_name,base_url,models_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (provider_id, "openai_compatible", clean_name, clean_url, json.dumps(clean_models), now, now))
        except Exception as error:
            if "UNIQUE constraint" in str(error):
                raise ValueError("provider display name already exists") from error
            raise
        try:
            if secret is not None:
                self._require_settings().set_provider_key(provider_id, secret)
            self._activate(provider_id, clean_url)
        except BaseException:
            storage.execute("DELETE FROM provider_connections WHERE id=?", (provider_id,))
            self._require_settings().delete_provider_key(provider_id)
            raise
        return next(item for item in self.list_connections() if item["id"] == provider_id)

    def update_connection(self, provider_id: str, *, display_name: object = None, base_url: object = None, models: object = None, enabled: object = None, secret: object = None, clear_secret: bool = False, revision: object = None) -> dict[str, object]:
        row = self._custom_row(provider_id)
        expected = int(row["revision"])
        if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool) or revision != expected):
            raise RuntimeError("provider connection changed; reload and try again")
        name = str(row["display_name"]) if display_name is None else _connection_name(display_name)
        url = str(row["base_url"]) if base_url is None else _connection_url(base_url)
        model_values = json.loads(row["models_json"]) if models is None else _connection_models(models)
        active = bool(row["enabled"]) if enabled is None else enabled
        if not isinstance(active, bool):
            raise ValueError("enabled must be true or false")
        if secret is not None and clear_secret:
            raise ValueError("secret and clear_secret cannot be used together")
        if secret is not None and not isinstance(secret, str):
            raise ValueError("secret must be a string")
        if isinstance(secret, str) and not secret.strip():
            raise ValueError("secret must be non-empty when provided")
        if not isinstance(clear_secret, bool):
            raise ValueError("clear_secret must be true or false")
        try:
            cursor = self._require_storage().execute("UPDATE provider_connections SET display_name=?,base_url=?,models_json=?,enabled=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?", (name, url, json.dumps(model_values), int(active), int(time.time()), provider_id, expected))
        except Exception as error:
            if "UNIQUE constraint" in str(error):
                raise ValueError("provider display name already exists") from error
            raise
        if cursor.rowcount != 1:
            raise RuntimeError("provider connection changed; reload and try again")
        if clear_secret:
            self._require_settings().delete_provider_key(provider_id)
        elif isinstance(secret, str):
            self._require_settings().set_provider_key(provider_id, secret)
        self._deactivate(provider_id)
        if active:
            self._activate(provider_id, url)
        elif self._require_settings().values["default_provider"] == provider_id:
            self._require_settings().update({"default_provider": "openai"})
        self._models.pop(provider_id, None)
        return next(item for item in self.list_connections() if item["id"] == provider_id)

    def delete_connection(self, provider_id: str) -> None:
        self._custom_row(provider_id)
        self._deactivate(provider_id)
        with self._require_storage().transaction() as connection:
            connection.execute("DELETE FROM provider_secrets WHERE provider=?", (provider_id,))
            connection.execute("DELETE FROM provider_connections WHERE id=?", (provider_id,))
        self._models.pop(provider_id, None)
        if self._require_settings().values["default_provider"] == provider_id:
            self._require_settings().update({"default_provider": "openai"})

    def _custom_row(self, provider_id: str):
        if provider_id in ("openai", "anthropic", "gemini"):
            raise ValueError("built-in providers cannot be changed")
        row = self._require_storage().fetchone("SELECT * FROM provider_connections WHERE id=?", (provider_id,))
        if row is None:
            raise LookupError("provider connection not found")
        return row

    def _activate(self, provider_id: str, base_url: str) -> None:
        adapter = OpenAICompatibleProviderBeing(provider_id, base_url)
        adapter._start(self._require_settings())
        with self._lock:
            self._providers[provider_id] = adapter

    def _deactivate(self, provider_id: str) -> None:
        with self._lock:
            provider = self._providers.pop(provider_id, None)
        if isinstance(provider, OpenAICompatibleProviderBeing):
            provider._close()

    def _require_storage(self) -> StorageBeing:
        if self._storage is None:
            raise RuntimeError("provider registry is not alive")
        return self._storage

    def _require_settings(self) -> SettingsBeing:
        if self._settings is None:
            raise RuntimeError("provider registry is not alive")
        return self._settings

    def _clear(self) -> None:
        for provider_id in self.names:
            self._deactivate(provider_id)
        with self._lock:
            self._providers.clear()
            self._models.clear()
        self._storage = None
        self._settings = None


def _connection_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("display_name must be a string")
    clean = " ".join(value.split())
    if not clean or len(clean) > 80 or any(ord(char) < 32 for char in clean):
        raise ValueError("display_name must contain 1 to 80 safe characters")
    return clean


def _connection_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("base_url must be an HTTPS URL")
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base_url must be a credential-free HTTPS URL without query or fragment")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("base_url must use a public host")
    return value.strip().rstrip("/")


def _connection_models(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) > 200:
        raise ValueError("models must be a list of at most 200 model IDs")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item.strip()) > 160:
            raise ValueError("each model ID must contain 1 to 160 characters")
        clean = item.strip()
        if clean not in seen:
            result.append(clean)
            seen.add(clean)
    return result


__all__ = [
    "AnthropicProviderBeing",
    "GeminiProviderBeing",
    "OpenAIProviderBeing",
    "OpenAICompatibleProviderBeing",
    "ProviderCapabilities",
    "ProviderError",
    "ProviderEvent",
    "ProviderRegistryBeing",
    "ProviderRequest",
]
