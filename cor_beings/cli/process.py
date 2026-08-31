"""Interactive terminal loop owned by the Lions-heart product layer."""

from __future__ import annotations

import select
import sys
from collections.abc import Callable
from threading import Event, Thread, current_thread
from typing import Protocol

from cor_being import Life


class CliRunner(Protocol):
    """Small shape required by the interactive terminal adapter."""

    def run_once(
        self,
        message: str,
        *,
        write: Callable[[str], object] = print,
        cancel: Event | None = None,
    ) -> str: ...


ConsoleRead = Callable[[str, Event], str]
_TERMINAL_POLL_SECONDS = 0.05


class _ConsoleStopped(Exception):
    """End a pending console read because its owning Life died."""


def stdin_is_interactive() -> bool:
    """Return whether the current process has an interactive stdin terminal."""
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def _cancel_terminal_read() -> None:
    """Move past the abandoned prompt and end the console loop."""
    sys.stdout.write("\n")
    sys.stdout.flush()
    raise _ConsoleStopped


def _read_selectable_terminal(prompt: str, stop: Event) -> str:
    """Read one POSIX terminal line while periodically checking ``stop``."""
    sys.stdout.write(prompt)
    sys.stdout.flush()

    while not stop.is_set():
        try:
            readable, _, _ = select.select(
                (sys.stdin,),
                (),
                (),
                _TERMINAL_POLL_SECONDS,
            )
        except (OSError, ValueError) as error:
            if stop.is_set():
                _cancel_terminal_read()
            raise RuntimeError("cannot poll interactive stdin") from error

        if stop.is_set():
            _cancel_terminal_read()
        if not readable:
            continue

        line = sys.stdin.readline()
        if line == "":
            raise EOFError
        return line.removesuffix("\n").removesuffix("\r")

    _cancel_terminal_read()


def _read_windows_terminal(prompt: str, stop: Event) -> str:
    """Read one Windows console line without becoming stuck in ``input``."""
    import msvcrt

    characters: list[str] = []
    sys.stdout.write(prompt)
    sys.stdout.flush()

    while not stop.is_set():
        if not msvcrt.kbhit():
            stop.wait(_TERMINAL_POLL_SECONDS)
            continue

        character = msvcrt.getwch()
        if stop.is_set():
            _cancel_terminal_read()
        if character in ("\x00", "\xe0"):
            # Consume the second half of arrows/function keys. They are not
            # message text, and leaving them queued would create ghost input.
            while not stop.is_set() and not msvcrt.kbhit():
                stop.wait(_TERMINAL_POLL_SECONDS)
            if stop.is_set():
                _cancel_terminal_read()
            msvcrt.getwch()
            continue
        if character in ("\r", "\n"):
            sys.stdout.write("\n")
            sys.stdout.flush()
            return "".join(characters)
        if character == "\x03":
            raise KeyboardInterrupt
        if character == "\x1a":
            raise EOFError
        if character == "\b":
            if characters:
                characters.pop()
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue
        if character.isprintable() or character == "\t":
            characters.append(character)
            sys.stdout.write(character)
            sys.stdout.flush()

    _cancel_terminal_read()


def _read_terminal(prompt: str, stop: Event) -> str:
    """Read one terminal line with cancellation on supported host consoles."""
    if sys.platform == "win32":
        return _read_windows_terminal(prompt, stop)
    return _read_selectable_terminal(prompt, stop)


def _run_console(
    cli: CliRunner,
    *,
    read: ConsoleRead,
    write: Callable[[str], object],
    stop: Event,
) -> None:
    """Shared loop for blocking test readers and the cancellable real terminal."""
    while not stop.is_set():
        try:
            message = read("You > ", stop)
        except (EOFError, _ConsoleStopped):
            return
        if stop.is_set():
            return
        cli.run_once(message, write=write, cancel=stop)


def run_console(
    cli: CliRunner,
    *,
    read: Callable[[str], str] = input,
    write: Callable[[str], object] = print,
    stop: Event | None = None,
) -> None:
    """Run repeated ``You >`` turns until the process stops or stdin closes."""
    stop_event = stop if stop is not None else Event()

    def blocking_read(prompt: str, _stop: Event) -> str:
        return read(prompt)

    _run_console(cli, read=blocking_read, write=write, stop=stop_event)


def start_console_thread(
    cli: CliRunner,
    life: Life,
    *,
    read: ConsoleRead | None = None,
    write: Callable[[str], object] = print,
) -> Thread:
    """Start the product console without taking over the host's main wait loop."""
    stop = Event()
    console_read = _read_terminal if read is None else read

    thread = Thread(
        target=_run_console,
        kwargs={"cli": cli, "read": console_read, "write": write, "stop": stop},
        name="lions-heart-cli",
        daemon=True,
    )

    def stop_console() -> None:
        stop.set()
        if thread.ident is not None and thread is not current_thread():
            thread.join(timeout=0.5)

    life.on_death(stop_console)
    thread.start()

    # TODO: Add optional history-aware line editing without sacrificing prompt cancellation.
    return thread


__all__ = ["run_console", "start_console_thread", "stdin_is_interactive"]
