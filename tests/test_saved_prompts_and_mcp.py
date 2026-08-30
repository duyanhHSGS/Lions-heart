"""Phase 2/3 behavior, boundary, security, concurrency, and lifecycle tests."""

# TODO: Add a deterministic subprocess fixture for full stdio framing on every OS.

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cor_being import Life
from cor_beings.bash import BashBeing
from cor_beings.edit import EditBeing
from cor_beings.mcp import McpBeing
from cor_beings.read import ReadBeing
from cor_beings.saved_prompts import MAX_PROMPT_BODY, MAX_PROMPT_NAME, SavedPromptsBeing
from cor_beings.storage import SCHEMA_VERSION, StorageBeing
from cor_beings.tool_shelf import ToolShelfBeing


class World:
    name = "phase-test"
    news = None
    alive = ()

    def __init__(self, mapping): self.mapping = mapping
    def need(self, kind): return self.mapping[kind]


class Builtin:
    def __init__(self, name): self.name = name
    def run(self, arguments): return json.dumps(dict(arguments), sort_keys=True)


class FakeRpc:
    def __init__(self, tools=None):
        self.tools = tools or [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}}]
        self.closed = False
        self.calls = []

    def request(self, method, params=None):
        self.calls.append((method, params))
        if method == "tools/list": return {"tools": self.tools}
        if method == "tools/call": return {"content": [{"type": "text", "text": params["arguments"]["text"]}]}
        return {"protocolVersion": "2025-03-26"}

    def close(self): self.closed = True


@pytest.fixture
def storage(tmp_path):
    value = StorageBeing(data_root=tmp_path / "runtime")
    life = Life("storage"); value.birth(World({}), life)
    try: yield value
    finally: life.die()


@pytest.fixture
def prompts(storage):
    value = SavedPromptsBeing(); life = Life("saved-prompts")
    value.birth(World({StorageBeing: storage}), life)
    try: yield value
    finally: life.die()


def test_saved_prompt_crud_unicode_search_and_stable_order(prompts):
    first = prompts.create("  Café helper  ", "print('bonjour')")
    second = prompts.create("Python lion", "Explain a Python list to a child")
    assert prompts.get(first["id"])["name"] == "Café helper"
    assert [row["id"] for row in prompts.search("Python list")] == [second["id"]]
    changed = prompts.update(first["id"], "Café helper", "new body", revision=1)
    assert changed["revision"] == 2
    with pytest.raises(RuntimeError, match="changed"):
        prompts.update(first["id"], "Café helper", "stale", revision=1)
    prompts.delete(first["id"])
    with pytest.raises(LookupError): prompts.get(first["id"])


@pytest.mark.parametrize(
    "name,body",
    [("", "x"), ("x", ""), ("x" * (MAX_PROMPT_NAME + 1), "x"), ("x", "x" * (MAX_PROMPT_BODY + 1)), ("bad\x00name", "x"), ("x", "bad\x00body")],
    ids=("empty-name", "empty-body", "long-name", "long-body", "control-name", "nul-body"),
)
def test_saved_prompt_rejects_empty_oversized_and_controls(prompts, name, body):
    with pytest.raises((TypeError, ValueError)): prompts.create(name, body)


def test_saved_prompt_duplicate_scope_and_search_escaping(prompts):
    prompts.create("Lion", "one")
    with pytest.raises(ValueError, match="already exists"): prompts.create("  LION  ", "two")
    assert prompts.search('" OR *') == ()


def _shelf():
    shelf = ToolShelfBeing(); life = Life("shelf")
    world = World({ReadBeing: Builtin("read"), EditBeing: Builtin("edit"), BashBeing: Builtin("bash")})
    shelf.birth(world, life)
    return shelf, life


def test_mcp_discovers_namespaces_validates_invokes_encrypts_and_cleans(storage, tmp_path):
    shelf, shelf_life = _shelf(); clients = []
    def factory(*_args):
        client = FakeRpc(); clients.append(client); return client
    mcp = McpBeing(client_factory=factory); life = Life("mcp")
    mcp.birth(World({StorageBeing: storage, ToolShelfBeing: shelf}), life)
    executable = str((tmp_path / "server.exe").resolve())
    item = mcp.create("Local tools", "stdio", {"argv": [executable, "--safe"]}, credential="super-secret-token")
    assert item["health"] == "healthy" and item["credential_configured"] is True
    tool_name = item["tools"][0]
    assert tool_name.startswith("mcp_") and "echo" in tool_name
    assert "super-secret-token" not in str(storage.fetchall("SELECT * FROM mcp_connections"))
    result = shelf.execute(tool_name, {"text": "roar"})
    assert "roar" in result
    with pytest.raises(ValueError): shelf.execute(tool_name, {"unknown": True})
    mcp.delete(item["id"])
    with pytest.raises(LookupError): shelf.get(tool_name)
    assert clients[-1].closed
    life.die(); shelf_life.die()


def test_mcp_rejects_ssrf_shell_strings_and_hostile_schema(storage, tmp_path):
    shelf, shelf_life = _shelf()
    bad = FakeRpc([{"name": "evil", "inputSchema": {"type": "object", "$ref": "file:///secret"}}])
    mcp = McpBeing(client_factory=lambda *_args: bad); life = Life("mcp")
    mcp.birth(World({StorageBeing: storage, ToolShelfBeing: shelf}), life)
    with pytest.raises(ValueError, match="private or unsafe"):
        mcp.create("loopback", "http", {"url": "https://127.0.0.1/mcp"})
    with pytest.raises(ValueError, match="argv"):
        mcp.create("shell", "stdio", {"command": "anything; do-bad-things"})
    item = mcp.create("hostile", "stdio", {"argv": [str((tmp_path / "bad.exe").resolve())]})
    assert item["health"] == "error" and item["tools"] == ()
    assert bad.closed
    life.die(); shelf_life.die()


def test_mcp_duplicate_names_and_lifecycle_remove_tools(storage, tmp_path):
    shelf, shelf_life = _shelf(); client = FakeRpc()
    mcp = McpBeing(client_factory=lambda *_args: client); life = Life("mcp")
    mcp.birth(World({StorageBeing: storage, ToolShelfBeing: shelf}), life)
    config = {"argv": [str((tmp_path / "ok.exe").resolve())]}
    item = mcp.create("same", "stdio", config)
    with pytest.raises(ValueError, match="already exists"): mcp.create("same", "stdio", config)
    before = len(mcp.list())
    with pytest.raises(ValueError, match="duplicate"):
        mcp.import_connections([
            {"name": "batch", "transport": "stdio", "config": config},
            {"name": "batch", "transport": "stdio", "config": config},
        ])
    assert len(mcp.list()) == before
    tool_name = item["tools"][0]
    life.die()
    assert client.closed
    with pytest.raises(LookupError): shelf.get(tool_name)
    shelf_life.die()


def test_current_schema_has_prompt_indexes(storage):
    assert storage.fetchone("PRAGMA user_version")[0] == SCHEMA_VERSION
    names = {row["name"] for row in storage.fetchall("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    assert {"saved_prompt_search", "saved_prompts_scope_name", "saved_prompts_order"} <= names
