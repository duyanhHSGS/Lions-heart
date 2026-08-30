"""Lifecycle-owned, approval-only Model Context Protocol connections."""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import queue
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cor_being import Being, Life, World
from cor_beings.storage import StorageBeing
from cor_beings.tool_shelf import ToolShelfBeing

MAX_CONNECTIONS = 32
MAX_MCP_MESSAGE = 1024 * 1024


class RpcClient(Protocol):
    def request(self, method: str, params: Mapping[str, object] | None = None) -> object: ...
    def close(self) -> None: ...


@dataclass(slots=True)
class _RemoteTool:
    name: str
    connection_id: str
    remote_name: str
    invoke: object

    def run(self, arguments: Mapping[str, object]) -> str:
        result = self.invoke(self.connection_id, self.remote_name, arguments)  # type: ignore[operator]
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


class McpBeing(Being):
    """Persist safe metadata and own MCP clients, processes, and discovered tools."""

    name = "mcp"
    needs = (StorageBeing, ToolShelfBeing)

    def __init__(self, *, timeout: float = 5.0, client_factory: object | None = None) -> None:
        if not 0.1 <= timeout <= 30:
            raise ValueError("MCP timeout must be from 0.1 through 30 seconds")
        self._timeout = timeout
        self._client_factory = client_factory
        self._storage: StorageBeing | None = None
        self._shelf: ToolShelfBeing | None = None
        self._clients: dict[str, RpcClient] = {}
        self._tool_names: dict[str, dict[str, str]] = {}
        self._lock = threading.RLock()

    def birth(self, world: World, life: Life) -> None:
        self._storage = world.need(StorageBeing)
        self._shelf = world.need(ToolShelfBeing)
        life.on_death(self._stop)
        for row in self._storage.fetchall("SELECT id FROM mcp_connections WHERE enabled=1 ORDER BY name, id"):
            try:
                self.refresh(str(row["id"]))
            except Exception as error:  # connection failures must not kill Lion startup
                self._set_health(str(row["id"]), "error", error)
        # TODO: Add bounded exponential reconnect scheduling after operational metrics exist.

    def create(self, name: str, transport: str, config: Mapping[str, object], *, credential: str | None = None) -> dict[str, object]:
        clean_name = _name(name)
        clean_config = _config(transport, config)
        storage = self._require_storage()
        if len(self.list()) >= MAX_CONNECTIONS:
            raise ValueError("too many MCP connections")
        connection_id = uuid4().hex
        ciphertext = nonce = None
        if credential is not None:
            ciphertext, nonce = self._encrypt(connection_id, credential)
        try:
            storage.execute(
                "INSERT INTO mcp_connections(id, name, transport, config_json, credential_ciphertext, credential_nonce, enabled, health, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, 'unknown', ?)",
                (connection_id, clean_name, transport, json.dumps(clean_config, separators=(",", ":")), ciphertext, nonce, int(time.time())),
            )
        except __import__("sqlite3").IntegrityError as error:
            raise ValueError("an MCP connection with this name already exists") from error
        try:
            self.refresh(connection_id)
        except Exception as error:
            self._set_health(connection_id, "error", error)
        return self.get(connection_id)

    def import_connections(self, items: list[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
        """Validate and persist a bounded import as one all-or-nothing transaction."""
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_CONNECTIONS:
            raise ValueError("connections must be a bounded list")
        if len(self.list()) + len(items) > MAX_CONNECTIONS:
            raise ValueError("too many MCP connections")
        prepared = []
        for item in items:
            if not isinstance(item, Mapping):
                raise ValueError("connection must be an object")
            name = _name(item.get("name"))  # type: ignore[arg-type]
            transport = item.get("transport")
            config = item.get("config")
            if not isinstance(transport, str) or not isinstance(config, Mapping):
                raise ValueError("connection transport and config are required")
            clean_config = _config(transport, config)
            connection_id = uuid4().hex
            credential = item.get("credential")
            if credential is not None and not isinstance(credential, str):
                raise ValueError("MCP credential must be a string")
            ciphertext = nonce = None
            if credential is not None:
                ciphertext, nonce = self._encrypt(connection_id, credential)
            prepared.append((connection_id, name, transport, clean_config, ciphertext, nonce))
        storage = self._require_storage()
        try:
            with storage.transaction() as connection:
                for connection_id, name, transport, config, ciphertext, nonce in prepared:
                    connection.execute(
                        "INSERT INTO mcp_connections(id, name, transport, config_json, credential_ciphertext, credential_nonce, enabled, health, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, 1, 'unknown', ?)",
                        (connection_id, name, transport, json.dumps(config, separators=(",", ":")), ciphertext, nonce, int(time.time())),
                    )
        except __import__("sqlite3").IntegrityError as error:
            raise ValueError("MCP import contains a duplicate connection name") from error
        for connection_id, *_rest in prepared:
            try:
                self.refresh(connection_id)
            except Exception as error:
                self._set_health(connection_id, "error", error)
        return tuple(self.get(item[0]) for item in prepared)

    def list(self) -> tuple[dict[str, object], ...]:
        rows = self._require_storage().fetchall(
            "SELECT id, name, transport, config_json, credential_ciphertext IS NOT NULL AS credential_configured, enabled, health, updated_at "
            "FROM mcp_connections ORDER BY name, id"
        )
        return tuple(_public(row) for row in rows)

    def get(self, connection_id: str) -> dict[str, object]:
        row = self._require_storage().fetchone(
            "SELECT id, name, transport, config_json, credential_ciphertext IS NOT NULL AS credential_configured, enabled, health, updated_at "
            "FROM mcp_connections WHERE id=?", (connection_id,)
        )
        if row is None:
            raise LookupError("MCP connection not found")
        result = _public(row)
        result["tools"] = tuple(self._tool_names.get(connection_id, {}).values())
        return result

    def update(self, connection_id: str, name: str, transport: str, config: Mapping[str, object], *, enabled: bool, credential: str | None = None, clear_credential: bool = False) -> dict[str, object]:
        clean_name = _name(name)
        clean_config = _config(transport, config)
        storage = self._require_storage()
        fields: list[object] = [clean_name, transport, json.dumps(clean_config, separators=(",", ":")), int(enabled)]
        credential_sql = ""
        if credential is not None:
            ciphertext, nonce = self._encrypt(connection_id, credential)
            credential_sql = ", credential_ciphertext=?, credential_nonce=?"
            fields.extend((ciphertext, nonce))
        elif clear_credential:
            credential_sql = ", credential_ciphertext=NULL, credential_nonce=NULL"
        fields.extend((int(time.time()), connection_id))
        try:
            cursor = storage.execute(
                "UPDATE mcp_connections SET name=?, transport=?, config_json=?, enabled=?" + credential_sql + ", health='unknown', updated_at=? WHERE id=?",
                tuple(fields),
            )
        except __import__("sqlite3").IntegrityError as error:
            raise ValueError("an MCP connection with this name already exists") from error
        if cursor.rowcount != 1:
            raise LookupError("MCP connection not found")
        self._disconnect(connection_id)
        if enabled:
            try:
                self.refresh(connection_id)
            except Exception as error:
                self._set_health(connection_id, "error", error)
        return self.get(connection_id)

    def delete(self, connection_id: str) -> None:
        self._disconnect(connection_id)
        if self._require_storage().execute("DELETE FROM mcp_connections WHERE id=?", (connection_id,)).rowcount != 1:
            raise LookupError("MCP connection not found")

    def test(self, connection_id: str) -> dict[str, object]:
        self.refresh(connection_id)
        return self.get(connection_id)

    def refresh(self, connection_id: str) -> tuple[str, ...]:
        row = self._require_storage().fetchone("SELECT * FROM mcp_connections WHERE id=?", (connection_id,))
        if row is None:
            raise LookupError("MCP connection not found")
        if not bool(row["enabled"]):
            self._disconnect(connection_id)
            return ()
        self._disconnect(connection_id)
        client = self._make_client(row)
        try:
            client.request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "Lions-heart", "version": "1"}})
            response = client.request("tools/list", {})
            tools = response.get("tools") if isinstance(response, Mapping) else None
            if not isinstance(tools, list) or len(tools) > 128:
                raise ValueError("MCP server returned an invalid tool list")
            owner = _owner(connection_id)
            registered: dict[str, str] = {}
            shelf = self._require_shelf()
            for item in tools:
                if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
                    raise ValueError("MCP server returned an invalid tool")
                remote = str(item["name"])
                tool = _RemoteTool(remote, connection_id, remote, self.invoke)
                public = shelf.register_dynamic(owner, remote, tool, item)
                registered[remote] = public
        except BaseException:
            client.close()
            self._require_shelf().unregister_owner(_owner(connection_id))
            raise
        with self._lock:
            self._clients[connection_id] = client
            self._tool_names[connection_id] = registered
        self._set_health(connection_id, "healthy")
        return tuple(registered.values())

    def invoke(self, connection_id: str, remote_name: str, arguments: Mapping[str, object]) -> object:
        with self._lock:
            client = self._clients.get(connection_id)
        if client is None:
            self.refresh(connection_id)
            with self._lock:
                client = self._clients.get(connection_id)
        if client is None:
            raise RuntimeError("MCP connection is unavailable")
        try:
            return client.request("tools/call", {"name": remote_name, "arguments": dict(arguments)})
        except Exception as error:
            self._disconnect(connection_id)
            self._set_health(connection_id, "error", error)
            raise RuntimeError("MCP tool call failed") from error

    def _make_client(self, row: object) -> RpcClient:
        config = json.loads(str(row["config_json"]))  # type: ignore[index]
        credential = self._decrypt(row)  # type: ignore[arg-type]
        if self._client_factory is not None:
            return self._client_factory(str(row["transport"]), config, credential, self._timeout)  # type: ignore[operator,index]
        if row["transport"] == "http":  # type: ignore[index]
            return _HttpRpcClient(str(config["url"]), credential, self._timeout)
        return _StdioRpcClient([str(item) for item in config["argv"]], credential, self._timeout)

    def _encrypt(self, connection_id: str, credential: str) -> tuple[bytes, bytes]:
        if not isinstance(credential, str) or not credential or len(credential) > 8192:
            raise ValueError("MCP credential must contain 1 through 8192 characters")
        nonce = os.urandom(12)
        return AESGCM(self._master_key()).encrypt(nonce, credential.encode(), connection_id.encode()), nonce

    def _decrypt(self, row: Mapping[str, object]) -> str | None:
        if row["credential_ciphertext"] is None:
            return None
        value = AESGCM(self._master_key()).decrypt(row["credential_nonce"], row["credential_ciphertext"], str(row["id"]).encode())  # type: ignore[arg-type]
        return value.decode()

    def _master_key(self) -> bytes:
        return base64.urlsafe_b64decode(str(self._require_storage().config["master_key"]).encode("ascii"))

    def _set_health(self, connection_id: str, health: str, error: BaseException | None = None) -> None:
        # Store only a normalized state; provider/server text may contain secrets.
        del error
        self._require_storage().execute("UPDATE mcp_connections SET health=?, updated_at=? WHERE id=?", (health, int(time.time()), connection_id))

    def _disconnect(self, connection_id: str) -> None:
        self._require_shelf().unregister_owner(_owner(connection_id))
        with self._lock:
            client = self._clients.pop(connection_id, None)
            self._tool_names.pop(connection_id, None)
        if client is not None:
            client.close()

    def _stop(self) -> None:
        for connection_id in tuple(self._clients):
            self._disconnect(connection_id)
        self._storage = None
        self._shelf = None

    def _require_storage(self) -> StorageBeing:
        if self._storage is None:
            raise RuntimeError("MCP is not alive")
        return self._storage

    def _require_shelf(self) -> ToolShelfBeing:
        if self._shelf is None:
            raise RuntimeError("MCP is not alive")
        return self._shelf


class _HttpRpcClient:
    def __init__(self, url: str, credential: str | None, timeout: float) -> None:
        _safe_http_url(url)
        self._url, self._credential, self._timeout, self._next = url, credential, timeout, 0

    def request(self, method: str, params: Mapping[str, object] | None = None) -> object:
        _safe_http_url(self._url)
        self._next += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._next, "method": method, "params": params or {}}, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._credential:
            headers["Authorization"] = f"Bearer {self._credential}"
        request = urllib.request.Request(self._url, data=body, headers=headers, method="POST")
        try:
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(request, timeout=self._timeout) as response:
                data = response.read(MAX_MCP_MESSAGE + 1)
        except (OSError, urllib.error.URLError) as error:
            raise RuntimeError("MCP HTTP request failed") from error
        return _rpc_result(data)

    def close(self) -> None: pass


class _StdioRpcClient:
    def __init__(self, argv: list[str], credential: str | None, timeout: float) -> None:
        environment = {"PATH": os.environ.get("PATH", "")}
        if credential:
            environment["LIONS_HEART_MCP_TOKEN"] = credential
        self._process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment, text=False, shell=False)
        self._timeout, self._next = timeout, 0

    def request(self, method: str, params: Mapping[str, object] | None = None) -> object:
        process = self._process
        if process.poll() is not None or process.stdin is None or process.stdout is None:
            raise RuntimeError("MCP process stopped")
        self._next += 1
        data = json.dumps({"jsonrpc": "2.0", "id": self._next, "method": method, "params": params or {}}, separators=(",", ":")).encode() + b"\n"
        process.stdin.write(data); process.stdin.flush()
        result: queue.Queue[bytes | BaseException] = queue.Queue(maxsize=1)
        def read() -> None:
            try: result.put(process.stdout.readline(MAX_MCP_MESSAGE + 1))
            except BaseException as error: result.put(error)
        threading.Thread(target=read, name="lion-mcp-read", daemon=True).start()
        try: value = result.get(timeout=self._timeout)
        except queue.Empty as error:
            self.close(); raise TimeoutError("MCP process timed out") from error
        if isinstance(value, BaseException): raise RuntimeError("MCP process read failed") from value
        return _rpc_result(value)

    def close(self) -> None:
        process = self._process
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=1)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Prevent a public endpoint from bouncing Lion into a private network."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        raise urllib.error.HTTPError("", 502, "MCP redirects are disabled", {}, None)


def _rpc_result(data: bytes) -> object:
    if not data or len(data) > MAX_MCP_MESSAGE:
        raise ValueError("MCP response is empty or too large")
    try: payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error: raise ValueError("MCP response is invalid") from error
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0": raise ValueError("MCP response is invalid")
    if "error" in payload: raise RuntimeError("MCP server returned an error")
    return payload.get("result", {})


def _name(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 120 or any(ord(c) < 32 for c in value):
        raise ValueError("MCP name must contain 1 through 120 safe characters")
    return value.strip()


def _config(transport: str, value: Mapping[str, object]) -> dict[str, object]:
    if transport not in ("http", "stdio") or not isinstance(value, Mapping): raise ValueError("invalid MCP transport configuration")
    if transport == "http":
        if set(value) != {"url"} or not isinstance(value.get("url"), str): raise ValueError("HTTP MCP config needs only a URL")
        _safe_http_url(str(value["url"])); return {"url": value["url"]}
    argv = value.get("argv")
    if set(value) != {"argv"} or not isinstance(argv, list) or not 1 <= len(argv) <= 64 or any(not isinstance(x, str) or not x or len(x) > 1024 for x in argv):
        raise ValueError("stdio MCP config needs a bounded argv list")
    if not os.path.isabs(argv[0]): raise ValueError("stdio MCP executable must be an absolute path")
    return {"argv": list(argv)}


def _safe_http_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError("MCP HTTP URL must be a credential-free HTTPS URL")
    try: addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as error: raise ValueError("MCP HTTP host cannot be resolved") from error
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("MCP HTTP URL resolves to a private or unsafe address")


def _owner(connection_id: str) -> str: return connection_id[:16].lower()


def _public(row: Mapping[str, object]) -> dict[str, object]:
    config = json.loads(str(row["config_json"]))
    if row["transport"] == "stdio": config = {"argv": [config["argv"][0], *(["…"] if len(config["argv"]) > 1 else [])]}
    return {"id": row["id"], "name": row["name"], "transport": row["transport"], "config": config, "credential_configured": bool(row["credential_configured"]), "enabled": bool(row["enabled"]), "health": row["health"], "updated_at": row["updated_at"]}


__all__ = ["MAX_CONNECTIONS", "MAX_MCP_MESSAGE", "McpBeing", "RpcClient"]
