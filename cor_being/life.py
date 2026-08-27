"""Lifetime ownership for one living Being."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field

DeathTask = Callable[[], None]


@dataclass(slots=True)
class Life:
    """Own what one living Being must erase when it dies.

    Death tasks run in reverse registration order, like a small stack.
    That lets dependent resources unwind naturally.
    """

    being_name: str
    _death_tasks: list[DeathTask] = field(default_factory=list)
    dead: bool = False

    def on_death(self, death_task: DeathTask) -> None:
        """Register a zero-argument task to run when this Life dies."""
        if self.dead:
            raise RuntimeError("cannot add a death task to a dead Life")
        self._death_tasks.append(death_task)

    def die(self) -> None:
        """Run all death tasks exactly once."""
        if self.dead:
            return
        self.dead = True
        callbacks = ExitStack()
        for death_task in self._death_tasks:
            callbacks.callback(death_task)
        self._death_tasks.clear()
        try:
            callbacks.close()
        except BaseException as error:  # noqa: BLE001 - cleanup must catch process-level exits too.
            errors: list[BaseException] = []
            current: BaseException | None = error
            while current is not None:
                errors.append(current)
                current = current.__context__
            # TODO: Preserve Python 3.10 compatibility while retaining combined death-task details.
            message = f"death task failed for {self.being_name}: " + "; ".join(
                f"{type(item).__name__}: {item}" for item in errors
            )
            raise RuntimeError(message) from errors[0]
