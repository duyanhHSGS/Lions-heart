"""Command-line entrypoint for the Cor Leonis runtime."""

from __future__ import annotations

from argparse import ArgumentParser
from collections.abc import Sequence
from threading import Event

from . import __version__
from ._genesis import genesis
from ._world import RuntimeWorld


def create_parser() -> ArgumentParser:
    """Create the runtime parser; every built-in Being always starts."""
    return ArgumentParser(prog="python -m cor_runtime")


def wait_for_interrupt() -> None:
    """Keep the runtime alive while yielding to Windows Ctrl+C checks."""
    stop = Event()
    try:
        while not stop.wait(0.25):
            pass
    except KeyboardInterrupt:
        return


def main(argv: Sequence[str] | None = None) -> None:
    """Own the World, birth every discovered Being, wait, and shut down."""
    parser = create_parser()
    parser.parse_args(argv)

    with RuntimeWorld() as world:
        beings = genesis(world)
        print(f"Cor Leonis {__version__} ready ({world.name})")
        if beings:
            print("Beings:")
            for being_type in beings:
                print(f"  - {being_type.name}")
        print("Press Ctrl+C to stop.")
        wait_for_interrupt()
        print("\nCor Leonis stopped.")


# TODO: Keep CLI configuration small while every discovered Being boots by default.


if __name__ == "__main__":
    main()
