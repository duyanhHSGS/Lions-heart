"""Dependency-free HTTP transport for the Lions-heart web UI."""

# TODO: Add lifecycle-owned incremental event delivery for long-running turns.
# TODO: Add ordinary feature Beings before wiring the preserved Studio controls.
# TODO: Add optional authentication before allowing any non-loopback bind address.
# TODO: Add configurable body/message limits when product configuration exists.
# TODO: Add content-hashed asset URLs before enabling browser caching.

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import urlsplit

from cor_beings.session import SessionEvent

MAX_BODY_BYTES = 64 * 1024
MAX_MESSAGE_CHARS = 16 * 1024

StaticAsset = tuple[str, bytes]
SubmitTurn = Callable[[str], str]
SnapshotSession = Callable[[], tuple[SessionEvent, ...]]


class WebUiHttpServer(ThreadingHTTPServer):
    """Small same-origin server whose request threads finish before close."""

    allow_reuse_address = True
    daemon_threads = False
    block_on_close = True


def _load_static_assets() -> dict[str, StaticAsset]:
    """Load the tiny frontend once so request hot paths do not touch disk."""
    static_dir = Path(__file__).with_name("static")
    declarations = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/index.html": ("index.html", "text/html; charset=utf-8"),
        "/styles.css": ("styles.css", "text/css; charset=utf-8"),
        "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    }
    loaded: dict[str, StaticAsset] = {}
    for route, (filename, content_type) in declarations.items():
        loaded[route] = (content_type, (static_dir / filename).read_bytes())
    return loaded


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
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _handler_type(
    *,
    submit: SubmitTurn,
    snapshot: SnapshotSession,
    assets: Mapping[str, StaticAsset],
) -> type[BaseHTTPRequestHandler]:
    class WebUiRequestHandler(BaseHTTPRequestHandler):
        server_version = "LionsHeart"
        sys_version = ""

        def log_message(self, _format: str, *_args: Any) -> None:
            """Keep the product terminal quiet; the UI owns request feedback."""

        def _send_bytes(
            self,
            status: HTTPStatus,
            body: bytes,
            content_type: str,
            *,
            include_body: bool = True,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'",
            )
            self.end_headers()
            if include_body:
                self.wfile.write(body)

        def _send_json(
            self,
            status: HTTPStatus,
            payload: object,
            *,
            include_body: bool = True,
        ) -> None:
            self._send_bytes(
                status,
                _encode_json(payload),
                "application/json; charset=utf-8",
                include_body=include_body,
            )

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
            path = urlsplit(self.path).path
            asset = assets.get(path)
            if asset is not None:
                content_type, body = asset
                self._send_bytes(
                    HTTPStatus.OK,
                    body,
                    content_type,
                    include_body=include_body,
                )
                return
            if path == "/api/session":
                events = snapshot()
                self._send_json(
                    HTTPStatus.OK,
                    {"events": serialize_events(events), "count": len(events)},
                    include_body=include_body,
                )
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

        def do_GET(self) -> None:
            self._serve_get(include_body=True)

        def do_HEAD(self) -> None:
            self._serve_get(include_body=False)

        def do_POST(self) -> None:
            if urlsplit(self.path).path != "/api/turn":
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

        def _method_not_allowed(self) -> None:
            self._send_json(
                HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"}
            )

        do_DELETE = _method_not_allowed
        do_PATCH = _method_not_allowed
        do_PUT = _method_not_allowed

    return WebUiRequestHandler


def create_server(
    *,
    host: str,
    port: int,
    submit: SubmitTurn,
    snapshot: SnapshotSession,
) -> tuple[WebUiHttpServer, Thread]:
    """Create but do not start the loopback UI server and its owned thread."""
    assets = _load_static_assets()
    handler = _handler_type(submit=submit, snapshot=snapshot, assets=assets)
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
    "create_server",
    "serialize_events",
]
