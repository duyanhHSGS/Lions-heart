# `cor_beings` implementation rules

`cor_beings` contains the built-in features that live inside a Corleonis
`World`. Each feature is a `Being`: a small owner of one job and the resources
needed to perform that job.

## Required package shape

- Give each built-in Being its own package: `cor_beings/<being_name>/`.
- Define the main Being class in that package's `__init__.py` unless the feature
  is large enough to benefit from private helper modules.
- Export the public feature types through `__all__`.
- Give every Being a stable, unique, lowercase `name`.
- Import the feature-facing contract from `cor_being` only:
  `Being`, `Life`, and `World`.
- Never import `cor_runtime`, access Population, create a `RuntimeWorld`, run
  genesis, or own the application's wait loop.

## Lifecycle rule

`birth` starts the feature. `Life` owns everything that must stop when the
feature dies.

Register cleanup immediately after creating a resource and before starting it
whenever possible. This lets failed births roll back safely instead of leaving
threads, sockets, files, or other tiny gremlins alive.

```python
from cor_being import Being, Life, World


class Clock(Being):
    name = "clock"

    def birth(self, world: World, life: Life) -> None:
        timer = Timer()
        life.on_death(timer.stop)
        timer.start()
        # TODO: Publish useful clock ticks through world.news.
```

Cleanup callbacks must be safe when startup completed only partly. Prefer
idempotent `stop` or `close` methods because cleanup may be reached through
normal shutdown or failed-birth rollback.

## Dependency rule

- Declare required Being types in the class-level `needs` tuple.
- Retrieve a dependency with `world.need(Dependency)` inside `birth` or later.
- Never instantiate another Being directly.
- Never depend on tuple position in `get_beings()` for correctness; dependency
  ordering belongs to the runtime.
- Every required type must still be registered before birth. Declaring `needs`
  does not register it automatically.

```python
class SunClock(Being):
    name = "sun_clock"
    needs = (Clock,)

    def birth(self, world: World, life: Life) -> None:
        clock = world.need(Clock)
        print(f"reading time from {clock.name}")
        # TODO: Replace the demo output with the real display.
```

## World boundary rule

A Being may use only the public `World` surface:

- `world.name`
- `world.news`
- `world.alive`
- `world.need(...)`
- `world.branch(...)`

Host controls such as `_add`, `_birth`, `_kill`, `_end`, and `_population` are
staff-only doors. A leading underscore is the code equivalent of a sign saying
"Uncle Cave, please do not press this button." 🦍

## Built-in registration rule

Add every built-in Being type to the tuple returned by
`cor_beings.get_beings()`. Keep that tuple deterministic because its order
affects normal birth order, output, inspection, and reverse shutdown order.
Every registered built-in starts by default; do not add feature-selection or
process-control logic to a Being.

## Design and performance rule

- Keep one clear responsibility per Being.
- Put reusable calculation and presentation logic in pure functions; keep
  transport code such as HTTP handlers thin.
- Prefer one pass over `world.alive` when building a full snapshot instead of
  repeatedly scanning it. Runtime speed gets the eagle; needless repeated work
  gets the sad trombone. 🦅
- Background threads may read only deliberately stable state. Do not mutate
  lifecycle state from them until the runtime provides a thread-safe command
  boundary.
- Announce cross-feature facts through `world.news`; do not build hidden global
  registries or reach into another Being's private state.

## Test rule

Add tests under `tests/` for every new Being or material behavior change. Cover:

- normal birth and useful behavior;
- declared dependencies and missing-dependency failure;
- partial-start failure and cleanup;
- normal death and idempotent cleanup;
- edge cases in public inputs;
- deterministic built-in registration when the Being is built in;
- the boundary that built-ins do not import `cor_runtime`.

Run tests with the repository virtual environment's Python and its pytest, not
a global pytest command. Tests must not be skipped.

## TODO

- TODO: Split this contract into an automated Being scaffold only when the
  package shape needs enough boilerplate to justify a generator.
