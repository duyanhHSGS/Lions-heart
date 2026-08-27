Corleonis
=========

Corleonis is a small system for keeping a large program organized.

The big idea
------------

Large programs become difficult when everything knows about everything else.
Corleonis gives the program a simple structure so different parts can live,
work, communicate, and finish without turning the whole thing into spaghetti.

Think of it like a well-run building:

    Building
      ↓
    Rooms
      ↓
    People doing jobs
      ↓
    Their work and belongings

Each part has a clear place and a clear lifetime.

Spaces
------

A space is a place where related parts of a program live together.

A large program might have:

    Main space
    ├── Logging
    ├── Data services
    ├── Communication
    └── Intelligence

A space can contain smaller spaces. This lets a huge program be divided into
smaller areas without losing the overall structure.

Components
----------

A component is one useful part of the program.

A component might handle things such as:

    Logging
    Data storage
    Communication
    Web features
    Background work

A component can start work, react to things happening around it, and clean up
when its work is finished.

Needs
-----

A component can say what other parts it needs instead of searching through the
entire program by itself.

For example:

    "I need the data service."

The surrounding system finds the appropriate resource and provides it.

This keeps components independent and makes the overall program easier to
change.

Organization
------------

Corleonis keeps track of the parts that belong to each area of the program and
which parts are currently active.

That means the program can answer simple questions such as:

    - What parts exist?
    - What parts are currently working?
    - What does each part need?
    - Which work belongs to which part?
    - What needs to happen when something stops?

Lifetimes
---------

Every running part has a beginning and an end.

    START
      ↓
    WORK
      ↓
    STOP
      ↓
    CLEAN UP

Anything created for a particular piece of work can be tied to that work's
lifetime. When the work ends, its resources can be cleaned up instead of being
left behind like abandoned shopping carts. 🛒💀

Communication
-------------

Parts of a large program often need to know when something important happens.

Instead of forcing every part to directly control every other part, Corleonis
provides a simple way to announce events.

For example:

    "Someone joined."
    "The data service connected."
    "A component started."
    "A component stopped."

Other interested parts can listen and react.

This keeps relationships loose instead of creating a giant web of spaghetti.
🍝💀

Nested areas
------------

Large programs can divide themselves into smaller areas:

    Main area
    ├── Communication
    │   ├── Server A
    │   └── Server B
    └── Web area
        ├── Public features
        └── Administration

Each area can have its own resources and its own lifetime while still belonging
to the larger program.

Starting work
-------------

When something starts working:

    1. Its needs are identified.
    2. Required resources are prepared.
    3. The work begins.
    4. Everything created for that work is associated with its lifetime.

Stopping work
-------------

When something stops:

    1. Its active work is stopped.
    2. Related work can be stopped when appropriate.
    3. Its resources are cleaned up.
    4. The system forgets that the work is active.

The big picture
---------------

    ┌───────────────────────┐
    │      Main Area        │
    │                       │
    │   ┌─────┐ ┌─────┐     │
    │   │Work │ │Work │ ... │
    │   └──┬──┘ └──┬──┘     │
    │      │       │        │
    │   resources  resources│
    │      │       │        │
    │      └───┬───┘        │
    │          ↓            │
    │       cleanup         │
    └───────────────────────┘

The goal is simple:

    BIG PROGRAM
        ↓
    clear areas
        ↓
    clear responsibilities
        ↓
    clear lifetimes
        ↓
    controlled communication
        ↓
    cleanup
        ↓
    🗿 PROGRAM REMAINS HUMAN

Adding a Being
--------------

A Being is a small class with an explicit beginning and end. Put built-in
Beings under ``cor_beings``, inherit from ``Being``, and register anything
owned by that Being with ``life.on_death``. The complete package, lifecycle,
dependency, registration, performance, and test contract lives in
``cor_beings/README.md``:

    from cor_being import Being, Life, World


    class Clock(Being):
        name = "clock"

        def birth(self, world: World, life: Life) -> None:
            print("clock started")
            life.on_death(lambda: print("clock stopped"))
            # TODO: Add the clock's real work.

Add an automatically started built-in Being to ``cor_beings.get_beings``.
Declare dependencies through ``needs``; every dependency must be added to the
World before the dependent Being is born.

Feature code receives a deliberately small public World surface: ``name``,
``news``, ``alive``, ``need``, and ``branch``. Use ``world.need(Dependency)``
to retrieve an already-alive declared dependency. Population storage and the
``_add``, ``_birth``, ``_kill``, and ``_end`` controls belong to the runtime
host; their leading underscores are the staff-only sign. ðŸ”’

For example:

    class SunClock(Being):
        needs = (Clock,)

        def birth(self, world: World, life: Life) -> None:
            clock = world.need(Clock)
            print(f"time from {clock.name}")
            # TODO: Add the real clock display.

Built-in browser dashboard
--------------------------

The dashboard is a read-only built-in Being in ``cor_beings.get_beings``.
The runtime births it automatically with every other discovered Being and
remains the only process owner. Start everything with:

    .\.venv\Scripts\python.exe -m cor_runtime

Then open ``http://127.0.0.1:8765``. The dashboard lists the World name, alive
Beings, their modules, and their declared needs. Press ``Ctrl+C`` to end the
World and release the dashboard server.

Why Corleonis exists
--------------------

Corleonis exists so a giant program does not become:

    everything depends on everything
        ↓
    random background work
        ↓
    forgotten resources
        ↓
    mystery communication
        ↓
    spaghetti
        ↓
    💀💀💀

Instead, the goal is:

    organized parts
        ↓
    predictable lifetimes
        ↓
    simple communication
        ↓
    automatic cleanup
        ↓
    🧠 PROGRAM HUMAN AGAIN
