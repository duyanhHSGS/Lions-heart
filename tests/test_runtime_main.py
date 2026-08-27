"""Tests for runtime-owned bootstrap, waiting, and shutdown."""

from __future__ import annotations

import importlib
from typing import ClassVar

import pytest

from cor_being import Being, Life
from cor_runtime._world import RuntimeWorld as World

runtime_main = importlib.import_module("cor_runtime.__main__")


class ManagedBeing(Being):
    name = "managed"
    cleaned: ClassVar[list[str]] = []

    def birth(self, world: World, life: Life) -> None:
        life.on_death(lambda: self.cleaned.append("cleaned"))


class ReportedBeing(Being):
    name = "reported"

    def birth(self, world: World, life: Life) -> None:
        pass


class InterruptingEvent:
    """Small Event fake proving the host yields for Windows signal checks."""

    timeouts: ClassVar[list[float]] = []

    def wait(self, timeout: float) -> bool:
        self.timeouts.append(timeout)
        raise KeyboardInterrupt


def test_main_rejects_removed_with_argument(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        runtime_main.main(("--with", "dashboard"))

    assert caught.value.code == 2
    assert "unrecognized arguments: --with dashboard" in capsys.readouterr().err


def test_main_bootstraps_every_discovered_being_and_reports_it(
    monkeypatch, capsys
) -> None:
    bootstrapped: list[World] = []

    def fake_genesis(world: World):
        bootstrapped.append(world)
        return (ReportedBeing,)

    monkeypatch.setattr(runtime_main, "genesis", fake_genesis)
    monkeypatch.setattr(runtime_main, "wait_for_interrupt", lambda: None)

    runtime_main.main(())

    assert len(bootstrapped) == 1
    assert "  - reported\n" in capsys.readouterr().out


def test_main_owns_world_wait_and_cleanup(monkeypatch) -> None:
    ManagedBeing.cleaned = []
    living_during_wait: list[tuple[type[Being], ...]] = []
    owned_worlds: list[World] = []

    def fake_genesis(world: World):
        owned_worlds.append(world)
        world._add(ManagedBeing)
        world._birth(ManagedBeing)
        return (ManagedBeing,)

    def observe_wait() -> None:
        living_during_wait.append(owned_worlds[0].alive)

    monkeypatch.setattr(runtime_main, "genesis", fake_genesis)
    monkeypatch.setattr(runtime_main, "wait_for_interrupt", observe_wait)

    runtime_main.main(())

    assert living_during_wait == [(ManagedBeing,)]
    assert ManagedBeing.cleaned == ["cleaned"]
    assert owned_worlds[0].alive == ()


def test_wait_for_interrupt_uses_short_windows_friendly_waits(monkeypatch) -> None:
    InterruptingEvent.timeouts = []
    monkeypatch.setattr(runtime_main, "Event", InterruptingEvent)

    runtime_main.wait_for_interrupt()

    assert InterruptingEvent.timeouts == [0.25]


# TODO: Add runtime argument tests when the host gains real configuration.
