"""Tests for the built-in plugin manager hand."""

from __future__ import annotations

import pytest

from cor_being import Being, Life
from cor_beings.watcher import BeingInfo, Watcher
from cor_runtime._world import RuntimeWorld as World


class SoloBeing(Being):
    name = "solo"

    def birth(self, world: World, life: Life) -> None:
        pass


class FirstBeing(Being):
    name = "first"

    def birth(self, world: World, life: Life) -> None:
        pass


class SecondBeing(Being):
    name = "second"
    needs = (FirstBeing,)

    def birth(self, world: World, life: Life) -> None:
        pass


class NamelessBeing(Being):
    name = ""

    def birth(self, world: World, life: Life) -> None:
        pass


class RenamedBeing(Being):
    name = "before-birth"

    def birth(self, world: World, life: Life) -> None:
        self.name = "after-birth"


def manager_with(world: World, *being_types: type[Being]) -> Watcher:
    all_types: list[type[Being]] = [Watcher]
    for being_type in being_types:
        if being_type not in all_types:
            all_types.append(being_type)
        for dependency in being_type.needs:
            if dependency not in all_types:
                all_types.append(dependency)
    world._add(*all_types)
    world._birth(Watcher)
    for being_type in being_types:
        world._birth(being_type)
    watcher = world.need(Watcher)
    assert isinstance(watcher, Watcher)
    return watcher


def test_plugin_manager_is_a_valid_plugin() -> None:
    assert issubclass(Watcher, Being)
    assert Watcher.name == "watcher"


def test_empty_plugin_set_lists_no_plugins() -> None:
    with World() as world:
        world._add(Watcher)
        world._birth(Watcher)
        watcher = world.need(Watcher)

        assert isinstance(watcher, Watcher)
        assert watcher.list_beings(world) == ("watcher",)


def test_list_plugins_returns_deterministic_running_order() -> None:
    with World() as world:
        watcher = manager_with(world, FirstBeing, SecondBeing)

        assert watcher.list_beings(world) == ("watcher", "first", "second")


def test_list_plugins_does_not_duplicate_a_running_plugin() -> None:
    with World() as world:
        watcher = manager_with(world, SoloBeing)
        world._birth(SoloBeing)

        assert watcher.list_beings(world) == ("watcher", "solo")


def test_list_plugins_includes_the_manager_itself() -> None:
    with World() as world:
        watcher = manager_with(world)

        assert watcher.list_beings(world) == ("watcher",)


def test_snapshot_describes_all_alive_beings_in_one_stable_view() -> None:
    with World() as world:
        watcher = manager_with(world, SecondBeing)

        assert watcher.snapshot(world) == (
            BeingInfo(
                name="watcher",
                being_type=Watcher,
                needs=(),
                module="cor_beings.watcher",
            ),
            BeingInfo(
                name="first",
                being_type=FirstBeing,
                needs=(),
                module=__name__,
            ),
            BeingInfo(
                name="second",
                being_type=SecondBeing,
                needs=("first",),
                module=__name__,
            ),
        )


def test_info_returns_useful_plugin_metadata() -> None:
    with World() as world:
        watcher = manager_with(world, SecondBeing)

        info = watcher.info(world, "second")

        assert info == BeingInfo(
            name="second",
            being_type=SecondBeing,
            needs=("first",),
            module=__name__,
        )


def test_info_returns_minimal_metadata_for_plugin_without_dependencies() -> None:
    with World() as world:
        watcher = manager_with(world, SoloBeing)

        info = watcher.info(world, "solo")

        assert info.name == "solo"
        assert info.being_type is SoloBeing
        assert info.needs == ()
        assert info.module == __name__


def test_info_handles_empty_plugin_name() -> None:
    with World() as world:
        watcher = manager_with(world, NamelessBeing)

        info = watcher.info(world, "")

        assert info.name == ""
        assert info.being_type is NamelessBeing


def test_watcher_reads_metadata_from_the_alive_instance() -> None:
    with World() as world:
        watcher = manager_with(world, RenamedBeing)

        assert watcher.list_beings(world) == ("watcher", "after-birth")
        assert watcher.info(world, "after-birth").being_type is RenamedBeing


def test_info_rejects_unknown_plugin() -> None:
    with World() as world:
        watcher = manager_with(world, SoloBeing)

        with pytest.raises(LookupError, match="being is not alive: missing"):
            watcher.info(world, "missing")


def test_info_does_not_report_registered_but_stopped_plugin() -> None:
    with World() as world:
        watcher = manager_with(world, SoloBeing)
        world._kill(SoloBeing)

        with pytest.raises(LookupError, match="being is not alive: solo"):
            watcher.info(world, "solo")


def test_manager_reads_runtime_state_without_discovering_plugins() -> None:
    with World() as world:
        world._add(Watcher, SoloBeing)
        world._birth(Watcher)
        world._birth(SoloBeing)
        watcher = world.need(Watcher)

        assert watcher.list_beings(world) == ("watcher", "solo")
        assert watcher.info(world, "solo").being_type is SoloBeing
