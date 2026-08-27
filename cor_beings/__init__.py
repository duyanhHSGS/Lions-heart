"""Built-in Cor Leonis hands."""

from cor_being import Being

from .dashboard import Dashboard
from .hello_moon import HelloMoon
from .hello_sun import HelloSun
from .hello_world import HelloWorld
from .watcher import Watcher


# TODO: Keep built-in hand listing centralized here and aligned with this package's README.
def get_beings() -> tuple[type[Being], ...]:
    """Return all built-in hand Being classes."""
    return (HelloWorld, HelloMoon, HelloSun, Watcher, Dashboard)


__all__ = [
    "Dashboard",
    "HelloMoon",
    "HelloSun",
    "HelloWorld",
    "Watcher",
    "get_beings",
]
