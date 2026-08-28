"""Selected-project filesystem boundary shared by Lion's built-in tools."""

from __future__ import annotations

from pathlib import Path

from cor_being import Being, Life, World


class WorkspaceBeing(Being):
    """Resolve tool paths beneath one explicit project root."""

    name = "workspace"

    def __init__(self, *, root: str | Path | None = None) -> None:
        self._configured_root = Path(root) if root is not None else Path.cwd()
        self._root: Path | None = None

    @property
    def root(self) -> Path:
        if self._root is None:
            raise RuntimeError("workspace is not alive")
        return self._root

    def birth(self, world: World, life: Life) -> None:
        del world
        root = self._configured_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("workspace root must be a directory")
        self._root = root
        life.on_death(self._forget)
        # TODO: Switch this root from durable project selection instead of constructor config.

    def resolve(self, path: str, *, writing: bool = False) -> Path:
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a non-empty string")
        root = self.root
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise PermissionError("path escapes the selected project workspace") from error
        if writing:
            parent = resolved.parent.resolve(strict=True)
            try:
                parent.relative_to(root)
            except ValueError as error:
                raise PermissionError("path parent escapes the selected project workspace") from error
        else:
            resolved = candidate.resolve(strict=True)
            try:
                resolved.relative_to(root)
            except ValueError as error:
                raise PermissionError("path escapes the selected project workspace") from error
        return resolved

    def _forget(self) -> None:
        self._root = None


__all__ = ["WorkspaceBeing"]
