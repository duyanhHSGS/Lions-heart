"""Validated product preferences and encrypted remote-provider credentials."""

from __future__ import annotations

import json
import os
import time
from types import MappingProxyType
from typing import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from cor_being import Being, Life, World
from cor_beings.storage import StorageBeing


PROVIDERS = ("openai", "anthropic", "gemini")
DEFAULTS: dict[str, object] = {
    "default_provider": "openai",
    "default_text_model": "",
    "default_image_model": "",
    "default_video_model": "",
    "default_speech_model": "",
    "default_transcription_model": "",
    "system_prompt": "You are Lion, a helpful remote-provider coding assistant.",
    "theme": "system",
    "retention_days": 90,
}


class SettingsBeing(Being):
    """Own settings validation and secret-safe provider configuration."""

    name = "settings"
    needs = (StorageBeing,)

    def __init__(self) -> None:
        self._storage: StorageBeing | None = None
        self._values: dict[str, object] = dict(DEFAULTS)

    def birth(self, world: World, life: Life) -> None:
        storage = world.need(StorageBeing)
        rows = storage.fetchall("SELECT key, value_json FROM settings")
        values = dict(DEFAULTS)
        for row in rows:
            if row["key"] in DEFAULTS:
                values[row["key"]] = json.loads(row["value_json"])
        self._validate(values)
        self._storage = storage
        self._values = values
        life.on_death(self._forget)
        # TODO: Add optional semantic-embedding settings when retrieval needs them.

    @property
    def values(self) -> Mapping[str, object]:
        return MappingProxyType(dict(self._values))

    @staticmethod
    def _validate(values: Mapping[str, object]) -> None:
        provider = values.get("default_provider")
        if provider not in PROVIDERS:
            raise ValueError("default_provider must be openai, anthropic, or gemini")
        theme = values.get("theme")
        if theme not in ("system", "light", "dark"):
            raise ValueError("theme must be system, light, or dark")
        retention = values.get("retention_days")
        if not isinstance(retention, int) or isinstance(retention, bool) or retention < 1:
            raise ValueError("retention_days must be a positive integer")
        for key in DEFAULTS:
            if key.endswith("_model") or key == "system_prompt":
                if not isinstance(values.get(key), str):
                    raise ValueError(f"{key} must be a string")

    def update(self, changes: Mapping[str, object]) -> Mapping[str, object]:
        if not isinstance(changes, Mapping):
            raise TypeError("settings changes must be a mapping")
        unknown = set(changes) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"unknown setting: {sorted(unknown)[0]}")
        merged = {**self._values, **changes}
        self._validate(merged)
        storage = self._require_storage()
        now = int(time.time())
        with storage.transaction() as connection:
            for key, value in changes.items():
                connection.execute(
                    "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                    (key, json.dumps(value, ensure_ascii=False), now),
                )
        self._values = merged
        return self.values

    def set_provider_key(self, provider: str, secret: str) -> None:
        if provider not in PROVIDERS:
            raise ValueError("unknown provider")
        if not isinstance(secret, str) or not secret.strip():
            raise ValueError("provider key must be a non-empty string")
        storage = self._require_storage()
        master = self._master_key(storage)
        nonce = os.urandom(12)
        ciphertext = AESGCM(master).encrypt(nonce, secret.encode("utf-8"), provider.encode("ascii"))
        now = int(time.time())
        storage.execute(
            "INSERT INTO provider_secrets(provider, ciphertext, nonce, suffix, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(provider) DO UPDATE SET ciphertext=excluded.ciphertext, nonce=excluded.nonce, "
            "suffix=excluded.suffix, updated_at=excluded.updated_at",
            (provider, ciphertext, nonce, secret[-4:], now),
        )

    def delete_provider_key(self, provider: str) -> None:
        if provider not in PROVIDERS:
            raise ValueError("unknown provider")
        self._require_storage().execute("DELETE FROM provider_secrets WHERE provider=?", (provider,))

    def provider_key(self, provider: str) -> str | None:
        if provider not in PROVIDERS:
            raise ValueError("unknown provider")
        storage = self._require_storage()
        row = storage.fetchone(
            "SELECT ciphertext, nonce FROM provider_secrets WHERE provider=?", (provider,)
        )
        if row is None:
            return None
        plaintext = AESGCM(self._master_key(storage)).decrypt(
            row["nonce"], row["ciphertext"], provider.encode("ascii")
        )
        return plaintext.decode("utf-8")

    def public_snapshot(self) -> dict[str, object]:
        storage = self._require_storage()
        rows = storage.fetchall("SELECT provider, suffix FROM provider_secrets")
        configured = {row["provider"]: {"configured": True, "suffix": row["suffix"]} for row in rows}
        return {
            "values": dict(self._values),
            "providers": {
                provider: configured.get(provider, {"configured": False, "suffix": ""})
                for provider in PROVIDERS
            },
        }

    @staticmethod
    def _master_key(storage: StorageBeing) -> bytes:
        import base64

        return base64.urlsafe_b64decode(str(storage.config["master_key"]).encode("ascii"))

    def _require_storage(self) -> StorageBeing:
        if self._storage is None:
            raise RuntimeError("settings is not alive")
        return self._storage

    def _forget(self) -> None:
        self._storage = None
        self._values = dict(DEFAULTS)


__all__ = ["DEFAULTS", "PROVIDERS", "SettingsBeing"]
