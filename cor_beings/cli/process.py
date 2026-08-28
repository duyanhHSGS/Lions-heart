"""Interactive terminal loop owned by the Lions-heart product layer."""

from __future__ import annotations

import sys
from collections.abc import Callable
from threading import Event, Thread
from typing import Protocol

from cor_being import Life


class CliRunner(Protocol):
    """Small shape required by the interactive terminal adapter."""

    def run_once(self, message: str, *, write: Callable[[str], object] = print) -> str: ...


def stdin_is_interactive() -> bool:
    """Return whether the current process has an interactive stdin terminal."""
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def run_console(
    cli: CliRunner,
    *,
    read: Callable[[str], str] = input,
    write: Callable[[str], object] = print,
    stop: Event | None = None,
) -> None:
    """Run repeated ``You >`` turns until the process stops or stdin closes."""
    stop_event = stop if stop is not None else Event()

    while not stop_event.is_set():
        try:
            message = read("You > ")
        except EOFError:
            return
        if stop_event.is_set():
            return
        cli.run_once(message, write=write)


def start_console_thread(
    cli: CliRunner,
    life: Life,
    *,
    read: Callable[[str], str] = input,
    write: Callable[[str], object] = print,
) -> Thread:
    """Start the product console without taking over the host's main wait loop."""
    stop = Event()
    life.on_death(stop.set)

    thread = Thread(
        target=run_console,
        kwargs={"cli": cli, "read": read, "write": write, "stop": stop},
        name="lions-heart-cli",
        daemon=True,
    )
    thread.start()

    # TODO: Replace blocking input with a cancellable terminal reader if the CLI
    # ever needs graceful thread joining before whole-process shutdown.
    return thread


__all__ = ["run_console", "start_console_thread", "stdin_is_interactive"]
