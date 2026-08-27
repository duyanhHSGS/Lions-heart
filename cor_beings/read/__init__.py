"""Tiny UTF-8 file-reading tool Being."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from cor_being import Being, Life, World


class ReadBeing(Being):
    """Read one UTF-8 text file when the tool shelf asks."""

    name = "read"
    description = "Read one UTF-8 text file."

    def birth(self, world: World, life: Life) -> None:
        # TODO: Add workspace fencing through a separate safety Being.
        return None

    def run(self, arguments: Mapping[str, object]) -> str:
        path = arguments.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError("read requires a non-empty string path")
        return Path(path).read_text(encoding="utf-8")
