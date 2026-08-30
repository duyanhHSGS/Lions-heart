"""Durable Lion projects and indexed project search."""

from __future__ import annotations

import time
from pathlib import Path
import json
import unicodedata
from uuid import uuid4

from cor_being import Being, Life, World
from cor_beings.storage import StorageBeing
from cor_beings.workspace import WorkspaceBeing


class ProjectsBeing(Being):
    """Own project metadata and selected workspace changes."""

    name = "projects"
    needs = (StorageBeing, WorkspaceBeing)

    def __init__(self) -> None:
        self._storage: StorageBeing | None = None
        self._workspace: WorkspaceBeing | None = None

    def birth(self, world: World, life: Life) -> None:
        self._storage = world.need(StorageBeing)
        self._workspace = world.need(WorkspaceBeing)
        selected = self._storage.fetchone("SELECT value_json FROM app_state WHERE key='selected_project'")
        if selected is not None:
            try:
                project_id = json.loads(selected["value_json"])
                if not isinstance(project_id, str):
                    raise ValueError("selected project ID is invalid")
                row = self._storage.fetchone("SELECT workspace FROM projects WHERE id=?", (project_id,))
                if row is None:
                    self._storage.execute("DELETE FROM app_state WHERE key='selected_project'")
                elif row["workspace"]:
                    self._workspace.select(row["workspace"])
            except (ValueError, OSError, json.JSONDecodeError):
                self._storage.execute("DELETE FROM app_state WHERE key='selected_project'")
        life.on_death(self._forget)
        # TODO: Block selection changes while a project conversation has an active turn.

    @property
    def selected_id(self) -> str | None:
        row = self._require_storage().fetchone("SELECT value_json FROM app_state WHERE key='selected_project'")
        if row is None: return None
        value = json.loads(row["value_json"])
        return value if isinstance(value, str) else None

    def select(self, project_id: str) -> dict[str, object]:
        storage = self._require_storage()
        row = storage.fetchone("SELECT id,name,workspace,created_at,updated_at FROM projects WHERE id=?", (project_id,))
        if row is None: raise LookupError("project not found")
        if not row["workspace"]: raise ValueError("project has no workspace")
        workspace = self._workspace
        if workspace is None: raise RuntimeError("projects is not alive")
        workspace.select(str(row["workspace"]))
        storage.execute(
            "INSERT INTO app_state(key,value_json,updated_at) VALUES ('selected_project',?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
            (json.dumps(project_id), int(time.time())),
        )
        return dict(row)

    def create(self, name: str, *, workspace: str | None = None) -> str:
        clean = _name(name)
        resolved = self._validate_workspace(workspace)
        project_id = uuid4().hex
        now = int(time.time())
        storage = self._require_storage()
        with storage.transaction() as connection:
            connection.execute(
                "INSERT INTO projects(id, name, workspace, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (project_id, clean, resolved, now, now),
            )
            connection.execute(
                "INSERT INTO project_search(project_id, name) VALUES (?, ?)",
                (project_id, clean),
            )
        return project_id

    def list(self) -> tuple[dict[str, object], ...]:
        rows = self._require_storage().fetchall(
            "SELECT id, name, workspace, created_at, updated_at FROM projects ORDER BY updated_at DESC"
        )
        return tuple(dict(row) for row in rows)

    def rename(self, project_id: str, name: str) -> None:
        clean = _name(name)
        storage = self._require_storage()
        with storage.transaction() as connection:
            cursor = connection.execute(
                "UPDATE projects SET name=?, updated_at=? WHERE id=?",
                (clean, int(time.time()), project_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("project not found")
            connection.execute(
                "DELETE FROM project_search WHERE project_id=?", (project_id,)
            )
            connection.execute(
                "INSERT INTO project_search(project_id, name) VALUES (?, ?)",
                (project_id, clean),
            )

    def delete(self, project_id: str) -> None:
        storage = self._require_storage()
        with storage.transaction() as connection:
            connection.execute(
                "DELETE FROM project_search WHERE project_id=?", (project_id,)
            )
            cursor = connection.execute(
                "DELETE FROM projects WHERE id=?", (project_id,)
            )
            if cursor.rowcount != 1:
                raise LookupError("project not found")
            selected = connection.execute("SELECT value_json FROM app_state WHERE key='selected_project'").fetchone()
            if selected is not None and json.loads(selected["value_json"]) == project_id:
                connection.execute("DELETE FROM app_state WHERE key='selected_project'")

    def search(self, query: str, *, limit: int = 30) -> tuple[dict[str, object], ...]:
        if not isinstance(query, str) or not query.strip():
            return self.list()[:limit]
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be from 1 through 100")
        terms = [term for term in unicodedata.normalize("NFKC", query).split() if term][:20]
        expression = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
        rows = self._require_storage().fetchall(
            "SELECT p.id, p.name, p.workspace, p.updated_at FROM project_search s "
            "JOIN projects p ON p.id=s.project_id WHERE project_search MATCH ? "
            "ORDER BY rank LIMIT ?",
            (expression, limit),
        )
        return tuple(dict(row) for row in rows)

    def _validate_workspace(self, workspace: str | None) -> str | None:
        if workspace is None or workspace == "":
            return None
        if not isinstance(workspace, str):
            raise TypeError("workspace must be a directory path")
        path = Path(workspace).resolve(strict=True)
        if not path.is_dir():
            raise ValueError("workspace must be a directory")
        return str(path)

    def _require_storage(self) -> StorageBeing:
        if self._storage is None:
            raise RuntimeError("projects is not alive")
        return self._storage

    def _forget(self) -> None:
        self._storage = None
        self._workspace = None


def _name(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 120:
        raise ValueError("project name must contain 1 through 120 characters")
    return value.strip()


__all__ = ["ProjectsBeing"]
