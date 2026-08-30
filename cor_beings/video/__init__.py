"""Validated cloud-video generation facade."""

from __future__ import annotations
from cor_being import Being, Life, World
from cor_beings.media_jobs import MediaJobBeing


class VideoBeing(Being):
    name = "video"
    needs = (MediaJobBeing,)

    def __init__(self) -> None: self._jobs: MediaJobBeing | None = None
    def birth(self, world: World, life: Life) -> None:
        self._jobs = world.need(MediaJobBeing); life.on_death(self._forget)
        # TODO: Add owned keyframe references with a separately audited byte budget.

    def generate(self, prompt: str, *, provider: str, model: str, duration_seconds: int = 5,
                 width: int = 1280, height: int = 720) -> dict[str, object]:
        if not isinstance(duration_seconds, int) or isinstance(duration_seconds, bool) or not 1 <= duration_seconds <= 120:
            raise ValueError("video duration must be from 1 through 120 seconds")
        if (width, height) not in {(1280, 720), (720, 1280), (1920, 1080), (1080, 1920)}:
            raise ValueError("unsupported video dimensions")
        return self._require().submit("video", provider, model, prompt,
                                      {"duration_seconds": duration_seconds, "width": width, "height": height})

    def _require(self) -> MediaJobBeing:
        if self._jobs is None: raise RuntimeError("video is not alive")
        return self._jobs
    def _forget(self) -> None: self._jobs = None


__all__ = ["VideoBeing"]
