from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing_extensions import Self


class ErrorCatcher:
    """Capture and suppress one error through the context-manager protocol."""

    def __init__(self, *, capture_base_exceptions: bool = False) -> None:
        self.capture_base_exceptions = capture_base_exceptions
        self.exception: BaseException | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc_value is None:
            return False
        if self.capture_base_exceptions or isinstance(exc_value, Exception):
            self.exception = exc_value
            return True
        return False


class UndoOnError:
    """Run an undo callback when the protected operation raises."""

    def __init__(self, undo: Callable[[], Any]) -> None:
        self._undo = undo

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc_value is not None:
            self._undo()
        return False


# TODO: Keep death-task error policy centralized here as the runtime grows.
