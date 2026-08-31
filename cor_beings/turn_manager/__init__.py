"""Lifecycle-owned background turns with resumable ordered event traces."""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Condition, Event, RLock, Thread, current_thread
from uuid import uuid4

from cor_being import Being, Life, World
from cor_beings.agent_loop import AgentLoopBeing
from cor_beings.approval import ApprovalBeing
from cor_beings.providers import ProviderEvent
from cor_beings.session import SessionBeing
from cor_beings.storage import StorageBeing


TERMINAL_STATUSES = frozenset({"completed", "cancelled", "failed"})
MAX_COMPLETED_JOBS = 128


@dataclass(slots=True)
class _TurnJob:
    turn_id: str
    conversation_id: str
    message: str
    persistent: bool
    cancel: Event = field(default_factory=Event)
    condition: Condition = field(default_factory=Condition)
    events: list[dict[str, object]] = field(default_factory=list)
    status: str = "queued"
    thread: Thread | None = None


class TurnManagerBeing(Being):
    """Run browser turns off request threads and preserve reconnectable events."""

    name = "turn_manager"
    needs = (AgentLoopBeing, SessionBeing, StorageBeing, ApprovalBeing)

    def __init__(self) -> None:
        self._agent: AgentLoopBeing | None = None
        self._session: SessionBeing | None = None
        self._storage: StorageBeing | None = None
        self._approval: ApprovalBeing | None = None
        self._jobs: dict[str, _TurnJob] = {}
        self._active_by_conversation: dict[str, str] = {}
        self._completed: deque[str] = deque()
        self._lock = RLock()
        self._stopping = Event()

    def birth(self, world: World, life: Life) -> None:
        self._agent = world.need(AgentLoopBeing)
        self._session = world.need(SessionBeing)
        self._storage = world.need(StorageBeing)
        self._approval = world.need(ApprovalBeing)
        now = int(time.time())
        self._storage.execute(
            "UPDATE turns SET status='failed', error_kind='interrupted_restart', updated_at=? "
            "WHERE status IN ('queued', 'running', 'waiting_approval')",
            (now,),
        )
        life.on_death(self._stop)
        # TODO: Make the completed-cache bound owner-configurable after operational metrics exist.

    def create(self, conversation_id: str, message: str) -> str:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")
        session, storage = self._require_alive()
        if not session.has_conversation(conversation_id):
            raise LookupError("conversation not found")
        turn_id = uuid4().hex
        persistent = not session.temporary
        job = _TurnJob(turn_id, conversation_id, message, persistent)
        with self._lock:
            if conversation_id in self._active_by_conversation:
                raise RuntimeError("conversation already has an active turn")
            self._jobs[turn_id] = job
            self._active_by_conversation[conversation_id] = turn_id
        now = int(time.time())
        try:
            if persistent:
                storage.execute(
                    "INSERT INTO turns(id, conversation_id, status, created_at, updated_at) "
                    "VALUES (?, ?, 'queued', ?, ?)",
                    (turn_id, conversation_id, now, now),
                )
            thread = Thread(
                target=self._run,
                args=(job,),
                name=f"lion-turn-{turn_id[:8]}",
                daemon=False,
            )
            job.thread = thread
            thread.start()
        except BaseException:
            with self._lock:
                self._jobs.pop(turn_id, None)
                self._active_by_conversation.pop(conversation_id, None)
            raise
        return turn_id

    def _run(self, job: _TurnJob) -> None:
        try:
            self._set_status(job, "running")
            self._publish(job, ProviderEvent.make("turn_started", turn_id=job.turn_id))
            agent = self._agent
            if agent is None:
                raise RuntimeError("turn manager is stopping")
            agent.stream_turn(
                job.message,
                turn_id=job.turn_id,
                cancel=job.cancel,
                emit=lambda event: self._publish(job, event),
                conversation_id=job.conversation_id,
            )
            if job.cancel.is_set():
                self._publish(job, ProviderEvent.make("turn_cancelled", turn_id=job.turn_id))
                self._set_status(job, "cancelled")
            else:
                self._publish(job, ProviderEvent.make("turn_completed", turn_id=job.turn_id))
                self._set_status(job, "completed")
        except Exception as error:  # noqa: BLE001 - normalize the background boundary.
            error_kind = type(error).__name__
            try:
                self._publish(
                    job,
                    ProviderEvent.make(
                        "normalized_error",
                        error_kind=error_kind,
                        message="turn failed; inspect provider and approval settings",
                    ),
                )
                self._publish(job, ProviderEvent.make("turn_failed", turn_id=job.turn_id))
            finally:
                self._set_status(job, "failed", error_kind=error_kind)
            # TODO: Add redacted structured logging for failures at this background boundary.
        finally:
            with self._lock:
                self._active_by_conversation.pop(job.conversation_id, None)
                self._completed.append(job.turn_id)
                while len(self._completed) > MAX_COMPLETED_JOBS:
                    expired = self._completed.popleft()
                    self._jobs.pop(expired, None)
            with job.condition:
                job.condition.notify_all()

    def _publish(self, job: _TurnJob, event: ProviderEvent) -> None:
        with job.condition:
            sequence = len(job.events) + 1
            item = {
                "sequence": sequence,
                "kind": event.kind,
                "data": _json_safe(dict(event.data)),
            }
            job.events.append(item)
            if job.persistent:
                storage = self._storage
                if storage is not None:
                    storage.execute(
                        "INSERT INTO turn_events(turn_id, sequence, kind, data_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            job.turn_id,
                            sequence,
                            event.kind,
                            json.dumps(item["data"], ensure_ascii=False, separators=(",", ":")),
                            int(time.time()),
                        ),
                    )
            job.condition.notify_all()

    def _set_status(self, job: _TurnJob, status: str, *, error_kind: str | None = None) -> None:
        with job.condition:
            job.status = status
            if job.persistent and self._storage is not None:
                self._storage.execute(
                    "UPDATE turns SET status=?, error_kind=?, updated_at=? WHERE id=?",
                    (status, error_kind, int(time.time()), job.turn_id),
                )
            job.condition.notify_all()

    def events_after(
        self, turn_id: str, after: int
    ) -> tuple[tuple[dict[str, object], ...], str]:
        if not isinstance(after, int) or after < 0:
            raise ValueError("after must be a non-negative integer")
        with self._lock:
            job = self._jobs.get(turn_id)
        if job is not None:
            with job.condition:
                return tuple(event for event in job.events if int(event["sequence"]) > after), job.status
        storage = self._storage
        if storage is None:
            raise RuntimeError("turn manager is not alive")
        turn = storage.fetchone("SELECT status FROM turns WHERE id=?", (turn_id,))
        if turn is None:
            raise LookupError("turn not found")
        rows = storage.fetchall(
            "SELECT sequence, kind, data_json FROM turn_events "
            "WHERE turn_id=? AND sequence>? ORDER BY sequence",
            (turn_id, after),
        )
        return tuple(
            {"sequence": row["sequence"], "kind": row["kind"], "data": json.loads(row["data_json"])}
            for row in rows
        ), str(turn["status"])

    def wait_for_events(self, turn_id: str, after: int, timeout: float = 10.0) -> None:
        with self._lock:
            job = self._jobs.get(turn_id)
        if job is None:
            return
        with job.condition:
            if job.status not in TERMINAL_STATUSES and len(job.events) <= after:
                job.condition.wait(timeout=timeout)

    def cancel(self, turn_id: str) -> None:
        with self._lock:
            job = self._jobs.get(turn_id)
        if job is None:
            storage = self._storage
            if storage is None or storage.fetchone("SELECT id FROM turns WHERE id=?", (turn_id,)) is None:
                raise LookupError("turn not found")
            raise RuntimeError("turn is no longer active")
        if job.status in TERMINAL_STATUSES:
            return
        job.cancel.set()
        with job.condition:
            job.condition.notify_all()

    def decide_approval(self, approval_id: str, approved: bool) -> None:
        if self._approval is None:
            raise RuntimeError("turn manager is not alive")
        self._approval.decide(approval_id, approved=approved)

    def approvals(self, turn_id: str) -> tuple[dict[str, object], ...]:
        if self._approval is None:
            raise RuntimeError("turn manager is not alive")
        return self._approval.pending_for_turn(turn_id)

    def _require_alive(self) -> tuple[SessionBeing, StorageBeing]:
        if self._session is None or self._storage is None:
            raise RuntimeError("turn manager is not alive")
        return self._session, self._storage

    def _stop(self) -> None:
        self.begin_shutdown()
        with self._lock:
            jobs = tuple(self._jobs.values())
        deadline = time.monotonic() + 2.0
        for job in jobs:
            thread = job.thread
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if thread is not None and thread.ident is not None and thread is not current_thread():
                thread.join(timeout=remaining)
        with self._lock:
            self._jobs.clear()
            self._active_by_conversation.clear()
            self._completed.clear()
        self._agent = None
        self._session = None
        self._storage = None
        self._approval = None

    def begin_shutdown(self) -> None:
        """Signal every worker without waiting, so transports can unwind concurrently."""
        self._stopping.set()
        with self._lock:
            jobs = tuple(self._jobs.values())
        for job in jobs:
            job.cancel.set()
            with job.condition:
                job.condition.notify_all()
        # TODO: Replace the bounded grace period with provider-native request cancellation where available.


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return str(value)


__all__ = ["MAX_COMPLETED_JOBS", "TERMINAL_STATUSES", "TurnManagerBeing"]
