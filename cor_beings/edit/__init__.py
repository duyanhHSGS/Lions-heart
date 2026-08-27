"""Tiny UTF-8 file-writing tool Being."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from cor_being import Being, Life, World


class EditBeing(Being):
    """Replace one UTF-8 text file with caller-provided content."""

    name = "edit"
    description = "Replace one UTF-8 text file with new content."

    def birth(self, world: World, life: Life) -> None:
        # TODO: Add patch-style edits and atomic replacement after the tiny spine proves itself.
        return None

    def run(self, arguments: Mapping[str, object]) -> str:
        path = arguments.get("path")
        content = arguments.get("content")
        if not isinstance(path, str) or not path:
            raise ValueError("edit requires a non-empty string path")
        if not isinstance(content, str):
            raise TypeError("edit requires string content")
        written = Path(path).write_text(content, encoding="utf-8")
        return f"wrote {written} characters to {path}"
