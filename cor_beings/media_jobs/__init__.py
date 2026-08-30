"""Durable, provider-neutral cloud media job ownership."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import json
import mimetypes
import os
from pathlib import Path
from threading import Event, RLock, Thread, current_thread
import time
from types import MappingProxyType
from uuid import uuid4

from cor_being import Being, Life, World
from cor_beings.storage import StorageBeing


MAX_OUTPUT_BYTES = 1024 * 1024 * 1024
TERMINAL_STATES = frozenset({"completed", "cancelled", "failed"})


@dataclass(frozen=True, slots=True)
class MediaRequest:
    id: str
    kind: str
    provider: str
    model: str
    prompt: str
    settings: Mapping[str, object]
    remote_id: str | None = None


@dataclass(frozen=True, slots=True)
class MediaResult:
    content: bytes | Iterable[bytes]
    mime_type: str
    extension: str
    remote_id: str | None = None


MediaRunner = Callable[[MediaRequest, Event, Callable[[int], None]], MediaResult]


class MediaJobBeing(Being):
    """Persist and execute bounded image, audio, and video generation jobs."""

    name = "media_jobs"
    needs = (StorageBeing,)

    def __init__(self, *, runner: MediaRunner | None = None) -> None:
        self._runner = runner
        self._storage: StorageBeing | None = None
        self._root: Path | None = None
        self._lock = RLock()
        self._workers: dict[str, tuple[Thread, Event]] = {}

    def birth(self, world: World, life: Life) -> None:
        storage = world.need(StorageBeing)
        root = storage.data_root / "media"
        root.mkdir(parents=True, exist_ok=True)
        self._storage = storage
        self._root = root.resolve()
        # A dead process cannot safely guess what a provider did. Make uncertainty explicit.
        storage.execute(
            "UPDATE media_jobs SET status='failed', error_kind='restart_interrupted', "
            "updated_at=?, revision=revision+1 WHERE status IN ('queued','running','cancelling')",
            (int(time.time()),),
        )
        life.on_death(self._stop)
        # TODO: Add provider-specific remote reconciliation for resumable remote IDs.

    def submit(
        self, kind: str, provider: str, model: str, prompt: str,
        settings: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        clean_kind = _choice(kind, "kind", {"image", "audio", "video"})
        clean_provider = _text(provider, "provider", 64)
        clean_model = _text(model, "model", 160)
        clean_prompt = _text(prompt, "prompt", 20_000)
        safe_settings = _json_mapping(settings or {}, "settings", max_bytes=32_768)
        if self._runner is None:
            raise RuntimeError("media generation provider is not configured")
        job_id = uuid4().hex
        now = int(time.time())
        storage = self._require_storage()
        storage.execute(
            "INSERT INTO media_jobs(id,kind,provider,model,status,prompt,settings_json,progress,created_at,updated_at) "
            "VALUES (?,?,?,?, 'queued', ?, ?, 0, ?, ?)",
            (job_id, clean_kind, clean_provider, clean_model, clean_prompt,
             json.dumps(safe_settings, ensure_ascii=False, separators=(",", ":")), now, now),
        )
        cancel = Event()
        worker = Thread(target=self._run, args=(job_id, cancel), name=f"lion-media-{job_id[:8]}", daemon=False)
        with self._lock:
            self._workers[job_id] = (worker, cancel)
        worker.start()
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, object]:
        row = self._require_storage().fetchone(
            "SELECT id,kind,provider,model,status,prompt,settings_json,remote_id,progress,output_name,"
            "output_mime,output_bytes,error_kind,revision,created_at,updated_at FROM media_jobs WHERE id=?",
            (job_id,),
        )
        if row is None:
            raise LookupError("media job not found")
        item = dict(row)
        item["settings"] = json.loads(item.pop("settings_json"))
        return item

    def list(self, *, kind: str | None = None, status: str | None = None,
             limit: int = 50, before: tuple[int, str] | None = None) -> tuple[dict[str, object], ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be from 1 through 100")
        clauses: list[str] = []
        values: list[object] = []
        if kind is not None:
            clauses.append("kind=?"); values.append(_choice(kind, "kind", {"image", "audio", "video"}))
        if status is not None:
            clauses.append("status=?"); values.append(_choice(status, "status", TERMINAL_STATES | {"queued", "running", "cancelling"}))
        if before is not None:
            clauses.append("(created_at < ? OR (created_at=? AND id < ?))")
            values.extend((before[0], before[0], before[1]))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        values.append(limit)
        rows = self._require_storage().fetchall(
            "SELECT id,kind,provider,model,status,progress,output_mime,output_bytes,error_kind,revision,"
            f"created_at,updated_at FROM media_jobs{where} ORDER BY created_at DESC,id DESC LIMIT ?", values,
        )
        return tuple(dict(row) for row in rows)

    def cancel(self, job_id: str) -> dict[str, object]:
        storage = self._require_storage()
        row = storage.fetchone("SELECT status FROM media_jobs WHERE id=?", (job_id,))
        if row is None:
            raise LookupError("media job not found")
        if row["status"] in TERMINAL_STATES:
            return self.get(job_id)
        with self._lock:
            pair = self._workers.get(job_id)
            if pair is not None:
                pair[1].set()
        storage.execute(
            "UPDATE media_jobs SET status='cancelling',updated_at=?,revision=revision+1 "
            "WHERE id=? AND status IN ('queued','running')", (int(time.time()), job_id),
        )
        return self.get(job_id)

    def download(self, job_id: str) -> tuple[dict[str, object], Path]:
        item = self.get(job_id)
        if item["status"] != "completed" or not item["output_name"]:
            raise RuntimeError("media output is unavailable")
        path = self._safe_output(str(item["output_name"]), must_exist=True)
        return item, path

    def delete(self, job_id: str) -> None:
        item = self.get(job_id)
        if item["status"] not in TERMINAL_STATES:
            raise RuntimeError("cancel the media job before deleting it")
        path = self._safe_output(str(item["output_name"]), must_exist=False) if item["output_name"] else None
        self._require_storage().execute("DELETE FROM media_jobs WHERE id=?", (job_id,))
        if path is not None:
            try: path.unlink()
            except FileNotFoundError: pass

    def _run(self, job_id: str, cancel: Event) -> None:
        storage = self._require_storage()
        try:
            changed = storage.execute(
                "UPDATE media_jobs SET status='running',updated_at=?,revision=revision+1 "
                "WHERE id=? AND status='queued'", (int(time.time()), job_id),
            ).rowcount
            if changed != 1:
                return
            item = self.get(job_id)
            request = MediaRequest(job_id, str(item["kind"]), str(item["provider"]), str(item["model"]),
                                   str(item["prompt"]), MappingProxyType(dict(item["settings"])), item["remote_id"])
            result = self._runner(request, cancel, lambda value: self._progress(job_id, value))  # type: ignore[misc]
            if cancel.is_set():
                self._finish_cancel(job_id); return
            self._store_result(request, result, cancel)
        except BaseException as error:
            kind = "cancelled" if cancel.is_set() else type(error).__name__[:80]
            state = "cancelled" if cancel.is_set() else "failed"
            storage.execute(
                "UPDATE media_jobs SET status=?,error_kind=?,updated_at=?,revision=revision+1 "
                "WHERE id=? AND status NOT IN ('completed','cancelled','failed')",
                (state, kind, int(time.time()), job_id),
            )
        finally:
            with self._lock: self._workers.pop(job_id, None)

    def _progress(self, job_id: str, value: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 99:
            raise ValueError("media progress must be from 0 through 99")
        self._require_storage().execute(
            "UPDATE media_jobs SET progress=MAX(progress,?),updated_at=?,revision=revision+1 "
            "WHERE id=? AND status='running'", (value, int(time.time()), job_id),
        )

    def _store_result(self, request: MediaRequest, result: MediaResult, cancel: Event) -> None:
        if not isinstance(result, MediaResult):
            raise TypeError("media runner must return MediaResult")
        mime = _text(result.mime_type, "output MIME type", 120)
        extension = result.extension.lower().lstrip(".")
        if not extension.isalnum() or len(extension) > 10:
            raise ValueError("media output extension is invalid")
        name = f"{request.id}.{extension}"
        path = self._safe_output(name, must_exist=False)
        temporary = path.with_suffix(path.suffix + ".part")
        total = 0
        chunks = (result.content,) if isinstance(result.content, bytes) else result.content
        try:
            with temporary.open("xb") as handle:
                for chunk in chunks:
                    if cancel.is_set(): raise RuntimeError("cancelled")
                    if not isinstance(chunk, bytes) or not chunk: raise ValueError("media output contains an invalid chunk")
                    total += len(chunk)
                    if total > MAX_OUTPUT_BYTES: raise ValueError("media output is too large")
                    handle.write(chunk)
                handle.flush(); os.fsync(handle.fileno())
            if total == 0: raise ValueError("media output is empty")
            os.replace(temporary, path)
            changed = self._require_storage().execute(
                "UPDATE media_jobs SET status='completed',progress=100,output_name=?,output_mime=?,"
                "output_bytes=?,remote_id=?,error_kind=NULL,updated_at=?,revision=revision+1 "
                "WHERE id=? AND status='running'",
                (name, mime, total, result.remote_id, int(time.time()), request.id),
            ).rowcount
            if changed != 1: path.unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)

    def _finish_cancel(self, job_id: str) -> None:
        self._require_storage().execute(
            "UPDATE media_jobs SET status='cancelled',error_kind=NULL,updated_at=?,revision=revision+1 "
            "WHERE id=? AND status NOT IN ('completed','failed')", (int(time.time()), job_id),
        )

    def _safe_output(self, name: str, *, must_exist: bool) -> Path:
        root = self._root
        if root is None: raise RuntimeError("media jobs is not alive")
        if Path(name).name != name: raise RuntimeError("unsafe media output reference")
        path = (root / name).resolve(strict=must_exist)
        if path.parent != root: raise RuntimeError("unsafe media output reference")
        return path

    def _require_storage(self) -> StorageBeing:
        if self._storage is None: raise RuntimeError("media jobs is not alive")
        return self._storage

    def _stop(self) -> None:
        with self._lock:
            workers = tuple(self._workers.values())
            for _, cancel in workers: cancel.set()
        for thread, _ in workers:
            if thread.ident is not None and thread is not current_thread(): thread.join(timeout=10)
        with self._lock: self._workers.clear()
        self._storage = None; self._root = None


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise ValueError(f"{label} must contain 1 through {maximum} characters")
    return value.strip()


def _choice(value: object, label: str, choices: set[str] | frozenset[str]) -> str:
    clean = _text(value, label, 40)
    if clean not in choices: raise ValueError(f"unsupported {label}")
    return clean


def _json_mapping(value: Mapping[str, object], label: str, *, max_bytes: int) -> dict[str, object]:
    if not isinstance(value, Mapping): raise TypeError(f"{label} must be an object")
    try: encoded = json.dumps(dict(value), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error: raise ValueError(f"{label} must contain JSON values") from error
    if len(encoded.encode("utf-8")) > max_bytes: raise ValueError(f"{label} is too large")
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict): raise TypeError(f"{label} must be an object")
    return decoded


__all__ = ["MAX_OUTPUT_BYTES", "MediaJobBeing", "MediaRequest", "MediaResult", "MediaRunner", "TERMINAL_STATES"]
