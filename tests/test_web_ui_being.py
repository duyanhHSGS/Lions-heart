"""Focused product tests for the pure-Python web UI Being."""

# TODO: Add real-browser accessibility snapshots when a dependency-free QA path exists.
# TODO: Add streaming tests when the product gains a streaming event contract.

from __future__ import annotations

import http.client
import json
import socket
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import cor_beings.web_ui as web_ui_module
from cor_being import Life
from cor_beings.agent_loop import AgentLoopBeing
from cor_beings.lion import ToolCall
from cor_beings.session import SessionBeing
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


class UiWorld:
    name = "web-test"
    news = None
    alive = ()

    def __init__(self, agent: RecordingAgent, session: SessionBeing) -> None:
        self.agent = agent
        self.session = session

    def need(self, being_type):
        if being_type is AgentLoopBeing:
            return self.agent
        if being_type is SessionBeing:
            return self.session
        raise LookupError(being_type)


@pytest.fixture
def live_ui() -> Iterator[tuple[WebUiBeing, RecordingAgent, SessionBeing, Life]]:
    session = SessionBeing()
    agent = RecordingAgent(session)
    ui = WebUiBeing(port=0)
    life = Life("web_ui")
    ui.birth(UiWorld(agent, session), life)  # type: ignore[arg-type]
    try:
        yield ui, agent, session, life
    finally:
        life.die()


def request(
    ui: WebUiBeing,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    parsed = urlsplit(ui.url or "")
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2)
    try:
        connection.request(method, path, body=body, headers=headers or {})
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
        "Model hub",
        "Projects",
        "Images",
        "Video",
        "Train",
        "More",
        "Audio",
        "Recipes",
        "Export",
        "API",
        "Settings",
        "Guided Tour",
        "Add photos &amp; files",
        "Web search",
        "Code",
        "Chat with Files",
        "MCP",
        "Ask for approval",
        "Approve for me",
        "Run automatically",
        "Full access",
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


def test_frontend_has_no_node_manifest_or_external_runtime_assets() -> None:
    static = ROOT / "cor_beings" / "web_ui" / "static"
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (static / "index.html", static / "styles.css", static / "app.js")
    )
    assert not (ROOT / "package.json").exists()
    assert not (ROOT / "package-lock.json").exists()
    assert "https://" not in combined
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
    }


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


def test_web_ui_translates_agent_failure_without_leaking_details() -> None:
    session = SessionBeing()
    agent = RecordingAgent(session, failure=RuntimeError("secret path C:/nope"))
    ui = WebUiBeing(port=0)
    life = Life("web_ui")
    ui.birth(UiWorld(agent, session), life)  # type: ignore[arg-type]
    try:
        status, payload = json_request(ui, {"message": "boom"})
        assert status == 500
        assert payload == {"error": "turn failed", "kind": "RuntimeError"}
        assert "secret" not in json.dumps(payload)
    finally:
        life.die()


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
    address = urlsplit(ui.url or "")
    life.die()
    life.die()
    assert ui.url is None
    with pytest.raises(RuntimeError, match="not alive"):
        ui.submit("too late")
    with pytest.raises(OSError):
        socket.create_connection((address.hostname or "127.0.0.1", address.port or 0), timeout=0.2)


def test_web_ui_failed_thread_start_rolls_back_server(monkeypatch) -> None:
    closed: list[str] = []

    class FakeServer:
        server_address = ("127.0.0.1", 9999)

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
    session = SessionBeing()
    ui = WebUiBeing(port=0)
    life = Life("web_ui")
    with pytest.raises(RuntimeError, match="thread exploded"):
        ui.birth(UiWorld(RecordingAgent(session), session), life)  # type: ignore[arg-type]
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
