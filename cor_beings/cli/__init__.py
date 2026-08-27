"""One-shot CLI-facing Being for the first Lions-heart spine."""

from __future__ import annotations

from collections.abc import Callable

from cor_being import Being, Life, World
from cor_beings.agent_loop import AgentLoopBeing


class CliBeing(Being):
    """Send one message through the agent and print the final reply."""

    name = "cli"
    needs = (AgentLoopBeing,)

    def __init__(self) -> None:
        self._agent: AgentLoopBeing | None = None

    def birth(self, world: World, life: Life) -> None:
        self._agent = world.need(AgentLoopBeing)
        # TODO: Add a real process entrypoint/argv adapter without putting a permanent wait loop in birth().

    def run_once(self, message: str, *, write: Callable[[str], object] = print) -> str:
        if self._agent is None:
            raise RuntimeError("cli is not alive")
        reply = self._agent.run_turn(message)
        write(reply)
        return reply
