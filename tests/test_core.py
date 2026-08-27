from dataclasses import dataclass

import pytest

from cor_being import Being, Life
from cor_runtime._erase import ErrorCatcher, UndoOnError
from cor_runtime._news import News
from cor_runtime._population import Population
from cor_runtime._world import RuntimeWorld as World


@dataclass(frozen=True, slots=True)
class Ping:
    value: int


@dataclass(frozen=True, slots=True)
class ChildPing(Ping):
    pass


def test_event_bus_dispatches_only_exact_event_type() -> None:
    bus = News()
    seen: list[object] = []
    bus.listen(Ping, seen.append)

    bus.announce(Ping(1))
    bus.announce(ChildPing(2))

    assert seen == [Ping(1)]


def test_event_bus_supports_its_typed_keyword_arguments() -> None:
    bus = News()
    seen: list[Ping] = []

    bus.listen(item_type=Ping, listener=seen.append)
    bus.announce(item=Ping(3))

    assert seen == [Ping(3)]


def test_event_bus_preserves_subscription_order() -> None:
    bus = News()
    seen: list[str] = []
    bus.listen(Ping, lambda _: seen.append("first"))
    bus.listen(Ping, lambda _: seen.append("second"))

    bus.announce(Ping(1))

    assert seen == ["first", "second"]


def test_event_bus_unsubscribe_removes_handler() -> None:
    bus = News()
    seen: list[int] = []
    unsubscribe = bus.listen(Ping, lambda event: seen.append(event.value))

    unsubscribe()
    bus.announce(Ping(7))

    assert seen == []


def test_event_bus_unsubscribe_is_idempotent() -> None:
    bus = News()
    unsubscribe = bus.listen(Ping, lambda _: None)

    unsubscribe()
    unsubscribe()


def test_event_bus_emit_uses_snapshot_when_handler_unsubscribes_itself() -> None:
    bus = News()
    seen: list[str] = []
    unsubscribe = None

    def handler(_: Ping) -> None:
        seen.append("self")
        assert unsubscribe is not None
        unsubscribe()

    unsubscribe = bus.listen(Ping, handler)
    bus.listen(Ping, lambda _: seen.append("other"))

    bus.announce(Ping(1))
    bus.announce(Ping(2))

    assert seen == ["self", "other", "other"]


def test_event_bus_handler_exception_propagates() -> None:
    bus = News()

    def explode(_: Ping) -> None:
        raise ValueError("boom")

    bus.listen(Ping, explode)

    with pytest.raises(ValueError, match="boom"):
        bus.announce(Ping(1))


def test_exception_capture_suppresses_normal_exception() -> None:
    capture = ErrorCatcher()

    with capture:
        raise ValueError("boom")

    assert isinstance(capture.exception, ValueError)
    assert str(capture.exception) == "boom"


def test_exception_capture_can_capture_base_exception() -> None:
    capture = ErrorCatcher(capture_base_exceptions=True)

    with capture:
        raise SystemExit(7)

    assert isinstance(capture.exception, SystemExit)
    assert capture.exception.code == 7


def test_exception_capture_allows_base_exception_by_default() -> None:
    capture = ErrorCatcher()

    with pytest.raises(SystemExit), capture:
        raise SystemExit(7)

    assert capture.exception is None


def test_rollback_on_error_runs_for_termination_exception() -> None:
    rolled_back: list[str] = []

    with pytest.raises(SystemExit), UndoOnError(
        lambda: rolled_back.append("rolled back")
    ):
        raise SystemExit(7)

    assert rolled_back == ["rolled back"]


def test_fiber_cleanup_runs_in_reverse_registration_order() -> None:
    cleaned: list[str] = []
    life = Life("demo")

    life.on_death(lambda: cleaned.append("first"))
    life.on_death(lambda: cleaned.append("second"))
    life.on_death(lambda: cleaned.append("third"))

    life.die()

    assert cleaned == ["third", "second", "first"]
    assert life.dead is True


def test_fiber_stop_is_idempotent() -> None:
    calls: list[str] = []
    life = Life("demo")
    life.on_death(lambda: calls.append("cleaned"))

    life.die()
    life.die()

    assert calls == ["cleaned"]


def test_fiber_rejects_new_cleanup_after_stop() -> None:
    life = Life("demo")
    life.die()

    with pytest.raises(RuntimeError, match="dead Life"):
        life.on_death(lambda: None)


def test_fiber_runs_remaining_cleanups_after_one_failure() -> None:
    calls: list[str] = []
    life = Life("demo")

    def bad_cleanup() -> None:
        calls.append("bad")
        raise ValueError("cleanup failed")

    life.on_death(lambda: calls.append("first"))
    life.on_death(bad_cleanup)
    life.on_death(lambda: calls.append("last"))

    with pytest.raises(RuntimeError) as caught:
        life.die()

    assert calls == ["last", "bad", "first"]
    assert isinstance(caught.value.__cause__, ValueError)
    assert str(caught.value.__cause__) == "cleanup failed"
    assert "ValueError: cleanup failed" in str(caught.value)


def test_fiber_aggregates_system_exit_cleanup_failure() -> None:
    calls: list[str] = []
    life = Life("demo")

    def exit_cleanup() -> None:
        calls.append("exit")
        raise SystemExit(7)

    life.on_death(lambda: calls.append("first"))
    life.on_death(exit_cleanup)
    life.on_death(lambda: calls.append("last"))

    with pytest.raises(RuntimeError) as caught:
        life.die()

    assert calls == ["last", "exit", "first"]
    assert isinstance(caught.value.__cause__, SystemExit)
    assert caught.value.__cause__.code == 7


def test_registry_rejects_duplicate_plugin_registration() -> None:
    class Demo(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

    population = Population()
    population.add(Demo)

    with pytest.raises(ValueError, match="already added"):
        population.add(Demo)


def test_registry_get_rejects_unregistered_plugin() -> None:
    class Demo(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

    with pytest.raises(LookupError, match="not in the population"):
        Population().get(Demo)


def test_registry_start_is_idempotent() -> None:
    starts: list[str] = []

    class Demo(Being):
        def birth(self, world: World, life: Life) -> None:
            starts.append("start")

    population = Population()
    population.add(Demo)
    world = World()

    first = population.birth(Demo, world)
    second = population.birth(Demo, world)

    assert first is second
    assert starts == ["start"]
    assert population.alive == (Demo,)
    population.kill_all(world)


def test_registry_starts_dependencies_before_dependents() -> None:
    order: list[str] = []

    class Dependency(Being):
        def birth(self, world: World, life: Life) -> None:
            order.append("dependency")

    class App(Being):
        needs = (Dependency,)

        def birth(self, world: World, life: Life) -> None:
            order.append("app")

    population = Population()
    population.add(Dependency)
    population.add(App)
    world = World()

    population.birth(App, world)

    assert order == ["dependency", "app"]
    assert population.alive == (Dependency, App)
    population.kill_all(world)


def test_registry_stop_calls_plugin_hook_before_fiber_cleanup() -> None:
    order: list[str] = []

    class Demo(Being):
        def birth(self, world: World, life: Life) -> None:
            life.on_death(lambda: order.append("fiber"))

        def die(self, world: World, life: Life) -> None:
            order.append("plugin")

    population = Population()
    population.add(Demo)
    world = World()
    population.birth(Demo, world)

    population.kill(Demo, world)

    assert order == ["plugin", "fiber"]
    assert population.alive == ()


def test_registry_stop_unknown_plugin_is_noop() -> None:
    class Demo(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

    population = Population()
    population.add(Demo)

    population.kill(Demo, World())

    assert population.alive == ()


def test_registry_start_failure_cleans_fiber_resources() -> None:
    cleaned: list[str] = []

    class Broken(Being):
        def birth(self, world: World, life: Life) -> None:
            life.on_death(lambda: cleaned.append("cleaned"))
            raise RuntimeError("birth failed")

    population = Population()
    population.add(Broken)

    with pytest.raises(RuntimeError, match="birth failed"):
        population.birth(Broken, World())

    assert cleaned == ["cleaned"]
    assert population.alive == ()


def test_registry_stop_all_uses_reverse_start_order() -> None:
    dead: list[str] = []

    class First(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("first")

    class Second(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("second")

    population = Population()
    population.add(First)
    population.add(Second)
    world = World()
    population.birth(First, world)
    population.birth(Second, world)

    population.kill_all(world)

    assert dead == ["second", "first"]
    assert population.alive == ()


def test_registry_rejects_dependency_cycle_and_recovers() -> None:
    class First(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

    class Second(Being):
        needs = (First,)

        def birth(self, world: World, life: Life) -> None:
            pass

    First.needs = (Second,)
    population = Population()
    population.add(First)
    population.add(Second)
    world = World()

    with pytest.raises(
        RuntimeError,
        match="need cycle: First -> Second -> First",
    ):
        population.birth(First, world)

    assert population.alive == ()

    First.needs = ()
    population.birth(First, world)
    assert population.alive == (First,)
    population.kill_all(world)


def test_registry_rejects_self_dependency_cycle() -> None:
    class SelfCycle(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

    SelfCycle.needs = (SelfCycle,)
    population = Population()
    population.add(SelfCycle)

    with pytest.raises(
        RuntimeError,
        match="need cycle: SelfCycle -> SelfCycle",
    ):
        population.birth(SelfCycle, World())

    assert population.alive == ()


def test_registry_allows_diamond_dependencies_without_false_cycle() -> None:
    started: list[str] = []

    class Shared(Being):
        def birth(self, world: World, life: Life) -> None:
            started.append("shared")

    class Left(Being):
        needs = (Shared,)

        def birth(self, world: World, life: Life) -> None:
            started.append("left")

    class Right(Being):
        needs = (Shared,)

        def birth(self, world: World, life: Life) -> None:
            started.append("right")

    class App(Being):
        needs = (Left, Right)

        def birth(self, world: World, life: Life) -> None:
            started.append("app")

    population = Population()
    for being_type in (Shared, Left, Right, App):
        population.add(being_type)
    world = World()

    population.birth(App, world)

    assert started == ["shared", "left", "right", "app"]
    assert population.alive == (Shared, Left, Right, App)
    population.kill_all(world)


def test_registry_shutdown_does_not_scan_unrelated_being_needs() -> None:
    membership_checks = 0
    deaths = 0

    class ProbedNeeds(tuple):
        def __contains__(self, item: object) -> bool:
            nonlocal membership_checks
            membership_checks += 1
            return super().__contains__(item)

    class Independent(Being):
        needs = ProbedNeeds()

        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            nonlocal deaths
            deaths += 1

    being_count = 5_000
    being_types = tuple(
        type(
            f"Independent{index}",
            (Independent,),
            {"name": f"independent-{index}"},
        )
        for index in range(being_count)
    )
    population = Population()
    world = World()
    for being_type in being_types:
        population.add(being_type)
        population.birth(being_type, world)

    population.kill_all(world)

    assert deaths == being_count
    assert membership_checks == 0
    assert population.alive == ()


def test_registry_handles_dependency_chain_deeper_than_python_recursion() -> None:
    dead: list[str] = []

    class ChainBeing(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append(self.name)

    depth = 2_000
    being_types: list[type[Being]] = []
    for index in range(depth):
        needs = () if not being_types else (being_types[-1],)
        being_types.append(
            type(
                f"Chain{index}",
                (ChainBeing,),
                {"name": f"chain-{index}", "needs": needs},
            )
        )

    population = Population()
    world = World()
    for being_type in being_types:
        population.add(being_type)

    population.birth(being_types[-1], world)
    assert population.alive == tuple(being_types)

    population.kill(being_types[0], world)

    assert dead == [f"chain-{index}" for index in reversed(range(depth))]
    assert population.alive == ()


def test_registry_detects_deep_cycle_without_python_recursion() -> None:
    class ChainBeing(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

    depth = 2_000
    being_types: list[type[Being]] = []
    for index in range(depth):
        needs = () if not being_types else (being_types[-1],)
        being_types.append(
            type(
                f"Cycle{index}",
                (ChainBeing,),
                {"needs": needs},
            )
        )
    being_types[0].needs = (being_types[-1],)

    population = Population()
    world = World()
    for being_type in being_types:
        population.add(being_type)

    with pytest.raises(RuntimeError, match="need cycle"):
        population.birth(being_types[-1], world)

    assert population.alive == ()
    being_types[0].needs = ()
    population.birth(being_types[-1], world)
    assert population.alive == tuple(being_types)
    population.kill_all(world)


def test_registry_reverse_index_preserves_diamond_death_order() -> None:
    dead: list[str] = []

    class Shared(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("shared")

    class Left(Being):
        needs = (Shared,)

        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("left")

    class Right(Being):
        needs = (Shared,)

        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("right")

    class App(Being):
        needs = (Left, Right)

        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("app")

    population = Population()
    for being_type in (Shared, Left, Right, App):
        population.add(being_type)
    world = World()
    population.birth(App, world)

    population.kill(Shared, world)

    assert dead == ["app", "right", "left", "shared"]
    assert population.alive == ()


def test_registry_rebuilds_reverse_dependency_edge_after_rebirth() -> None:
    dead: list[str] = []

    class Dependency(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("dependency")

    class App(Being):
        needs = (Dependency,)

        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("app")

    population = Population()
    population.add(Dependency)
    population.add(App)
    world = World()
    population.birth(App, world)

    population.kill(App, world)
    population.birth(App, world)
    population.kill(Dependency, world)

    assert dead == ["app", "app", "dependency"]
    assert population.alive == ()


def test_registry_failed_birth_does_not_leave_reverse_dependency_edge() -> None:
    class Dependency(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

    class BrokenApp(Being):
        needs = (Dependency,)

        def birth(self, world: World, life: Life) -> None:
            raise ValueError("broken birth")

    population = Population()
    population.add(Dependency)
    population.add(BrokenApp)
    world = World()

    with pytest.raises(ValueError, match="broken birth"):
        population.birth(BrokenApp, world)

    assert population.alive == (Dependency,)
    assert population._dependents == {}
    population.kill_all(world)


def test_registry_freezes_dependencies_for_each_active_life() -> None:
    dead: list[str] = []

    class First(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("first")

    class Second(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("second")

    class App(Being):
        needs = (First,)

        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("app")

    population = Population()
    for being_type in (First, Second, App):
        population.add(being_type)
    world = World()
    population.birth(App, world)
    population.birth(Second, world)

    App.needs = (Second,)
    population.kill(First, world)

    assert dead == ["app", "first"]
    assert population.alive == (Second,)
    population.kill_all(world)


def test_registry_duplicate_needs_create_one_dependent_death() -> None:
    deaths = 0

    class Dependency(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

    class App(Being):
        needs = (Dependency, Dependency)

        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            nonlocal deaths
            deaths += 1

    population = Population()
    population.add(Dependency)
    population.add(App)
    world = World()
    population.birth(App, world)

    population.kill(Dependency, world)

    assert deaths == 1
    assert population.alive == ()


def test_registry_stopping_dependency_stops_transitive_dependents_first() -> None:
    dead: list[str] = []

    class Foundation(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("foundation")

    class Middle(Being):
        needs = (Foundation,)

        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("middle")

    class Top(Being):
        needs = (Middle,)

        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("top")

    population = Population()
    for being_type in (Foundation, Middle, Top):
        population.add(being_type)
    world = World()
    population.birth(Top, world)

    population.kill(Foundation, world)

    assert dead == ["top", "middle", "foundation"]
    assert population.alive == ()


def test_registry_stopping_dependency_leaves_unrelated_plugin_running() -> None:
    dead: list[str] = []

    class Dependency(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("dependency")

    class App(Being):
        needs = (Dependency,)

        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("app")

    class Independent(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("independent")

    population = Population()
    for being_type in (Dependency, App, Independent):
        population.add(being_type)
    world = World()
    population.birth(App, world)
    population.birth(Independent, world)

    population.kill(Dependency, world)

    assert dead == ["app", "dependency"]
    assert population.alive == (Independent,)
    population.kill_all(world)
    assert dead == ["app", "dependency", "independent"]


def test_registry_stops_dependency_even_when_dependent_stop_fails() -> None:
    dead: list[str] = []

    class Dependency(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("dependency")

    class BrokenApp(Being):
        needs = (Dependency,)

        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("app")
            raise ValueError("app death failed")

    population = Population()
    population.add(Dependency)
    population.add(BrokenApp)
    world = World()
    population.birth(BrokenApp, world)

    with pytest.raises(ValueError, match="app death failed"):
        population.kill(Dependency, world)

    assert dead == ["app", "dependency"]
    assert population.alive == ()


def test_registry_stop_all_continues_after_one_plugin_failure() -> None:
    dead: list[str] = []

    class First(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("first")

    class Broken(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("broken")
            raise ValueError("boom")

    class Last(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("last")

    population = Population()
    for being_type in (First, Broken, Last):
        population.add(being_type)
    world = World()
    for being_type in (First, Broken, Last):
        population.birth(being_type, world)

    with pytest.raises(ValueError, match="boom"):
        population.kill_all(world)

    assert dead == ["last", "broken", "first"]
    assert population.alive == ()


def test_registry_stop_all_aggregates_multiple_failures() -> None:
    class First(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            raise ValueError("first boom")

    class Second(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            raise LookupError("second boom")

    population = Population()
    population.add(First)
    population.add(Second)
    world = World()
    population.birth(First, world)
    population.birth(Second, world)

    with pytest.raises(RuntimeError, match="being death failed") as caught:
        population.kill_all(world)

    assert "LookupError: second boom" in str(caught.value)
    assert "ValueError: first boom" in str(caught.value)
    assert isinstance(caught.value.__cause__, LookupError)
    assert population.alive == ()


def test_registry_stop_all_finishes_cleanup_before_reraising_system_exit() -> None:
    dead: list[str] = []

    class First(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("first")

    class Exiting(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("exiting")
            raise SystemExit(9)

    class Last(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            dead.append("last")

    population = Population()
    for being_type in (First, Exiting, Last):
        population.add(being_type)
    world = World()
    for being_type in (First, Exiting, Last):
        population.birth(being_type, world)

    with pytest.raises(SystemExit) as caught:
        population.kill_all(world)

    assert caught.value.code == 9
    assert dead == ["last", "exiting", "first"]
    assert population.alive == ()


def test_being_name_defaults_to_class_name() -> None:
    class NamedBeing(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

    assert NamedBeing.name == "NamedBeing"


def test_being_can_override_name() -> None:
    class NamedBeing(Being):
        name = "custom-name"

        def birth(self, world: World, life: Life) -> None:
            pass

    assert NamedBeing.name == "custom-name"


# TODO: Keep vocabulary-sensitive assertions aligned with the public Being API.


def test_context_register_start_and_stop_delegate_to_registry() -> None:
    calls: list[str] = []

    class Demo(Being):
        def birth(self, world: World, life: Life) -> None:
            calls.append("start")

        def die(self, world: World, life: Life) -> None:
            calls.append("stop")

    with World() as world:
        world._add(Demo)
        world._birth(Demo)
        world._kill(Demo)

    assert calls == ["start", "stop"]


def test_context_child_links_to_parent() -> None:
    parent = World("parent")
    branch = parent.branch("child")

    assert branch.name == "child"
    assert branch.parent is parent
    assert parent.branches == [branch]

    parent._end()


def test_context_rejects_branch_after_shutdown() -> None:
    parent = World("parent")
    parent._end()

    with pytest.raises(RuntimeError, match="cannot branch an ended World"):
        parent.branch("ghost")

    assert parent.branches == []


def test_context_rejects_branch_created_from_being_death() -> None:
    rejected: list[str] = []

    class BranchingBeing(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            try:
                world.branch("ghost")
            except RuntimeError as error:
                rejected.append(str(error))

    parent = World("parent")
    parent._add(BranchingBeing)
    parent._birth(BranchingBeing)

    parent._end()

    assert rejected == ["cannot branch an ended World"]
    assert parent.branches == []


def test_failed_context_shutdown_cannot_acquire_new_branch() -> None:
    branch_rejected = False

    class BrokenBranchingBeing(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            nonlocal branch_rejected
            try:
                world.branch("ghost")
            except RuntimeError:
                branch_rejected = True
            raise ValueError("death failed")

    parent = World("parent")
    parent._add(BrokenBranchingBeing)
    parent._birth(BrokenBranchingBeing)

    with pytest.raises(RuntimeError, match="world erasure failed: parent"):
        parent._end()

    assert branch_rejected
    assert parent.branches == []
    assert parent._ended


def test_context_shutdown_ends_every_owned_branch() -> None:
    parent = World("parent")
    first = parent.branch("first")
    second = parent.branch("second")
    grandchild = first.branch("grandchild")

    parent._end()

    assert parent.branches == []
    assert parent._ended
    assert first._ended
    assert second._ended
    assert grandchild._ended


def test_context_close_closes_children_before_parent_plugins() -> None:
    order: list[str] = []

    class ParentBeing(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            order.append("parent")

    class BranchBeing(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            order.append("child")

    parent = World("parent")
    branch = parent.branch("child")
    parent._add(ParentBeing)
    branch._add(BranchBeing)
    parent._birth(ParentBeing)
    branch._birth(BranchBeing)

    parent._end()

    assert order == ["child", "parent"]
    assert parent.branches == []


def test_context_close_is_idempotent() -> None:
    calls: list[str] = []

    class Demo(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

        def die(self, world: World, life: Life) -> None:
            calls.append("stop")

    world = World()
    world._add(Demo)
    world._birth(Demo)

    world._end()
    world._end()

    assert calls == ["stop"]


def test_context_manager_closes_started_plugins() -> None:
    calls: list[str] = []

    class Demo(Being):
        def birth(self, world: World, life: Life) -> None:
            calls.append("start")

        def die(self, world: World, life: Life) -> None:
            calls.append("stop")

    with World() as world:
        world._add(Demo)
        world._birth(Demo)

    assert calls == ["start", "stop"]


def test_world_old_lifecycle_controls_are_not_public() -> None:
    world = World()

    assert not hasattr(world, "population")
    assert not hasattr(world, "add")
    assert not hasattr(world, "birth")
    assert not hasattr(world, "kill")
    assert not hasattr(world, "end")

    world._end()


def test_world_need_returns_the_same_alive_being_instance() -> None:
    class Demo(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

    with World() as world:
        world._add(Demo)
        world._birth(Demo)

        first = world.need(Demo)
        second = world.need(Demo)

        assert isinstance(first, Demo)
        assert first is second
        assert world.alive == (Demo,)


def test_world_need_rejects_registered_but_unborn_being() -> None:
    class Sleeping(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

    with World() as world:
        world._add(Sleeping)

        with pytest.raises(LookupError, match="being is not alive: Sleeping"):
            world.need(Sleeping)


def test_world_need_rejects_being_after_death() -> None:
    class Demo(Being):
        def birth(self, world: World, life: Life) -> None:
            pass

    with World() as world:
        world._add(Demo)
        world._birth(Demo)
        world._kill(Demo)

        with pytest.raises(LookupError, match="being is not alive: Demo"):
            world.need(Demo)

        assert world.alive == ()
