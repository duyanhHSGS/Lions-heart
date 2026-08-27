"""Deterministic fake model Being and tiny model reply contract."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass

from cor_being import Being, Life, World
from cor_beings.prompt import PromptSnapshot


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One model-requested tool invocation."""

    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ModelReply:
    """Tiny provider-neutral reply shape consumed by the agent loop."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


class LionBeing(Being):
    """Fake deterministic model used until a real provider Being exists."""

    name = "lion"

    def __init__(self) -> None:
        self._script: deque[ModelReply] = deque()
        self._seen_prompts: list[PromptSnapshot] = []

    def birth(self, world: World, life: Life) -> None:
        # TODO: Add a separate real model-provider Being while keeping ModelReply provider-neutral.
        return None

    @property
    def seen_prompts(self) -> tuple[PromptSnapshot, ...]:
        return tuple(self._seen_prompts)

    def queue_reply(self, reply: ModelReply) -> None:
        """Queue a deterministic fake response for tests and tiny demos."""
        if not isinstance(reply, ModelReply):
            raise TypeError("reply must be a ModelReply")
        self._script.append(reply)

    def respond(self, prompt: PromptSnapshot) -> ModelReply:
        """Return the next scripted reply, otherwise echo the latest user text."""
        self._seen_prompts.append(prompt)
        if self._script:
            return self._script.popleft()

        latest_user = ""
        for event in reversed(prompt.events):
            if event.kind == "user":
                value = event.data.get("text", "")
                latest_user = value if isinstance(value, str) else str(value)
                break
        return ModelReply(text=f"Lion heard: {latest_user}")
