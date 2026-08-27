"""Small synchronous News channel."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any, TypeVar

EventT = TypeVar("EventT")
Listener = Callable[[EventT], None]


class News:
    """Announce items to listeners registered for an exact item type."""

    __slots__ = ("_listeners",)

    def __init__(self) -> None:
        self._listeners: defaultdict[type, list[Listener[Any]]] = defaultdict(list)

    def listen(
        self, item_type: type[EventT], listener: Listener[EventT]
    ) -> Callable[[], None]:
        """Listen for one exact item type and return a function that stops listening."""
        listeners = self._listeners[item_type]
        listeners.append(listener)

        def stop_listening() -> None:
            try:
                listeners.remove(listener)
            except ValueError:
                pass

        return stop_listening

    def announce(self, item: object) -> None:
        """Synchronously call listeners for the item's exact type."""
        for listener in tuple(self._listeners.get(type(item), ())):
            listener(item)


# TODO: Keep News method signatures aligned with the Being-facing _News protocol;
# use a TypeVar only where listen correlates an item type with its listener.
# TODO: Keep News synchronous until the World needs an explicit async story.
