"""Tiny UTF-8 file-reading tool Being."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from cor_being import Being, Life, World
from cor_beings.workspace import WorkspaceBeing


class ReadBeing(Being):
    """Read one UTF-8 text file when the tool shelf asks."""

    name = "read"
    description = "Read one UTF-8 text file."
    needs = (WorkspaceBeing,)

    def __init__(self) -> None:
        self._workspace: WorkspaceBeing | None = None

    def birth(self, world: World, life: Life) -> None:
        self._workspace = world.need(WorkspaceBeing)
        life.on_death(self._forget)
        # TODO: Add bounded binary sniffing before future attachment reuse.

    def _forget(self) -> None:
        self._workspace = None

    def run(self, arguments: Mapping[str, object]) -> str:
        path = arguments.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError("read requires a non-empty string path")
        target = self._workspace.resolve(path) if self._workspace is not None else Path(path)
        return target.read_text(encoding="utf-8")
