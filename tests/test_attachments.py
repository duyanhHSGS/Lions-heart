"""Phase-one attachment storage, retrieval, and cleanup tests."""

# TODO: Add PDF/DOCX fixtures if those formats pass a future parser security audit.

from __future__ import annotations

from pathlib import Path
from threading import Barrier, Thread

import pytest

from cor_being import Life
from cor_beings.attachments import AttachmentBeing, MAX_ATTACHMENT_BYTES
from cor_beings.session import SessionBeing
from cor_beings.storage import StorageBeing


class World:
    name = "attachment-test"
    news = None
    alive = ()

    def __init__(self, *instances: object) -> None:
        self.instances = {type(instance): instance for instance in instances}

    def need(self, being_type):
        try:
            return self.instances[being_type]
        except KeyError as error:
            raise LookupError(being_type) from error


@pytest.fixture
def attachment_platform(tmp_path: Path):
    storage = StorageBeing(data_root=tmp_path / "runtime")
    session = SessionBeing()
    attachments = AttachmentBeing()
    world = World(storage, session, attachments)
    lives: list[Life] = []
    for being in (storage, session, attachments):
        life = Life(being.name)
        being.birth(world, life)  # type: ignore[arg-type]
        lives.append(life)
    try:
        yield storage, session, attachments, lives
    finally:
        for life in reversed(lives):
            life.die()


def test_upload_download_index_search_delete_and_unicode_name(attachment_platform) -> None:
    storage, session, attachments, _lives = attachment_platform
    body = "The moon password is banana. 🦁".encode()
    item = attachments.upload("nhớ-me.md", "text/markdown; charset=utf-8", body,
                              conversation_id=session.conversation_id)
    assert item["file_name"] == "nhớ-me.md"
    assert item["size_bytes"] == len(body)
    assert "storage_name" not in item
    assert storage.fetchone("SELECT extracted_text FROM attachments WHERE id=?", (item["id"],))[0].startswith("The moon")
    assert attachments.list(conversation_id=session.conversation_id) == (item,)
    metadata, downloaded = attachments.download(str(item["id"]))
    assert metadata == item
    assert downloaded == body
    matches = attachments.search("banana?!", conversation_id=session.conversation_id)
    assert matches[0]["id"] == item["id"]
    assert "banana" in matches[0]["snippet"]
    attachments.delete(str(item["id"]))
    assert attachments.list(conversation_id=session.conversation_id) == ()
    with pytest.raises(LookupError):
        attachments.download(str(item["id"]))


def test_empty_and_duplicate_uploads_are_safe_and_deduplicated(attachment_platform) -> None:
    storage, session, attachments, _lives = attachment_platform
    first = attachments.upload("empty.txt", "text/plain", b"", conversation_id=session.conversation_id)
    duplicate = attachments.upload("another.txt", "text/plain", b"", conversation_id=session.conversation_id)
    assert duplicate == first
    assert storage.fetchone("SELECT count(*) FROM attachments")[0] == 1
    assert len(tuple((storage.data_root / "attachments").iterdir())) == 1


@pytest.mark.parametrize("name", ["../secret.txt", "folder/file.txt", "folder\\file.txt", "..", "bad\x00.txt"])
def test_upload_rejects_unsafe_names(attachment_platform, name: str) -> None:
    _storage, session, attachments, _lives = attachment_platform
    with pytest.raises(ValueError, match="unsafe"):
        attachments.upload(name, "text/plain", b"hello", conversation_id=session.conversation_id)


@pytest.mark.parametrize(
    ("name", "mime", "body", "message"),
    [
        ("fake.png", "text/plain", b"hello", "extension"),
        ("fake.txt", "image/png", b"hello", "only UTF-8"),
        ("binary.txt", "text/plain", b"a\x00b", "binary"),
        ("broken.txt", "text/plain", b"\xff", "UTF-8"),
    ],
)
def test_upload_rejects_misleading_or_invalid_content(attachment_platform, name, mime, body, message) -> None:
    _storage, session, attachments, _lives = attachment_platform
    with pytest.raises(ValueError, match=message):
        attachments.upload(name, mime, body, conversation_id=session.conversation_id)


def test_upload_rejects_oversize_before_writing(attachment_platform) -> None:
    storage, session, attachments, _lives = attachment_platform
    with pytest.raises(ValueError, match="too large"):
        attachments.upload("huge.txt", "text/plain", b"x" * (MAX_ATTACHMENT_BYTES + 1),
                           conversation_id=session.conversation_id)
    assert tuple((storage.data_root / "attachments").iterdir()) == ()


def test_temporary_attachment_never_enters_database_and_dies_with_life(attachment_platform) -> None:
    storage, session, attachments, lives = attachment_platform
    temporary_id = session.new_conversation(temporary=True)
    item = attachments.upload("ghost.txt", "text/plain", b"temporary secret",
                              conversation_id=temporary_id, temporary=True)
    assert storage.fetchone("SELECT count(*) FROM attachments")[0] == 0
    path = next((storage.data_root / "attachments").iterdir())
    assert path.exists()
    assert attachments.list(conversation_id=temporary_id) == (item,)
    lives[-1].die()
    assert not path.exists()
    with pytest.raises(RuntimeError, match="not alive"):
        attachments.list(conversation_id=temporary_id)


def test_search_is_scoped_and_has_deterministic_limits(attachment_platform) -> None:
    _storage, session, attachments, _lives = attachment_platform
    first_id = session.conversation_id
    attachments.upload("one.txt", "text/plain", b"private aardvark", conversation_id=first_id)
    second_id = session.new_conversation()
    attachments.upload("two.txt", "text/plain", b"public aardvark", conversation_id=second_id)
    assert [row["file_name"] for row in attachments.search("aardvark", conversation_id=second_id)] == ["two.txt"]
    assert attachments.search("!!!", conversation_id=second_id) == ()
    with pytest.raises(ValueError, match="top_k"):
        attachments.search("aardvark", conversation_id=second_id, top_k=99)


def test_missing_scope_and_corrupt_or_symlink_content_are_denied(attachment_platform) -> None:
    storage, session, attachments, _lives = attachment_platform
    with pytest.raises(LookupError, match="conversation"):
        attachments.upload("nope.txt", "text/plain", b"x", conversation_id="missing")
    item = attachments.upload("okay.txt", "text/plain", b"safe", conversation_id=session.conversation_id)
    row = storage.fetchone("SELECT storage_name FROM attachments WHERE id=?", (item["id"],))
    path = storage.data_root / "attachments" / row["storage_name"]
    path.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="corrupt"):
        attachments.download(str(item["id"]))


def test_symlink_content_is_denied_before_read(attachment_platform, monkeypatch) -> None:
    storage, session, attachments, _lives = attachment_platform
    item = attachments.upload("safe.txt", "text/plain", b"safe", conversation_id=session.conversation_id)
    row = storage.fetchone("SELECT storage_name FROM attachments WHERE id=?", (item["id"],))
    target = storage.data_root / "attachments" / row["storage_name"]
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == target or original(path))
    with pytest.raises(RuntimeError, match="unsafe"):
        attachments.download(str(item["id"]))


def test_concurrent_delete_executes_once_without_leaving_bytes(attachment_platform) -> None:
    storage, session, attachments, _lives = attachment_platform
    item = attachments.upload("race.txt", "text/plain", b"one winner", conversation_id=session.conversation_id)
    gate = Barrier(3)
    outcomes: list[str] = []

    def remove() -> None:
        gate.wait()
        try:
            attachments.delete(str(item["id"]))
            outcomes.append("deleted")
        except LookupError:
            outcomes.append("missing")

    threads = [Thread(target=remove), Thread(target=remove)]
    for thread in threads:
        thread.start()
    gate.wait()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["deleted", "missing"]
    assert tuple((storage.data_root / "attachments").iterdir()) == ()
