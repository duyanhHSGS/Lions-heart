"""One-shot CLI-facing Being for the first Lions-heart spine."""

from __future__ import annotations

from collections.abc import Callable

from cor_being import Being, Life, World
from cor_beings.agent_loop import AgentLoopBeing

from .process import start_console_thread, stdin_is_interactive


class CliBeing(Being):
    """Send one message through the agent and print the final reply."""

    name = "cli"
    needs = (AgentLoopBeing,)

    def __init__(self) -> None:
        self._agent: AgentLoopBeing | None = None

    def birth(self, world: World, life: Life) -> None:
        self._agent = world.need(AgentLoopBeing)
        if stdin_is_interactive():
            start_console_thread(self, life)
        # TODO: Keep terminal startup thin if future CLI configuration is added.

    def run_once(self, message: str, *, write: Callable[[str], object] = print) -> str:
        if self._agent is None:
            raise RuntimeError("cli is not alive")
        reply = self._agent.run_turn(message)
        write(reply)
        return reply
