"""Durable Lion projects and indexed project search."""

from __future__ import annotations

import time
from pathlib import Path
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
        life.on_death(self._forget)
        # TODO: Persist the currently selected project separately from chat assignment.

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

    def search(self, query: str, *, limit: int = 30) -> tuple[dict[str, object], ...]:
        if not isinstance(query, str) or not query.strip():
            return self.list()[:limit]
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be from 1 through 100")
        rows = self._require_storage().fetchall(
            "SELECT p.id, p.name, p.workspace, p.updated_at FROM project_search s "
            "JOIN projects p ON p.id=s.project_id WHERE project_search MATCH ? "
            "ORDER BY rank LIMIT ?",
            (query.strip(), limit),
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
