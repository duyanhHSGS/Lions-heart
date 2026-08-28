"""Storage, security, settings, and remote-provider foundation tests."""

# TODO: Add provider media fixtures when cloud media endpoints are implemented.

from __future__ import annotations

import json
from pathlib import Path
from threading import Event

import httpx
import pytest

from cor_being import Being, Life
from cor_beings.auth import AuthBeing
from cor_beings.providers import (
    AnthropicProviderBeing,
    GeminiProviderBeing,
    OpenAIProviderBeing,
    ProviderError,
    ProviderRegistryBeing,
    ProviderRequest,
)
from cor_beings.session import SessionBeing
from cor_beings.settings import DEFAULTS, SettingsBeing
from cor_beings.storage import SCHEMA_VERSION, StorageBeing


class World:
    name = "provider-platform-test"
    news = None
    alive = ()

    def __init__(self, *instances: Being) -> None:
        self.instances = {type(instance): instance for instance in instances}

    def need(self, being_type):
        try:
            return self.instances[being_type]
        except KeyError as error:
            raise LookupError(being_type) from error


def born(being: Being, world: World) -> Life:
    life = Life(being.name)
    being.birth(world, life)
    return life


@pytest.fixture
def platform(tmp_path: Path):
    storage = StorageBeing(data_root=tmp_path / "user")
    settings = SettingsBeing()
    auth = AuthBeing()
    world = World(storage, settings, auth)
    lives = [born(storage, world), born(settings, world), born(auth, world)]
    try:
        yield storage, settings, auth, world
    finally:
        for life in reversed(lives):
            life.die()


def test_storage_creates_private_config_and_versioned_database(platform) -> None:
    storage, _settings, _auth, _world = platform
    config_path = storage.data_root / "config.json"
    database_path = storage.data_root / "lions-heart.sqlite3"
    assert config_path.is_file()
    assert database_path.is_file()
    assert storage.config["master_key"]
    assert storage.fetchone("PRAGMA user_version")[0] == SCHEMA_VERSION
    assert storage.fetchone("PRAGMA journal_mode")[0].lower() == "wal"
    assert storage.fetchone("PRAGMA foreign_keys")[0] == 1


def test_storage_rejects_corrupt_config(tmp_path: Path) -> None:
    root = tmp_path / "user"
    root.mkdir()
    (root / "config.json").write_text("kaboom", encoding="utf-8")
    storage = StorageBeing(data_root=root)
    with pytest.raises(RuntimeError, match="unreadable"):
        storage.birth(World(storage), Life("storage"))


def test_storage_transaction_rolls_back(platform) -> None:
    storage = platform[0]
    with pytest.raises(RuntimeError, match="boom"):
        with storage.transaction() as connection:
            connection.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES ('theme', '\"dark\"', 1)"
            )
            raise RuntimeError("boom")
    assert storage.fetchone("SELECT key FROM settings WHERE key='theme'") is None


def test_settings_validate_persist_and_reload(platform) -> None:
    storage, settings, _auth, world = platform
    updated = settings.update({"theme": "dark", "retention_days": 30, "default_provider": "gemini"})
    assert updated["theme"] == "dark"
    with pytest.raises(ValueError, match="unknown setting"):
        settings.update({"gpu_layers": 99})
    with pytest.raises(ValueError, match="positive integer"):
        settings.update({"retention_days": 0})

    replacement = SettingsBeing()
    replacement_life = born(replacement, World(storage, replacement))
    try:
        assert replacement.values["theme"] == "dark"
        assert replacement.values["default_provider"] == "gemini"
    finally:
        replacement_life.die()


def test_provider_secrets_are_encrypted_and_public_snapshot_is_redacted(platform) -> None:
    storage, settings, _auth, _world = platform
    secret = "sk-this-must-never-escape"
    settings.set_provider_key("openai", secret)
    row = storage.fetchone("SELECT ciphertext, nonce, suffix FROM provider_secrets WHERE provider='openai'")
    assert row is not None
    assert secret.encode() not in row["ciphertext"]
    assert len(row["nonce"]) == 12
    assert settings.provider_key("openai") == secret
    snapshot = settings.public_snapshot()
    assert secret not in json.dumps(snapshot)
    assert snapshot["providers"]["openai"] == {"configured": True, "suffix": "cape"}
    settings.delete_provider_key("openai")
    assert settings.provider_key("openai") is None


def test_auth_setup_login_csrf_logout_and_death(platform) -> None:
    _storage, _settings, auth, _world = platform
    assert auth.setup_required
    with pytest.raises(ValueError, match="at least 10"):
        auth.setup("lion", "tiny")
    session = auth.setup("lion", "a-roaring-password")
    assert not auth.setup_required
    assert auth.authenticate(session.token)
    assert auth.validate_csrf(session.token, session.csrf_token)
    assert not auth.validate_csrf(session.token, "wrong")
    with pytest.raises(RuntimeError, match="already exists"):
        auth.setup("other", "another-password")
    with pytest.raises(PermissionError, match="invalid credentials"):
        auth.login("lion", "wrong", remote="one")
    logged_in = auth.login("lion", "a-roaring-password", remote="one")
    assert auth.authenticate(logged_in.token)
    auth.logout(logged_in.token)
    assert not auth.authenticate(logged_in.token)


def test_auth_rate_limits_repeated_bad_passwords(platform) -> None:
    auth = platform[2]
    auth.setup("lion", "a-roaring-password")
    for _ in range(5):
        with pytest.raises(PermissionError, match="invalid credentials"):
            auth.login("lion", "wrong", remote="attacker")
    with pytest.raises(PermissionError, match="too many"):
        auth.login("lion", "wrong", remote="attacker")


def test_session_persists_conversations_and_temporary_chat_does_not(platform) -> None:
    storage = platform[0]
    first = SessionBeing()
    life = born(first, World(storage, first))
    first_id = first.conversation_id
    first.append("user", text="remember this")
    first.rename_conversation(first_id, "Remembered")
    temporary_id = first.new_conversation(title="Ghost", temporary=True)
    first.append("user", text="forget this")
    assert temporary_id not in {row["id"] for row in first.list_conversations()}
    life.die()

    second = SessionBeing()
    second_life = born(second, World(storage, second))
    try:
        second.open_conversation(first_id)
        assert second.events[0].data["text"] == "remember this"
        assert second.list_conversations()[0]["title"] == "Remembered"
    finally:
        second_life.die()


def request(provider: str = "openai") -> ProviderRequest:
    return ProviderRequest(
        provider=provider,
        model="remote-model",
        system="Be Lion",
        messages=({"role": "user", "content": "hello"},),
        tools=(
            {
                "name": "read",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        ),
    )


def provider_world(tmp_path: Path, provider_being, key_name: str):
    storage = StorageBeing(data_root=tmp_path / key_name)
    settings = SettingsBeing()
    world = World(storage, settings, provider_being)
    lives = [born(storage, world), born(settings, world)]
    settings.set_provider_key(key_name, "test-secret")
    lives.append(born(provider_being, world))
    return lives


def test_openai_stream_normalizes_text_tools_usage_and_completion(tmp_path: Path) -> None:
    sse = """event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"Hi"}\n\nevent: response.output_item.done\ndata: {"type":"response.output_item.done","item":{"type":"function_call","call_id":"c1","name":"read","arguments":"{\\"path\\":\\"x\\"}"}}\n\nevent: response.completed\ndata: {"type":"response.completed","response":{"usage":{"input_tokens":4,"output_tokens":2}}}\n\ndata: [DONE]\n\n"""

    def handler(incoming: httpx.Request) -> httpx.Response:
        assert incoming.url.path == "/responses"
        assert incoming.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(200, text=sse, headers={"Content-Type": "text/event-stream"})

    provider = OpenAIProviderBeing(base_url="https://test", transport=httpx.MockTransport(handler))
    lives = provider_world(tmp_path, provider, "openai")
    try:
        events = tuple(provider.stream(request(), Event()))
        assert [event.kind for event in events] == ["start", "text_delta", "tool_call", "usage", "completed"]
        assert events[1].data["text"] == "Hi"
        assert events[2].data["name"] == "read"
    finally:
        for life in reversed(lives):
            life.die()


@pytest.mark.parametrize(
    ("provider_type", "provider_name", "sse", "expected"),
    [
        (
            AnthropicProviderBeing,
            "anthropic",
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Claude"}}\n\nevent: message_stop\ndata: {"type":"message_stop"}\n\n',
            "Claude",
        ),
        (
            GeminiProviderBeing,
            "gemini",
            'event: step.delta\ndata: {"event_type":"step.delta","index":0,"delta":{"type":"text","text":"Gemini"}}\n\nevent: done\ndata: [DONE]\n\n',
            "Gemini",
        ),
    ],
)
def test_anthropic_and_gemini_stream_text(tmp_path: Path, provider_type, provider_name, sse, expected) -> None:
    provider = provider_type(base_url="https://test", transport=httpx.MockTransport(lambda _request: httpx.Response(200, text=sse)))
    lives = provider_world(tmp_path, provider, provider_name)
    try:
        events = tuple(provider.stream(request(provider_name), Event()))
        assert next(event.data["text"] for event in events if event.kind == "text_delta") == expected
        assert events[-1].kind == "completed"
    finally:
        for life in reversed(lives):
            life.die()


def test_provider_redacts_http_authentication_and_network_failures(tmp_path: Path) -> None:
    provider = OpenAIProviderBeing(
        base_url="https://test",
        transport=httpx.MockTransport(lambda _request: httpx.Response(401, json={"secret": "leak"})),
    )
    lives = provider_world(tmp_path, provider, "openai")
    try:
        with pytest.raises(ProviderError) as captured:
            tuple(provider.stream(request(), Event()))
        assert captured.value.kind == "authentication"
        assert "leak" not in str(captured.value)
    finally:
        for life in reversed(lives):
            life.die()


def test_provider_requires_configured_key(tmp_path: Path) -> None:
    storage = StorageBeing(data_root=tmp_path / "nokey")
    settings = SettingsBeing()
    provider = OpenAIProviderBeing(base_url="https://test", transport=httpx.MockTransport(lambda _request: httpx.Response(200)))
    world = World(storage, settings, provider)
    lives = [born(storage, world), born(settings, world), born(provider, world)]
    try:
        with pytest.raises(ProviderError, match="not configured"):
            tuple(provider.stream(request(), Event()))
    finally:
        for life in reversed(lives):
            life.die()


def test_defaults_contain_no_local_model_configuration() -> None:
    serialized = json.dumps(DEFAULTS).lower()
    for forbidden in ("gguf", "ollama", "llama.cpp", "vram", "gpu_layers", "huggingface"):
        assert forbidden not in serialized
