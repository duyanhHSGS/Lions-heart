"""Prompt snapshot assembly for the tiny Lions-heart harness."""

from __future__ import annotations

from dataclasses import dataclass

from cor_being import Being, Life, World
from cor_beings.session import SessionBeing, SessionEvent
from cor_beings.tool_shelf import ToolShelfBeing


@dataclass(frozen=True, slots=True)
class PromptSnapshot:
    """The exact session/tool snapshot handed to a model for one step."""

    events: tuple[SessionEvent, ...]
    tools: tuple[str, ...]


class PromptBeing(Being):
    """Build tiny deterministic model input from authoritative session state."""

    name = "prompt"
    needs = (SessionBeing, ToolShelfBeing)

    def __init__(self) -> None:
        self._session: SessionBeing | None = None
        self._tools: ToolShelfBeing | None = None

    def birth(self, world: World, life: Life) -> None:
        self._session = world.need(SessionBeing)
        self._tools = world.need(ToolShelfBeing)
        # TODO: Add ordered prompt contributors and real tool schemas without moving logic into AgentLoopBeing.

    def build(self) -> PromptSnapshot:
        if self._session is None or self._tools is None:
            raise RuntimeError("prompt is not alive")
        return PromptSnapshot(events=self._session.events, tools=self._tools.names)
