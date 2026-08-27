# Lions-heart `cor_beings` rules

`cor_beings/` is Lions-heart's product layer.

Lions-heart is a fork built on top of Cor Leonis, but this repository is allowed to evolve freely inside `cor_beings/` as long as the rules in this file are followed.

The current Beings are examples and starter material. They are not sacred architecture. Lions-heart may add, replace, move, rewrite, or delete them when the real harness grows.

## Ownership

Lions-heart owns `cor_beings/`.

Inside this directory, Lions-heart may:

- add new Beings;
- modify existing Beings;
- delete example Beings;
- move or split Being packages;
- add private helper modules;
- add feature-specific configuration and data helpers;
- add and strengthen tests for Lions-heart behavior.

Do not make product features by changing upstream host internals just because they are visible in the repository. Product behavior belongs in normal Beings.

## Required Being shape

- Give each substantial Being its own package: `cor_beings/<being_name>/`.
- Define the main Being class in that package's `__init__.py` unless private helper modules make the feature clearer.
- Export public feature types through `__all__`.
- Give every Being a stable, unique, lowercase `name`.
- Import the feature-facing contract from `cor_being` only: `Being`, `Life`, and `World`.
- A Being must not reach through private host controls to manage another Being's lifecycle.
- A Being must not own the application's top-level wait loop.

## Lifecycle rule

`birth` starts the feature. `Life` owns everything that must stop when that feature dies.

Register cleanup immediately after creating a resource and before starting it whenever possible. Failed birth must not leave threads, sockets, files, subprocesses, subscriptions, registrations, or other tiny gremlins alive.

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

Cleanup callbacks should be idempotent. A partially started Being must still be safe to clean up.

## Dependency rule

- Declare required Being types in the class-level `needs` tuple.
- Retrieve a declared dependency with `world.need(Dependency)`.
- Never instantiate another Being directly to bypass lifecycle ownership.
- Never depend on tuple position in `get_beings()` for correctness.
- Required dependency types must be available before the dependent Being can live.

```python
class SunClock(Being):
    name = "sun_clock"
    needs = (Clock,)

    def birth(self, world: World, life: Life) -> None:
        clock = world.need(Clock)
        print(f"reading time from {clock.name}")
        # TODO: Replace the demo output with the real display.
```

## Public World boundary

Feature code uses the small public World surface only.

Allowed feature-facing operations currently include:

- `world.name`
- `world.news`
- `world.alive`
- `world.need(...)`
- `world.branch(...)`

Private lifecycle controls are staff-only doors. Do not route Lions-heart features through them.

## Registration rule

Built-in Lions-heart Beings that should start automatically are listed by `cor_beings.get_beings()`.

Keep that list deterministic. Treat it as composition, not as hidden control flow.

The current registered Beings are examples. Their presence today does not make them permanent product architecture.

## Design rule

- One clear responsibility per Being.
- Keep the agent loop small; do not turn one Being into the whole product wearing a trench coat. 😂
- Put reusable calculation and presentation logic in pure functions when practical.
- Keep transport adapters thin.
- Prefer explicit dependencies over hidden globals.
- Announce cross-feature facts through `world.news` when event-style communication fits.
- Do not create secret global registries that duplicate lifecycle state.
- UI, models, tools, sessions, persistence, sandboxing, compaction, memory, and agent control should remain replaceable product Beings rather than privileged kernel concepts.

## Runtime-performance rule

Runtime speed matters more than compile/startup cleverness.

- Do not scan all Beings on every hot-path lookup when an index can make the lookup direct.
- Tool lookup should become `name -> tool`, not a population scan.
- Event delivery should target interested subscribers, not wake unrelated Beings.
- Prompt assembly should iterate registered contributors, not every living Being.
- Session append should stay cheap.
- Cleanup should remove only the dying Life's owned registrations where possible.
- Cache derived schemas or snapshots when invalidation is cheaper than rebuilding them repeatedly.

A future Lions-heart World may contain very large Being counts. Design hot paths accordingly.

## Test law

Every new Being or material behavior change gets tests. Tests must not be skipped.

Cover as applicable:

- normal birth;
- useful normal behavior;
- declared dependencies;
- missing dependency failure;
- partial-start failure;
- rollback and cleanup;
- normal death;
- idempotent cleanup;
- malformed or edge-case public input;
- registration and removal;
- disappearing dependencies/resources;
- failure propagation;
- deterministic composition;
- architecture boundaries;
- scale-sensitive behavior when a design could accidentally scan all Beings.

Use the repository virtual environment's Python and pytest.

Inherited tests that exist only to police upstream Cor Leonis behavior are not sacred Lions-heart tests. They may be deleted when they no longer protect Lions-heart behavior. Do not delete useful coverage merely to make a failing suite green.

## Current examples

These are starter/example Beings, not permanent Lions-heart architecture:

- `HelloWorld`
- `HelloMoon`
- `HelloSun`
- `Watcher`
- `Dashboard`

They may be replaced or removed as real harness Beings arrive.

## Documentation law

`git-plz-ignore/projarch.md` is the architecture/context map for Lions-heart.

Whenever Lions-heart adds, removes, moves, renames, or materially changes product code or behavior:

1. update the relevant tests;
2. update `git-plz-ignore/projarch.md` in the same task;
3. add or update a meaningful `TODO` in changed/new code;
4. keep a corresponding `TODO` in `projarch.md` when future work remains.

`projarch.md` documents Lions-heart only. It must not become a map of upstream internals.

## TODO

- TODO: Replace the example Being set with the first minimal Lions-heart harness Beings.
- TODO: Add automated scaffolding only if repeated Being boilerplate becomes large enough to justify it.
- TODO: Add scale tests as soon as indexed tool/event/prompt registries become real product features.
