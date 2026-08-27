"""Inspection features for the built-in Cor Leonis Watcher hand."""

from __future__ import annotations

from dataclasses import dataclass

from cor_being import Being, Life, World


@dataclass(frozen=True, slots=True)
class BeingInfo:
    """Small, stable description of a Being currently alive in a World."""

    name: str
    being_type: type[Being]
    needs: tuple[str, ...]
    module: str


class Watcher(Being):
    """Watch Beings already known to a World without controlling their Lives."""

    name = "watcher"

    def birth(self, world: World, life: Life) -> None:
        """Become alive without owning any extra runtime resources."""
        # TODO: Expose Watcher features through a simple command/News interface when one exists.

    def list_beings(self, world: World) -> tuple[str, ...]:
        """Return alive Being names in deterministic birth order."""
        return tuple(world.need(being_type).name for being_type in world.alive)

    def snapshot(self, world: World) -> tuple[BeingInfo, ...]:
        """Describe every alive Being in one deterministic population pass."""
        return tuple(
            BeingInfo(
                name=being.name,
                being_type=being_type,
                needs=tuple(need.name for need in being.needs),
                module=being_type.__module__,
            )
            for being_type in world.alive
            for being in (world.need(being_type),)
        )

    def info(self, world: World, name: str) -> BeingInfo:
        """Return information for an alive Being by name."""
        for being_type in world.alive:
            being = world.need(being_type)
            if being.name == name:
                return BeingInfo(
                    name=being.name,
                    being_type=being_type,
                    needs=tuple(need.name for need in being.needs),
                    module=being_type.__module__,
                )
        raise LookupError(f"being is not alive: {name}")


__all__ = ["BeingInfo", "Watcher"]

# TODO: Add install/remove/enable/disable only after a persistent Being-management boundary exists.
