"""Validated product preferences and encrypted remote-provider credentials."""

from __future__ import annotations

import json
import os
import re
import time
from types import MappingProxyType
from typing import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from cor_being import Being, Life, World
from cor_beings.storage import StorageBeing


PROVIDERS = ("openai", "anthropic", "gemini")
_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
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
        inferred_model = self._sole_custom_model(storage, values)
        if inferred_model is not None:
            values["default_text_model"] = inferred_model
            storage.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                ("default_text_model", json.dumps(inferred_model), int(time.time())),
            )
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
        if not isinstance(provider, str) or _PROVIDER_ID.fullmatch(provider) is None:
            raise ValueError("default_provider must be a valid provider ID")
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
        effective_changes = dict(changes)
        merged = {**self._values, **effective_changes}
        self._validate(merged)
        storage = self._require_storage()
        inferred_model = self._sole_custom_model(storage, merged)
        if inferred_model is not None:
            merged["default_text_model"] = inferred_model
            effective_changes["default_text_model"] = inferred_model
        now = int(time.time())
        with storage.transaction() as connection:
            for key, value in effective_changes.items():
                connection.execute(
                    "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                    (key, json.dumps(value, ensure_ascii=False), now),
                )
        self._values = merged
        return self.values

    @staticmethod
    def _sole_custom_model(storage: StorageBeing, values: Mapping[str, object]) -> str | None:
        model = values.get("default_text_model")
        provider = values.get("default_provider")
        if not isinstance(model, str) or model.strip() or not isinstance(provider, str):
            return None
        row = storage.fetchone(
            "SELECT models_json FROM provider_connections WHERE id=? AND enabled=1",
            (provider,),
        )
        if row is None:
            return None
        try:
            models = json.loads(row["models_json"])
        except (TypeError, ValueError):
            return None
        if isinstance(models, list) and len(models) == 1 and isinstance(models[0], str) and models[0]:
            return models[0]
        # TODO: Never guess among multiple models; require an explicit owner choice.
        return None

    def set_provider_key(self, provider: str, secret: str) -> None:
        provider = self._provider_id(provider)
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
        provider = self._provider_id(provider)
        self._require_storage().execute("DELETE FROM provider_secrets WHERE provider=?", (provider,))

    def provider_key(self, provider: str) -> str | None:
        provider = self._provider_id(provider)
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
        custom = storage.fetchall("SELECT id FROM provider_connections ORDER BY created_at,id")
        provider_ids = (*PROVIDERS, *(str(row["id"]) for row in custom))
        return {
            "values": dict(self._values),
            "providers": {
                provider: configured.get(provider, {"configured": False, "suffix": ""})
                for provider in provider_ids
            },
        }

    @staticmethod
    def _provider_id(provider: str) -> str:
        if not isinstance(provider, str) or _PROVIDER_ID.fullmatch(provider) is None:
            raise ValueError("invalid provider ID")
        return provider

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
