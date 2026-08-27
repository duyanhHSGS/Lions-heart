"""Lions-heart tests for the current example Beings only.

These tests deliberately use the public Being-facing contract and small fakes.
They do not make upstream host internals part of Lions-heart's test contract.
"""

from __future__ import annotations

import ast
import json
from http.client import HTTPConnection
from pathlib import Path
from socket import socket
from typing import Any
from urllib.parse import urlsplit

import pytest

from cor_being import Being, Life
from cor_beings import get_beings
from cor_beings.dashboard import Dashboard, DashboardServer, build_snapshot
from cor_beings.hello_moon import HelloMoon
from cor_beings.hello_sun import HelloSun
from cor_beings.hello_world import HelloWorld
from cor_beings.watcher import BeingInfo, Watcher

ROOT = Path(__file__).resolve().parents[1]


class FakeWorld:
    """Tiny public-surface World fake for example-Being tests."""

    def __init__(
        self,
        *instances: Being,
        name: str = "test-world",
    ) -> None:
        self.name = name
        self._instances = {type(instance): instance for instance in instances}
        self._alive = tuple(type(instance) for instance in instances)
        self.news = None

    @property
    def alive(self) -> tuple[type[Being], ...]:
        return self._alive

    def need(self, being_type: type[Being]):
        try:
            return self._instances[being_type]
        except KeyError as error:
            raise LookupError(f"being is not alive: {being_type.__name__}") from error

    def branch(self, name: str) -> FakeWorld:
        return FakeWorld(name=name)


class FirstExample(Being):
    name = "first"

    def birth(self, world, life) -> None:
        pass


class SecondExample(Being):
    name = "second"
    needs = (FirstExample,)

    def birth(self, world, life) -> None:
        pass


class RenamedExample(Being):
    name = "before"

    def birth(self, world, life) -> None:
        self.name = "after"


def request(
    url: str,
    path: str,
    *,
    method: str = "GET",
) -> tuple[int, dict[str, str], bytes]:
    parsed = urlsplit(url)
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=2)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def start_server(snapshot: dict[str, Any] | None = None) -> DashboardServer:
    payload = snapshot or {"world": "test", "count": 0, "beings": []}
    server = DashboardServer(lambda: payload, port=0)
    server.start()
    return server


def test_example_composition_is_deterministic() -> None:
    assert get_beings() == (HelloWorld, HelloMoon, HelloSun, Watcher, Dashboard)


def test_example_composition_contains_only_beings() -> None:
    assert all(issubclass(being_type, Being) for being_type in get_beings())


def test_example_names_are_unique_and_lowercase() -> None:
    names = [being_type.name for being_type in get_beings()]
    assert len(names) == len(set(names))
    assert all(name == name.lower() for name in names)


def test_hello_world_prints_exact_greeting(capsys) -> None:
    HelloWorld().birth(FakeWorld(), Life("hello_world"))
    assert capsys.readouterr().out == "hello world\n"


def test_hello_moon_prints_exact_greeting(capsys) -> None:
    HelloMoon().birth(FakeWorld(), Life("hello_moon"))
    assert capsys.readouterr().out == "hello moon\n"


def test_hello_sun_declares_both_example_dependencies() -> None:
    assert HelloSun.needs == (HelloMoon, HelloWorld)


def test_hello_sun_prints_exact_greeting(capsys) -> None:
    HelloSun().birth(FakeWorld(HelloMoon(), HelloWorld()), Life("hello_sun"))
    assert capsys.readouterr().out == "SON!\n"


def test_watcher_is_an_example_being() -> None:
    assert issubclass(Watcher, Being)
    assert Watcher.name == "watcher"


def test_watcher_lists_alive_instances_in_world_order() -> None:
    watcher = Watcher()
    world = FakeWorld(watcher, FirstExample(), SecondExample())
    assert watcher.list_beings(world) == ("watcher", "first", "second")


def test_watcher_snapshot_describes_public_metadata() -> None:
    watcher = Watcher()
    world = FakeWorld(watcher, FirstExample(), SecondExample())

    assert watcher.snapshot(world) == (
        BeingInfo(
            name="watcher",
            being_type=Watcher,
            needs=(),
            module="cor_beings.watcher",
        ),
        BeingInfo(
            name="first",
            being_type=FirstExample,
            needs=(),
            module=__name__,
        ),
        BeingInfo(
            name="second",
            being_type=SecondExample,
            needs=("first",),
            module=__name__,
        ),
    )


def test_watcher_info_returns_one_alive_being() -> None:
    watcher = Watcher()
    world = FakeWorld(watcher, FirstExample(), SecondExample())
    info = watcher.info(world, "second")

    assert info.name == "second"
    assert info.being_type is SecondExample
    assert info.needs == ("first",)


def test_watcher_info_rejects_unknown_name() -> None:
    watcher = Watcher()
    world = FakeWorld(watcher)

    with pytest.raises(LookupError, match="being is not alive: missing"):
        watcher.info(world, "missing")


def test_watcher_reads_name_from_alive_instance() -> None:
    renamed = RenamedExample()
    renamed.birth(FakeWorld(), Life("renamed"))
    watcher = Watcher()
    world = FakeWorld(watcher, renamed)

    assert watcher.list_beings(world) == ("watcher", "after")
    assert watcher.info(world, "after").being_type is RenamedExample


def test_build_snapshot_returns_json_ready_data() -> None:
    watcher = Watcher()
    world = FakeWorld(watcher, FirstExample(), SecondExample(), name="tiny")

    assert build_snapshot(watcher, world) == {
        "world": "tiny",
        "count": 3,
        "beings": [
            {"name": "watcher", "module": "cor_beings.watcher", "needs": []},
            {"name": "first", "module": __name__, "needs": []},
            {"name": "second", "module": __name__, "needs": ["first"]},
        ],
    }


def test_dashboard_server_serves_html() -> None:
    server = start_server()
    try:
        status, headers, body = request(server.url, "/")
    finally:
        server.stop()

    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert b"/api/beings" in body


def test_dashboard_server_serves_compact_json() -> None:
    snapshot = {
        "world": "tiny",
        "count": 1,
        "beings": [{"name": "watcher", "module": "demo", "needs": []}],
    }
    server = start_server(snapshot)
    try:
        status, headers, body = request(server.url, "/api/beings?fresh=yes")
    finally:
        server.stop()

    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert json.loads(body) == snapshot
    assert b" " not in body


def test_dashboard_server_hides_snapshot_failure() -> None:
    def broken_snapshot() -> dict[str, object]:
        raise RuntimeError("secret")

    server = DashboardServer(broken_snapshot, port=0)
    server.start()
    try:
        status, _, body = request(server.url, "/api/beings")
    finally:
        server.stop()

    assert status == 500
    assert json.loads(body) == {"error": "snapshot unavailable"}
    assert b"secret" not in body


def test_dashboard_server_rejects_unknown_path() -> None:
    server = start_server()
    try:
        status, _, body = request(server.url, "/missing")
    finally:
        server.stop()

    assert status == 404
    assert json.loads(body) == {"error": "not found"}


def test_dashboard_server_is_read_only() -> None:
    server = start_server()
    try:
        status, headers, body = request(server.url, "/api/beings", method="POST")
    finally:
        server.stop()

    assert status == 405
    assert headers["Allow"] == "GET, HEAD"
    assert json.loads(body) == {"error": "read only"}


def test_dashboard_server_supports_head_without_body() -> None:
    server = start_server()
    try:
        status, headers, body = request(server.url, "/", method="HEAD")
    finally:
        server.stop()

    assert status == 200
    assert int(headers["Content-Length"]) > 0
    assert body == b""


def test_dashboard_server_start_and_stop_are_idempotent() -> None:
    server = start_server()
    original_address = server.address

    server.start()
    assert server.running
    assert server.address == original_address

    server.stop()
    server.stop()
    assert not server.running

    with pytest.raises(RuntimeError, match="not running"):
        _ = server.address

    with socket() as released_port:
        released_port.bind(original_address)


def test_dashboard_being_registers_life_cleanup(monkeypatch, capsys) -> None:
    monkeypatch.setattr(Dashboard, "port", 0)
    watcher = Watcher()
    world = FakeWorld(watcher)
    life = Life("dashboard")
    dashboard = Dashboard()

    dashboard.birth(world, life)
    assert dashboard.server is not None
    assert dashboard.server.running
    assert capsys.readouterr().out.startswith("Dashboard: http://127.0.0.1:")

    life.die()
    assert not dashboard.server.running


def test_cor_beings_does_not_import_private_host_package() -> None:
    forbidden_imports: list[str] = []

    for source_path in sorted((ROOT / "cor_beings").rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path.as_posix())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                forbidden_imports.extend(
                    alias.name
                    for alias in node.names
                    if alias.name == "cor_runtime"
                    or alias.name.startswith("cor_runtime.")
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and (
                    node.module == "cor_runtime"
                    or node.module.startswith("cor_runtime.")
                )
            ):
                forbidden_imports.append(node.module)

    assert forbidden_imports == []


# TODO: Delete these example-specific tests as their example Beings are replaced by real harness Beings.
