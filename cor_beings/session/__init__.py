"""Authoritative durable conversation history for Lions-heart."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import time
from threading import Lock
from types import MappingProxyType
from uuid import uuid4

from cor_being import Being, Life, World
from cor_beings.storage import StorageBeing


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """One immutable event in the current in-memory session."""

    kind: str
    data: Mapping[str, object]


class SessionBeing(Being):
    """Own the active conversation and its append-only event history."""

    name = "session"
    needs = (StorageBeing,)

    def __init__(self) -> None:
        self._events: list[SessionEvent] = []
        self._events_lock = Lock()
        self._storage: StorageBeing | None = None
        self._conversation_id: str | None = None
        self._temporary = False

    def birth(self, world: World, life: Life) -> None:
        storage = world.need(StorageBeing)
        self._storage = storage
        row = storage.fetchone(
            "SELECT id FROM conversations WHERE archived=0 ORDER BY updated_at DESC LIMIT 1"
        )
        if row is None:
            self.new_conversation()
        else:
            self.open_conversation(row["id"])
        life.on_death(self._forget)
        # TODO: Add subscriber notifications without making UI state authoritative.

    @property
    def conversation_id(self) -> str:
        if self._conversation_id is None:
            raise RuntimeError("session is not alive")
        return self._conversation_id

    @property
    def events(self) -> tuple[SessionEvent, ...]:
        """Return a stable snapshot of all events in append order."""
        with self._events_lock:
            return tuple(self._events)

    @property
    def temporary(self) -> bool:
        with self._events_lock:
            return self._temporary

    def has_conversation(self, conversation_id: str) -> bool:
        with self._events_lock:
            if self._temporary and self._conversation_id == conversation_id:
                return True
        storage = self._require_storage()
        return storage.fetchone("SELECT 1 FROM conversations WHERE id=?", (conversation_id,)) is not None

    def append(self, kind: str, **data: object) -> SessionEvent:
        """Append one immutable event and return it."""
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("session event kind must be a non-empty string")
        event = SessionEvent(kind=kind, data=MappingProxyType(dict(data)))
        with self._events_lock:
            self._events.append(event)
            conversation_id = self._conversation_id
            storage = self._storage
            temporary = self._temporary
        if storage is not None and conversation_id is not None and not temporary:
            self._persist(storage, conversation_id, event)
        return event

    def append_to(self, conversation_id: str, kind: str, **data: object) -> SessionEvent:
        """Append to one conversation without changing which chat the UI is viewing."""
        with self._events_lock:
            active = self._conversation_id == conversation_id
        if active:
            return self.append(kind, **data)
        if not self.has_conversation(conversation_id):
            raise LookupError("conversation not found")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("session event kind must be a non-empty string")
        event = SessionEvent(kind=kind, data=MappingProxyType(dict(data)))
        self._persist(self._require_storage(), conversation_id, event)
        return event

    def events_for(self, conversation_id: str) -> tuple[SessionEvent, ...]:
        with self._events_lock:
            if self._conversation_id == conversation_id:
                return tuple(self._events)
        rows = self._require_storage().fetchall(
            "SELECT kind, data_json FROM events WHERE conversation_id=? ORDER BY id",
            (conversation_id,),
        )
        if not rows and not self.has_conversation(conversation_id):
            raise LookupError("conversation not found")
        return tuple(
            SessionEvent(row["kind"], MappingProxyType(json.loads(row["data_json"])))
            for row in rows
        )

    @staticmethod
    def _persist(storage: StorageBeing, conversation_id: str, event: SessionEvent) -> None:
        now = int(time.time())
        safe = _json_safe(event.data)
        storage.execute(
            "INSERT INTO events(conversation_id, kind, data_json, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, event.kind, json.dumps(safe, ensure_ascii=False), now),
        )
        storage.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
        if event.kind in ("user", "assistant"):
            text = event.data.get("text")
            if isinstance(text, str) and text:
                storage.execute(
                    "INSERT INTO conversation_search(conversation_id, title, body) VALUES (?, '', ?)",
                    (conversation_id, text),
                )

    def new_conversation(self, *, title: str = "New chat", temporary: bool = False) -> str:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("conversation title must be a non-empty string")
        conversation_id = uuid4().hex
        storage = self._storage
        if storage is None:
            raise RuntimeError("session is not alive")
        if not temporary:
            now = int(time.time())
            storage.execute(
                "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (conversation_id, title.strip(), now, now),
            )
            storage.execute(
                "INSERT INTO conversation_search(conversation_id, title, body) VALUES (?, ?, '')",
                (conversation_id, title.strip()),
            )
        with self._events_lock:
            self._conversation_id = conversation_id
            self._temporary = temporary
            self._events = []
        return conversation_id

    def open_conversation(self, conversation_id: str) -> None:
        if not isinstance(conversation_id, str) or not conversation_id:
            raise ValueError("conversation id must be a non-empty string")
        storage = self._storage
        if storage is None:
            raise RuntimeError("session is not alive")
        if storage.fetchone("SELECT id FROM conversations WHERE id=?", (conversation_id,)) is None:
            raise LookupError("conversation not found")
        rows = storage.fetchall(
            "SELECT kind, data_json FROM events WHERE conversation_id=? ORDER BY id", (conversation_id,)
        )
        events = [
            SessionEvent(row["kind"], MappingProxyType(json.loads(row["data_json"])))
            for row in rows
        ]
        with self._events_lock:
            self._conversation_id = conversation_id
            self._temporary = False
            self._events = events

    def list_conversations(self, *, include_archived: bool = False) -> tuple[dict[str, object], ...]:
        storage = self._storage
        if storage is None:
            raise RuntimeError("session is not alive")
        where = "" if include_archived else "WHERE archived=0"
        rows = storage.fetchall(
            f"SELECT id, project_id, title, pinned, archived, created_at, updated_at FROM conversations {where} "
            "ORDER BY pinned DESC, updated_at DESC"
        )
        return tuple(dict(row) for row in rows)

    def rename_conversation(self, conversation_id: str, title: str) -> None:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("conversation title must be a non-empty string")
        cursor = self._require_storage().execute(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
            (title.strip(), int(time.time()), conversation_id),
        )
        if cursor.rowcount != 1:
            raise LookupError("conversation not found")

    def archive_conversation(self, conversation_id: str, *, archived: bool = True) -> None:
        cursor = self._require_storage().execute(
            "UPDATE conversations SET archived=?, updated_at=? WHERE id=?",
            (int(archived), int(time.time()), conversation_id),
        )
        if cursor.rowcount != 1:
            raise LookupError("conversation not found")

    def pin_conversation(self, conversation_id: str, *, pinned: bool = True) -> None:
        cursor = self._require_storage().execute(
            "UPDATE conversations SET pinned=?, updated_at=? WHERE id=?",
            (int(pinned), int(time.time()), conversation_id),
        )
        if cursor.rowcount != 1:
            raise LookupError("conversation not found")

    def assign_project(self, conversation_id: str, project_id: str | None) -> None:
        storage = self._require_storage()
        if project_id is not None and storage.fetchone(
            "SELECT id FROM projects WHERE id=?", (project_id,)
        ) is None:
            raise LookupError("project not found")
        cursor = storage.execute(
            "UPDATE conversations SET project_id=?, updated_at=? WHERE id=?",
            (project_id, int(time.time()), conversation_id),
        )
        if cursor.rowcount != 1:
            raise LookupError("conversation not found")

    def fork_conversation(self, conversation_id: str, *, title: str | None = None) -> str:
        storage = self._require_storage()
        source = storage.fetchone(
            "SELECT project_id, title FROM conversations WHERE id=?", (conversation_id,)
        )
        if source is None:
            raise LookupError("conversation not found")
        fork_id = uuid4().hex
        fork_title = (title or f"{source['title']} (fork)").strip()
        if not fork_title:
            raise ValueError("conversation title must be a non-empty string")
        now = int(time.time())
        with storage.transaction() as connection:
            connection.execute(
                "INSERT INTO conversations(id, project_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (fork_id, source["project_id"], fork_title, now, now),
            )
            connection.execute(
                "INSERT INTO events(conversation_id, kind, data_json, created_at) "
                "SELECT ?, kind, data_json, created_at FROM events WHERE conversation_id=? ORDER BY id",
                (fork_id, conversation_id),
            )
            connection.execute(
                "INSERT INTO conversation_search(conversation_id, title, body) VALUES (?, ?, '')",
                (fork_id, fork_title),
            )
            texts = connection.execute(
                "SELECT data_json FROM events WHERE conversation_id=? AND kind IN ('user', 'assistant')",
                (fork_id,),
            ).fetchall()
            for row in texts:
                data = json.loads(row["data_json"])
                if isinstance(data.get("text"), str) and data["text"]:
                    connection.execute(
                        "INSERT INTO conversation_search(conversation_id, title, body) VALUES (?, '', ?)",
                        (fork_id, data["text"]),
                    )
        return fork_id

    def search(self, query: str, *, limit: int = 50) -> tuple[dict[str, object], ...]:
        if not isinstance(query, str) or not query.strip():
            return self.list_conversations()[:limit]
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be from 1 through 100")
        rows = self._require_storage().fetchall(
            "SELECT c.id, c.project_id, c.title, c.pinned, c.archived, c.updated_at, "
            "MIN(s.rank) AS score FROM conversation_search s "
            "JOIN conversations c ON c.id=s.conversation_id "
            "WHERE conversation_search MATCH ? GROUP BY c.id ORDER BY score LIMIT ?",
            (query.strip(), limit),
        )
        return tuple(dict(row) for row in rows)

    def export_conversation(self, conversation_id: str, *, format: str = "markdown") -> str:
        events = self.events_for(conversation_id)
        if format == "json":
            return json.dumps(
                [{"kind": event.kind, "data": _json_safe(event.data)} for event in events],
                ensure_ascii=False,
                indent=2,
            )
        if format != "markdown":
            raise ValueError("format must be markdown or json")
        parts: list[str] = []
        for event in events:
            if event.kind in ("user", "assistant"):
                parts.append(f"## {event.kind.title()}\n\n{event.data.get('text', '')}")
        return "\n\n".join(parts) + ("\n" if parts else "")

    def delete_conversation(self, conversation_id: str) -> None:
        storage = self._require_storage()
        with storage.transaction() as connection:
            connection.execute("DELETE FROM conversation_search WHERE conversation_id=?", (conversation_id,))
            cursor = connection.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
            if cursor.rowcount != 1:
                raise LookupError("conversation not found")
        if self._conversation_id == conversation_id:
            self.new_conversation()

    def _require_storage(self) -> StorageBeing:
        if self._storage is None:
            raise RuntimeError("session is not alive")
        return self._storage

    def _forget(self) -> None:
        with self._events_lock:
            self._events = []
            self._conversation_id = None
            self._temporary = False
            self._storage = None


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return str(value)
