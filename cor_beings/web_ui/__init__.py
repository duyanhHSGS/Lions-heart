"""Pure-Python local web interface for Lions-heart."""

# TODO: Wire preserved Studio controls only through future permission/tool Beings.
# TODO: Add product configuration for host/port instead of environment/global state.
# TODO: Add streaming after LionBeing and AgentLoopBeing expose a neutral stream contract.
# TODO: Add browser auto-open as an optional adapter behavior, never a required side effect.

from __future__ import annotations

from threading import Thread, current_thread

from cor_being import Being, Life, World
from cor_beings.agent_loop import AgentLoopBeing
from cor_beings.session import SessionBeing, SessionEvent

from .server import WebUiHttpServer, create_server


class WebUiBeing(Being):
    """Serve the local static UI and adapt browser turns to the agent loop."""

    name = "web_ui"
    needs = (AgentLoopBeing, SessionBeing)

    def __init__(self, *, host: str = "127.0.0.1", port: int = 8765) -> None:
        if not isinstance(host, str) or not host.strip():
            raise ValueError("web UI host must be a non-empty string")
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            raise ValueError("web UI port must be an integer from 0 through 65535")
        self._host = host
        self._port = port
        self._agent: AgentLoopBeing | None = None
        self._session: SessionBeing | None = None
        self._server: WebUiHttpServer | None = None
        self._thread: Thread | None = None

    @property
    def url(self) -> str | None:
        """Return the active local address, including an OS-selected test port."""
        server = self._server
        if server is None:
            return None
        host, port = server.server_address[:2]
        return f"http://{host}:{port}"

    def birth(self, world: World, life: Life) -> None:
        if self._server is not None:
            raise RuntimeError("web UI is already alive")

        agent = world.need(AgentLoopBeing)
        session = world.need(SessionBeing)
        server, thread = create_server(
            host=self._host,
            port=self._port,
            submit=self.submit,
            snapshot=self.snapshot,
        )

        self._agent = agent
        self._session = session
        self._server = server
        self._thread = thread
        life.on_death(self._stop)
        thread.start()
        print(f"Lions-heart UI: {self.url}")

    def submit(self, message: str) -> str:
        """Run one agent turn; AgentLoopBeing owns cross-adapter serialization."""
        agent = self._agent
        if agent is None or self._session is None:
            raise RuntimeError("web UI is not alive")
        return agent.run_turn(message)

    def snapshot(self) -> tuple[SessionEvent, ...]:
        """Return the authoritative session snapshot without keeping a UI copy."""
        session = self._session
        if session is None:
            raise RuntimeError("web UI is not alive")
        return session.events

    def _stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        self._agent = None
        self._session = None

        if server is not None:
            if thread is not None and thread.is_alive():
                server.shutdown()
            server.server_close()
        if thread is not None and thread.ident is not None and thread is not current_thread():
            thread.join()


__all__ = ["WebUiBeing"]
