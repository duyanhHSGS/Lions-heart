"""Bounded declarative recipes with immutable execution history."""

from __future__ import annotations
from collections.abc import Mapping
import json
import time
import unicodedata
from uuid import uuid4
from cor_being import Being, Life, World
from cor_beings.storage import StorageBeing

MAX_STEPS = 64


class RecipeBeing(Being):
    name = "recipes"
    needs = (StorageBeing,)

    def __init__(self) -> None: self._storage: StorageBeing | None = None
    def birth(self, world: World, life: Life) -> None:
        self._storage = world.need(StorageBeing); life.on_death(self._forget)
        # TODO: Add resumable media/tool step executors after their typed value contracts stabilize.

    def create(self, name: str, graph: Mapping[str, object], *, description: str = "") -> dict[str, object]:
        clean_name = _text(name, "recipe name", 120)
        clean_description = _optional_text(description, "recipe description", 2_000)
        safe_graph = _validate_graph(graph)
        recipe_id = uuid4().hex; now = int(time.time())
        self._require().execute(
            "INSERT INTO recipes(id,name,description,graph_json,revision,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",
            (recipe_id, clean_name, clean_description, _dump(safe_graph), now, now),
        )
        return self.get(recipe_id)

    def get(self, recipe_id: str) -> dict[str, object]:
        row = self._require().fetchone(
            "SELECT id,name,description,graph_json,revision,created_at,updated_at FROM recipes WHERE id=?", (recipe_id,)
        )
        if row is None: raise LookupError("recipe not found")
        item = dict(row); item["graph"] = json.loads(item.pop("graph_json")); return item

    def list(self, *, limit: int = 100) -> tuple[dict[str, object], ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100: raise ValueError("limit must be from 1 through 100")
        return tuple(dict(row) for row in self._require().fetchall(
            "SELECT id,name,description,revision,created_at,updated_at FROM recipes ORDER BY updated_at DESC,id LIMIT ?", (limit,)
        ))

    def update(self, recipe_id: str, name: str, graph: Mapping[str, object], *, description: str = "",
               revision: int) -> dict[str, object]:
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1: raise ValueError("revision is invalid")
        cursor = self._require().execute(
            "UPDATE recipes SET name=?,description=?,graph_json=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?",
            (_text(name, "recipe name", 120), _optional_text(description, "recipe description", 2_000),
             _dump(_validate_graph(graph)), int(time.time()), recipe_id, revision),
        )
        if cursor.rowcount != 1:
            if self._require().fetchone("SELECT 1 FROM recipes WHERE id=?", (recipe_id,)) is None: raise LookupError("recipe not found")
            raise RuntimeError("recipe changed since it was opened")
        return self.get(recipe_id)

    def delete(self, recipe_id: str) -> None:
        if self._require().execute("DELETE FROM recipes WHERE id=?", (recipe_id,)).rowcount != 1:
            raise LookupError("recipe not found")

    def run(self, recipe_id: str, inputs: Mapping[str, object] | None = None) -> dict[str, object]:
        recipe = self.get(recipe_id); safe_inputs = _json_object(inputs or {}, "recipe inputs", 64 * 1024)
        snapshot = {"recipe_id": recipe_id, "revision": recipe["revision"], "graph": recipe["graph"]}
        run_id = uuid4().hex; now = int(time.time())
        storage = self._require()
        storage.execute(
            "INSERT INTO recipe_runs(id,recipe_id,status,inputs_json,snapshot_json,created_at,updated_at) "
            "VALUES (?,?,'running',?,?,?,?)", (run_id, recipe_id, _dump(safe_inputs), _dump(snapshot), now, now),
        )
        try:
            outputs = _execute(recipe["graph"], safe_inputs)
        except BaseException as error:
            storage.execute("UPDATE recipe_runs SET status='failed',error_kind=?,updated_at=? WHERE id=?",
                            (type(error).__name__[:80], int(time.time()), run_id))
            raise
        storage.execute("UPDATE recipe_runs SET status='completed',outputs_json=?,updated_at=? WHERE id=? AND status='running'",
                        (_dump(outputs), int(time.time()), run_id))
        return self.run_record(run_id)

    def cancel(self, run_id: str) -> dict[str, object]:
        if self._require().execute(
            "UPDATE recipe_runs SET status='cancelled',updated_at=? WHERE id=? AND status='running'",
            (int(time.time()), run_id),
        ).rowcount == 0 and self._require().fetchone("SELECT 1 FROM recipe_runs WHERE id=?", (run_id,)) is None:
            raise LookupError("recipe run not found")
        return self.run_record(run_id)

    def run_record(self, run_id: str) -> dict[str, object]:
        row = self._require().fetchone(
            "SELECT id,recipe_id,status,inputs_json,outputs_json,snapshot_json,error_kind,created_at,updated_at "
            "FROM recipe_runs WHERE id=?", (run_id,),
        )
        if row is None: raise LookupError("recipe run not found")
        item = dict(row)
        for source, target in (("inputs_json", "inputs"), ("outputs_json", "outputs"), ("snapshot_json", "snapshot")):
            value = item.pop(source); item[target] = json.loads(value) if value is not None else None
        return item

    def history(self, recipe_id: str, *, limit: int = 50) -> tuple[dict[str, object], ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100: raise ValueError("limit must be from 1 through 100")
        rows = self._require().fetchall("SELECT id FROM recipe_runs WHERE recipe_id=? ORDER BY created_at DESC,id DESC LIMIT ?", (recipe_id, limit))
        return tuple(self.run_record(str(row["id"])) for row in rows)

    def _require(self) -> StorageBeing:
        if self._storage is None: raise RuntimeError("recipes is not alive")
        return self._storage
    def _forget(self) -> None: self._storage = None


def _validate_graph(graph: Mapping[str, object]) -> dict[str, object]:
    safe = _json_object(graph, "recipe graph", 128 * 1024)
    steps = safe.get("steps"); outputs = safe.get("outputs", [])
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS: raise ValueError(f"recipe needs 1 through {MAX_STEPS} steps")
    if not isinstance(outputs, list) or not all(isinstance(value, str) for value in outputs): raise ValueError("recipe outputs must be step IDs")
    identifiers: set[str] = set(); dependencies: dict[str, tuple[str, ...]] = {}
    for step in steps:
        if not isinstance(step, dict): raise ValueError("recipe steps must be objects")
        step_id = step.get("id"); operation = step.get("operation"); needs = step.get("needs", [])
        if not isinstance(step_id, str) or not step_id.isidentifier() or len(step_id) > 64: raise ValueError("recipe step ID is invalid")
        if step_id in identifiers: raise ValueError("recipe step IDs must be unique")
        identifiers.add(step_id)
        if operation not in {"input", "literal", "concat"}: raise ValueError("recipe operation is not allowed")
        if not isinstance(needs, list) or len(needs) > MAX_STEPS or not all(isinstance(value, str) for value in needs): raise ValueError("recipe dependencies are invalid")
        dependencies[step_id] = tuple(needs)
        if operation == "input" and not isinstance(step.get("key"), str): raise ValueError("input step needs a key")
        if operation == "literal" and "value" not in step: raise ValueError("literal step needs a value")
    if any(value not in identifiers for values in dependencies.values() for value in values): raise ValueError("recipe dependency is missing")
    if any(value not in identifiers for value in outputs): raise ValueError("recipe output is missing")
    pending = dict(dependencies); resolved: set[str] = set()
    while pending:
        ready = [key for key, values in pending.items() if set(values) <= resolved]
        if not ready: raise ValueError("recipe graph contains a cycle")
        resolved.update(ready)
        for key in ready: pending.pop(key)
    return safe


def _execute(graph: object, inputs: dict[str, object]) -> dict[str, object]:
    assert isinstance(graph, dict); steps = graph["steps"]; values: dict[str, object] = {}
    assert isinstance(steps, list)
    pending = {str(step["id"]): step for step in steps if isinstance(step, dict)}
    while pending:
        for step_id, step in tuple(pending.items()):
            needs = step.get("needs", [])
            if not all(value in values for value in needs): continue
            operation = step["operation"]
            if operation == "input":
                key = str(step["key"])
                if key not in inputs: raise ValueError(f"missing recipe input: {key}")
                value = inputs[key]
            elif operation == "literal": value = step["value"]
            else:
                separator = step.get("separator", "")
                if not isinstance(separator, str) or len(separator) > 100: raise ValueError("concat separator is invalid")
                value = separator.join(str(values[key]) for key in needs)
                if len(value) > 100_000: raise ValueError("recipe text output is too large")
            values[step_id] = value; pending.pop(step_id)
    requested = graph.get("outputs") or [steps[-1]["id"]]
    return {key: values[key] for key in requested}


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum: raise ValueError(f"{label} must contain 1 through {maximum} characters")
    return unicodedata.normalize("NFKC", value.strip())
def _optional_text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum: raise ValueError(f"{label} is too long")
    return value.strip()
def _dump(value: object) -> str: return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
def _json_object(value: object, label: str, maximum: int) -> dict[str, object]:
    if not isinstance(value, Mapping): raise TypeError(f"{label} must be an object")
    try: encoded = _dump(dict(value))
    except (TypeError, ValueError) as error: raise ValueError(f"{label} must contain JSON values") from error
    if len(encoded.encode()) > maximum: raise ValueError(f"{label} is too large")
    result = json.loads(encoded); assert isinstance(result, dict); return result


__all__ = ["MAX_STEPS", "RecipeBeing"]
