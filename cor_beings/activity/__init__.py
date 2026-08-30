"""Strictly redacted provider activity metrics."""

from __future__ import annotations
import time
from cor_being import Being, Life, World
from cor_beings.storage import StorageBeing

_STATUSES = frozenset({"completed", "failed", "cancelled", "refused"})
_CAPABILITIES = frozenset({"text", "image", "audio", "video", "transcription", "tool"})


class ActivityBeing(Being):
    name = "activity"
    needs = (StorageBeing,)

    def __init__(self) -> None: self._storage: StorageBeing | None = None
    def birth(self, world: World, life: Life) -> None:
        self._storage = world.need(StorageBeing); life.on_death(self._forget)
        # TODO: Add owner-configurable automatic retention after encrypted backups exist.

    def record(self, *, provider: str, model: str, capability: str, status: str,
               latency_ms: int | None = None, input_tokens: int | None = None,
               output_tokens: int | None = None, retry_count: int = 0,
               error_kind: str | None = None, cost_microusd: int | None = None,
               pricing_version: str | None = None) -> int:
        provider = _identifier(provider, "provider", 64); model = _identifier(model, "model", 160)
        if capability not in _CAPABILITIES: raise ValueError("unsupported activity capability")
        if status not in _STATUSES: raise ValueError("unsupported activity status")
        latency_ms = _count(latency_ms, "latency", maximum=86_400_000, optional=True)
        input_tokens = _count(input_tokens, "input tokens", maximum=2_000_000_000, optional=True)
        output_tokens = _count(output_tokens, "output tokens", maximum=2_000_000_000, optional=True)
        retry_count = _count(retry_count, "retry count", maximum=100, optional=False)
        cost_microusd = _count(cost_microusd, "cost", maximum=10**15, optional=True)
        if cost_microusd is not None and not pricing_version: raise ValueError("priced activity needs a pricing version")
        safe_error = _error_kind(error_kind)
        cursor = self._require().execute(
            "INSERT INTO activity(provider,model,capability,status,latency_ms,input_tokens,output_tokens,retry_count,"
            "error_kind,cost_microusd,pricing_version,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (provider, model, capability, status, latency_ms, input_tokens, output_tokens, retry_count,
             safe_error, cost_microusd, _identifier(pricing_version, "pricing version", 80) if pricing_version else None,
             int(time.time())),
        )
        return int(cursor.lastrowid)

    def list(self, *, provider: str | None = None, status: str | None = None,
             limit: int = 100, before_id: int | None = None) -> tuple[dict[str, object], ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200: raise ValueError("limit must be from 1 through 200")
        clauses: list[str] = []; values: list[object] = []
        if provider is not None: clauses.append("provider=?"); values.append(_identifier(provider, "provider", 64))
        if status is not None:
            if status not in _STATUSES: raise ValueError("unsupported activity status")
            clauses.append("status=?"); values.append(status)
        if before_id is not None:
            if not isinstance(before_id, int) or isinstance(before_id, bool) or before_id < 1: raise ValueError("before_id is invalid")
            clauses.append("id<?"); values.append(before_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""; values.append(limit)
        rows = self._require().fetchall(
            "SELECT id,provider,model,capability,status,latency_ms,input_tokens,output_tokens,retry_count,error_kind,"
            f"cost_microusd,pricing_version,created_at FROM activity{where} ORDER BY id DESC LIMIT ?", values,
        )
        return tuple(dict(row) for row in rows)

    def totals(self, *, since: int | None = None) -> dict[str, object]:
        if since is not None and (not isinstance(since, int) or isinstance(since, bool) or since < 0): raise ValueError("since is invalid")
        where = " WHERE created_at>=?" if since is not None else ""; parameters = (since,) if since is not None else ()
        row = self._require().fetchone(
            "SELECT COUNT(*) AS requests,COALESCE(SUM(input_tokens),0) AS input_tokens,"
            "COALESCE(SUM(output_tokens),0) AS output_tokens,COALESCE(SUM(cost_microusd),0) AS cost_microusd,"
            f"SUM(status='failed') AS failures FROM activity{where}", parameters,
        )
        return dict(row) if row is not None else {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_microusd": 0, "failures": 0}

    def prune(self, *, before: int) -> int:
        if not isinstance(before, int) or isinstance(before, bool) or before < 0: raise ValueError("retention boundary is invalid")
        return self._require().execute("DELETE FROM activity WHERE created_at<?", (before,)).rowcount

    def _require(self) -> StorageBeing:
        if self._storage is None: raise RuntimeError("activity is not alive")
        return self._storage
    def _forget(self) -> None: self._storage = None


def _identifier(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum: raise ValueError(f"{label} is invalid")
    clean = value.strip()
    if any(ord(char) < 32 for char in clean): raise ValueError(f"{label} is invalid")
    return clean
def _error_kind(value: object) -> str | None:
    if value is None: return None
    clean = _identifier(value, "error kind", 80)
    if not all(char.isalnum() or char in "_-" for char in clean): raise ValueError("error kind must be a normalized code")
    return clean
def _count(value: object, label: str, *, maximum: int, optional: bool) -> int | None:
    if value is None and optional: return None
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum: raise ValueError(f"{label} is invalid")
    return value


__all__ = ["ActivityBeing"]
