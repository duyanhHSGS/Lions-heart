"""Focused product tests for the pure-Python web UI Being."""

# TODO: Add real-browser accessibility snapshots when a dependency-free QA path exists.
# TODO: Add browser-engine reconnect coverage in addition to transport-level SSE tests.

from __future__ import annotations

import http.client
import json
import re
import socket
import time
from collections.abc import Iterator
from pathlib import Path
from threading import Event, Thread
from urllib.parse import urlsplit

import pytest

import cor_beings.web_ui as web_ui_module
from cor_being import Life
from cor_beings.agent_loop import AgentLoopBeing
from cor_beings.activity import ActivityBeing
from cor_beings.audio import AudioBeing
from cor_beings.auth import AuthBeing
from cor_beings.attachments import AttachmentBeing
from cor_beings.lion import ToolCall
from cor_beings.mcp import McpBeing
from cor_beings.images import ImageBeing
from cor_beings.media_jobs import MediaJobBeing
from cor_beings.providers import (
    AnthropicProviderBeing,
    GeminiProviderBeing,
    OpenAIProviderBeing,
    ProviderRegistryBeing,
)
from cor_beings.projects import ProjectsBeing
from cor_beings.session import SessionBeing
from cor_beings.settings import SettingsBeing
from cor_beings.saved_prompts import SavedPromptsBeing
from cor_beings.recipes import RecipeBeing
from cor_beings.storage import StorageBeing
from cor_beings.turn_manager import TurnManagerBeing
from cor_beings.video import VideoBeing
from cor_beings.web_ui import WebUiBeing
from cor_beings.web_ui.server import MAX_BODY_BYTES, MAX_MESSAGE_CHARS, serialize_events

ROOT = Path(__file__).resolve().parents[1]


class RecordingAgent:
    """Agent-shaped test helper that writes through the real SessionBeing."""

    def __init__(self, session: SessionBeing, *, failure: Exception | None = None) -> None:
        self.session = session
        self.failure = failure
        self.messages: list[str] = []

    def run_turn(self, message: str) -> str:
        if self.failure is not None:
            raise self.failure
        self.messages.append(message)
        self.session.append("user", text=message)
        reply = f"Lion heard: {message}"
        self.session.append("assistant", text=reply)
        return reply


class RecordingTurns:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []
        self.shutdown_started = False
        self.stream_running = False
        self.entered_wait = Event()
        self.release_wait = Event()

    def begin_shutdown(self) -> None:
        self.shutdown_started = True
        self.release_wait.set()

    def create(self, conversation_id: str, message: str) -> str:
        self.created.append((conversation_id, message))
        return "turn-test"

    def events_after(self, _turn_id: str, _after: int):
        if self.stream_running:
            return (), "running"
        return (({"sequence": 1, "kind": "turn_completed", "data": {}},), "completed")

    def wait_for_events(self, _turn_id: str, _after: int, _timeout: float) -> None:
        if self.stream_running:
            self.entered_wait.set()
            self.release_wait.wait(timeout=_timeout)

    def cancel(self, _turn_id: str) -> None:
        return None

    def approvals(self, _turn_id: str):
        return ()

    def decide_approval(self, _approval_id: str, _approved: bool) -> None:
        return None


class RecordingProjects:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}

    def list(self):
        return tuple(self.rows.values())

    def create(self, name: str, *, workspace: str | None = None) -> str:
        project_id = f"project-{len(self.rows) + 1}"
        self.rows[project_id] = {"id": project_id, "name": name, "workspace": workspace}
        return project_id

    def rename(self, project_id: str, name: str) -> None:
        if project_id not in self.rows:
            raise LookupError
        self.rows[project_id]["name"] = name

    def delete(self, project_id: str) -> None:
        if self.rows.pop(project_id, None) is None:
            raise LookupError

    def select(self, project_id: str):
        if project_id not in self.rows: raise LookupError
        if not self.rows[project_id].get("workspace"): raise ValueError("project has no workspace")
        return self.rows[project_id]


class RecordingMcp:
    def __init__(self): self.rows = {}
    def list(self): return tuple(self.rows.values())
    def create(self, name, transport, config, **_kwargs):
        item = {"id": "mcp-test", "name": name, "transport": transport, "config": config, "health": "healthy"}
        self.rows[item["id"]] = item; return item
    def import_connections(self, items):
        return tuple(self.create(item["name"], item["transport"], item["config"]) for item in items)
    def update(self, connection_id, name, transport, config, **_kwargs):
        if connection_id not in self.rows: raise LookupError
        self.rows[connection_id].update(name=name, transport=transport, config=config); return self.rows[connection_id]
    def delete(self, connection_id):
        if self.rows.pop(connection_id, None) is None: raise LookupError
    def test(self, _connection_id): return {"id": "mcp-test", "health": "healthy"}


class UiWorld:
    name = "web-test"
    news = None
    alive = ()

    def __init__(self, *instances: object, turns: RecordingTurns | None = None, projects: RecordingProjects | None = None, mcp: RecordingMcp | None = None) -> None:
        self.instances = {type(instance): instance for instance in instances}
        self.agent = next((item for item in instances if isinstance(item, RecordingAgent)), None)
        self.turns = turns
        self.projects = projects
        self.mcp = mcp

    def need(self, being_type):
        if being_type is AgentLoopBeing and self.agent is not None:
            return self.agent
        if being_type is TurnManagerBeing and self.turns is not None:
            return self.turns
        if being_type is ProjectsBeing and self.projects is not None:
            return self.projects
        if being_type is McpBeing and self.mcp is not None:
            return self.mcp
        try:
            return self.instances[being_type]
        except KeyError as error:
            raise LookupError(being_type) from error


@pytest.fixture
def live_ui(tmp_path: Path) -> Iterator[tuple[WebUiBeing, RecordingAgent, SessionBeing, Life]]:
    storage = StorageBeing(data_root=tmp_path / "runtime")
    settings = SettingsBeing()
    auth = AuthBeing()
    session = SessionBeing()
    attachments = AttachmentBeing()
    saved_prompts = SavedPromptsBeing()
    agent = RecordingAgent(session)
    turns = RecordingTurns()
    projects = RecordingProjects()
    openai = OpenAIProviderBeing()
    anthropic = AnthropicProviderBeing()
    gemini = GeminiProviderBeing()
    providers = ProviderRegistryBeing()
    activity = ActivityBeing()
    media = MediaJobBeing()
    images = ImageBeing()
    audio = AudioBeing()
    video = VideoBeing()
    recipes = RecipeBeing()
    support = (storage, settings, auth, session, attachments, saved_prompts, openai, anthropic, gemini, providers,
               activity, media, images, audio, video, recipes)
    world = UiWorld(agent, *support, turns=turns, projects=projects, mcp=RecordingMcp())
    support_lives: list[Life] = []
    for being in support:
        being_life = Life(being.name)
        being.birth(world, being_life)  # type: ignore[arg-type]
        support_lives.append(being_life)
    ui = WebUiBeing(port=0)
    life = Life("web_ui")
    ui.birth(world, life)  # type: ignore[arg-type]
    setattr(ui, "_test_world", world)
    status, headers, body = request(
        ui,
        "POST",
        "/api/auth/setup",
        body=json.dumps({"username": "owner", "password": "lion-password"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert status == 200
    setattr(ui, "_test_cookie", headers["Set-Cookie"].split(";", 1)[0])
    setattr(ui, "_test_csrf", json.loads(body)["csrf_token"])
    try:
        yield ui, agent, session, life
    finally:
        life.die()
        for being_life in reversed(support_lives):
            being_life.die()


def request(
    ui: WebUiBeing,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    effective_headers = dict(headers or {})
    if hasattr(ui, "_test_cookie"):
        effective_headers.setdefault("Cookie", getattr(ui, "_test_cookie"))
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            effective_headers.setdefault("X-CSRF-Token", getattr(ui, "_test_csrf"))
    parsed = urlsplit(ui.url or "")
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2)
    try:
        connection.request(method, path, body=body, headers=effective_headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def json_request(
    ui: WebUiBeing,
    payload: object,
    *,
    path: str = "/api/turn",
) -> tuple[int, dict[str, object]]:
    status, _headers, body = request(
        ui,
        "POST",
        path,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    return status, json.loads(body)


def test_web_ui_constructor_rejects_bad_bind_configuration() -> None:
    for host in ("", "   ", 123):
        with pytest.raises(ValueError, match="host"):
            WebUiBeing(host=host)  # type: ignore[arg-type]
    for port in (-1, 65536, True, 1.5):
        with pytest.raises(ValueError, match="port"):
            WebUiBeing(port=port)  # type: ignore[arg-type]


def test_saved_prompt_and_mcp_routes_are_authenticated_csrf_crud(live_ui) -> None:
    ui, _agent, _session, _life = live_ui
    status, created = json_request(ui, {"name": "Explain", "body": "Explain like I am ten"}, path="/api/saved-prompts")
    assert status == 201
    prompt = created["prompt"]
    status, _headers, body = request(ui, "GET", "/api/saved-prompts?q=Explain")
    assert status == 200 and json.loads(body)["prompts"][0]["id"] == prompt["id"]
    status, _headers, _body = request(ui, "DELETE", f"/api/saved-prompts/{prompt['id']}")
    assert status == 200

    status, created_mcp = json_request(ui, {"name": "Docs", "transport": "http", "config": {"url": "https://example.invalid/mcp"}}, path="/api/mcp/connections")
    assert status == 201 and created_mcp["connections"][0]["name"] == "Docs"
    status, _headers, body = request(ui, "GET", "/api/mcp/connections")
    assert status == 200 and json.loads(body)["connections"][0]["id"] == "mcp-test"
    status, _headers, _body = request(ui, "DELETE", "/api/mcp/connections/mcp-test")
    assert status == 200


def test_web_ui_requires_birth_for_public_operations() -> None:
    ui = WebUiBeing(port=0)
    assert ui.url is None
    with pytest.raises(RuntimeError, match="not alive"):
        ui.snapshot()
    with pytest.raises(RuntimeError, match="not alive"):
        ui.submit("hello")


def test_web_ui_serves_text_only_chat_shell_and_controls(live_ui) -> None:
    ui, _agent, _session, _life = live_ui
    status, headers, body = request(ui, "GET", "/")
    page = body.decode("utf-8")
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert "What’s on your mind today?" in page
    assert 'placeholder="Ask anything"' in page
    assert ">Lion's Heart<" in page
    assert "<img" not in page
    for label in (
        "New chat",
        "Models",
        "Projects",
        "Images",
        "Video",
        "More",
        "Audio",
        "Recipes",
        "API Activity",
        "Settings",
        "Guided Tour",
        "Add text files",
        "Web search",
        "Code",
        "Chat with Files",
        "MCP",
        "Ask for approval",
        "Providers",
        "Tools &amp; MCP",
        "Security",
    ):
        assert label in page


def test_web_ui_css_keeps_reference_geometry_and_theme_tokens(live_ui) -> None:
    status, _headers, body = request(live_ui[0], "GET", "/styles.css")
    stylesheet = body.decode("utf-8")
    assert status == 200
    for declaration in (
        "--background: #fefefd",
        "--primary: #17b88b",
        "--sidebar-width: 280px",
        "--thread-max-width: 46rem",
        "top: 27.5dvh",
        "border-radius: 32px",
        "--background: #181818",
        "--sidebar: #1f1f1f",
    ):
        assert declaration in stylesheet


def test_settings_content_remains_scrollable_in_short_and_mobile_viewports(live_ui) -> None:
    status, _headers, body = request(live_ui[0], "GET", "/styles.css")
    stylesheet = body.decode("utf-8")
    assert status == 200
    assert "grid-template-rows: auto minmax(0, 1fr);" in stylesheet
    assert "height: min(820px, calc(100dvh - 48px));" in stylesheet
    assert "min-height: 0;\n  overflow: hidden;" in stylesheet
    assert "flex: 1 1 auto;" in stylesheet
    assert "overflow-x: auto; overflow-y: hidden;" in stylesheet


def test_general_settings_have_grouped_functional_preferences(live_ui) -> None:
    ui = live_ui[0]
    html_status, _headers, html_body = request(ui, "GET", "/index.html")
    js_status, _headers, js_body = request(ui, "GET", "/app.js")
    page = html_body.decode("utf-8")
    controller = js_body.decode("utf-8")

    assert html_status == 200
    assert js_status == 200
    for label in ("Defaults", "Enter to send", "Answer completed", "History retention", "Attachment limit", "Tool calls", "Restore General defaults"):
        assert label in page
    assert 'name="send_on_enter" type="checkbox" role="switch"' in page
    assert 'name="notify_on_completion" type="checkbox" role="switch"' in page
    assert "settingsSnapshot.values.send_on_enter !== false" in controller
    assert 'new Notification("Lion finished answering"' in controller
    assert 'for (const name of ["send_on_enter", "notify_on_completion"]) changes[name] = data.has(name);' in controller


def test_recent_conversations_have_no_fade_overlay(live_ui) -> None:
    ui = live_ui[0]
    html_status, _html_headers, html_body = request(ui, "GET", "/index.html")
    css_status, _css_headers, css_body = request(ui, "GET", "/styles.css")
    page = html_body.decode("utf-8")
    stylesheet = css_body.decode("utf-8")

    assert html_status == 200
    assert css_status == 200
    assert 'class="sidebar-fade"' not in page
    assert ".sidebar-fade" not in stylesheet
    assert "linear-gradient(to top, var(--sidebar), transparent)" not in stylesheet


def test_chat_row_hover_surface_stays_continuous_across_actions(live_ui) -> None:
    status, _headers, body = request(live_ui[0], "GET", "/styles.css")
    stylesheet = body.decode("utf-8")

    assert status == 200
    assert ".chat-row:hover,\n.chat-row:focus-within,\n.chat-row.active { background: var(--nav-surface-hover); }" in stylesheet
    assert ".chat-row .current-chat:hover { background: transparent; }" in stylesheet
    assert "transition: background-color 120ms ease;" in stylesheet


def test_chat_options_reveal_does_not_paint_a_second_hover_patch(live_ui) -> None:
    status, _headers, body = request(live_ui[0], "GET", "/styles.css")
    stylesheet = body.decode("utf-8")

    assert status == 200
    reveal_rule = stylesheet.split(".chat-row:hover .chat-options-trigger,", 1)[1].split("}", 1)[0]
    assert "opacity: 1" in reveal_rule
    assert "background:" not in reveal_rule


@pytest.mark.parametrize(
    ("path", "content_type", "needle"),
    [
        ("/index.html", "text/html; charset=utf-8", b"Lion's Heart"),
        ("/styles.css", "text/css; charset=utf-8", b"--primary: #17b88b"),
        ("/app.js", "text/javascript; charset=utf-8", b"loadSession"),
    ],
)
def test_web_ui_serves_owned_static_assets_without_stale_browser_cache(
    live_ui, path, content_type, needle
) -> None:
    status, headers, body = request(live_ui[0], "GET", path)
    assert status == 200
    assert headers["Content-Type"] == content_type
    assert headers["Cache-Control"] == "no-store"
    assert needle in body


def test_html_uses_content_hashed_immutable_assets(live_ui) -> None:
    status, headers, body = request(live_ui[0], "GET", "/")
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    match = re.search(rb'href="(/styles\.[0-9a-f]{12}\.css)"', body)
    assert match is not None
    status, headers, css = request(live_ui[0], "GET", match.group(1).decode("ascii"))
    assert status == 200
    assert headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert b"--primary: #17b88b" in css


def test_frontend_has_no_node_manifest_or_external_runtime_assets() -> None:
    static = ROOT / "cor_beings" / "web_ui" / "static"
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            static / "index.html",
            static / "styles.css",
            static / "app.js",
            static / "markdown.js",
        )
    )
    assert not (ROOT / "package.json").exists()
    assert not (ROOT / "package-lock.json").exists()
    assert "src=\"https://" not in combined
    assert "href=\"https://" not in combined
    assert "node_modules" not in combined
    assert "npm" not in combined.lower()
    assert "vite" not in combined.lower()
    assert "<img" not in combined
    assert "@font-face" not in combined
    assert ".png" not in combined.lower()
    assert ".woff" not in combined.lower()
    assert {
        path.suffix for path in static.rglob("*") if path.is_file()
    } == {".html", ".css", ".js"}


@pytest.mark.parametrize(
    "removed_path",
    [
        "/circle-logo-small.png",
        "/sloth-magnify.png",
        "/fonts/Hellix-Regular.woff",
        "/fonts/Hellix-Medium.woff",
    ],
)
def test_removed_binary_assets_are_not_served(live_ui, removed_path) -> None:
    status, _headers, body = request(live_ui[0], "GET", removed_path)
    assert status == 404
    assert json.loads(body) == {"error": "route not found"}


def test_web_ui_session_api_uses_authoritative_session(live_ui) -> None:
    ui, _agent, session, _life = live_ui
    session.append("assistant", text="hello", tool_calls=(ToolCall("read", {"path": "x"}),))
    status, headers, body = request(ui, "GET", "/api/session")
    payload = json.loads(body)
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert payload == {
        "events": [
            {
                "kind": "assistant",
                "data": {
                    "text": "hello",
                    "tool_calls": [{"name": "read", "arguments": {"path": "x"}}],
                },
            }
        ],
        "count": 1,
        "conversation_id": session.conversation_id,
        "temporary": False,
    }


def test_protected_api_rejects_missing_cookie(live_ui) -> None:
    ui, _agent, _session, _life = live_ui
    cookie = getattr(ui, "_test_cookie")
    delattr(ui, "_test_cookie")
    try:
        status, _headers, body = request(ui, "GET", "/api/settings")
    finally:
        setattr(ui, "_test_cookie", cookie)
    assert status == 401
    assert json.loads(body)["error"] == "authentication required"


def test_mutation_rejects_bad_csrf(live_ui) -> None:
    ui, _agent, _session, _life = live_ui
    status, _headers, body = request(
        ui,
        "POST",
        "/api/settings",
        body=b'{"changes":{"theme":"dark"}}',
        headers={"Content-Type": "application/json", "X-CSRF-Token": "wrong"},
    )
    assert status == 403
    assert json.loads(body)["error"] == "invalid CSRF token"


def test_settings_update_serializes_immutable_values_and_persists(live_ui) -> None:
    ui, _agent, _session, _life = live_ui
    status, payload = json_request(
        ui,
        {"changes": {"theme": "dark", "default_text_model": "lion-test-model"}},
        path="/api/settings",
    )

    assert status == 200
    assert payload["values"]["theme"] == "dark"
    assert payload["values"]["default_text_model"] == "lion-test-model"

    status, _headers, body = request(ui, "GET", "/api/settings")
    assert status == 200
    persisted = json.loads(body)
    assert persisted["values"]["theme"] == "dark"
    assert persisted["values"]["default_text_model"] == "lion-test-model"


def test_provider_key_api_returns_only_masked_state(live_ui) -> None:
    ui, _agent, _session, _life = live_ui
    secret = "sk-super-secret-tail"
    status, payload = json_request(
        ui,
        {"provider": "openai", "secret": secret},
        path="/api/provider-key",
    )
    assert status == 200
    assert payload["providers"]["openai"] == {"configured": True, "suffix": "tail"}
    assert secret not in json.dumps(payload)


def test_generic_provider_api_and_settings_ui_cover_full_crud(live_ui) -> None:
    ui = live_ui[0]
    status, _headers, body = request(ui, "GET", "/api/providers")
    assert status == 200
    assert [item["id"] for item in json.loads(body)["providers"][:3]] == ["openai", "anthropic", "gemini"]

    status, created = json_request(
        ui,
        {"display_name": "My Gateway", "base_url": "https://gateway.example/v1", "models": ["lion-fast"], "secret": "secret-tail"},
        path="/api/providers",
    )
    assert status == 201
    provider = created["provider"]
    assert provider["configured"] is True
    assert "secret-tail" not in json.dumps(created)

    status, _headers, body = request(
        ui, "PUT", f"/api/providers/{provider['id']}",
        body=json.dumps({"display_name": "Second Gateway", "enabled": False, "revision": provider["revision"]}).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert status == 200
    updated = json.loads(body)["provider"]
    assert updated["display_name"] == "Second Gateway"
    assert updated["enabled"] is False

    status, _headers, body = request(ui, "DELETE", f"/api/providers/{provider['id']}")
    assert status == 200
    assert json.loads(body)["deleted"] is True

    _, _, html = request(ui, "GET", "/index.html")
    _, _, script = request(ui, "GET", "/app.js")
    page = html.decode("utf-8")
    controller = script.decode("utf-8")
    assert 'id="provider-base-url"' in page
    assert "OpenAI-compatible" in page
    assert 'apiFetch("/api/providers"' in controller


def test_model_menu_uses_live_provider_readiness_instead_of_hard_coded_state(live_ui) -> None:
    ui = live_ui[0]
    _, _, html = request(ui, "GET", "/index.html")
    _, _, script = request(ui, "GET", "/app.js")
    page = html.decode("utf-8")
    controller = script.decode("utf-8")

    assert 'id="active-model-menu-label"' in page
    assert 'id="active-model-menu-detail"' in page
    assert 'id="active-model-ready" hidden' in page
    assert ">Not configured<" not in page
    assert "renderActiveModel(values, connections, snapshot.providers || {})" in controller
    assert "!connection.built_in || state.configured" in controller
    assert "declaredModels.length === 1" in controller
    assert '$("#active-model-ready").hidden = !ready' in controller
    assert '$("#model-menu-configure").addEventListener' in controller
    assert "failureMessage = `${data.message || \"Turn failed\"}${suffix}`" in controller
    assert 'messages.append(makeMessage({ kind: "agent_error"' in controller
    assert "selected · save settings" in controller
    assert 'error.startsWith("Provider returned:")' in controller


def test_generic_provider_api_rejects_unsafe_and_stale_updates(live_ui) -> None:
    ui = live_ui[0]
    status, payload = json_request(ui, {"changes": {"default_provider": "missing_provider"}}, path="/api/settings")
    assert status == 422
    assert "enabled provider" in payload["error"]
    status, payload = json_request(ui, {"display_name": "Nope", "base_url": "http://localhost:9", "models": []}, path="/api/providers")
    assert status == 422
    assert "HTTPS" in payload["error"]

    status, created = json_request(ui, {"display_name": "Safe", "base_url": "https://safe.example/v1", "models": []}, path="/api/providers")
    assert status == 201
    provider_id = created["provider"]["id"]
    for body, expected in (({"enabled": False, "revision": 99}, 409), ({"enabled": "sometimes", "revision": 1}, 422)):
        status, _headers, _body = request(ui, "PUT", f"/api/providers/{provider_id}", body=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        assert status == expected


def test_background_turn_creation_and_resumable_sse(live_ui) -> None:
    ui, _agent, session, _life = live_ui
    status, payload = json_request(
        ui,
        {"message": "stream me"},
        path=f"/api/sessions/{session.conversation_id}/turns",
    )
    assert status == 202
    assert payload == {"turn_id": "turn-test"}
    status, headers, body = request(ui, "GET", "/api/turns/turn-test/events?after=0")
    assert status == 200
    assert headers["Content-Type"] == "text/event-stream; charset=utf-8"
    assert b"id: 1" in body
    assert b"event: turn_completed" in body


def test_turn_cancel_requires_csrf_and_accepts_valid_request(live_ui) -> None:
    ui, _agent, _session, _life = live_ui
    status, _headers, _body = request(
        ui,
        "DELETE",
        "/api/turns/turn-test",
        headers={"X-CSRF-Token": "wrong"},
    )
    assert status == 403
    status, _headers, body = request(ui, "DELETE", "/api/turns/turn-test")
    assert status == 202
    assert json.loads(body)["cancelled"] is True


def test_project_json_crud_routes(live_ui) -> None:
    ui, _agent, _session, _life = live_ui
    status, created = json_request(ui, {"name": "Lion Lab"}, path="/api/projects")
    assert status == 201
    project_id = created["id"]
    status, _headers, body = request(ui, "GET", "/api/projects")
    assert status == 200
    assert json.loads(body)["projects"][0]["name"] == "Lion Lab"
    status, _headers, body = request(
        ui,
        "PUT",
        f"/api/projects/{project_id}",
        body=b'{"name":"Roar Lab"}',
        headers={"Content-Type": "application/json"},
    )
    assert status == 200
    assert json.loads(body)["name"] == "Roar Lab"
    status, _headers, body = request(ui, "DELETE", f"/api/projects/{project_id}")
    assert status == 200
    assert json.loads(body)["deleted"] is True


def test_conversation_menu_routes_rename_pin_move_export_archive_and_delete(live_ui) -> None:
    ui, _agent, session, _life = live_ui
    conversation_id = session.conversation_id
    session.append("user", text="menu route roar")
    status, created = json_request(ui, {"name": "Lion Den"}, path="/api/projects")
    assert status == 201
    # RecordingProjects owns the route fixture; add matching durable ownership so
    # SessionBeing can enforce the same project foreign-key check as production.
    ui._storage.execute(  # type: ignore[union-attr]
        "INSERT INTO projects(id,name,workspace,created_at,updated_at) VALUES (?,?,?,?,?)",
        (created["id"], "Lion Den", None, 1, 1),
    )

    updates = (
        ({"title": "Important roar"}, "title"),
        ({"pinned": True}, "pinned"),
        ({"project_id": created["id"]}, "project_id"),
    )
    for change, expected_field in updates:
        status, _headers, body = request(
            ui,
            "PUT",
            f"/api/conversations/{conversation_id}",
            body=json.dumps(change).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert status == 200
        assert json.loads(body) == {"id": conversation_id, "updated": expected_field}

    status, _headers, body = request(ui, "GET", "/api/conversations")
    row = json.loads(body)["conversations"][0]
    assert row["title"] == "Important roar"
    assert row["pinned"] == 1
    assert row["project_id"] == created["id"]

    for format_name, content_type, needle in (
        ("markdown", "text/markdown; charset=utf-8", b"menu route roar"),
        ("json", "application/json; charset=utf-8", b'"kind": "user"'),
    ):
        status, headers, body = request(
            ui, "GET", f"/api/conversations/{conversation_id}/export?format={format_name}"
        )
        assert status == 200
        assert headers["Content-Type"] == content_type
        assert headers["Content-Disposition"].endswith(f'.{"json" if format_name == "json" else "md"}"')
        assert needle in body

    status, _headers, _body = request(
        ui,
        "PUT",
        f"/api/conversations/{conversation_id}",
        body=b'{"archived":true}',
        headers={"Content-Type": "application/json"},
    )
    assert status == 200
    assert session.list_conversations() == ()
    status, _headers, body = request(ui, "DELETE", f"/api/conversations/{conversation_id}")
    assert status == 200
    assert json.loads(body)["deleted"] is True
    assert session.conversation_id != conversation_id


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"title": "x", "pinned": True},
        {"title": 7},
        {"pinned": "yes"},
        {"project_id": 4},
        {"archived": 1},
    ),
)
def test_conversation_update_rejects_ambiguous_or_wrong_typed_payloads(live_ui, payload) -> None:
    ui, _agent, session, _life = live_ui
    status, _headers, body = request(
        ui,
        "PUT",
        f"/api/conversations/{session.conversation_id}",
        body=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert status == 422
    assert json.loads(body) == {"error": "valid update required"}


def test_conversation_update_reports_missing_chat_or_project(live_ui) -> None:
    ui, _agent, session, _life = live_ui
    status, _headers, _body = request(
        ui,
        "PUT",
        "/api/conversations/missing",
        body=b'{"pinned":true}',
        headers={"Content-Type": "application/json"},
    )
    assert status == 404
    status, _headers, body = request(
        ui,
        "PUT",
        f"/api/conversations/{session.conversation_id}",
        body=b'{"project_id":"missing"}',
        headers={"Content-Type": "application/json"},
    )
    assert status == 404
    assert json.loads(body) == {"error": "resource not found"}


def test_sidebar_chat_options_are_real_controls_not_todo_placeholders(live_ui) -> None:
    ui = live_ui[0]
    _, _, html = request(ui, "GET", "/index.html")
    _, _, script = request(ui, "GET", "/app.js")
    _, _, css = request(ui, "GET", "/styles.css")
    page = html.decode("utf-8")
    controller = script.decode("utf-8")
    stylesheet = css.decode("utf-8")
    for label in ("Rename", "Pin chat", "Move to project", "New project", "Export Markdown", "Archive", "Delete forever"):
        assert label in controller
    assert 'id="chat-action-modal"' in page
    assert "chat-options-trigger" in stylesheet
    assert "data-todo=\"Rename" not in page


def test_attachment_routes_require_auth_csrf_and_round_trip_utf8(live_ui) -> None:
    ui, _agent, session, _life = live_ui
    body = "Lion reads café notes".encode("utf-8")
    status, _headers, response = request(
        ui,
        "POST",
        "/api/attachments",
        body=body,
        headers={
            "Content-Type": "text/plain",
            "X-File-Name": "caf%C3%A9.txt",
            "X-Conversation-Id": session.conversation_id,
        },
    )
    assert status == 201
    item = json.loads(response)["attachment"]
    assert item["file_name"] == "café.txt"

    status, _headers, response = request(ui, "GET", "/api/attachments")
    assert status == 200
    assert json.loads(response)["attachments"] == [item]

    status, headers, response = request(ui, "GET", f"/api/attachments/{item['id']}/content")
    assert status == 200
    assert headers["Content-Type"] == "text/plain"
    assert response == body

    status, _headers, response = request(ui, "DELETE", f"/api/attachments/{item['id']}")
    assert status == 200
    assert json.loads(response)["deleted"] is True

    delattr(ui, "_test_cookie")
    status, _headers, _response = request(ui, "GET", "/api/attachments")
    assert status == 401


def test_web_ui_turn_api_runs_agent_and_returns_reply(live_ui) -> None:
    ui, agent, session, _life = live_ui
    session.append("assistant", text="older")
    status, payload = json_request(ui, {"message": "ROAR 🦁"})
    assert status == 200
    assert payload == {"reply": "Lion heard: ROAR 🦁"}
    assert agent.messages == ["ROAR 🦁"]
    assert len(session.events) == 3


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"message": None}, {"message": ""}, {"message": "   "}, {"message": 7}],
)
def test_web_ui_turn_api_rejects_bad_messages_without_calling_agent(live_ui, payload) -> None:
    ui, agent, session, _life = live_ui
    status, response = json_request(ui, payload)
    assert status == 422
    assert "error" in response
    assert agent.messages == []
    assert session.events == ()


def test_web_ui_turn_api_rejects_too_long_message(live_ui) -> None:
    status, payload = json_request(live_ui[0], {"message": "x" * (MAX_MESSAGE_CHARS + 1)})
    assert status == 422
    assert payload == {"error": "message is too long"}


def test_web_ui_rejects_wrong_content_type_and_malformed_json(live_ui) -> None:
    status, _headers, body = request(live_ui[0], "POST", "/api/turn", body=b"{}")
    assert status == 415
    assert json.loads(body)["error"].startswith("Content-Type")

    status, _headers, body = request(
        live_ui[0],
        "POST",
        "/api/turn",
        body=b"{broken",
        headers={"Content-Type": "application/json"},
    )
    assert status == 400
    assert json.loads(body) == {"error": "invalid JSON body"}


def test_web_ui_rejects_oversized_body_before_reading_it(live_ui) -> None:
    status, _headers, body = request(
        live_ui[0],
        "POST",
        "/api/turn",
        body=b"x" * (MAX_BODY_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert status == 413
    assert json.loads(body) == {"error": "request body too large"}


def test_web_ui_translates_agent_failure_without_leaking_details(live_ui) -> None:
    ui, agent, _session, _life = live_ui
    agent.failure = RuntimeError("secret path C:/nope")
    status, payload = json_request(ui, {"message": "boom"})
    assert status == 500
    assert payload == {"error": "turn failed", "kind": "RuntimeError"}
    assert "secret" not in json.dumps(payload)


def test_web_ui_unknown_routes_and_unsupported_methods_are_boring(live_ui) -> None:
    ui = live_ui[0]
    for method, path, expected in (
        ("GET", "/../requirements.txt", 404),
        ("POST", "/api/missing", 404),
        ("PUT", "/api/turn", 405),
    ):
        status, _headers, _body = request(ui, method, path)
        assert status == expected


def test_web_ui_health_and_head_routes(live_ui) -> None:
    ui = live_ui[0]
    status, _headers, body = request(ui, "GET", "/api/healthz")
    assert status == 200
    assert json.loads(body) == {"status": "alive", "being": "web_ui"}
    status, headers, body = request(ui, "HEAD", "/styles.css")
    assert status == 200
    assert int(headers["Content-Length"]) > 0
    assert body == b""


def test_web_ui_life_closes_socket_and_forgets_dependencies(live_ui) -> None:
    ui, _agent, _session, life = live_ui
    turns = getattr(ui, "_test_world").turns
    address = urlsplit(ui.url or "")
    life.die()
    life.die()
    assert turns.shutdown_started is True
    assert ui.url is None
    with pytest.raises(RuntimeError, match="not alive"):
        ui.submit("too late")
    with pytest.raises(OSError):
        socket.create_connection((address.hostname or "127.0.0.1", address.port or 0), timeout=0.2)


def test_web_ui_shutdown_wakes_long_lived_event_stream_immediately(live_ui) -> None:
    ui, _agent, _session, life = live_ui
    turns = getattr(ui, "_test_world").turns
    responses: list[int] = []
    turns.stream_running = True

    request_thread = Thread(
        target=lambda: responses.append(request(ui, "GET", "/api/turns/slow/events?after=0")[0])
    )
    request_thread.start()
    assert turns.entered_wait.wait(timeout=1)
    started = time.monotonic()
    life.die()
    elapsed = time.monotonic() - started
    request_thread.join(timeout=1)

    assert elapsed < 1.0
    assert responses == [200]
    assert not request_thread.is_alive()


def test_web_ui_failed_thread_start_rolls_back_server(monkeypatch, live_ui) -> None:
    closed: list[str] = []

    class FakeServer:
        server_address = ("127.0.0.1", 9999)

        def begin_shutdown(self) -> None:
            return None

        def server_close(self) -> None:
            closed.append("closed")

    class FakeThread:
        ident = None

        def start(self) -> None:
            raise RuntimeError("thread exploded")

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(
        web_ui_module,
        "create_server",
        lambda **_kwargs: (FakeServer(), FakeThread()),
    )
    ui = WebUiBeing(port=0)
    life = Life("web_ui")
    with pytest.raises(RuntimeError, match="thread exploded"):
        ui.birth(getattr(live_ui[0], "_test_world"), life)  # type: ignore[arg-type]
    life.die()
    assert closed == ["closed"]
    assert ui.url is None


def test_web_ui_rejects_second_birth(live_ui) -> None:
    ui, agent, session, _life = live_ui
    with pytest.raises(RuntimeError, match="already alive"):
        ui.birth(UiWorld(agent, session), Life("duplicate"))  # type: ignore[arg-type]


def test_event_serializer_handles_unknown_values_without_crashing() -> None:
    session = SessionBeing()
    event = session.append("odd", value=Path("hello.txt"))
    assert serialize_events((event,)) == [
        {"kind": "odd", "data": {"value": "hello.txt"}}
    ]
