# Lions-heart `cor_beings` rules

`cor_beings/` is Lions-heart's product layer.

Lions-heart is a fork built on top of Cor Leonis, but this repository is allowed to evolve freely inside `cor_beings/` as long as the rules in this file are followed.

The current Beings form Lion's remote-provider application spine. They are real product code, but they remain replaceable when clearer Being boundaries prove themselves.

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

The registered Beings compose storage, owner security, remote providers, conversations, approvals, tools, turns, and adapters. Their presence proves the product shape, not permanent implementation details; future replacements must keep the same ordinary-Being rules.

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

## Current Lion application spine

- `StorageBeing`, `SettingsBeing`, and `AuthBeing` own migrations, safe settings, encrypted provider credentials, the single owner, and expiring server sessions.
- `OpenAIProviderBeing`, `AnthropicProviderBeing`, and `GeminiProviderBeing` normalize remote streaming behind `ModelGatewayBeing`; `LionBeing` is only an injectable deterministic test fake.
- `SessionBeing` owns durable or temporary conversation history; `ProjectsBeing` owns searchable project metadata and assignment targets.
- `TurnManagerBeing` owns background turns, cancellation, persisted ordered events, and resumable SSE traces.
- `ApprovalBeing` makes every tool call wait for an explicit, expiring, duplicate-safe owner decision and executes an approved call once.
- `WorkspaceBeing`, `ReadBeing`, `EditBeing`, `BashBeing`, and `ToolShelfBeing` provide indexed schemas plus a selected-project path boundary.
- `WebUiBeing` serves the authenticated build-free module UI from birth-cached, content-hashed assets; `CliBeing` keeps the blocking compatibility adapter.

This is a usable remote-chat/security foundation, not a promise that every class or contract is final. Dynamic tool and MCP registration can replace the starter concrete tool composition when that wave lands.

## Documentation law

`git-plz-ignore/projarch.md` is the architecture/context map for Lions-heart.

Whenever Lions-heart adds, removes, moves, renames, or materially changes product code or behavior:

1. update the relevant tests;
2. update `git-plz-ignore/projarch.md` in the same task;
3. add or update a meaningful `TODO` in changed/new code;
4. keep a corresponding `TODO` in `projarch.md` when future work remains.

`projarch.md` documents Lions-heart only. It must not become a map of upstream internals.

## TODO

- TODO: Replace `ToolShelfBeing`'s direct starter-tool dependencies with Life-owned dynamic registration only when dynamic tools are actually needed.
- TODO: Add optional history-aware CLI line editing without sacrificing cancellable reads or Life-owned thread joining.
- TODO: Add attachment extraction, MCP connections, cloud-media jobs, recipes, and API-activity screens through their own ordinary Beings.
- TODO: Bound idle per-conversation lock and completed-turn caches after large chat churn.
- TODO: Keep Web Search disabled until a dedicated security and provider contract is deliberately implemented.
- TODO: Add math and Mermaid only through audited build-free renderers, and semantic embeddings only as an optional retrieval layer.
- TODO: Add automated scaffolding only if repeated Being boilerplate becomes large enough to justify it.
- TODO: Add large-count scale tests when dynamic tool/event/prompt registries become real product features.
