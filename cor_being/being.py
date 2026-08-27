"""Being primitives for things that can live inside a World."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .life import Life
    from .world import World


class Being(ABC):
    """Base class for something that can live inside a Cor Leonis World.

    Keep birth and death small and explicit. Anything owned by one Life should
    be registered with ``life.on_death()`` so the runtime can erase it reliably.
    """

    name: str
    needs: tuple[type[Being], ...] = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        cls.name = getattr(cls, "name", cls.__name__)

    @abstractmethod
    def birth(self, world: World, life: Life) -> None:
        """Bring this Being to life inside ``world``."""

    def die(self, world: World, life: Life) -> None:
        """End this Being's active Life before its death tasks run."""


# TODO: Keep future Being lifecycle names inside the living-world vocabulary.
