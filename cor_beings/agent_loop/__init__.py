"""Small model -> tools -> model driver for Lions-heart."""

from __future__ import annotations

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

    def birth(self, world: World, life: Life) -> None:
        self._session = world.need(SessionBeing)
        self._prompt = world.need(PromptBeing)
        self._tools = world.need(ToolShelfBeing)
        self._lion = world.need(LionBeing)
        # TODO: Add cancellation/streaming later without teaching this loop concrete model or tool types.

    def run_turn(self, message: str, *, max_steps: int = 16) -> str:
        if not isinstance(message, str):
            raise TypeError("message must be a string")
        if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps <= 0:
            raise ValueError("max_steps must be a positive integer")
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
