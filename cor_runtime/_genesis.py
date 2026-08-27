"""Find and birth external Cor Leonis Beings without feature-specific imports."""

from __future__ import annotations

import importlib
from collections.abc import Iterable
from importlib import metadata
from types import ModuleType
from typing import Any

from cor_being import Being

from ._world import RuntimeWorld

BEING_GROUP = "corleonis.beings"
DEFAULT_BEING_MODULES = ("cor_beings",)


def _being_types_from_module(module: ModuleType) -> tuple[type[Being], ...]:
    """Read Being classes from a module's conventional ``get_beings`` hook."""
    creator = getattr(module, "get_beings", None)
    if creator is None:
        return ()
    being_types = tuple(creator())
    if any(
        not isinstance(being_type, type) or not issubclass(being_type, Being)
        for being_type in being_types
    ):
        raise TypeError(f"invalid Being returned by {module.__name__}.get_beings()")
    return being_types


def find_beings(
    *,
    module_names: Iterable[str] = DEFAULT_BEING_MODULES,
    entry_points: Iterable[Any] | None = None,
) -> tuple[type[Being], ...]:
    """Find Being classes in local modules and Python entry points.

    A missing conventional module is ignored so the core runtime remains useful
    before any hands are installed. Entry points use the ``corleonis.beings``
    group and may resolve to either a Being class or a module exposing
    ``get_beings``.
    """
    found: list[type[Being]] = []
    seen: set[type[Being]] = set()

    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            continue
        for being_type in _being_types_from_module(module):
            if being_type not in seen:
                found.append(being_type)
                seen.add(being_type)

    points = entry_points
    if points is None:
        points = metadata.entry_points(group=BEING_GROUP)
    else:
        points = tuple(points)

    for entry_point in points:
        loaded = entry_point.load()
        if isinstance(loaded, type) and issubclass(loaded, Being):
            being_types = (loaded,)
        elif isinstance(loaded, ModuleType):
            being_types = _being_types_from_module(loaded)
        else:
            raise TypeError(f"invalid Being entry point: {entry_point.name}")
        for being_type in being_types:
            if being_type not in seen:
                found.append(being_type)
                seen.add(being_type)

    return tuple(found)


def genesis(world: RuntimeWorld) -> tuple[type[Being], ...]:
    """Find, add, and birth all external Beings for ``world``.

    TODO: Add explicit Being rules once the runtime has a stable rules boundary.
    """
    being_types = find_beings()
    world._add(*being_types)
    for being_type in being_types:
        world._birth(being_type)
    return being_types
