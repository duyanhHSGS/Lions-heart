"""Small model -> tools -> model driver for Lions-heart."""

from __future__ import annotations

from threading import Lock

from cor_being import Being, Life, World
from cor_beings.lion import LionBeing
from cor_beings.prompt import PromptBeing
from cor_beings.session import SessionBeing
from cor_beings.tool_shelf import ToolShelfBeing


class AgentLoopBeing(Being):
    """Drive one complete user turn without knowing concrete tool internals."""

    name = "agent_loop"
    needs = (SessionBeing, PromptBeing, ToolShelfBeing, LionBeing)

    def __init__(self) -> None:
        self._session: SessionBeing | None = None
        self._prompt: PromptBeing | None = None
        self._tools: ToolShelfBeing | None = None
        self._lion: LionBeing | None = None
        self._turn_lock = Lock()

    def birth(self, world: World, life: Life) -> None:
        session = world.need(SessionBeing)
        prompt = world.need(PromptBeing)
        tools = world.need(ToolShelfBeing)
        lion = world.need(LionBeing)
        life.on_death(self._forget_dependencies)
        self._session = session
        self._prompt = prompt
        self._tools = tools
        self._lion = lion
        # TODO: Add cancellation/streaming later without teaching this loop
        # concrete model, tool, CLI, or web UI types.

    def _forget_dependencies(self) -> None:
        with self._turn_lock:
            self._session = None
            self._prompt = None
            self._tools = None
            self._lion = None

    def run_turn(self, message: str, *, max_steps: int = 16) -> str:
        if not isinstance(message, str):
            raise TypeError("message must be a string")
        if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps <= 0:
            raise ValueError("max_steps must be a positive integer")
        with self._turn_lock:
            return self._run_serial_turn(message, max_steps=max_steps)

    def _run_serial_turn(self, message: str, *, max_steps: int) -> str:
        """Run one turn while preventing CLI/web history interleaving."""
        if self._session is None or self._prompt is None or self._tools is None or self._lion is None:
            raise RuntimeError("agent loop is not alive")

        self._session.append("user", text=message)

        for _ in range(max_steps):
            reply = self._lion.respond(self._prompt.build())
            self._session.append(
                "assistant",
                text=reply.text,
                tool_calls=reply.tool_calls,
            )

            if not reply.tool_calls:
                return reply.text

            for call in reply.tool_calls:
                try:
                    result = self._tools.execute(call.name, call.arguments)
                except Exception as error:
                    self._session.append(
                        "tool_error",
                        name=call.name,
                        error=type(error).__name__,
                        message=str(error),
                    )
                    raise
                self._session.append(
                    "tool_result",
                    name=call.name,
                    result=result,
                )

        self._session.append("agent_error", error="step_limit", max_steps=max_steps)
        raise RuntimeError(f"agent turn exceeded {max_steps} model steps")
