"""Bounded, fenced attachment storage and lexical retrieval for Lions-heart."""

from __future__ import annotations

import hashlib
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from uuid import uuid4

from cor_being import Being, Life, World
from cor_beings.storage import StorageBeing


MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_EXTRACTED_CHARS = 256 * 1024
MAX_FILE_NAME_CHARS = 255
MAX_RETRIEVAL_RESULTS = 8
MAX_SNIPPET_CHARS = 1_200
_TEXT_MIMES = {"text/plain", "text/markdown", "text/x-markdown"}
_TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class Attachment:
    id: str
    project_id: str | None
    conversation_id: str | None
    file_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    created_at: int
    temporary: bool = False

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "conversation_id": self.conversation_id,
            "file_name": self.file_name,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "created_at": self.created_at,
            "temporary": self.temporary,
        }


class AttachmentBeing(Being):
    """Own raw attachment files while StorageBeing owns all durable metadata."""

    name = "attachments"
    needs = (StorageBeing,)

    def __init__(self) -> None:
        self._storage: StorageBeing | None = None
        self._root: Path | None = None
        self._temporary: dict[str, tuple[Attachment, Path, str]] = {}
        self._lock = RLock()

    def birth(self, world: World, life: Life) -> None:
        storage = world.need(StorageBeing)
        root = storage.data_root / "attachments"
        root.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve(strict=True)
        if root.is_symlink() or not resolved_root.is_relative_to(storage.data_root.resolve(strict=True)):
            raise RuntimeError("attachment storage root is not safely fenced")
        self._storage = storage
        self._root = resolved_root
        self._prune_orphans()
        life.on_death(self._close)
        # TODO: Add audited PDF/DOCX parsers only with page, archive, and time limits.

    def upload(
        self,
        file_name: str,
        mime_type: str,
        data: bytes,
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
        temporary: bool = False,
    ) -> dict[str, object]:
        name = self._safe_name(file_name)
        normalized_mime = mime_type.split(";", 1)[0].strip().lower()
        if normalized_mime not in _TEXT_MIMES:
            raise ValueError("only UTF-8 plain text and Markdown files are supported")
        if Path(name).suffix.lower() not in _TEXT_SUFFIXES:
            raise ValueError("file extension does not match an allowed text format")
        if not isinstance(data, bytes):
            raise TypeError("attachment data must be bytes")
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ValueError("attachment is too large")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("attachment must contain valid UTF-8 text") from error
        if "\x00" in text:
            raise ValueError("attachment contains binary data")
        extracted = text[:MAX_EXTRACTED_CHARS]
        digest = hashlib.sha256(data).hexdigest()
        now = int(time.time())
        storage = self._require_storage()
        if not temporary:
            if conversation_id is None and project_id is None:
                raise ValueError("a durable attachment needs a conversation or project")
            self._validate_scope(storage, conversation_id, project_id)
            duplicate = storage.fetchone(
                "SELECT * FROM attachments WHERE sha256=? AND conversation_id IS ? AND project_id IS ?",
                (digest, conversation_id, project_id),
            )
            if duplicate is not None:
                return self._from_row(duplicate).public()

        attachment_id = uuid4().hex
        storage_name = f"{attachment_id}.blob"
        path = self._path_for(storage_name)
        self._write_exclusive(path, data)
        attachment = Attachment(
            attachment_id, project_id, conversation_id, name, normalized_mime,
            len(data), digest, now, temporary,
        )
        try:
            with self._lock:
                if temporary:
                    self._temporary[attachment_id] = (attachment, path, extracted)
                else:
                    with storage.transaction() as connection:
                        connection.execute(
                            "INSERT INTO attachments(id, project_id, conversation_id, file_name, mime_type, "
                            "size_bytes, sha256, storage_name, extracted_text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (attachment_id, project_id, conversation_id, name, normalized_mime,
                             len(data), digest, storage_name, extracted, now),
                        )
                        connection.execute(
                            "INSERT INTO attachment_search(attachment_id, project_id, body) VALUES (?, ?, ?)",
                            (attachment_id, project_id or "", extracted),
                        )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return attachment.public()

    def list(self, *, conversation_id: str | None = None, project_id: str | None = None) -> tuple[dict[str, object], ...]:
        storage = self._require_storage()
        clauses: list[str] = []
        values: list[object] = []
        if conversation_id is not None:
            clauses.append("conversation_id=?")
            values.append(conversation_id)
        if project_id is not None:
            clauses.append("project_id=?")
            values.append(project_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = storage.fetchall(f"SELECT * FROM attachments{where} ORDER BY created_at, id", values)
        durable = [self._from_row(row).public() for row in rows]
        with self._lock:
            ephemeral = [
                item.public() for item, _path, _text in self._temporary.values()
                if (conversation_id is None or item.conversation_id == conversation_id)
                and (project_id is None or item.project_id == project_id)
            ]
        return tuple(durable + ephemeral)

    def download(self, attachment_id: str) -> tuple[dict[str, object], bytes]:
        with self._lock:
            temporary = self._temporary.get(attachment_id)
            if temporary is not None:
                item, path, _text = temporary
                return item.public(), self._read_bounded(path, item.size_bytes)
        row = self._require_storage().fetchone("SELECT * FROM attachments WHERE id=?", (attachment_id,))
        if row is None:
            raise LookupError("attachment not found")
        item = self._from_row(row)
        return item.public(), self._read_bounded(self._path_for(row["storage_name"]), item.size_bytes)

    def delete(self, attachment_id: str) -> None:
        with self._lock:
            temporary = self._temporary.pop(attachment_id, None)
            if temporary is not None:
                temporary[1].unlink(missing_ok=True)
                return
            storage = self._require_storage()
            with storage.transaction() as connection:
                row = connection.execute("SELECT storage_name FROM attachments WHERE id=?", (attachment_id,)).fetchone()
                if row is None:
                    raise LookupError("attachment not found")
                connection.execute("DELETE FROM attachment_search WHERE attachment_id=?", (attachment_id,))
                connection.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
            self._path_for(row["storage_name"]).unlink(missing_ok=True)

    def clear_temporary(self, conversation_id: str | None = None) -> None:
        with self._lock:
            ids = [key for key, (item, _path, _text) in self._temporary.items()
                   if conversation_id is None or item.conversation_id == conversation_id]
            for key in ids:
                _item, path, _text = self._temporary.pop(key)
                path.unlink(missing_ok=True)

    def search(self, query: str, *, conversation_id: str, project_id: str | None = None,
               top_k: int = 4) -> tuple[dict[str, object], ...]:
        if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= MAX_RETRIEVAL_RESULTS:
            raise ValueError("top_k is outside the allowed range")
        tokens = tuple(dict.fromkeys(word.casefold() for word in _WORD.findall(query)))[:16]
        if not tokens:
            return ()
        match = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        scope_sql = "a.conversation_id=?"
        parameters: list[object] = [match, conversation_id]
        if project_id is not None:
            scope_sql = "(a.conversation_id=? OR a.project_id=?)"
            parameters.append(project_id)
        parameters.append(top_k)
        rows = self._require_storage().fetchall(
            "SELECT a.id, a.file_name, a.mime_type, a.extracted_text, bm25(attachment_search) AS score "
            "FROM attachment_search JOIN attachments a ON a.id=attachment_search.attachment_id "
            f"WHERE attachment_search MATCH ? AND {scope_sql} ORDER BY score, a.id LIMIT ?",
            parameters,
        )
        results = [self._snippet(dict(row), tokens) for row in rows]
        with self._lock:
            for item, _path, text in self._temporary.values():
                if item.conversation_id != conversation_id:
                    continue
                folded = text.casefold()
                hits = sum(folded.count(token) for token in tokens)
                if hits:
                    results.append({"id": item.id, "file_name": item.file_name,
                                    "mime_type": item.mime_type, "snippet": text[:MAX_SNIPPET_CHARS],
                                    "score": -float(hits)})
        return tuple(sorted(results, key=lambda row: (float(row["score"]), str(row["id"])))[:top_k])

    def context_for(self, query: str, *, conversation_id: str, project_id: str | None = None) -> tuple[dict[str, object], ...]:
        if project_id is None:
            row = self._require_storage().fetchone(
                "SELECT project_id FROM conversations WHERE id=?", (conversation_id,)
            )
            if row is not None:
                project_id = row["project_id"]
        return tuple({"id": row["id"], "name": row["file_name"], "text": row["snippet"]}
                     for row in self.search(query, conversation_id=conversation_id, project_id=project_id))

    @staticmethod
    def _snippet(row: dict[str, object], tokens: tuple[str, ...]) -> dict[str, object]:
        text = str(row.pop("extracted_text") or "")
        folded = text.casefold()
        starts = [folded.find(token) for token in tokens if token in folded]
        start = max(0, (min(starts) if starts else 0) - 160)
        row["snippet"] = text[start:start + MAX_SNIPPET_CHARS]
        return row

    @staticmethod
    def _safe_name(file_name: str) -> str:
        if not isinstance(file_name, str):
            raise TypeError("file name must be a string")
        name = unicodedata.normalize("NFC", file_name).strip()
        if (not name or len(name) > MAX_FILE_NAME_CHARS or name in {".", ".."}
                or "/" in name or "\\" in name or any(ord(char) < 32 for char in name)):
            raise ValueError("file name is unsafe")
        return name

    @staticmethod
    def _validate_scope(storage: StorageBeing, conversation_id: str | None, project_id: str | None) -> None:
        if conversation_id is not None and storage.fetchone("SELECT 1 FROM conversations WHERE id=?", (conversation_id,)) is None:
            raise LookupError("conversation not found")
        if project_id is not None and storage.fetchone("SELECT 1 FROM projects WHERE id=?", (project_id,)) is None:
            raise LookupError("project not found")

    def _path_for(self, storage_name: str) -> Path:
        root = self._root
        if root is None or not re.fullmatch(r"[0-9a-f]{32}\.blob", storage_name):
            raise RuntimeError("unsafe attachment storage reference")
        path = root / storage_name
        if not path.parent.resolve(strict=True) == root:
            raise RuntimeError("attachment path escaped storage root")
        return path

    @staticmethod
    def _write_exclusive(path: Path, data: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _read_bounded(path: Path, expected: int) -> bytes:
        if path.is_symlink():
            raise RuntimeError("attachment file is unsafe")
        data = path.read_bytes()
        if len(data) != expected or len(data) > MAX_ATTACHMENT_BYTES:
            raise RuntimeError("attachment file is missing or corrupt")
        return data

    @staticmethod
    def _from_row(row: object) -> Attachment:
        return Attachment(row["id"], row["project_id"], row["conversation_id"], row["file_name"],
                          row["mime_type"], row["size_bytes"], row["sha256"], row["created_at"], False)

    def _require_storage(self) -> StorageBeing:
        if self._storage is None:
            raise RuntimeError("attachments are not alive")
        return self._storage

    def _prune_orphans(self) -> None:
        root = self._root
        storage = self._require_storage()
        if root is None:
            return
        referenced = {
            str(row["storage_name"])
            for row in storage.fetchall("SELECT storage_name FROM attachments")
        }
        for path in root.iterdir():
            if path.is_file() and not path.is_symlink() and re.fullmatch(r"[0-9a-f]{32}\.blob", path.name):
                if path.name not in referenced:
                    path.unlink(missing_ok=True)

    def _close(self) -> None:
        self.clear_temporary()
        self._storage = None
        self._root = None


__all__ = ["Attachment", "AttachmentBeing", "MAX_ATTACHMENT_BYTES", "MAX_EXTRACTED_CHARS"]
