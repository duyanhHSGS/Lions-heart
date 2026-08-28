"""Durable SQLite and runtime-directory ownership for Lions-heart."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from secrets import token_bytes
from threading import RLock
from typing import Iterator, Sequence

from cor_being import Being, Life, World


SCHEMA_VERSION = 4


class StorageBeing(Being):
    """Own Lion's private runtime directory, configuration, and SQLite handle."""

    name = "storage"

    def __init__(self, *, data_root: str | Path | None = None) -> None:
        configured = data_root or os.environ.get("LIONS_HEART_DATA_DIR")
        self._data_root = Path(configured) if configured else Path.cwd() / "user"
        self._connection: sqlite3.Connection | None = None
        self._lock = RLock()
        self._config: dict[str, object] = {}

    @property
    def data_root(self) -> Path:
        return self._data_root

    @property
    def config(self) -> dict[str, object]:
        with self._lock:
            return dict(self._config)

    def birth(self, world: World, life: Life) -> None:
        del world
        self._data_root.mkdir(parents=True, exist_ok=True)
        config_path = self._data_root / "config.json"
        config = self._read_or_create_config(config_path)
        connection = sqlite3.connect(
            self._data_root / "lions-heart.sqlite3",
            check_same_thread=False,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            self._migrate(connection)
        except BaseException:
            connection.close()
            raise
        self._config = config
        self._connection = connection
        life.on_death(self._close)
        # TODO: Add encrypted backup/export with explicit owner confirmation.

    def _read_or_create_config(self, path: Path) -> dict[str, object]:
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RuntimeError("Lion config is unreadable") from error
            if not isinstance(payload, dict):
                raise RuntimeError("Lion config must contain a JSON object")
        else:
            payload = {
                "version": 1,
                "master_key": base64.urlsafe_b64encode(token_bytes(32)).decode("ascii"),
                "bind_host": "127.0.0.1",
                "bind_port": 8765,
                "public_base_url": "",
                "allow_insecure_http": False,
            }
            self._atomic_json_write(path, payload)
        key = payload.get("master_key")
        if not isinstance(key, str):
            raise RuntimeError("Lion config master_key is missing")
        try:
            decoded = base64.urlsafe_b64decode(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise RuntimeError("Lion config master_key is invalid") from error
        if len(decoded) != 32:
            raise RuntimeError("Lion config master_key must decode to 32 bytes")
        return payload

    @staticmethod
    def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
        temporary = path.with_suffix(".json.tmp")
        data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temporary.write_text(data, encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current > SCHEMA_VERSION:
            raise RuntimeError("Lion database was created by a newer version")
        if current == 0:
            connection.executescript(
                """
                BEGIN;
                CREATE TABLE owner (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    username TEXT NOT NULL UNIQUE,
                    password_hash BLOB NOT NULL,
                    password_salt BLOB NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE auth_sessions (
                    token_hash BLOB PRIMARY KEY,
                    csrf_hash BLOB NOT NULL,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX auth_sessions_expiry ON auth_sessions(expires_at);
                CREATE TABLE settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE provider_secrets (
                    provider TEXT PRIMARY KEY,
                    ciphertext BLOB NOT NULL,
                    nonce BLOB NOT NULL,
                    suffix TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    workspace TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE conversations (
                    id TEXT PRIMARY KEY,
                    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                    title TEXT NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    archived INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX conversations_updated ON conversations(updated_at DESC);
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX events_conversation ON events(conversation_id, id);
                CREATE VIRTUAL TABLE conversation_search USING fts5(
                    conversation_id UNINDEXED, title, body
                );
                CREATE TABLE activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms INTEGER,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    error_kind TEXT,
                    created_at INTEGER NOT NULL
                );
                PRAGMA user_version = 1;
                COMMIT;
                """
            )
            current = 1
        if current == 1:
            connection.executescript(
                """
                BEGIN;
                CREATE TABLE turns (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    error_kind TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX one_active_turn_per_conversation
                    ON turns(conversation_id) WHERE status IN ('queued', 'running', 'waiting_approval');
                CREATE TABLE turn_events (
                    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (turn_id, sequence)
                );
                CREATE TABLE approvals (
                    id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    risk_summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    result_json TEXT,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX approvals_turn_status ON approvals(turn_id, status);
                PRAGMA user_version = 2;
                COMMIT;
                """
            )
            current = 2
        if current == 2:
            connection.executescript(
                """
                BEGIN;
                CREATE TABLE attachments (
                    id TEXT PRIMARY KEY,
                    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
                    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
                    file_name TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    storage_name TEXT NOT NULL UNIQUE,
                    extracted_text TEXT,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX attachments_owner ON attachments(project_id, conversation_id, created_at);
                CREATE INDEX attachments_digest ON attachments(sha256);
                CREATE VIRTUAL TABLE attachment_search USING fts5(
                    attachment_id UNINDEXED, project_id UNINDEXED, body
                );
                CREATE TABLE mcp_connections (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    transport TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    credential_ciphertext BLOB,
                    credential_nonce BLOB,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    health TEXT NOT NULL DEFAULT 'unknown',
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE media_jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    settings_json TEXT NOT NULL,
                    output_name TEXT,
                    error_kind TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX media_jobs_gallery ON media_jobs(kind, created_at DESC);
                CREATE TABLE recipes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    graph_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE recipe_runs (
                    id TEXT PRIMARY KEY,
                    recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    inputs_json TEXT NOT NULL,
                    outputs_json TEXT,
                    error_kind TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE saved_prompts (
                    id TEXT PRIMARY KEY,
                    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                PRAGMA user_version = 3;
                COMMIT;
                """
            )
            current = 3
        if current == 3:
            connection.executescript(
                """
                BEGIN;
                CREATE VIRTUAL TABLE project_search USING fts5(
                    project_id UNINDEXED, name
                );
                INSERT INTO project_search(project_id, name) SELECT id, name FROM projects;
                PRAGMA user_version = 4;
                COMMIT;
                """
            )

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("storage is not alive")
        return self._connection

    def execute(self, sql: str, parameters: Sequence[object] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._require_connection().execute(sql, parameters)

    def fetchone(self, sql: str, parameters: Sequence[object] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._require_connection().execute(sql, parameters).fetchone()

    def fetchall(self, sql: str, parameters: Sequence[object] = ()) -> tuple[sqlite3.Row, ...]:
        with self._lock:
            return tuple(self._require_connection().execute(sql, parameters).fetchall())

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._require_connection()
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def _close(self) -> None:
        with self._lock:
            connection = self._connection
            self._connection = None
            self._config = {}
            if connection is not None:
                connection.close()


__all__ = ["SCHEMA_VERSION", "StorageBeing"]
