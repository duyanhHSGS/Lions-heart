"""Tests for the built-in Cor Leonis hands."""

from cor_being import Being
from cor_beings import get_beings
from cor_beings.dashboard import Dashboard
from cor_beings.hello_moon import HelloMoon
from cor_beings.hello_sun import HelloSun
from cor_beings.hello_world import HelloWorld
from cor_beings.watcher import Watcher
from cor_runtime._genesis import genesis
from cor_runtime._world import RuntimeWorld as World


def test_get_plugins_contains_all_builtin_hands() -> None:
    assert get_beings() == (HelloWorld, HelloMoon, HelloSun, Watcher, Dashboard)
    assert all(issubclass(being_type, Being) for being_type in get_beings())


def test_hello_world_prints_exact_greeting(capsys) -> None:
    with World() as world:
        world._add(HelloWorld)
        world._birth(HelloWorld)

    assert capsys.readouterr().out == "hello world\n"


def test_hello_moon_prints_exact_greeting(capsys) -> None:
    with World() as world:
        world._add(HelloMoon)
        world._birth(HelloMoon)

    assert capsys.readouterr().out == "hello moon\n"


def test_hello_sun_declares_both_greeting_dependencies() -> None:
    assert HelloSun.needs == (HelloMoon, HelloWorld)


def test_hello_sun_starts_dependencies_before_screaming(capsys) -> None:
    with World() as world:
        world._add(HelloWorld, HelloMoon, HelloSun)
        world._birth(HelloSun)

    assert capsys.readouterr().out == "hello moon\nhello world\nSON!\n"


def test_runtime_bootstrap_discovers_and_starts_every_being(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(Dashboard, "port", 0)

    with World() as world:
        started = genesis(world)
        world._birth(HelloSun)

    output = capsys.readouterr().out.splitlines()
    assert started == (HelloWorld, HelloMoon, HelloSun, Watcher, Dashboard)
    assert output[:3] == ["hello world", "hello moon", "SON!"]
    assert output[3].startswith("Dashboard: http://127.0.0.1:")


# TODO: Add a failure-path test when dependency startup error injection is exposed publicly.
