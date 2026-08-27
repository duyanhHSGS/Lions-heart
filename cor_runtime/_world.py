"""Hierarchical World ownership."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeVar

from typing_extensions import Self

from ._erase import ErrorCatcher
from ._news import News
from ._population import Population

if TYPE_CHECKING:
    from cor_being import Being


BeingT = TypeVar("BeingT", bound="Being")


@dataclass(slots=True)
class RuntimeWorld:
    """A home for Beings with a small feature-facing public surface.

    Beings may inspect ``alive``, resolve an active dependency with ``need``,
    communicate through ``news``, and create owned branch Worlds. Population
    mutation and lifecycle control are private runtime-host responsibilities.
    """

    name: str = "main"
    parent: RuntimeWorld | None = None
    _population: Population = field(default_factory=Population)
    news: News = field(default_factory=News)
    branches: list[RuntimeWorld] = field(default_factory=list)
    _ended: bool = False

    def __post_init__(self) -> None:
        if self.parent is not None:
            self.parent.branches.append(self)

    @property
    def alive(self) -> tuple[type[Being], ...]:
        """Return an immutable snapshot of currently living Being types."""
        return self._population.alive

    def need(self, being_type: type[BeingT]) -> BeingT:
        """Return one currently alive Being dependency."""
        return self._population.need(being_type)

    def _add(self, *being_types: type[Being]) -> None:
        """Register Being types; reserved for the runtime composition host."""
        for being_type in being_types:
            self._population.add(being_type)

    def _birth(self, being_type: type[Being]) -> None:
        """Birth a registered Being; reserved for the runtime host."""
        self._population.birth(being_type, self)

    def _kill(self, being_type: type[Being]) -> None:
        """Kill a living Being; reserved for the runtime host."""
        self._population.kill(being_type, self)

    def branch(self, name: str) -> RuntimeWorld:
        """Create a smaller World owned by this World."""
        if self._ended:
            raise RuntimeError("cannot branch an ended World")
        # TODO: Preserve this shutdown barrier if World branching becomes asynchronous.
        return RuntimeWorld(name=name, parent=self)

    def _end(self) -> None:
        """End branch Worlds first, then kill local Beings for the host."""
        if self._ended:
            return
        self._ended = True
        callbacks = ExitStack()
        callbacks.callback(self._population.kill_all, self)
        for branch in self.branches:
            callbacks.callback(branch._end)
        self.branches.clear()
        with ErrorCatcher(capture_base_exceptions=True) as capture:
            callbacks.close()
        if capture.exception is not None:
            errors: list[BaseException] = []
            current: BaseException | None = capture.exception
            while current is not None:
                errors.append(current)
                current = current.__context__
            # TODO: Keep World erasure error reporting compatible with Python 3.10+.
            message = f"world erasure failed: {self.name}: " + "; ".join(
                f"{type(error).__name__}: {error}" for error in errors
            )
            raise RuntimeError(message) from errors[0]

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._end()


# TODO: Add reactive service provision before expanding World.need beyond active Beings.
