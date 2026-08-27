from dataclasses import dataclass

from cor_being import Being
from cor_runtime._world import RuntimeWorld as World


@dataclass(frozen=True, slots=True)
class Started:
    being: str


class Dependency(Being):
    def birth(self, world: World, life) -> None:
        world.news.announce(Started(self.name))


class App(Being):
    needs = (Dependency,)

    def birth(self, world: World, life) -> None:
        world.news.announce(Started(self.name))


def test_dependencies_start_before_plugin() -> None:
    seen: list[str] = []

    with World() as world:
        world.news.listen(Started, lambda event: seen.append(event.being))
        world._add(Dependency, App)
        world._birth(App)

    assert seen == ["Dependency", "App"]


def test_fiber_cleanup_runs_in_reverse_order() -> None:
    cleaned: list[str] = []

    class ResourceBeing(Being):
        def birth(self, world: World, life) -> None:
            life.on_death(lambda: cleaned.append("first"))
            life.on_death(lambda: cleaned.append("second"))

    with World() as world:
        world._add(ResourceBeing)
        world._birth(ResourceBeing)

    assert cleaned == ["second", "first"]
