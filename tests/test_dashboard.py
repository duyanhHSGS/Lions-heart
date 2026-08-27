"""Tests for the optional local Cor Leonis dashboard."""

from __future__ import annotations

import json
from http.client import HTTPConnection
from socket import socket
from typing import Any
from urllib.parse import urlsplit

import pytest

from cor_being import Being, Life
from cor_beings.dashboard import (
    Dashboard,
    DashboardServer,
    build_snapshot,
)
from cor_beings.watcher import Watcher
from cor_runtime._genesis import genesis
from cor_runtime._world import RuntimeWorld as World


class DashboardDependency(Being):
    name = "dashboard_dependency"

    def birth(self, world: World, life: Life) -> None:
        pass


class DashboardSubject(Being):
    name = "dashboard_subject"
    needs = (DashboardDependency,)

    def birth(self, world: World, life: Life) -> None:
        pass


def request(
    url: str, path: str, *, method: str = "GET"
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


def test_dashboard_is_an_optional_being_that_needs_watcher() -> None:
    assert issubclass(Dashboard, Being)
    assert Dashboard.name == "dashboard"
    assert Dashboard.needs == (Watcher,)


def test_build_snapshot_returns_json_ready_beings_in_birth_order() -> None:
    with World(name="test-world") as world:
        world._add(Watcher, DashboardDependency, DashboardSubject)
        world._birth(Watcher)
        world._birth(DashboardSubject)
        watcher = world.need(Watcher)

        assert isinstance(watcher, Watcher)
        assert build_snapshot(watcher, world) == {
            "world": "test-world",
            "count": 3,
            "beings": [
                {
                    "name": "watcher",
                    "module": "cor_beings.watcher",
                    "needs": [],
                },
                {
                    "name": "dashboard_dependency",
                    "module": __name__,
                    "needs": [],
                },
                {
                    "name": "dashboard_subject",
                    "module": __name__,
                    "needs": ["dashboard_dependency"],
                },
            ],
        }


def test_dashboard_server_serves_the_html_shell() -> None:
    server = start_server()
    try:
        status, headers, body = request(server.url, "/")
    finally:
        server.stop()

    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert b"Cor Leonis" in body
    assert b"/api/beings" in body


def test_dashboard_server_serves_compact_json_and_ignores_query_string() -> None:
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


def test_dashboard_server_returns_safe_error_when_snapshot_fails() -> None:
    def broken_snapshot() -> dict[str, object]:
        raise RuntimeError("secret internals")

    server = DashboardServer(broken_snapshot, port=0)
    server.start()
    try:
        status, _, body = request(server.url, "/api/beings")
    finally:
        server.stop()

    assert status == 500
    assert json.loads(body) == {"error": "snapshot unavailable"}
    assert b"secret" not in body


def test_dashboard_server_rejects_unknown_paths() -> None:
    server = start_server()
    try:
        status, _, body = request(server.url, "/definitely-missing")
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


def test_dashboard_server_supports_head_without_a_body() -> None:
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


def test_runtime_discovery_births_dashboard_by_default(monkeypatch, capsys) -> None:
    monkeypatch.setattr(Dashboard, "port", 0)

    with World() as world:
        started = genesis(world)
        dashboard = world.need(Dashboard)

        assert started[-1] is Dashboard
        assert world.alive == started
        assert isinstance(dashboard, Dashboard)
        assert dashboard.server is not None
        assert dashboard.server.running

    assert not dashboard.server.running
    assert "Dashboard: http://127.0.0.1:" in capsys.readouterr().out


def test_dashboard_life_starts_watcher_reports_url_and_stops_server(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(Dashboard, "port", 0)
    server: DashboardServer

    with World(name="dashboard-test") as world:
        world._add(Watcher, Dashboard)
        world._birth(Dashboard)
        dashboard = world.need(Dashboard)

        assert isinstance(dashboard, Dashboard)
        assert dashboard.server is not None
        server = dashboard.server
        assert world.alive == (Watcher, Dashboard)
        assert server.running
        assert capsys.readouterr().out == f"Dashboard: {server.url}\n"
        status, _, body = request(server.url, "/api/beings")
        assert status == 200
        assert [item["name"] for item in json.loads(body)["beings"]] == [
            "watcher",
            "dashboard",
        ]

    assert not server.running


def test_dashboard_start_failure_runs_registered_cleanup(monkeypatch) -> None:
    stopped: list[DashboardServer] = []

    def fail_start(self: DashboardServer) -> None:
        raise OSError("port unavailable")

    def record_stop(self: DashboardServer) -> None:
        stopped.append(self)

    monkeypatch.setattr(DashboardServer, "start", fail_start)
    monkeypatch.setattr(DashboardServer, "stop", record_stop)

    with World() as world:
        world._add(Watcher, Dashboard)
        with pytest.raises(OSError, match="port unavailable"):
            world._birth(Dashboard)

        assert world.alive == (Watcher,)
        assert len(stopped) == 1


# TODO: Add a real browser accessibility pass when the project adopts browser tooling.
