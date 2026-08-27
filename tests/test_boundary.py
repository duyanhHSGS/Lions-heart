from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import cor_being
import cor_runtime
from cor_being import Being, Life, World
from cor_runtime._world import RuntimeWorld

ROOT = Path(__file__).resolve().parents[1]


def _runtime_imports_under(path: Path) -> tuple[str, ...]:
    imports: list[str] = []
    for source_path in sorted(path.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path.as_posix())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(
                    alias.name for alias in node.names if alias.name == "cor_runtime" or alias.name.startswith("cor_runtime.")
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and (node.module == "cor_runtime" or node.module.startswith("cor_runtime."))
            ):
                imports.append(node.module)
    return tuple(imports)


def test_cor_being_exports_only_being_author_names() -> None:
    assert cor_being.__all__ == ["Being", "Life", "World"]
    assert Being.__module__.startswith("cor_being")
    assert Life.__module__.startswith("cor_being")
    assert World.__module__.startswith("cor_being")


def test_cor_runtime_root_exposes_no_being_author_or_engine_objects() -> None:
    assert cor_runtime.__all__ == []
    for name in (
        "Being",
        "Life",
        "News",
        "Population",
        "World",
        "find_beings",
        "genesis",
    ):
        assert not hasattr(cor_runtime, name)


def test_old_runtime_module_doors_are_gone() -> None:
    for module_name in (
        "cor_runtime.being",
        "cor_runtime.life",
        "cor_runtime.news",
        "cor_runtime.population",
        "cor_runtime.world",
        "cor_runtime.genesis",
        "cor_runtime.erase",
    ):
        assert importlib.util.find_spec(module_name) is None


def test_cor_being_does_not_import_cor_runtime() -> None:
    assert _runtime_imports_under(ROOT / "cor_being") == ()


def test_builtin_beings_do_not_import_cor_runtime() -> None:
    assert _runtime_imports_under(ROOT / "cor_beings") == ()


def test_public_world_contract_has_no_host_controls() -> None:
    for name in ("_add", "_birth", "_kill", "_end", "_population", "parent", "branches"):
        assert not hasattr(World, name)


def test_public_world_news_is_a_read_only_protocol_view() -> None:
    news = World.__dict__["news"]

    assert isinstance(news, property)
    assert news.fset is None


def test_runtime_world_matches_the_being_world_contract() -> None:
    world = RuntimeWorld()
    try:
        assert isinstance(world, World)
        assert world.name == "main"
        assert world.alive == ()
        assert callable(world.need)
        assert callable(world.news.listen)
        assert callable(world.news.announce)
        assert callable(world.branch)
    finally:
        world._end()


# TODO: Extend this boundary suite whenever the Being-facing surface grows.
