"""Lions-heart product Beings and current examples."""

from cor_being import Being

from .dashboard import Dashboard
from .hello_moon import HelloMoon
from .hello_sun import HelloSun
from .hello_world import HelloWorld
from .watcher import Watcher


# TODO: Replace the example composition with the first minimal Lions-heart harness Beings.
def get_beings() -> tuple[type[Being], ...]:
    """Return the current example Being classes in deterministic order."""
    return (HelloWorld, HelloMoon, HelloSun, Watcher, Dashboard)


__all__ = [
    "Dashboard",
    "HelloMoon",
    "HelloSun",
    "HelloWorld",
    "Watcher",
    "get_beings",
]
