"""Tiny UTF-8 file-writing tool Being."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from cor_being import Being, Life, World
from cor_beings.workspace import WorkspaceBeing


class EditBeing(Being):
    """Replace one UTF-8 text file with caller-provided content."""

    name = "edit"
    description = "Replace one UTF-8 text file with new content."
    needs = (WorkspaceBeing,)

    def __init__(self) -> None:
        self._workspace: WorkspaceBeing | None = None

    def birth(self, world: World, life: Life) -> None:
        self._workspace = world.need(WorkspaceBeing)
        life.on_death(self._forget)
        # TODO: Add patch-style edits with conflict detection.

    def _forget(self) -> None:
        self._workspace = None

    def run(self, arguments: Mapping[str, object]) -> str:
        path = arguments.get("path")
        content = arguments.get("content")
        if not isinstance(path, str) or not path:
            raise ValueError("edit requires a non-empty string path")
        if not isinstance(content, str):
            raise TypeError("edit requires string content")
        target = self._workspace.resolve(path, writing=True) if self._workspace is not None else Path(path)
        written = target.write_text(content, encoding="utf-8")
        return f"wrote {written} characters to {path}"
