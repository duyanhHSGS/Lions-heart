"""Dependency-free HTTP transport for the Lions-heart web UI."""

# TODO: Add configurable body/message limits when product configuration exists.
# TODO: Add upload-specific streaming limits before attachment routes land.

from __future__ import annotations

import json
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from typing import Any
from urllib.parse import parse_qs, urlsplit

from cor_beings.attachments import MAX_ATTACHMENT_BYTES
from cor_beings.turn_manager import TERMINAL_STATUSES

from cor_beings.session import SessionEvent

MAX_BODY_BYTES = 64 * 1024
MAX_MESSAGE_CHARS = 16 * 1024

StaticAsset = tuple[str, bytes, bool]
SubmitTurn = Callable[[str], str]
SnapshotSession = Callable[[], tuple[SessionEvent, ...]]


@dataclass(frozen=True, slots=True)
class WebCallbacks:
    """Narrow feature callbacks exposed to the HTTP transport."""

    auth_status: Callable[[str | None], dict[str, object]]
    setup: Callable[[str, str], tuple[str, str, int]]
    login: Callable[[str, str, str], tuple[str, str, int]]
    authenticate: Callable[[str | None], bool]
    validate_csrf: Callable[[str | None, str | None], bool]
    logout: Callable[[str | None], None]
    settings_snapshot: Callable[[], dict[str, object]]
    update_settings: Callable[[Mapping[str, object]], Mapping[str, object]]
    set_provider_key: Callable[[str, str], None]
    delete_provider_key: Callable[[str], None]
    list_models: Callable[[str, bool], tuple[str, ...]]
    list_provider_connections: Callable[[], tuple[dict[str, object], ...]]
    create_provider_connection: Callable[[str, str, object, str | None], dict[str, object]]
    update_provider_connection: Callable[..., dict[str, object]]
    delete_provider_connection: Callable[[str], None]
    list_conversations: Callable[[], tuple[dict[str, object], ...]]
    active_conversation: Callable[[], tuple[str, bool]]
    new_conversation: Callable[[str, bool], str]
    open_conversation: Callable[[str], None]
    rename_conversation: Callable[[str, str], None]
    pin_conversation: Callable[[str, bool], None]
    assign_conversation_project: Callable[[str, str | None], None]
    archive_conversation: Callable[[str, bool], None]
    delete_conversation: Callable[[str], None]
    export_conversation: Callable[[str, str], str]
    list_projects: Callable[[], tuple[dict[str, object], ...]]
    create_project: Callable[[str, str | None], str]
    rename_project: Callable[[str, str], None]
    delete_project: Callable[[str], None]
    create_turn: Callable[[str, str], str]
    turn_events: Callable[[str, int], tuple[tuple[dict[str, object], ...], str]]
    wait_for_turn_events: Callable[[str, int, float], None]
    cancel_turn: Callable[[str], None]
    approvals: Callable[[str], tuple[dict[str, object], ...]]
    decide_approval: Callable[[str, bool], None]
    upload_attachment: Callable[[str, str, bytes, str, bool], dict[str, object]]
    list_attachments: Callable[[str], tuple[dict[str, object], ...]]
    download_attachment: Callable[[str], tuple[dict[str, object], bytes]]
    delete_attachment: Callable[[str], None]
    list_saved_prompts: Callable[[str], tuple[dict[str, object], ...]]
    create_saved_prompt: Callable[[str, str, str | None], dict[str, object]]
    update_saved_prompt: Callable[[str, str, str, int], dict[str, object]]
    delete_saved_prompt: Callable[[str], None]
    list_mcp: Callable[[], tuple[dict[str, object], ...]]
    create_mcp: Callable[[str, str, Mapping[str, object], str | None], dict[str, object]]
    import_mcp: Callable[[list[Mapping[str, object]]], tuple[dict[str, object], ...]]
    update_mcp: Callable[[str, str, str, Mapping[str, object], bool, str | None, bool], dict[str, object]]
    delete_mcp: Callable[[str], None]
    refresh_mcp: Callable[[str], dict[str, object]]
    list_media: Callable[[str | None], tuple[dict[str, object], ...]]
    get_media: Callable[[str], dict[str, object]]
    download_media: Callable[[str], tuple[dict[str, object], Path]]
    cancel_media: Callable[[str], dict[str, object]]
    delete_media: Callable[[str], None]
    generate_image: Callable[..., dict[str, object]]
    generate_audio: Callable[..., dict[str, object]]
    generate_video: Callable[..., dict[str, object]]
    list_recipes: Callable[[], tuple[dict[str, object], ...]]
    create_recipe: Callable[[str, Mapping[str, object], str], dict[str, object]]
    run_recipe: Callable[[str, Mapping[str, object]], dict[str, object]]
    recipe_history: Callable[[str], tuple[dict[str, object], ...]]
    delete_recipe: Callable[[str], None]
    list_activity: Callable[[], tuple[dict[str, object], ...]]
    activity_totals: Callable[[], dict[str, object]]
    select_project: Callable[[str], dict[str, object]]
    secure_cookie: bool = False


class WebUiHttpServer(ThreadingHTTPServer):
    """Small same-origin server whose request threads finish before close."""

    allow_reuse_address = True
    daemon_threads = False
    block_on_close = True

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.shutdown_requested = Event()
        super().__init__(*args, **kwargs)

    def begin_shutdown(self) -> None:
        """Tell long-lived request handlers to leave before socket close joins them."""
        self.shutdown_requested.set()


def _load_static_assets() -> dict[str, StaticAsset]:
    """Load the tiny frontend once so request hot paths do not touch disk."""
    static_dir = Path(__file__).with_name("static")
    css = (static_dir / "styles.css").read_bytes()
    markdown = (static_dir / "markdown.js").read_bytes()
    markdown_route = f"/markdown.{hashlib.sha256(markdown).hexdigest()[:12]}.js"
    script = (static_dir / "app.js").read_bytes().replace(
        b'"./markdown.js"', f'"{markdown_route}"'.encode("ascii")
    )
    css_route = f"/styles.{hashlib.sha256(css).hexdigest()[:12]}.css"
    script_route = f"/app.{hashlib.sha256(script).hexdigest()[:12]}.js"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/styles.css"', f'href="{css_route}"')
    html = html.replace('src="/app.js"', f'src="{script_route}"')
    page = html.encode("utf-8")
    return {
        "/": ("text/html; charset=utf-8", page, False),
        "/index.html": ("text/html; charset=utf-8", page, False),
        "/styles.css": ("text/css; charset=utf-8", css, False),
        "/app.js": ("text/javascript; charset=utf-8", script, False),
        "/markdown.js": ("text/javascript; charset=utf-8", markdown, False),
        css_route: ("text/css; charset=utf-8", css, True),
        script_route: ("text/javascript; charset=utf-8", script, True),
        markdown_route: ("text/javascript; charset=utf-8", markdown, True),
    }


def _json_safe(value: object) -> object:
    """Convert immutable product values into deterministic JSON values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_safe(getattr(value, field.name))
            for field in fields(value)
        }
    return str(value)


def serialize_events(events: tuple[SessionEvent, ...]) -> list[dict[str, object]]:
    """Serialize one stable session slice for the browser."""
    return [{"kind": event.kind, "data": _json_safe(event.data)} for event in events]


def _encode_json(payload: object) -> bytes:
    return json.dumps(
        _json_safe(payload),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _handler_type(
    *,
    submit: SubmitTurn,
    snapshot: SnapshotSession,
    assets: Mapping[str, StaticAsset],
    callbacks: WebCallbacks | None = None,
) -> type[BaseHTTPRequestHandler]:
    class WebUiRequestHandler(BaseHTTPRequestHandler):
        server_version = "LionsHeart"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            """Keep the product terminal quiet; the UI owns request feedback."""

        def _send_bytes(
            self,
            status: HTTPStatus,
            body: bytes,
            content_type: str,
            *,
            include_body: bool = True,
            extra_headers: Mapping[str, str] | None = None,
            immutable: bool = False,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Cache-Control",
                "public, max-age=31536000, immutable" if immutable else "no-store",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'",
            )
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if include_body:
                self.wfile.write(body)

        def _send_json(
            self,
            status: HTTPStatus,
            payload: object,
            *,
            include_body: bool = True,
            extra_headers: Mapping[str, str] | None = None,
        ) -> None:
            self._send_bytes(
                status,
                _encode_json(payload),
                "application/json; charset=utf-8",
                include_body=include_body,
                extra_headers=extra_headers,
            )

        def _send_file(self, status: HTTPStatus, path: Path, content_type: str, *, include_body: bool = True) -> None:
            size = path.stat().st_size
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            if include_body:
                with path.open("rb") as handle:
                    while chunk := handle.read(64 * 1024): self.wfile.write(chunk)

        def _cookies(self) -> dict[str, str]:
            cookies: dict[str, str] = {}
            for part in self.headers.get("Cookie", "").split(";"):
                name, separator, value = part.strip().partition("=")
                if separator and name:
                    cookies[name] = value
            return cookies

        def _auth_token(self) -> str | None:
            return self._cookies().get("lion_session")

        def _authorized(self) -> bool:
            if callbacks is None or callbacks.authenticate(self._auth_token()):
                return True
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
            return False

        def _csrf_valid(self) -> bool:
            if callbacks is None:
                return True
            token = self.headers.get("X-CSRF-Token")
            if callbacks.validate_csrf(self._auth_token(), token):
                return True
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "invalid CSRF token"})
            return False

        def _discard_oversized_prefix(self, length: int) -> None:
            """Drain one bounded prefix so Windows can deliver the 413 cleanly."""
            remaining = min(length, MAX_BODY_BYTES + 1)
            previous_timeout = self.connection.gettimeout()
            try:
                self.connection.settimeout(0.05)
                while remaining:
                    chunk = self.rfile.read(min(remaining, 8192))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except (OSError, TimeoutError):
                pass
            finally:
                self.connection.settimeout(previous_timeout)

        def _serve_get(self, *, include_body: bool) -> None:
            parsed = urlsplit(self.path)
            path = parsed.path
            asset = assets.get(path)
            if asset is not None:
                content_type, body, immutable = asset
                self._send_bytes(
                    HTTPStatus.OK,
                    body,
                    content_type,
                    include_body=include_body,
                    immutable=immutable,
                )
                return
            if path == "/api/session":
                if not self._authorized():
                    return
                events = snapshot()
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "events": serialize_events(events),
                        "count": len(events),
                        "conversation_id": callbacks.active_conversation()[0] if callbacks else None,
                        "temporary": callbacks.active_conversation()[1] if callbacks else False,
                    },
                    include_body=include_body,
                )
                return
            if path == "/api/auth/status" and callbacks is not None:
                self._send_json(HTTPStatus.OK, callbacks.auth_status(self._auth_token()), include_body=include_body)
                return
            if path == "/api/settings" and callbacks is not None:
                if not self._authorized():
                    return
                self._send_json(HTTPStatus.OK, callbacks.settings_snapshot(), include_body=include_body)
                return
            if path == "/api/providers" and callbacks is not None:
                if not self._authorized():
                    return
                self._send_json(HTTPStatus.OK, {"providers": callbacks.list_provider_connections()}, include_body=include_body)
                return
            if path == "/api/conversations" and callbacks is not None:
                if not self._authorized():
                    return
                self._send_json(
                    HTTPStatus.OK,
                    {"conversations": callbacks.list_conversations()},
                    include_body=include_body,
                )
                return
            if callbacks is not None and len(parts := path.strip("/").split("/")) == 4 and parts[:2] == ["api", "conversations"] and parts[3] == "export":
                if not self._authorized():
                    return
                format_name = parse_qs(parsed.query).get("format", ["markdown"])[0]
                try:
                    exported = callbacks.export_conversation(parts[2], format_name)
                except LookupError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "conversation not found"}, include_body=include_body)
                    return
                except ValueError as error:
                    self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)}, include_body=include_body)
                    return
                suffix = "json" if format_name == "json" else "md"
                mime = "application/json; charset=utf-8" if suffix == "json" else "text/markdown; charset=utf-8"
                self._send_bytes(
                    HTTPStatus.OK,
                    exported.encode("utf-8"),
                    mime,
                    include_body=include_body,
                    extra_headers={"Content-Disposition": f'attachment; filename="lion-chat-{parts[2][:12]}.{suffix}"'},
                )
                return
            if path == "/api/projects" and callbacks is not None:
                if not self._authorized():
                    return
                self._send_json(
                    HTTPStatus.OK,
                    {"projects": callbacks.list_projects()},
                    include_body=include_body,
                )
                return
            if path == "/api/saved-prompts" and callbacks is not None:
                if not self._authorized(): return
                query = parse_qs(parsed.query).get("q", [""])[0]
                try: rows = callbacks.list_saved_prompts(query)
                except (TypeError, ValueError) as error:
                    self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)}, include_body=include_body); return
                self._send_json(HTTPStatus.OK, {"prompts": rows}, include_body=include_body); return
            if path == "/api/mcp/connections" and callbacks is not None:
                if not self._authorized(): return
                self._send_json(HTTPStatus.OK, {"connections": callbacks.list_mcp()}, include_body=include_body); return
            if path == "/api/media" and callbacks is not None:
                if not self._authorized(): return
                kind = parse_qs(parsed.query).get("kind", [None])[0]
                try: rows = callbacks.list_media(kind)
                except ValueError as error:
                    self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)}, include_body=include_body); return
                self._send_json(HTTPStatus.OK, {"jobs": rows}, include_body=include_body); return
            if path == "/api/recipes" and callbacks is not None:
                if not self._authorized(): return
                self._send_json(HTTPStatus.OK, {"recipes": callbacks.list_recipes()}, include_body=include_body); return
            if path == "/api/activity" and callbacks is not None:
                if not self._authorized(): return
                self._send_json(HTTPStatus.OK, {"activity": callbacks.list_activity()}, include_body=include_body); return
            if path == "/api/activity/totals" and callbacks is not None:
                if not self._authorized(): return
                self._send_json(HTTPStatus.OK, callbacks.activity_totals(), include_body=include_body); return
            if path == "/api/attachments" and callbacks is not None:
                if not self._authorized():
                    return
                conversation_id = parse_qs(parsed.query).get("conversation_id", [""])[0]
                if not conversation_id:
                    conversation_id = callbacks.active_conversation()[0]
                try:
                    rows = callbacks.list_attachments(conversation_id)
                except LookupError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "conversation not found"}, include_body=include_body)
                    return
                self._send_json(HTTPStatus.OK, {"attachments": rows}, include_body=include_body)
                return
            parts = path.strip("/").split("/")
            if callbacks is not None and len(parts) == 3 and parts[:2] == ["api", "media"]:
                if not self._authorized(): return
                try: item = callbacks.get_media(parts[2])
                except LookupError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "media job not found"}, include_body=include_body); return
                self._send_json(HTTPStatus.OK, {"job": item}, include_body=include_body); return
            if callbacks is not None and len(parts) == 4 and parts[:2] == ["api", "media"] and parts[3] == "content":
                if not self._authorized(): return
                try: item, file_path = callbacks.download_media(parts[2])
                except LookupError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "media job not found"}, include_body=include_body); return
                except RuntimeError:
                    self._send_json(HTTPStatus.CONFLICT, {"error": "media output unavailable"}, include_body=include_body); return
                self._send_file(HTTPStatus.OK, file_path, str(item["output_mime"]), include_body=include_body); return
            if callbacks is not None and len(parts) == 4 and parts[:2] == ["api", "recipes"] and parts[3] == "history":
                if not self._authorized(): return
                try: rows = callbacks.recipe_history(parts[2])
                except LookupError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "recipe not found"}, include_body=include_body); return
                self._send_json(HTTPStatus.OK, {"runs": rows}, include_body=include_body); return
            if callbacks is not None and len(parts) == 4 and parts[:2] == ["api", "attachments"] and parts[3] == "content":
                if not self._authorized():
                    return
                try:
                    metadata, body = callbacks.download_attachment(parts[2])
                except LookupError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "attachment not found"}, include_body=include_body)
                    return
                except RuntimeError:
                    self._send_json(HTTPStatus.CONFLICT, {"error": "attachment content unavailable"}, include_body=include_body)
                    return
                safe_ascii = str(metadata["file_name"]).encode("ascii", "ignore").decode("ascii") or "attachment.txt"
                self._send_bytes(
                    HTTPStatus.OK, body, str(metadata["mime_type"]), include_body=include_body,
                    extra_headers={"Content-Disposition": f'attachment; filename="{safe_ascii.replace(chr(34), "")}"'},
                )
                return
            if (
                callbacks is not None
                and len(parts) == 4
                and parts[:2] == ["api", "turns"]
                and parts[3] == "events"
            ):
                if not self._authorized():
                    return
                raw_after = parse_qs(parsed.query).get("after", ["0"])[0]
                try:
                    after = int(raw_after)
                    if after < 0:
                        raise ValueError
                except ValueError:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "after must be a non-negative integer"})
                    return
                self._serve_turn_events(parts[2], after, include_body=include_body)
                return
            if (
                callbacks is not None
                and len(parts) == 4
                and parts[:2] == ["api", "turns"]
                and parts[3] == "approvals"
            ):
                if not self._authorized():
                    return
                try:
                    approvals = callbacks.approvals(parts[2])
                except LookupError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "turn not found"})
                    return
                self._send_json(HTTPStatus.OK, {"approvals": approvals}, include_body=include_body)
                return
            if path.startswith("/api/providers/") and path.endswith("/models") and callbacks is not None:
                if not self._authorized():
                    return
                provider = path.removeprefix("/api/providers/").removesuffix("/models").strip("/")
                try:
                    models = callbacks.list_models(provider, False)
                except (LookupError, ValueError):
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "provider not found"}, include_body=include_body)
                    return
                except Exception as error:  # noqa: BLE001 - redact provider boundary.
                    self._send_json(
                        HTTPStatus.BAD_GATEWAY,
                        {"error": "model discovery failed", "kind": type(error).__name__},
                        include_body=include_body,
                    )
                    return
                self._send_json(HTTPStatus.OK, {"provider": provider, "models": models}, include_body=include_body)
                return
            if path == "/api/healthz":
                self._send_json(
                    HTTPStatus.OK,
                    {"status": "alive", "being": "web_ui"},
                    include_body=include_body,
                )
                return
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "route not found"},
                include_body=include_body,
            )

        def _serve_turn_events(self, turn_id: str, after: int, *, include_body: bool) -> None:
            if callbacks is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
                return
            try:
                callbacks.turn_events(turn_id, after)
            except LookupError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "turn not found"})
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if not include_body:
                return
            sequence = after
            try:
                while not self.server.shutdown_requested.is_set():  # type: ignore[attr-defined]
                    events, status = callbacks.turn_events(turn_id, sequence)
                    for event in events:
                        sequence = int(event["sequence"])
                        payload = json.dumps(event["data"], ensure_ascii=False, separators=(",", ":"))
                        frame = f"id: {sequence}\nevent: {event['kind']}\ndata: {payload}\n\n"
                        self.wfile.write(frame.encode("utf-8"))
                    if events:
                        self.wfile.flush()
                    if status in TERMINAL_STATUSES:
                        break
                    callbacks.wait_for_turn_events(turn_id, sequence, 10.0)
                    if self.server.shutdown_requested.is_set():  # type: ignore[attr-defined]
                        break
                    if not events:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                self.close_connection = True

        def do_GET(self) -> None:
            self._serve_get(include_body=True)

        def do_HEAD(self) -> None:
            self._serve_get(include_body=False)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            parts = path.strip("/").split("/")
            dynamic_turn = len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] == "turns"
            dynamic_approval = len(parts) == 4 and parts[:2] == ["api", "approvals"] and parts[3] == "decision"
            if path == "/api/attachments" and callbacks is not None:
                self._upload_attachment()
                return
            allowed = {
                "/api/turn",
                "/api/auth/setup",
                "/api/auth/login",
                "/api/auth/logout",
                "/api/settings",
                "/api/provider-key",
                "/api/providers",
                "/api/conversations",
                "/api/conversations/open",
                "/api/projects",
                "/api/saved-prompts",
                "/api/mcp/connections",
                "/api/mcp/import",
            }
            dynamic_prompt = len(parts) == 3 and parts[:2] == ["api", "saved-prompts"]
            dynamic_mcp_test = len(parts) == 4 and parts[:2] == ["api", "mcp"] and parts[3] in ("test", "refresh")
            dynamic_media_cancel = len(parts) == 4 and parts[:2] == ["api", "media"] and parts[3] == "cancel"
            dynamic_recipe_run = len(parts) == 4 and parts[:2] == ["api", "recipes"] and parts[3] == "run"
            dynamic_project_select = len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "select"
            media_submit = path in ("/api/images", "/api/audio", "/api/video")
            recipe_create = path == "/api/recipes"
            if path not in allowed and not dynamic_turn and not dynamic_approval and not dynamic_prompt and not dynamic_mcp_test and not dynamic_media_cancel and not dynamic_recipe_run and not dynamic_project_select and not media_submit and not recipe_create:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
                return

            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
            if content_type != "application/json":
                raw_discard_length = self.headers.get("Content-Length", "0")
                try:
                    discard_length = int(raw_discard_length)
                except ValueError:
                    discard_length = -1
                if 0 <= discard_length <= MAX_BODY_BYTES:
                    self.rfile.read(discard_length)
                else:
                    self.close_connection = True
                self._send_json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"error": "Content-Type must be application/json"},
                )
                return

            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                self._send_json(
                    HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length required"}
                )
                return
            try:
                length = int(raw_length)
            except ValueError:
                self._send_json(
                    HTTPStatus.BAD_REQUEST, {"error": "invalid Content-Length"}
                )
                return
            if length < 0:
                self._send_json(
                    HTTPStatus.BAD_REQUEST, {"error": "invalid Content-Length"}
                )
                return
            if length > MAX_BODY_BYTES:
                self._discard_oversized_prefix(length)
                self.close_connection = True
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": "request body too large"},
                )
                return

            body = self.rfile.read(length)
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON body"})
                return

            if not isinstance(payload, dict):
                self._send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": "JSON body must be an object"},
                )
                return
            message = payload.get("message")
            if path == "/api/turn" or dynamic_turn:
                if not isinstance(message, str) or not message.strip():
                    self._send_json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "message must be a non-empty string"},
                    )
                    return
                if len(message) > MAX_MESSAGE_CHARS:
                    self._send_json(
                        HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "message is too long"}
                    )
                    return

            if callbacks is not None and path in ("/api/auth/setup", "/api/auth/login"):
                username = payload.get("username")
                password = payload.get("password")
                if not isinstance(username, str) or not isinstance(password, str):
                    self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "username and password are required"})
                    return
                try:
                    if path == "/api/auth/setup":
                        token, csrf, expires = callbacks.setup(username, password)
                    else:
                        token, csrf, expires = callbacks.login(username, password, self.client_address[0])
                except PermissionError:
                    self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid credentials"})
                    return
                except (ValueError, RuntimeError) as error:
                    self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
                    return
                secure = "; Secure" if callbacks.secure_cookie else ""
                cookie = f"lion_session={token}; Path=/; HttpOnly; SameSite=Strict{secure}; Max-Age={max(0, expires - int(__import__('time').time()))}"
                self._send_json(
                    HTTPStatus.OK,
                    {"authenticated": True, "csrf_token": csrf, "expires_at": expires},
                    extra_headers={"Set-Cookie": cookie},
                )
                return

            if callbacks is not None:
                if not self._authorized() or not self._csrf_valid():
                    return
                if path == "/api/auth/logout":
                    callbacks.logout(self._auth_token())
                    self._send_json(
                        HTTPStatus.OK,
                        {"authenticated": False},
                        extra_headers={"Set-Cookie": "lion_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"},
                    )
                    return
                if path == "/api/settings":
                    changes = payload.get("changes")
                    if not isinstance(changes, dict):
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "changes must be an object"})
                        return
                    try:
                        values = callbacks.update_settings(changes)
                    except (TypeError, ValueError) as error:
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
                        return
                    self._send_json(HTTPStatus.OK, {"values": values})
                    return
                if path == "/api/provider-key":
                    provider = payload.get("provider")
                    secret = payload.get("secret")
                    try:
                        if payload.get("delete") is True and isinstance(provider, str):
                            callbacks.delete_provider_key(provider)
                        elif isinstance(provider, str) and isinstance(secret, str):
                            callbacks.set_provider_key(provider, secret)
                        else:
                            raise ValueError("provider and secret are required")
                    except ValueError as error:
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
                        return
                    self._send_json(HTTPStatus.OK, callbacks.settings_snapshot())
                    return
                if path == "/api/providers":
                    try:
                        connection = callbacks.create_provider_connection(
                            payload.get("display_name"), payload.get("base_url"),
                            payload.get("models", []), payload.get("secret"),
                        )
                    except (TypeError, ValueError) as error:
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
                        return
                    self._send_json(HTTPStatus.CREATED, {"provider": connection})
                    return
                if path == "/api/conversations":
                    title = payload.get("title", "New chat")
                    temporary = payload.get("temporary", False)
                    if not isinstance(title, str) or not isinstance(temporary, bool):
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "invalid conversation request"})
                        return
                    conversation_id = callbacks.new_conversation(title, temporary)
                    self._send_json(HTTPStatus.CREATED, {"id": conversation_id, "temporary": temporary})
                    return
                if path == "/api/conversations/open":
                    conversation_id = payload.get("id")
                    if not isinstance(conversation_id, str):
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "id is required"})
                        return
                    try:
                        callbacks.open_conversation(conversation_id)
                    except LookupError:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "conversation not found"})
                        return
                    self._send_json(HTTPStatus.OK, {"id": conversation_id})
                    return
                if path == "/api/projects":
                    name = payload.get("name")
                    workspace = payload.get("workspace")
                    if not isinstance(name, str) or workspace is not None and not isinstance(workspace, str):
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "invalid project request"})
                        return
                    try:
                        project_id = callbacks.create_project(name, workspace)
                    except ValueError as error:
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
                        return
                    self._send_json(HTTPStatus.CREATED, {"id": project_id})
                    return
                if path == "/api/saved-prompts":
                    try:
                        item = callbacks.create_saved_prompt(payload.get("name"), payload.get("body"), payload.get("project_id"))
                    except LookupError:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "project not found"}); return
                    except (TypeError, ValueError) as error:
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)}); return
                    self._send_json(HTTPStatus.CREATED, {"prompt": item}); return
                if dynamic_prompt:
                    try:
                        item = callbacks.update_saved_prompt(parts[2], payload.get("name"), payload.get("body"), payload.get("revision"))
                    except LookupError:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "saved prompt not found"}); return
                    except RuntimeError as error:
                        self._send_json(HTTPStatus.CONFLICT, {"error": str(error)}); return
                    except (TypeError, ValueError) as error:
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)}); return
                    self._send_json(HTTPStatus.OK, {"prompt": item}); return
                if path in ("/api/mcp/connections", "/api/mcp/import"):
                    items = payload.get("connections") if path.endswith("import") else [payload]
                    if not isinstance(items, list) or not items or len(items) > 32:
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "connections must be a bounded list"}); return
                    try:
                        if path.endswith("import"):
                            created = list(callbacks.import_mcp(items))
                        else:
                            item = items[0]
                            if not isinstance(item, dict): raise ValueError("connection must be an object")
                            created = [callbacks.create_mcp(item.get("name"), item.get("transport"), item.get("config"), item.get("credential"))]
                    except (TypeError, ValueError) as error:
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)}); return
                    self._send_json(HTTPStatus.CREATED, {"connections": created}); return
                if dynamic_mcp_test:
                    try: item = callbacks.refresh_mcp(parts[2])
                    except LookupError:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "MCP connection not found"}); return
                    except Exception as error:
                        self._send_json(HTTPStatus.BAD_GATEWAY, {"error": "MCP connection test failed", "kind": type(error).__name__}); return
                    self._send_json(HTTPStatus.OK, {"connection": item}); return
                if media_submit:
                    common = {"prompt": payload.get("prompt"), "provider": payload.get("provider"), "model": payload.get("model")}
                    try:
                        if path == "/api/images":
                            item = callbacks.generate_image(**common, size=payload.get("size", "1024x1024"), quality=payload.get("quality", "auto"), count=payload.get("count", 1), seed=payload.get("seed"))
                        elif path == "/api/audio":
                            item = callbacks.generate_audio(**common, duration_seconds=payload.get("duration_seconds"), format=payload.get("format", "mp3"))
                        else:
                            item = callbacks.generate_video(**common, duration_seconds=payload.get("duration_seconds", 5), width=payload.get("width", 1280), height=payload.get("height", 720))
                    except (TypeError, ValueError) as error:
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)}); return
                    except RuntimeError as error:
                        self._send_json(HTTPStatus.CONFLICT, {"error": str(error)}); return
                    self._send_json(HTTPStatus.ACCEPTED, {"job": item}); return
                if dynamic_media_cancel:
                    try: item = callbacks.cancel_media(parts[2])
                    except LookupError:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "media job not found"}); return
                    self._send_json(HTTPStatus.OK, {"job": item}); return
                if recipe_create:
                    graph = payload.get("graph")
                    if not isinstance(graph, dict):
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "graph must be an object"}); return
                    try: item = callbacks.create_recipe(payload.get("name"), graph, payload.get("description", ""))
                    except (TypeError, ValueError) as error:
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)}); return
                    self._send_json(HTTPStatus.CREATED, {"recipe": item}); return
                if dynamic_recipe_run:
                    inputs = payload.get("inputs", {})
                    if not isinstance(inputs, dict):
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "inputs must be an object"}); return
                    try: item = callbacks.run_recipe(parts[2], inputs)
                    except LookupError:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "recipe not found"}); return
                    except (TypeError, ValueError) as error:
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)}); return
                    self._send_json(HTTPStatus.ACCEPTED, {"run": item}); return
                if dynamic_project_select:
                    try: item = callbacks.select_project(parts[2])
                    except LookupError:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "project not found"}); return
                    except ValueError as error:
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)}); return
                    self._send_json(HTTPStatus.OK, {"project": item}); return
                if dynamic_turn:
                    try:
                        turn_id = callbacks.create_turn(parts[2], message)
                    except LookupError:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "conversation not found"})
                        return
                    except RuntimeError as error:
                        self._send_json(HTTPStatus.CONFLICT, {"error": str(error)})
                        return
                    self._send_json(HTTPStatus.ACCEPTED, {"turn_id": turn_id})
                    return
                if dynamic_approval:
                    approved = payload.get("approved")
                    if not isinstance(approved, bool):
                        self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "approved must be a boolean"})
                        return
                    try:
                        callbacks.decide_approval(parts[2], approved)
                    except LookupError:
                        self._send_json(HTTPStatus.NOT_FOUND, {"error": "approval not found"})
                        return
                    except RuntimeError as error:
                        self._send_json(HTTPStatus.CONFLICT, {"error": str(error)})
                        return
                    self._send_json(HTTPStatus.OK, {"id": parts[2], "approved": approved})
                    return

            if path != "/api/turn":
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "route not found"})
                return

            try:
                reply = submit(message)
            except Exception as error:  # noqa: BLE001 - HTTP boundary translates product failures.
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "turn failed", "kind": type(error).__name__},
                )
                return

            self._send_json(
                HTTPStatus.OK,
                {"reply": reply},
            )

        def _upload_attachment(self) -> None:
            if callbacks is None or not self._authorized() or not self._csrf_valid():
                return
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                self._send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length required"})
                return
            try:
                length = int(raw_length)
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid Content-Length"})
                return
            if length < 0 or length > MAX_ATTACHMENT_BYTES:
                if length > MAX_ATTACHMENT_BYTES:
                    self.close_connection = True
                self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "attachment too large"})
                return
            file_name = self.headers.get("X-File-Name", "")
            try:
                file_name = __import__("urllib.parse", fromlist=["unquote"]).unquote(file_name)
            except Exception:
                file_name = ""
            mime_type = self.headers.get("Content-Type", "")
            conversation_id, temporary = callbacks.active_conversation()
            requested_conversation = self.headers.get("X-Conversation-Id")
            if requested_conversation and requested_conversation != conversation_id:
                self._send_json(HTTPStatus.CONFLICT, {"error": "conversation is not active"})
                return
            body = self.rfile.read(length)
            try:
                metadata = callbacks.upload_attachment(file_name, mime_type, body, conversation_id, temporary)
            except LookupError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "conversation not found"})
                return
            except (TypeError, ValueError) as error:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
                return
            self._send_json(HTTPStatus.CREATED, {"attachment": metadata})

        def _method_not_allowed(self) -> None:
            self._send_json(
                HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"}
            )

        def do_DELETE(self) -> None:
            path = urlsplit(self.path).path
            parts = path.strip("/").split("/")
            valid_delete = len(parts) == 3 and parts[0] == "api" and parts[1] in ("turns", "projects", "providers", "conversations", "attachments", "saved-prompts", "media", "recipes")
            mcp_delete = len(parts) == 4 and parts[:3] == ["api", "mcp", "connections"]
            if callbacks is None or not (valid_delete or mcp_delete):
                self._method_not_allowed()
                return
            if not self._authorized() or not self._csrf_valid():
                return
            try:
                if parts[1] == "turns":
                    callbacks.cancel_turn(parts[2])
                elif parts[1] == "projects":
                    callbacks.delete_project(parts[2])
                elif parts[1] == "providers":
                    callbacks.delete_provider_connection(parts[2])
                elif parts[1] == "conversations":
                    callbacks.delete_conversation(parts[2])
                elif parts[1] == "attachments":
                    callbacks.delete_attachment(parts[2])
                elif parts[1] == "saved-prompts":
                    callbacks.delete_saved_prompt(parts[2])
                elif parts[1] == "media":
                    callbacks.delete_media(parts[2])
                elif parts[1] == "recipes":
                    callbacks.delete_recipe(parts[2])
                else:
                    callbacks.delete_mcp(parts[3])
            except LookupError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "resource not found"})
                return
            except ValueError as error:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
                return
            except RuntimeError as error:
                self._send_json(HTTPStatus.CONFLICT, {"error": str(error)})
                return
            if parts[1] == "turns":
                self._send_json(HTTPStatus.ACCEPTED, {"turn_id": parts[2], "cancelled": True})
            elif parts[1] == "projects":
                self._send_json(HTTPStatus.OK, {"project_id": parts[2], "deleted": True})
            elif parts[1] == "providers":
                self._send_json(HTTPStatus.OK, {"provider_id": parts[2], "deleted": True})
            elif parts[1] == "conversations":
                self._send_json(HTTPStatus.OK, {"conversation_id": parts[2], "deleted": True})
            elif parts[1] == "attachments":
                self._send_json(HTTPStatus.OK, {"attachment_id": parts[2], "deleted": True})
            elif parts[1] == "saved-prompts":
                self._send_json(HTTPStatus.OK, {"prompt_id": parts[2], "deleted": True})
            elif parts[1] == "media":
                self._send_json(HTTPStatus.OK, {"job_id": parts[2], "deleted": True})
            elif parts[1] == "recipes":
                self._send_json(HTTPStatus.OK, {"recipe_id": parts[2], "deleted": True})
            else:
                self._send_json(HTTPStatus.OK, {"connection_id": parts[3], "deleted": True})
        do_PATCH = _method_not_allowed
        def do_PUT(self) -> None:
            path = urlsplit(self.path).path
            parts = path.strip("/").split("/")
            project_put = len(parts) == 3 and parts[:2] == ["api", "projects"]
            provider_put = len(parts) == 3 and parts[:2] == ["api", "providers"]
            conversation_put = len(parts) == 3 and parts[:2] == ["api", "conversations"]
            mcp_put = len(parts) == 4 and parts[:3] == ["api", "mcp", "connections"]
            if callbacks is None or not (project_put or provider_put or conversation_put or mcp_put):
                self._method_not_allowed()
                return
            if not self._authorized() or not self._csrf_valid():
                return
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                self._send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length required"})
                return
            try:
                length = int(raw_length)
                if length < 0 or length > MAX_BODY_BYTES:
                    raise ValueError
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError
                if project_put:
                    name = payload.get("name")
                    if not isinstance(name, str): raise ValueError
                    callbacks.rename_project(parts[2], name)
                elif provider_put:
                    connection = callbacks.update_provider_connection(
                        parts[2], display_name=payload.get("display_name"),
                        base_url=payload.get("base_url"), models=payload.get("models"),
                        enabled=payload.get("enabled"), secret=payload.get("secret"),
                        clear_secret=payload.get("clear_secret", False), revision=payload.get("revision"),
                    )
                elif conversation_put:
                    supplied = [key for key in ("title", "pinned", "project_id", "archived") if key in payload]
                    if len(supplied) != 1: raise ValueError
                    field = supplied[0]; value = payload[field]
                    if field == "title" and isinstance(value, str): callbacks.rename_conversation(parts[2], value)
                    elif field == "pinned" and isinstance(value, bool): callbacks.pin_conversation(parts[2], value)
                    elif field == "project_id" and (value is None or isinstance(value, str)): callbacks.assign_conversation_project(parts[2], value)
                    elif field == "archived" and isinstance(value, bool): callbacks.archive_conversation(parts[2], value)
                    else: raise ValueError
                else:
                    name = payload.get("name")
                    if not isinstance(name, str): raise ValueError
                    callbacks.update_mcp(parts[3], name, payload.get("transport"), payload.get("config"), payload.get("enabled", True), payload.get("credential"), payload.get("clear_credential", False))
            except LookupError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "resource not found"})
                return
            except RuntimeError as error:
                self._send_json(HTTPStatus.CONFLICT, {"error": str(error)})
                return
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error) if provider_put else "valid update required"})
                return
            if provider_put:
                self._send_json(HTTPStatus.OK, {"provider": connection})
            elif conversation_put:
                self._send_json(HTTPStatus.OK, {"id": parts[2], "updated": supplied[0]})
            else:
                self._send_json(HTTPStatus.OK, {"id": parts[2] if project_put else parts[3], "name": name.strip()})

    return WebUiRequestHandler


def create_server(
    *,
    host: str,
    port: int,
    submit: SubmitTurn,
    snapshot: SnapshotSession,
    callbacks: WebCallbacks | None = None,
) -> tuple[WebUiHttpServer, Thread]:
    """Create but do not start the loopback UI server and its owned thread."""
    assets = _load_static_assets()
    handler = _handler_type(submit=submit, snapshot=snapshot, assets=assets, callbacks=callbacks)
    server = WebUiHttpServer((host, port), handler)
    thread = Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        name="lions-heart-web-ui",
        daemon=False,
    )
    return server, thread


__all__ = [
    "MAX_BODY_BYTES",
    "MAX_MESSAGE_CHARS",
    "WebUiHttpServer",
    "WebCallbacks",
    "create_server",
    "serialize_events",
]
