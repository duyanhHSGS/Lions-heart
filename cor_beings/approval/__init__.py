"""Mandatory, durable approval gate for every Lion tool invocation."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from threading import Event, RLock
from uuid import uuid4

from cor_being import Being, Life, World
from cor_beings.storage import StorageBeing
from cor_beings.session import SessionBeing
from cor_beings.tool_shelf import ToolShelfBeing


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    approval_id: str
    approved: bool
    result: str


class ApprovalBeing(Being):
    """Persist requests, wait for the owner, and execute an approved call once."""

    name = "approval"
    needs = (StorageBeing, ToolShelfBeing, SessionBeing)

    def __init__(self, *, expiry_seconds: int = 300, auto_approve_for_tests: bool = False) -> None:
        if expiry_seconds <= 0:
            raise ValueError("approval expiry must be positive")
        self._expiry_seconds = expiry_seconds
        self._auto_approve_for_tests = auto_approve_for_tests
        self._storage: StorageBeing | None = None
        self._tools: ToolShelfBeing | None = None
        self._session: SessionBeing | None = None
        self._signals: dict[str, Event] = {}
        self._lock = RLock()
        self._dying = Event()

    def birth(self, world: World, life: Life) -> None:
        self._storage = world.need(StorageBeing)
        self._tools = world.need(ToolShelfBeing)
        self._session = world.need(SessionBeing)
        now = int(time.time())
        self._storage.execute(
            "UPDATE approvals SET status='cancelled', updated_at=? "
            "WHERE status IN ('pending', 'approved', 'executing')",
            (now,),
        )
        life.on_death(self._stop)
        # TODO: Add owner-configurable approval expiry presets after usability testing.

    def create(self, turn_id: str, tool_name: str, arguments: Mapping[str, object]) -> str:
        storage, tools = self._require_alive()
        tools.get(tool_name)
        safe_arguments = _json_object(arguments)
        approval_id = uuid4().hex
        now = int(time.time())
        signal = Event()
        with self._lock:
            self._signals[approval_id] = signal
        try:
            if storage.fetchone("SELECT id FROM turns WHERE id=?", (turn_id,)) is None:
                now = int(time.time())
                storage.execute(
                    "INSERT INTO turns(id, conversation_id, status, created_at, updated_at) "
                    "VALUES (?, ?, 'completed', ?, ?)",
                    (turn_id, self._require_session().conversation_id, now, now),
                )
            storage.execute(
                "INSERT INTO approvals(id, turn_id, tool_name, arguments_json, risk_summary, "
                "status, idempotency_key, expires_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                (
                    approval_id,
                    turn_id,
                    tool_name,
                    json.dumps(safe_arguments, ensure_ascii=False, separators=(",", ":")),
                    _risk_summary(tool_name, safe_arguments),
                    uuid4().hex,
                    now + self._expiry_seconds,
                    now,
                    now,
                ),
            )
        except BaseException:
            with self._lock:
                self._signals.pop(approval_id, None)
            raise
        if self._auto_approve_for_tests:
            self.decide(approval_id, approved=True)
        return approval_id

    def decide(self, approval_id: str, *, approved: bool) -> None:
        storage, _tools = self._require_alive()
        now = int(time.time())
        with storage.transaction() as connection:
            row = connection.execute(
                "SELECT status, expires_at FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
            if row is None:
                raise LookupError("approval not found")
            if row["status"] != "pending":
                raise RuntimeError("approval was already decided")
            status = "expired" if int(row["expires_at"]) <= now else ("approved" if approved else "rejected")
            connection.execute(
                "UPDATE approvals SET status=?, updated_at=? WHERE id=?",
                (status, now, approval_id),
            )
        with self._lock:
            signal = self._signals.get(approval_id)
        if signal is not None:
            signal.set()
        if status == "expired":
            raise RuntimeError("approval expired")

    def wait_and_execute(self, approval_id: str, *, cancel: Event) -> ApprovalDecision:
        storage, tools = self._require_alive()
        while True:
            row = storage.fetchone(
                "SELECT tool_name, arguments_json, status, expires_at FROM approvals WHERE id=?",
                (approval_id,),
            )
            if row is None:
                raise LookupError("approval not found")
            status = str(row["status"])
            now = int(time.time())
            if cancel.is_set() or self._dying.is_set():
                self._finish_if_waiting(approval_id, "cancelled")
                return ApprovalDecision(approval_id, False, "tool call cancelled")
            if status == "rejected":
                self._drop_signal(approval_id)
                return ApprovalDecision(approval_id, False, "owner denied this tool call")
            if status in ("cancelled", "expired"):
                self._drop_signal(approval_id)
                return ApprovalDecision(approval_id, False, f"tool approval {status}")
            if status == "approved":
                break
            if now >= int(row["expires_at"]):
                self._finish_if_waiting(approval_id, "expired")
                continue
            with self._lock:
                signal = self._signals.setdefault(approval_id, Event())
            signal.wait(timeout=0.1)

        with storage.transaction() as connection:
            cursor = connection.execute(
                "UPDATE approvals SET status='executing', updated_at=? "
                "WHERE id=? AND status='approved'",
                (int(time.time()), approval_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("approval execution was already claimed")
        arguments = json.loads(str(row["arguments_json"]))
        try:
            result = tools.execute(str(row["tool_name"]), arguments)
        except Exception as error:
            safe_result = json.dumps(
                {"error": type(error).__name__, "message": str(error)},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            storage.execute(
                "UPDATE approvals SET status='failed', result_json=?, updated_at=? WHERE id=?",
                (safe_result, int(time.time()), approval_id),
            )
            self._drop_signal(approval_id)
            raise
        storage.execute(
            "UPDATE approvals SET status='executed', result_json=?, updated_at=? WHERE id=?",
            (json.dumps({"result": result}, ensure_ascii=False), int(time.time()), approval_id),
        )
        self._drop_signal(approval_id)
        return ApprovalDecision(approval_id, True, result)

    def pending_for_turn(self, turn_id: str) -> tuple[dict[str, object], ...]:
        storage, _tools = self._require_alive()
        rows = storage.fetchall(
            "SELECT id, tool_name, arguments_json, risk_summary, status, expires_at "
            "FROM approvals WHERE turn_id=? ORDER BY created_at",
            (turn_id,),
        )
        return tuple(
            {
                "id": row["id"],
                "tool": row["tool_name"],
                "arguments": json.loads(row["arguments_json"]),
                "risk": row["risk_summary"],
                "status": row["status"],
                "expires_at": row["expires_at"],
            }
            for row in rows
        )

    def _finish_if_waiting(self, approval_id: str, status: str) -> None:
        storage, _tools = self._require_alive()
        storage.execute(
            "UPDATE approvals SET status=?, updated_at=? WHERE id=? AND status IN ('pending', 'approved')",
            (status, int(time.time()), approval_id),
        )
        with self._lock:
            signal = self._signals.get(approval_id)
        if signal is not None:
            signal.set()

    def _drop_signal(self, approval_id: str) -> None:
        with self._lock:
            self._signals.pop(approval_id, None)

    def _require_alive(self) -> tuple[StorageBeing, ToolShelfBeing]:
        if self._storage is None or self._tools is None:
            raise RuntimeError("approval gate is not alive")
        return self._storage, self._tools

    def _require_session(self) -> SessionBeing:
        if self._session is None:
            raise RuntimeError("approval gate is not alive")
        return self._session

    def _stop(self) -> None:
        self._dying.set()
        with self._lock:
            signals = tuple(self._signals.values())
            self._signals.clear()
        for signal in signals:
            signal.set()
        self._storage = None
        self._tools = None
        self._session = None


def _json_object(value: Mapping[str, object]) -> dict[str, object]:
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError("tool arguments must be a JSON object") from error
    if not isinstance(decoded, dict):
        raise ValueError("tool arguments must be a JSON object")
    return decoded


def _risk_summary(tool_name: str, arguments: Mapping[str, object]) -> str:
    if tool_name == "read":
        return f"Reads a file: {arguments.get('path', '(missing path)')}"
    if tool_name == "edit":
        return f"Overwrites a file: {arguments.get('path', '(missing path)')}"
    if tool_name == "bash":
        argv = arguments.get("argv", ())
        command = " ".join(str(part) for part in argv) if isinstance(argv, list) else str(argv)
        return f"Runs a local command: {command[:240]}"
    return f"Runs tool {tool_name} with the shown arguments"


__all__ = ["ApprovalBeing", "ApprovalDecision"]
