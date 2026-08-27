"""Optional local web dashboard for inspecting a living Cor Leonis World."""

from __future__ import annotations

import json
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import RLock, Thread
from urllib.parse import urlsplit

from cor_being import Being, Life, World
from cor_beings.watcher import Watcher

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

Snapshot = dict[str, object]
SnapshotProvider = Callable[[], Snapshot]

_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cor Leonis Dashboard</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #080b12; color: #eef2ff; }
    main { width: min(920px, calc(100% - 32px)); margin: 48px auto; }
    header { display: flex; align-items: end; justify-content: space-between; gap: 24px; }
    h1 { margin: 0; font-size: clamp(2rem, 7vw, 4.5rem); letter-spacing: -0.06em; }
    .eyebrow { color: #f5b942; font-weight: 800; letter-spacing: .16em; text-transform: uppercase; }
    .status { display: inline-flex; align-items: center; gap: 8px; color: #8ff0b3; font-weight: 800; }
    .status::before { content: ""; width: 10px; height: 10px; border-radius: 50%; background: #4ade80; box-shadow: 0 0 18px #4ade80; }
    .summary { margin: 36px 0 18px; color: #aab3c5; }
    .grid { display: grid; gap: 12px; }
    article { padding: 20px; border: 1px solid #20283a; border-radius: 18px; background: linear-gradient(135deg, #121827, #0d121e); }
    article div { display: flex; justify-content: space-between; gap: 16px; }
    h2 { margin: 0; font-size: 1.1rem; }
    code { color: #a7b9ff; }
    .needs { margin: 10px 0 0; color: #8e9aaf; }
    .error { color: #ff9292; }
  </style>
</head>
<body>
  <main>
    <header>
      <div><div class="eyebrow">Living World</div><h1>Cor Leonis</h1></div>
      <div class="status">ALIVE</div>
    </header>
    <p id="summary" class="summary">Looking for Beings...</p>
    <section id="beings" class="grid" aria-live="polite"></section>
  </main>
  <script>
    const summary = document.querySelector("#summary");
    const beings = document.querySelector("#beings");

    function row(item) {
      const card = document.createElement("article");
      const top = document.createElement("div");
      const name = document.createElement("h2");
      const moduleName = document.createElement("code");
      const needs = document.createElement("p");
      name.textContent = item.name || "(unnamed)";
      moduleName.textContent = item.module;
      needs.className = "needs";
      needs.textContent = item.needs.length ? `Needs: ${item.needs.join(", ")}` : "Needs: nothing";
      top.append(name, moduleName);
      card.append(top, needs);
      return card;
    }

    async function refresh() {
      try {
        const response = await fetch("/api/beings", { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        summary.className = "summary";
        summary.textContent = `World: ${data.world} · ${data.count} Being${data.count === 1 ? "" : "s"} alive`;
        beings.replaceChildren(...data.beings.map(row));
      } catch (error) {
        summary.className = "summary error";
        summary.textContent = `Dashboard lost the World: ${error.message}`;
      }
    }

    // TODO: Replace polling with News-backed server events when News gains an async boundary.
    refresh();
    setInterval(refresh, 1000);
  </script>
</body>
</html>
""".encode()


def build_snapshot(watcher: Watcher, world: World) -> Snapshot:
    """Return a small JSON-ready view of the World's currently alive Beings."""
    items = watcher.snapshot(world)
    return {
        "world": world.name,
        "count": len(items),
        "beings": [
            {
                "name": item.name,
                "module": item.module,
                "needs": list(item.needs),
            }
            for item in items
        ],
    }


def _make_handler(snapshot_provider: SnapshotProvider) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        """Serve the dashboard shell and its read-only population endpoint."""

        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", _DASHBOARD_HTML)
                return
            if path == "/api/beings":
                try:
                    payload = json.dumps(
                        snapshot_provider(), separators=(",", ":")
                    ).encode("utf-8")
                except Exception:  # noqa: BLE001 - local UI must hide provider failures.
                    # Do not leak runtime internals or tracebacks through the local UI.
                    self._send(
                        500,
                        "application/json; charset=utf-8",
                        b'{"error":"snapshot unavailable"}',
                    )
                    return
                self._send(200, "application/json; charset=utf-8", payload)
                return
            self._send(404, "application/json; charset=utf-8", b'{"error":"not found"}')

        def do_HEAD(self) -> None:
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", _DASHBOARD_HTML, head=True)
                return
            if path == "/api/beings":
                self._send(200, "application/json; charset=utf-8", b"", head=True)
                return
            self._send(404, "application/json; charset=utf-8", b"", head=True)

        def do_POST(self) -> None:
            self._send(
                405,
                "application/json; charset=utf-8",
                b'{"error":"read only"}',
                extra_headers=(("Allow", "GET, HEAD"),),
            )

        def _send(
            self,
            status: int,
            content_type: str,
            payload: bytes,
            *,
            head: bool = False,
            extra_headers: tuple[tuple[str, str], ...] = (),
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            for name, value in extra_headers:
                self.send_header(name, value)
            self.end_headers()
            if not head:
                self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            """Keep routine browser refreshes out of the runtime's stdout."""

    return DashboardHandler


class _DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class DashboardServer:
    """Own one stoppable local HTTP server and its serving thread."""

    def __init__(
        self,
        snapshot_provider: SnapshotProvider,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> None:
        self._snapshot_provider = snapshot_provider
        self._host = host
        self._port = port
        self._server: _DashboardHTTPServer | None = None
        self._thread: Thread | None = None
        self._lock = RLock()

    def start(self) -> None:
        """Start serving exactly once; repeated starts are harmless."""
        with self._lock:
            if self._server is not None:
                return
            server = _DashboardHTTPServer(
                (self._host, self._port), _make_handler(self._snapshot_provider)
            )
            thread = Thread(
                target=server.serve_forever,
                name="corleonis-dashboard",
                daemon=True,
            )
            self._server = server
            self._thread = thread
            try:
                thread.start()
            except BaseException:
                self._server = None
                self._thread = None
                server.server_close()
                raise

    def stop(self) -> None:
        """Stop serving and release the socket exactly once."""
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is None or thread is None:
            return
        try:
            server.shutdown()
            thread.join(timeout=5)
        finally:
            server.server_close()
        if thread.is_alive():
            raise RuntimeError("dashboard server did not stop")

    @property
    def running(self) -> bool:
        with self._lock:
            return self._server is not None and bool(
                self._thread and self._thread.is_alive()
            )

    @property
    def address(self) -> tuple[str, int]:
        with self._lock:
            if self._server is None:
                raise RuntimeError("dashboard server is not running")
            host, port = self._server.server_address[:2]
            return str(host), int(port)

    @property
    def url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}"


class Dashboard(Being):
    """Show a read-only browser dashboard without controlling other Beings."""

    name = "dashboard"
    needs = (Watcher,)
    host = DEFAULT_HOST
    port = DEFAULT_PORT

    def __init__(self) -> None:
        self.server: DashboardServer | None = None

    def birth(self, world: World, life: Life) -> None:
        watcher = world.need(Watcher)
        server = DashboardServer(
            lambda: build_snapshot(watcher, world),
            host=self.host,
            port=self.port,
        )
        life.on_death(server.stop)
        server.start()
        self.server = server
        print(f"Dashboard: {server.url}")


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "Dashboard",
    "DashboardServer",
    "build_snapshot",
]

# TODO: Add write controls only after Population has an explicit thread-safe command boundary.
