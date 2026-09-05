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
    OpenAICompatibleProviderBeing,
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
    updated = settings.update({"theme": "dark", "retention_days": 30, "default_provider": "gemini", "send_on_enter": False, "notify_on_completion": True})
    assert updated["theme"] == "dark"
    assert updated["send_on_enter"] is False
    assert updated["notify_on_completion"] is True
    with pytest.raises(ValueError, match="unknown setting"):
        settings.update({"gpu_layers": 99})
    with pytest.raises(ValueError, match="positive integer"):
        settings.update({"retention_days": 0})
    with pytest.raises(ValueError, match="fixed to dark"):
        settings.update({"theme": "light"})
    with pytest.raises(ValueError, match="fixed to dark"):
        settings.update({"theme": "system"})

    replacement = SettingsBeing()
    replacement_life = born(replacement, World(storage, replacement))
    try:
        assert replacement.values["theme"] == "dark"
        assert replacement.values["default_provider"] == "gemini"
        assert replacement.values["send_on_enter"] is False
        assert replacement.values["notify_on_completion"] is True
    finally:
        replacement_life.die()


@pytest.mark.parametrize("legacy_theme", ["light", "system"])
def test_settings_birth_repairs_legacy_theme_to_dark(platform, legacy_theme: str) -> None:
    storage = platform[0]
    storage.execute(
        "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, 1) "
        "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
        ("theme", json.dumps(legacy_theme)),
    )

    replacement = SettingsBeing()
    replacement_life = born(replacement, World(storage, replacement))
    try:
        assert replacement.values["theme"] == "dark"
        row = storage.fetchone("SELECT value_json FROM settings WHERE key='theme'")
        assert row is not None
        assert json.loads(row["value_json"]) == "dark"
    finally:
        replacement_life.die()


@pytest.mark.parametrize("name", ["send_on_enter", "notify_on_completion"])
@pytest.mark.parametrize("invalid", [0, 1, "true", None, [], {}])
def test_settings_boolean_preferences_reject_bool_lookalikes(platform, name, invalid) -> None:
    _storage, settings, _auth, _world = platform
    with pytest.raises(ValueError, match=f"{name} must be a boolean"):
        settings.update({name: invalid})


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


def test_provider_surfaces_bounded_structured_error_message(tmp_path: Path) -> None:
    message = (
        "No model loaded. Call POST /inference/load first. "
        "Or enable Model auto-switch (Settings > API)."
    )
    provider = OpenAIProviderBeing(
        base_url="https://test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(400, json={"error": {"message": message}})
        ),
    )
    lives = provider_world(tmp_path, provider, "openai")
    try:
        with pytest.raises(ProviderError) as captured:
            tuple(provider.stream(request(), Event()))
        assert captured.value.kind == "bad_request"
        assert str(captured.value) == f"Provider returned: {message}"
    finally:
        for life in reversed(lives):
            life.die()


def test_provider_redacts_secrets_and_urls_from_structured_error_message(tmp_path: Path) -> None:
    provider = OpenAIProviderBeing(
        base_url="https://test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                400,
                json={
                    "error": {
                        "message": "API key test-secret failed at https://private.example/path"
                    }
                },
            )
        ),
    )
    lives = provider_world(tmp_path, provider, "openai")
    try:
        with pytest.raises(ProviderError) as captured:
            tuple(provider.stream(request(), Event()))
        text = str(captured.value)
        assert "test-secret" not in text
        assert "private.example" not in text
        assert "[redacted-secret]" in text
        assert "[redacted-url]" in text
    finally:
        for life in reversed(lives):
            life.die()


def test_provider_rejects_oversized_or_unstructured_error_details(tmp_path: Path) -> None:
    responses = iter(
        (
            httpx.Response(400, json={"error": {"message": "x" * 20_000}}),
            httpx.Response(400, json={"secret": "never show me"}),
        )
    )
    provider = OpenAIProviderBeing(
        base_url="https://test",
        transport=httpx.MockTransport(lambda _request: next(responses)),
    )
    lives = provider_world(tmp_path, provider, "openai")
    try:
        for _ in range(2):
            with pytest.raises(ProviderError) as captured:
                tuple(provider.stream(request(), Event()))
            assert str(captured.value) == "openai request failed with HTTP 400"
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


def custom_registry(tmp_path: Path):
    storage = StorageBeing(data_root=tmp_path / "custom-registry")
    settings = SettingsBeing()
    openai = OpenAIProviderBeing()
    anthropic = AnthropicProviderBeing()
    gemini = GeminiProviderBeing()
    registry = ProviderRegistryBeing()
    world = World(storage, settings, openai, anthropic, gemini, registry)
    lives = [born(storage, world), born(settings, world), born(openai, world), born(anthropic, world), born(gemini, world), born(registry, world)]
    return storage, settings, registry, lives


def test_generic_provider_connection_crud_encrypts_and_activates(tmp_path: Path) -> None:
    storage, settings, registry, lives = custom_registry(tmp_path)
    try:
        created = registry.create_connection("  My   Gateway  ", "https://gateway.example/v1/", ["fast", "fast", "smart"], "super-secret-key")
        provider_id = str(created["id"])
        assert created == {
            "id": provider_id, "display_name": "My Gateway", "protocol": "openai_compatible",
            "base_url": "https://gateway.example/v1", "models": ["fast", "smart"],
            "enabled": True, "built_in": False, "revision": 1, "configured": True,
        }
        assert provider_id in registry.names
        assert settings.provider_key(provider_id) == "super-secret-key"
        row = storage.fetchone("SELECT ciphertext FROM provider_secrets WHERE provider=?", (provider_id,))
        assert row is not None and b"super-secret-key" not in row["ciphertext"]

        settings.update({"default_provider": provider_id})
        updated = registry.update_connection(provider_id, display_name="Gateway 2", models=["smart"], enabled=False, clear_secret=True, revision=1)
        assert updated["revision"] == 2
        assert updated["enabled"] is False
        assert provider_id not in registry.names
        assert settings.provider_key(provider_id) is None
        assert settings.values["default_provider"] == "openai"
        with pytest.raises(RuntimeError, match="changed"):
            registry.update_connection(provider_id, enabled=True, revision=1)

        registry.delete_connection(provider_id)
        assert all(item["id"] != provider_id for item in registry.list_connections())
        with pytest.raises(LookupError):
            registry.get(provider_id)
    finally:
        for life in reversed(lives):
            life.die()


def test_selecting_custom_provider_infers_its_only_declared_model(tmp_path: Path) -> None:
    _storage, settings, registry, lives = custom_registry(tmp_path)
    try:
        created = registry.create_connection(
            "One Model", "https://one.example/v1", ["sloth"], "secret"
        )
        values = settings.update(
            {"default_provider": created["id"], "default_text_model": ""}
        )
        assert values["default_provider"] == created["id"]
        assert values["default_text_model"] == "sloth"
    finally:
        for life in reversed(lives):
            life.die()


def test_selecting_custom_provider_never_guesses_between_multiple_models(tmp_path: Path) -> None:
    _storage, settings, registry, lives = custom_registry(tmp_path)
    try:
        created = registry.create_connection(
            "Many Models", "https://many.example/v1", ["fast", "smart"], None
        )
        values = settings.update(
            {"default_provider": created["id"], "default_text_model": ""}
        )
        assert values["default_text_model"] == ""
    finally:
        for life in reversed(lives):
            life.die()


def test_settings_birth_repairs_existing_single_model_provider_selection(tmp_path: Path) -> None:
    data_root = tmp_path / "custom-registry"
    storage, _settings, registry, lives = custom_registry(tmp_path)
    created = registry.create_connection(
        "Existing Provider", "https://existing.example/v1", ["only-model"], None
    )
    storage.execute(
        "INSERT INTO settings(key,value_json,updated_at) VALUES (?,?,0) "
        "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
        ("default_provider", json.dumps(created["id"])),
    )
    storage.execute(
        "INSERT INTO settings(key,value_json,updated_at) VALUES (?,?,0) "
        "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
        ("default_text_model", json.dumps("")),
    )
    for life in reversed(lives):
        life.die()

    replacement_storage = StorageBeing(data_root=data_root)
    replacement_settings = SettingsBeing()
    replacement_world = World(replacement_storage, replacement_settings)
    replacement_lives = [
        born(replacement_storage, replacement_world),
        born(replacement_settings, replacement_world),
    ]
    try:
        assert replacement_settings.values["default_provider"] == created["id"]
        assert replacement_settings.values["default_text_model"] == "only-model"
        row = replacement_storage.fetchone(
            "SELECT value_json FROM settings WHERE key='default_text_model'"
        )
        assert row is not None and json.loads(row["value_json"]) == "only-model"
    finally:
        for life in reversed(replacement_lives):
            life.die()


@pytest.mark.parametrize("url", ("http://example.com/v1", "https://user:pass@example.com", "https://127.0.0.1/v1", "https://example.com/v1?key=oops"))
def test_generic_provider_rejects_unsafe_urls_before_persistence(tmp_path: Path, url: str) -> None:
    storage, _settings, registry, lives = custom_registry(tmp_path)
    try:
        with pytest.raises(ValueError, match="base_url"):
            registry.create_connection("Unsafe", url, [], None)
        assert storage.fetchone("SELECT id FROM provider_connections") is None
    finally:
        for life in reversed(lives):
            life.die()


def test_generic_provider_rejects_duplicate_names_and_bad_models(tmp_path: Path) -> None:
    _storage, _settings, registry, lives = custom_registry(tmp_path)
    try:
        registry.create_connection("Gateway", "https://one.example/v1", [], None)
        with pytest.raises(ValueError, match="already exists"):
            registry.create_connection("gateway", "https://two.example/v1", [], None)
        with pytest.raises(ValueError, match="model ID"):
            registry.create_connection("Bad models", "https://two.example/v1", [""], None)
        with pytest.raises(ValueError, match="built-in"):
            registry.delete_connection("openai")
    finally:
        for life in reversed(lives):
            life.die()


def test_openai_compatible_stream_normalizes_fragmented_tools_reasoning_and_usage(tmp_path: Path) -> None:
    sse = """data: {"choices":[{"delta":{"content":"Hi","reasoning_content":"hmm","tool_calls":[{"index":0,"id":"call_","function":{"name":"re","arguments":"{\\"pa"}}]}}]}\n\ndata: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"1","function":{"name":"ad","arguments":"th\\":\\"x\\"}"}}]}}],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\ndata: [DONE]\n\n"""

    def handler(incoming: httpx.Request) -> httpx.Response:
        assert incoming.url.path.endswith("/chat/completions")
        assert incoming.headers["authorization"] == "Bearer test-secret"
        payload = json.loads(incoming.content)
        assert payload["messages"][0] == {"role": "system", "content": "Be Lion"}
        assert payload["tools"][0]["function"]["name"] == "read"
        return httpx.Response(200, text=sse, headers={"Content-Type": "text/event-stream"})

    provider_id = "custom_test"
    provider = OpenAICompatibleProviderBeing(provider_id, "https://test/v1", transport=httpx.MockTransport(handler))
    lives = provider_world(tmp_path, provider, provider_id)
    try:
        events = tuple(provider.stream(request(provider_id), Event()))
        assert [event.kind for event in events] == ["start", "text_delta", "reasoning_delta", "usage", "tool_call", "completed"]
        tool = next(event for event in events if event.kind == "tool_call")
        assert dict(tool.data) == {"id": "call_1", "name": "read", "arguments": '{"path":"x"}'}
    finally:
        for life in reversed(lives):
            life.die()


def test_openai_compatible_connection_allows_an_explicitly_keyless_gateway(tmp_path: Path) -> None:
    def handler(incoming: httpx.Request) -> httpx.Response:
        assert "authorization" not in incoming.headers
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"keyless"}}]}\n\ndata: [DONE]\n\n')

    storage = StorageBeing(data_root=tmp_path / "keyless")
    settings = SettingsBeing()
    provider = OpenAICompatibleProviderBeing("custom_keyless", "https://gateway.example/v1", transport=httpx.MockTransport(handler))
    world = World(storage, settings, provider)
    lives = [born(storage, world), born(settings, world), born(provider, world)]
    try:
        events = tuple(provider.stream(request("custom_keyless"), Event()))
        assert next(event.data["text"] for event in events if event.kind == "text_delta") == "keyless"
    finally:
        for life in reversed(lives):
            life.die()


def test_defaults_contain_no_local_model_configuration() -> None:
    serialized = json.dumps(DEFAULTS).lower()
    for forbidden in ("gguf", "ollama", "llama.cpp", "vram", "gpu_layers", "huggingface"):
        assert forbidden not in serialized
