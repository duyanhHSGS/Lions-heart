"""In-memory append-only session history for Lions-heart."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from types import MappingProxyType

from cor_being import Being, Life, World


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """One immutable event in the current in-memory session."""

    kind: str
    data: Mapping[str, object]


class SessionBeing(Being):
    """Own the single append-only event history for the tiny harness."""

    name = "session"

    def __init__(self) -> None:
        self._events: list[SessionEvent] = []
        self._events_lock = Lock()

    def birth(self, world: World, life: Life) -> None:
        # TODO: Add durable replay/fork storage and incremental notifications
        # without creating a second source of truth.
        return None

    @property
    def events(self) -> tuple[SessionEvent, ...]:
        """Return a stable snapshot of all events in append order."""
        with self._events_lock:
            return tuple(self._events)

    def append(self, kind: str, **data: object) -> SessionEvent:
        """Append one immutable event and return it."""
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("session event kind must be a non-empty string")
        event = SessionEvent(kind=kind, data=MappingProxyType(dict(data)))
        with self._events_lock:
            self._events.append(event)
        return event
