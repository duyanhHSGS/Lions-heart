"""The tiny World shape visible to a Being."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar, runtime_checkable

from .being import Being

BeingT = TypeVar("BeingT", bound=Being)
EventT = TypeVar("EventT")
Listener = Callable[[EventT], None]


class _News(Protocol):
    """Only the News operations a Being can use through its World."""

    def listen(
        self, item_type: type[EventT], listener: Listener[EventT]
    ) -> Callable[[], None]: ...

    def announce(self, item: object) -> None: ...


@runtime_checkable
class World(Protocol):
    """What a Being is allowed to know about its World."""

    name: str

    @property
    def news(self) -> _News: ...

    @property
    def alive(self) -> tuple[type[Being], ...]: ...

    def need(self, being_type: type[BeingT]) -> BeingT: ...

    def branch(self, name: str) -> World: ...


# TODO: Keep read-only protocol views for services that runtime implementations
# expose with more specific concrete types.
# TODO: Keep this Being-facing World contract tiny; host controls belong in cor_runtime.
