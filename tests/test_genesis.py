import importlib
from types import ModuleType, SimpleNamespace

import pytest

from cor_being import Being
from cor_runtime._genesis import find_beings, genesis
from cor_runtime._world import RuntimeWorld as World

genesis_module = importlib.import_module("cor_runtime._genesis")


class DemoBeing(Being):
    def birth(self, world: World, life) -> None:
        pass


def test_discover_plugins_loads_plugins_from_conventional_module(monkeypatch) -> None:
    module = ModuleType("fake_hands")
    module.__dict__["get_beings"] = lambda: (DemoBeing,)

    def import_fake(name: str):
        if name == "fake_hands":
            return module
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(genesis_module.importlib, "import_module", import_fake)

    assert find_beings(module_names=("fake_hands",), entry_points=()) == (
        DemoBeing,
    )


def test_discover_plugins_ignores_missing_conventional_module() -> None:
    assert (
        find_beings(
            module_names=("definitely_missing_corleonis_plugin",), entry_points=()
        )
        == ()
    )


def test_discover_plugins_reraises_missing_dependency_inside_plugin_module(
    monkeypatch,
) -> None:
    def import_broken(name: str):
        raise ModuleNotFoundError("missing dependency", name="dependency_inside_plugin")

    monkeypatch.setattr(genesis_module.importlib, "import_module", import_broken)

    with pytest.raises(ModuleNotFoundError, match="missing dependency"):
        find_beings(module_names=("fake_hands",), entry_points=())


def test_discover_plugins_accepts_entry_point_plugin_class() -> None:
    entry_point = SimpleNamespace(name="demo", load=lambda: DemoBeing)

    assert find_beings(module_names=(), entry_points=(entry_point,)) == (
        DemoBeing,
    )


def test_discover_plugins_accepts_entry_point_module() -> None:
    module = ModuleType("fake_hands")
    module.__dict__["get_beings"] = lambda: (DemoBeing,)
    entry_point = SimpleNamespace(name="demo", load=lambda: module)

    assert find_beings(module_names=(), entry_points=(entry_point,)) == (
        DemoBeing,
    )


def test_discover_plugins_deduplicates_plugin_types() -> None:
    module = ModuleType("fake_hands")
    module.__dict__["get_beings"] = lambda: (DemoBeing,)
    entry_point = SimpleNamespace(name="demo", load=lambda: DemoBeing)

    assert find_beings(
        module_names=("fake_hands",), entry_points=(entry_point,)
    ) == (DemoBeing,)


def test_discover_plugins_rejects_invalid_module_plugin(monkeypatch) -> None:
    module = ModuleType("fake_hands")
    module.__dict__["get_beings"] = lambda: (object,)
    monkeypatch.setattr(genesis_module.importlib, "import_module", lambda _: module)

    with pytest.raises(TypeError, match="invalid Being"):
        find_beings(module_names=("fake_hands",), entry_points=())


def test_discover_plugins_rejects_invalid_entry_point() -> None:
    entry_point = SimpleNamespace(name="bad", load=lambda: object())

    with pytest.raises(TypeError, match="invalid Being entry point"):
        find_beings(module_names=(), entry_points=(entry_point,))


def test_bootstrap_registers_and_starts_discovered_plugins(monkeypatch) -> None:
    monkeypatch.setattr(
        genesis_module, "find_beings", lambda **_: (DemoBeing,)
    )
    world = World()

    started = genesis(world)

    assert started == (DemoBeing,)
    assert world.alive == (DemoBeing,)
    world._end()


def test_bootstrap_with_no_plugins_is_a_noop() -> None:
    world = World()

    assert genesis_module.find_beings(module_names=(), entry_points=()) == ()
    assert world.alive == ()
    world._end()


def test_bootstrap_start_failure_is_cleaned_by_context_close(monkeypatch) -> None:
    cleaned: list[str] = []

    class BrokenBeing(Being):
        def birth(self, world: World, life) -> None:
            life.on_death(lambda: cleaned.append("cleaned"))
            raise RuntimeError("boom")

    monkeypatch.setattr(
        genesis_module, "find_beings", lambda **_: (BrokenBeing,)
    )
    world = World()

    with pytest.raises(RuntimeError, match="boom"):
        genesis(world)

    assert world.alive == ()
    world._end()
    assert cleaned == ["cleaned"]


# TODO: Add a type-checker fixture if the project adopts automated Pylance/Pyright CI.
