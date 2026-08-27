"""Being population and living-state bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar, cast

from cor_being import Being, Life, World
from cor_runtime._erase import ErrorCatcher, UndoOnError

BeingT = TypeVar("BeingT", bound=Being)


@dataclass(slots=True)
class LivingBeing:
    being: Being
    life: Life
    needs: tuple[type[Being], ...]


@dataclass(slots=True)
class _BirthFrame:
    being_type: type[Being]
    being: Being
    needs: tuple[type[Being], ...]
    next_need: int = 0


@dataclass(slots=True)
class _DeathFrame:
    being_type: type[Being]
    dependents: tuple[type[Being], ...]
    next_dependent: int = 0
    errors: list[BaseException] = field(default_factory=list)


class Population:
    """Own known Beings and their currently active Lives."""

    __slots__ = ("_alive", "_beings", "_birthing", "_dependents")

    def __init__(self) -> None:
        self._beings: dict[type[Being], Being] = {}
        self._alive: dict[type[Being], LivingBeing] = {}
        self._birthing: dict[type[Being], None] = {}
        self._dependents: dict[type[Being], dict[type[Being], None]] = {}

    def add(self, being_type: type[Being]) -> None:
        if being_type in self._beings:
            raise ValueError(f"being already added: {being_type.__name__}")
        self._beings[being_type] = being_type()

    def get(self, being_type: type[Being]) -> Being:
        try:
            return self._beings[being_type]
        except KeyError as exc:
            raise LookupError(
                f"being is not in the population: {being_type.__name__}"
            ) from exc

    def need(self, being_type: type[BeingT]) -> BeingT:
        """Return one currently alive Being required by feature code."""
        try:
            living = self._alive[being_type]
        except KeyError as exc:
            raise LookupError(f"being is not alive: {being_type.__name__}") from exc
        return cast(BeingT, living.being)

    def birth(self, being_type: type[Being], world: World) -> Life:
        if being_type in self._alive:
            return self._alive[being_type].life

        frames: list[_BirthFrame] = []
        self._push_birth_frame(frames, being_type)
        try:
            while frames:
                frame = frames[-1]
                if frame.next_need < len(frame.needs):
                    need = frame.needs[frame.next_need]
                    frame.next_need += 1
                    if need not in self._alive:
                        self._push_birth_frame(frames, need)
                    continue

                life = Life(frame.being.name)
                with UndoOnError(life.die):
                    frame.being.birth(world, life)
                self._alive[frame.being_type] = LivingBeing(
                    frame.being,
                    life,
                    frame.needs,
                )
                for need in frame.needs:
                    self._dependents.setdefault(need, {})[frame.being_type] = None
                self._birthing.pop(frame.being_type, None)
                frames.pop()
        finally:
            for frame in frames:
                self._birthing.pop(frame.being_type, None)

        return self._alive[being_type].life

    def _push_birth_frame(
        self,
        frames: list[_BirthFrame],
        being_type: type[Being],
    ) -> None:
        if being_type in self._birthing:
            birth_path = tuple(self._birthing)
            cycle_start = birth_path.index(being_type)
            cycle = (*birth_path[cycle_start:], being_type)
            names = " -> ".join(item.__name__ for item in cycle)
            raise RuntimeError(f"need cycle: {names}")

        being = self.get(being_type)
        self._birthing[being_type] = None
        frames.append(_BirthFrame(being_type, being, tuple(being.needs)))

    def kill(self, being_type: type[Being], world: World) -> None:
        if being_type not in self._alive:
            return

        frames = [self._death_frame(being_type)]
        while frames:
            frame = frames[-1]
            if frame.next_dependent < len(frame.dependents):
                dependent_type = frame.dependents[frame.next_dependent]
                frame.next_dependent += 1
                if dependent_type in self._alive:
                    frames.append(self._death_frame(dependent_type))
                continue

            living = self._alive.pop(frame.being_type, None)
            if living is not None:
                for need in living.needs:
                    dependents = self._dependents.get(need)
                    if dependents is None:
                        continue
                    dependents.pop(frame.being_type, None)
                    if not dependents:
                        self._dependents.pop(need, None)
                self._dependents.pop(frame.being_type, None)

                with ErrorCatcher(capture_base_exceptions=True) as capture:
                    try:
                        living.being.die(world, living.life)
                    finally:
                        living.life.die()
                if capture.exception is not None:
                    frame.errors.append(capture.exception)

            with ErrorCatcher(capture_base_exceptions=True) as capture:
                self._raise_death_errors(
                    f"being death failed: {frame.being_type.__name__}",
                    frame.errors,
                )

            frames.pop()
            if frames:
                if capture.exception is not None:
                    frames[-1].errors.append(capture.exception)
            elif capture.exception is not None:
                raise capture.exception

    def _death_frame(self, being_type: type[Being]) -> _DeathFrame:
        dependents = self._dependents.get(being_type, {})
        return _DeathFrame(being_type, tuple(reversed(dependents)))

    def kill_all(self, world: World) -> None:
        errors: list[BaseException] = []
        for being_type in tuple(reversed(tuple(self._alive))):
            with ErrorCatcher(capture_base_exceptions=True) as capture:
                self.kill(being_type, world)
            if capture.exception is not None:
                errors.append(capture.exception)
        self._raise_death_errors("being death failed", errors)

    @staticmethod
    def _raise_death_errors(message: str, errors: list[BaseException]) -> None:
        if not errors:
            return
        if len(errors) == 1:
            raise errors[0]
        # TODO: Use ExceptionGroup once Python 3.10 compatibility is no longer required.
        details = "; ".join(f"{type(error).__name__}: {error}" for error in errors)
        raise RuntimeError(f"{message}: {details}") from errors[0]

    @property
    def alive(self) -> tuple[type[Being], ...]:
        return tuple(self._alive)


# TODO: Benchmark compact dependency metadata at million-Being scale.
