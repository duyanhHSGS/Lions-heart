"""Provider-neutral remote model selection for Lion."""

from __future__ import annotations

from collections.abc import Iterator
from threading import Event
from types import MappingProxyType

from cor_being import Being, Life, World
from cor_beings.providers import ProviderEvent, ProviderRegistryBeing, ProviderRequest
from cor_beings.settings import SettingsBeing
from cor_beings.lion import LionBeing
from cor_beings.prompt import PromptSnapshot
from cor_beings.session import SessionEvent


class ModelGatewayBeing(Being):
    """Select a configured remote provider without leaking its wire format."""

    name = "model_gateway"
    needs = (ProviderRegistryBeing, SettingsBeing)

    def __init__(self, *, fake_provider: LionBeing | None = None) -> None:
        self._registry: ProviderRegistryBeing | None = None
        self._settings: SettingsBeing | None = None
        self._fake_provider = fake_provider

    @property
    def fake_provider(self) -> LionBeing | None:
        return self._fake_provider

    def birth(self, world: World, life: Life) -> None:
        self._registry = world.need(ProviderRegistryBeing)
        self._settings = world.need(SettingsBeing)
        life.on_death(self._forget)
        # TODO: Add future providers without teaching AgentLoopBeing their protocols.

    def stream(self, request: ProviderRequest, cancel: Event | None = None) -> Iterator[ProviderEvent]:
        registry = self._registry
        if registry is None or self._settings is None:
            raise RuntimeError("model gateway is not alive")
        if self._fake_provider is not None:
            return self._fake_stream(request, cancel or Event())
        return registry.get(request.provider).stream(request, cancel or Event())

    def _fake_stream(self, request: ProviderRequest, cancel: Event) -> Iterator[ProviderEvent]:
        fake = self._fake_provider
        if fake is None:
            return
        events = tuple(
            SessionEvent(
                str(message.get("_lion_kind") or message.get("role", "user")),
                MappingProxyType({"text": str(message.get("content", ""))}),
            )
            for message in request.messages
        )
        reply = fake.respond(PromptSnapshot(events, ()))
        yield ProviderEvent.make("start", provider="fake", model="deterministic")
        if cancel.is_set():
            yield ProviderEvent.make("cancelled")
            return
        if reply.text:
            yield ProviderEvent.make("text_delta", text=reply.text)
        for index, call in enumerate(reply.tool_calls):
            import json

            yield ProviderEvent.make(
                "tool_call",
                id=f"fake-{index}",
                name=call.name,
                arguments=json.dumps(dict(call.arguments)),
            )
        yield ProviderEvent.make("completed")

    def default_request(self, messages: tuple[dict[str, object], ...]) -> ProviderRequest:
        if self._settings is None:
            raise RuntimeError("model gateway is not alive")
        values = self._settings.values
        provider = str(values["default_provider"])
        model = str(values["default_text_model"])
        if not model and self._fake_provider is not None:
            provider, model = "fake", "deterministic"
        elif not model:
            raise RuntimeError("default text model is not configured")
        normalized_messages = messages
        if self._fake_provider is None:
            normalized_messages = tuple(
                {"role": message.get("role", "user"), "content": message.get("content", "")}
                for message in messages
            )
        return ProviderRequest(
            provider=provider,
            model=model,
            system=str(values["system_prompt"]),
            messages=normalized_messages,
        )

    def _forget(self) -> None:
        self._registry = None
        self._settings = None


__all__ = ["ModelGatewayBeing"]
