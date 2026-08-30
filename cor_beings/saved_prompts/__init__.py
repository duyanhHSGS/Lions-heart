"""Durable, searchable snippets that never send themselves."""

from __future__ import annotations

import time
import unicodedata
from uuid import uuid4

from cor_being import Being, Life, World
from cor_beings.storage import StorageBeing

MAX_PROMPT_NAME = 120
MAX_PROMPT_BODY = 32 * 1024


class SavedPromptsBeing(Being):
    """Own bounded prompt CRUD, optimistic edits, and indexed search."""

    name = "saved_prompts"
    needs = (StorageBeing,)

    def __init__(self) -> None:
        self._storage: StorageBeing | None = None

    def birth(self, world: World, life: Life) -> None:
        self._storage = world.need(StorageBeing)
        life.on_death(self._forget)
        # TODO: Add folders only if real prompt collections outgrow search.

    def create(self, name: str, body: str, *, project_id: str | None = None) -> dict[str, object]:
        clean_name, normalized = _prompt_name(name)
        clean_body = _prompt_body(body)
        prompt_id = uuid4().hex
        now = int(time.time())
        storage = self._require_storage()
        try:
            with storage.transaction() as connection:
                _require_project(connection, project_id)
                connection.execute(
                    "INSERT INTO saved_prompts(id, project_id, name, body, created_at, updated_at, normalized_name, revision) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                    (prompt_id, project_id, clean_name, clean_body, now, now, normalized),
                )
                connection.execute(
                    "INSERT INTO saved_prompt_search(prompt_id, name, body) VALUES (?, ?, ?)",
                    (prompt_id, clean_name, clean_body),
                )
        except __import__("sqlite3").IntegrityError as error:
            raise ValueError("a prompt with this name already exists in that scope") from error
        return self.get(prompt_id)

    def get(self, prompt_id: str) -> dict[str, object]:
        row = self._require_storage().fetchone(
            "SELECT id, project_id, name, body, revision, created_at, updated_at FROM saved_prompts WHERE id=?",
            (prompt_id,),
        )
        if row is None:
            raise LookupError("saved prompt not found")
        return dict(row)

    def list(self, *, project_id: str | None = None, limit: int = 100) -> tuple[dict[str, object], ...]:
        _limit(limit)
        if project_id is None:
            rows = self._require_storage().fetchall(
                "SELECT id, project_id, name, body, revision, created_at, updated_at FROM saved_prompts "
                "ORDER BY updated_at DESC, name, id LIMIT ?", (limit,)
            )
        else:
            rows = self._require_storage().fetchall(
                "SELECT id, project_id, name, body, revision, created_at, updated_at FROM saved_prompts "
                "WHERE project_id=? ORDER BY updated_at DESC, name, id LIMIT ?", (project_id, limit)
            )
        return tuple(dict(row) for row in rows)

    def search(self, query: str, *, project_id: str | None = None, limit: int = 50) -> tuple[dict[str, object], ...]:
        _limit(limit)
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        terms = [term for term in unicodedata.normalize("NFKC", query).split() if term]
        if not terms:
            return self.list(project_id=project_id, limit=limit)
        expression = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms[:20])
        scope_sql = "AND p.project_id=?" if project_id is not None else ""
        parameters: tuple[object, ...] = (expression, project_id, limit) if project_id is not None else (expression, limit)
        rows = self._require_storage().fetchall(
            "SELECT p.id, p.project_id, p.name, p.body, p.revision, p.created_at, p.updated_at "
            "FROM saved_prompt_search s JOIN saved_prompts p ON p.id=s.prompt_id "
            f"WHERE saved_prompt_search MATCH ? {scope_sql} ORDER BY rank, p.updated_at DESC, p.id LIMIT ?",
            parameters,
        )
        return tuple(dict(row) for row in rows)

    def update(self, prompt_id: str, name: str, body: str, *, revision: int) -> dict[str, object]:
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError("revision must be a positive integer")
        clean_name, normalized = _prompt_name(name)
        clean_body = _prompt_body(body)
        storage = self._require_storage()
        try:
            with storage.transaction() as connection:
                cursor = connection.execute(
                    "UPDATE saved_prompts SET name=?, normalized_name=?, body=?, revision=revision+1, updated_at=? "
                    "WHERE id=? AND revision=?",
                    (clean_name, normalized, clean_body, int(time.time()), prompt_id, revision),
                )
                if cursor.rowcount != 1:
                    if connection.execute("SELECT 1 FROM saved_prompts WHERE id=?", (prompt_id,)).fetchone() is None:
                        raise LookupError("saved prompt not found")
                    raise RuntimeError("saved prompt changed; refresh before editing")
                connection.execute("DELETE FROM saved_prompt_search WHERE prompt_id=?", (prompt_id,))
                connection.execute(
                    "INSERT INTO saved_prompt_search(prompt_id, name, body) VALUES (?, ?, ?)",
                    (prompt_id, clean_name, clean_body),
                )
        except __import__("sqlite3").IntegrityError as error:
            raise ValueError("a prompt with this name already exists in that scope") from error
        return self.get(prompt_id)

    def delete(self, prompt_id: str) -> None:
        storage = self._require_storage()
        with storage.transaction() as connection:
            connection.execute("DELETE FROM saved_prompt_search WHERE prompt_id=?", (prompt_id,))
            if connection.execute("DELETE FROM saved_prompts WHERE id=?", (prompt_id,)).rowcount != 1:
                raise LookupError("saved prompt not found")

    def _require_storage(self) -> StorageBeing:
        if self._storage is None:
            raise RuntimeError("saved prompts is not alive")
        return self._storage

    def _forget(self) -> None:
        self._storage = None


def _prompt_name(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise TypeError("prompt name must be a string")
    clean = unicodedata.normalize("NFKC", value).strip()
    if not clean or len(clean) > MAX_PROMPT_NAME or any(unicodedata.category(c) == "Cc" for c in clean):
        raise ValueError(f"prompt name must contain 1 through {MAX_PROMPT_NAME} safe characters")
    return clean, clean.casefold()


def _prompt_body(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("prompt body must be a string")
    value = unicodedata.normalize("NFC", value)
    if not value.strip() or len(value) > MAX_PROMPT_BODY or "\x00" in value:
        raise ValueError(f"prompt body must contain 1 through {MAX_PROMPT_BODY} characters")
    return value


def _limit(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
        raise ValueError("limit must be from 1 through 100")


def _require_project(connection: object, project_id: str | None) -> None:
    if project_id is not None and connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:  # type: ignore[attr-defined]
        raise LookupError("project not found")


__all__ = ["MAX_PROMPT_BODY", "MAX_PROMPT_NAME", "SavedPromptsBeing"]
