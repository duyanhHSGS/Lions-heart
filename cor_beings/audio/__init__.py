"""Validated speech, transcription, and generated-audio facade."""

from __future__ import annotations
from cor_being import Being, Life, World
from cor_beings.media_jobs import MediaJobBeing


class AudioBeing(Being):
    name = "audio"
    needs = (MediaJobBeing,)

    def __init__(self) -> None: self._jobs: MediaJobBeing | None = None

    def birth(self, world: World, life: Life) -> None:
        self._jobs = world.need(MediaJobBeing); life.on_death(self._forget)
        # TODO: Add bounded streamed microphone uploads after browser permission QA.

    def text_to_speech(self, text: str, *, provider: str, model: str, voice: str,
                       format: str = "mp3") -> dict[str, object]:
        if format not in {"mp3", "wav", "opus", "aac", "flac"}: raise ValueError("unsupported audio format")
        if not isinstance(voice, str) or not 1 <= len(voice.strip()) <= 80: raise ValueError("voice is invalid")
        return self._require().submit("audio", provider, model, text,
                                      {"operation": "text_to_speech", "voice": voice.strip(), "format": format})

    def generate(self, prompt: str, *, provider: str, model: str, duration_seconds: int,
                 format: str = "mp3") -> dict[str, object]:
        if not isinstance(duration_seconds, int) or isinstance(duration_seconds, bool) or not 1 <= duration_seconds <= 600:
            raise ValueError("audio duration must be from 1 through 600 seconds")
        if format not in {"mp3", "wav", "opus", "aac", "flac"}: raise ValueError("unsupported audio format")
        return self._require().submit("audio", provider, model, prompt,
                                      {"operation": "generate", "duration_seconds": duration_seconds, "format": format})

    def transcribe(self, *, provider: str, model: str, attachment_id: str,
                   language: str | None = None) -> dict[str, object]:
        if not isinstance(attachment_id, str) or not attachment_id: raise ValueError("attachment id is required")
        if language is not None and (not isinstance(language, str) or not 2 <= len(language) <= 20):
            raise ValueError("language is invalid")
        return self._require().submit("audio", provider, model, "Transcribe attached audio",
                                      {"operation": "transcribe", "attachment_id": attachment_id, "language": language})

    def _require(self) -> MediaJobBeing:
        if self._jobs is None: raise RuntimeError("audio is not alive")
        return self._jobs
    def _forget(self) -> None: self._jobs = None


__all__ = ["AudioBeing"]
