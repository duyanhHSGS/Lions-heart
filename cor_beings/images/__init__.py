"""Validated image-generation facade."""

from __future__ import annotations
from collections.abc import Mapping
from cor_being import Being, Life, World
from cor_beings.media_jobs import MediaJobBeing

_SIZES = frozenset({"256x256", "512x512", "1024x1024", "1024x1536", "1536x1024"})
_QUALITIES = frozenset({"low", "medium", "high", "auto"})


class ImageBeing(Being):
    name = "images"
    needs = (MediaJobBeing,)

    def __init__(self) -> None: self._jobs: MediaJobBeing | None = None

    def birth(self, world: World, life: Life) -> None:
        self._jobs = world.need(MediaJobBeing); life.on_death(self._forget)
        # TODO: Add owned reference-image inputs after the provider contract carries them.

    def generate(self, prompt: str, *, provider: str, model: str, size: str = "1024x1024",
                 quality: str = "auto", count: int = 1, seed: int | None = None) -> dict[str, object]:
        if size not in _SIZES: raise ValueError("unsupported image size")
        if quality not in _QUALITIES: raise ValueError("unsupported image quality")
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 4:
            raise ValueError("image count must be from 1 through 4")
        if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 2**32 - 1):
            raise ValueError("image seed must be a 32-bit unsigned integer")
        return self._require().submit("image", provider, model, prompt,
                                      {"size": size, "quality": quality, "count": count, "seed": seed})

    def _require(self) -> MediaJobBeing:
        if self._jobs is None: raise RuntimeError("images is not alive")
        return self._jobs

    def _forget(self) -> None: self._jobs = None


__all__ = ["ImageBeing"]
