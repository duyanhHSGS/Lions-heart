"""Pure-Python local web interface for Lions-heart."""

# TODO: Read host/port from owner configuration when constructor overrides are absent.
# TODO: Add browser auto-open as an optional adapter behavior, never a required side effect.

from __future__ import annotations

from threading import Thread, current_thread

from cor_being import Being, Life, World
from cor_beings.agent_loop import AgentLoopBeing
from cor_beings.auth import AuthBeing
from cor_beings.providers import ProviderRegistryBeing
from cor_beings.projects import ProjectsBeing
from cor_beings.session import SessionBeing, SessionEvent
from cor_beings.settings import SettingsBeing
from cor_beings.storage import StorageBeing
from cor_beings.turn_manager import TurnManagerBeing

from .server import WebCallbacks, WebUiHttpServer, create_server


class WebUiBeing(Being):
    """Serve the local static UI and adapt browser turns to the agent loop."""

    name = "web_ui"
    needs = (
        AgentLoopBeing,
        SessionBeing,
        AuthBeing,
        SettingsBeing,
        ProviderRegistryBeing,
        StorageBeing,
        TurnManagerBeing,
        ProjectsBeing,
    )

    def __init__(self, *, host: str = "127.0.0.1", port: int = 8765) -> None:
        if not isinstance(host, str) or not host.strip():
            raise ValueError("web UI host must be a non-empty string")
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            raise ValueError("web UI port must be an integer from 0 through 65535")
        self._host = host
        self._port = port
        self._agent: AgentLoopBeing | None = None
        self._session: SessionBeing | None = None
        self._auth: AuthBeing | None = None
        self._settings: SettingsBeing | None = None
        self._providers: ProviderRegistryBeing | None = None
        self._storage: StorageBeing | None = None
        self._turns: TurnManagerBeing | None = None
        self._projects: ProjectsBeing | None = None
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
        auth = world.need(AuthBeing)
        settings = world.need(SettingsBeing)
        providers = world.need(ProviderRegistryBeing)
        storage = world.need(StorageBeing)
        turns = world.need(TurnManagerBeing)
        projects = world.need(ProjectsBeing)
        if self._host not in ("127.0.0.1", "localhost", "::1"):
            config = storage.config
            base_url = str(config.get("public_base_url", ""))
            if not base_url.startswith("https://") and config.get("allow_insecure_http") is not True:
                raise ValueError("non-loopback web UI requires an HTTPS public_base_url")
        callbacks = WebCallbacks(
            auth_status=self._auth_status,
            setup=self._setup,
            login=self._login,
            authenticate=auth.authenticate,
            validate_csrf=auth.validate_csrf,
            logout=auth.logout,
            settings_snapshot=settings.public_snapshot,
            update_settings=settings.update,
            set_provider_key=settings.set_provider_key,
            delete_provider_key=settings.delete_provider_key,
            list_models=lambda name, refresh: providers.list_models(name, refresh=refresh),
            list_conversations=session.list_conversations,
            active_conversation=lambda: (session.conversation_id, session.temporary),
            new_conversation=lambda title, temporary: session.new_conversation(
                title=title, temporary=temporary
            ),
            open_conversation=session.open_conversation,
            list_projects=projects.list,
            create_project=lambda name, workspace: projects.create(name, workspace=workspace),
            rename_project=projects.rename,
            delete_project=projects.delete,
            create_turn=turns.create,
            turn_events=turns.events_after,
            wait_for_turn_events=turns.wait_for_events,
            cancel_turn=turns.cancel,
            approvals=turns.approvals,
            decide_approval=turns.decide_approval,
            secure_cookie=str(storage.config.get("public_base_url", "")).startswith("https://"),
        )
        server, thread = create_server(
            host=self._host,
            port=self._port,
            submit=self.submit,
            snapshot=self.snapshot,
            callbacks=callbacks,
        )

        self._agent = agent
        self._session = session
        self._auth = auth
        self._settings = settings
        self._providers = providers
        self._storage = storage
        self._turns = turns
        self._projects = projects
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

    def _auth_status(self, token: str | None) -> dict[str, object]:
        auth = self._auth
        if auth is None:
            raise RuntimeError("web UI is not alive")
        authenticated = auth.authenticate(token)
        return {
            "setup_required": auth.setup_required,
            "authenticated": authenticated,
            "csrf_token": auth.refresh_csrf(token) if authenticated else None,
        }

    def _setup(self, username: str, password: str) -> tuple[str, str, int]:
        auth = self._auth
        if auth is None:
            raise RuntimeError("web UI is not alive")
        session = auth.setup(username, password)
        return session.token, session.csrf_token, session.expires_at

    def _login(self, username: str, password: str, remote: str) -> tuple[str, str, int]:
        auth = self._auth
        if auth is None:
            raise RuntimeError("web UI is not alive")
        session = auth.login(username, password, remote=remote)
        return session.token, session.csrf_token, session.expires_at

    def _stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        self._agent = None
        self._session = None
        self._auth = None
        self._settings = None
        self._providers = None
        self._storage = None
        self._turns = None
        self._projects = None

        if server is not None:
            if thread is not None and thread.is_alive():
                server.shutdown()
            server.server_close()
        if thread is not None and thread.ident is not None and thread is not current_thread():
            thread.join()


__all__ = ["WebUiBeing"]
