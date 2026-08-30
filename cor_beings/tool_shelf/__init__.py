"""Indexed tool shelf for the first tiny Lions-heart harness."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Protocol

from cor_being import Being, Life, World
from cor_beings.bash import BashBeing
from cor_beings.edit import EditBeing
from cor_beings.read import ReadBeing

class RunnableTool(Protocol):
    name: str

    def run(self, arguments: Mapping[str, object]) -> str: ...


ToolBeing = ReadBeing | EditBeing | BashBeing | RunnableTool


class ToolShelfBeing(Being):
    """Own O(1) lookup for the three concrete starter tools."""

    name = "tool_shelf"
    needs = (ReadBeing, EditBeing, BashBeing)

    def __init__(self) -> None:
        self._tools: dict[str, ToolBeing] = {}
        self._schemas: dict[str, dict[str, object]] = {}
        self._owners: dict[str, set[str]] = {}

    def birth(self, world: World, life: Life) -> None:
        tools = tuple(world.need(tool_type) for tool_type in self.needs)
        by_name = {tool.name: tool for tool in tools}
        if len(by_name) != len(tools):
            raise ValueError("tool names must be unique")

        self._tools = by_name
        self._schemas = _builtin_schemas()
        life.on_death(self._clear)
        # TODO: Add optional schema formats only after provider compatibility tests exist.

    @property
    def names(self) -> tuple[str, ...]:
        """Return tool names in deterministic starter-composition order."""
        return tuple(self._tools)

    @property
    def schemas(self) -> tuple[dict[str, object], ...]:
        """Return cached-size provider-neutral schemas in deterministic order."""
        return tuple(self._schemas[name] for name in self._tools)

    def register_dynamic(
        self, owner: str, name: str, tool: RunnableTool, schema: Mapping[str, object]
    ) -> str:
        """Register one bounded MCP-owned tool and return its conflict-safe public name."""
        if not isinstance(owner, str) or not re.fullmatch(r"[a-z0-9_]{1,48}", owner):
            raise ValueError("dynamic tool owner is invalid")
        clean = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")[:64]
        if not clean:
            raise ValueError("dynamic tool name is invalid")
        public_name = f"mcp_{owner}_{clean}"
        if len(public_name) > 128 or public_name in self._tools:
            raise ValueError("dynamic tool name conflicts with an existing tool")
        validated = _dynamic_schema(public_name, schema)
        self._tools[public_name] = tool
        self._schemas[public_name] = validated
        self._owners.setdefault(owner, set()).add(public_name)
        return public_name

    def unregister_owner(self, owner: str) -> None:
        for name in self._owners.pop(owner, set()):
            self._tools.pop(name, None)
            self._schemas.pop(name, None)

    def get(self, name: str) -> ToolBeing:
        try:
            return self._tools[name]
        except KeyError as error:
            raise LookupError(f"unknown tool: {name}") from error

    def execute(self, name: str, arguments: Mapping[str, object]) -> str:
        """Resolve one tool by indexed name, validate input, and execute it."""
        schema = self._schemas.get(name)
        if schema is None:
            raise LookupError(f"unknown tool: {name}")
        _validate_arguments(arguments, schema["parameters"])
        return self.get(name).run(arguments)

    def _clear(self) -> None:
        self._tools.clear()
        self._schemas.clear()
        self._owners.clear()


def _builtin_schemas() -> dict[str, dict[str, object]]:
    return {
            "read": {
                "name": "read",
                "description": "Read one UTF-8 text file from the selected project.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            "edit": {
                "name": "edit",
                "description": "Replace one UTF-8 project file with new content.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
            "bash": {
                "name": "bash",
                "description": "Run one argv-style project command without a shell.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        "cwd": {"type": ["string", "null"]},
                        "timeout": {"type": "number", "exclusiveMinimum": 0},
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
            },
        }

def _dynamic_schema(name: str, schema: Mapping[str, object]) -> dict[str, object]:
    try:
        value = json.loads(json.dumps(dict(schema), ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError("tool schema must be bounded JSON") from error
    if len(json.dumps(value, separators=(",", ":"))) > 32 * 1024:
        raise ValueError("tool schema is too large")
    parameters = value.get("parameters", value.get("inputSchema"))
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        raise ValueError("tool schema must describe an object")
    _check_schema(parameters, 0)
    description = value.get("description", "MCP tool")
    if not isinstance(description, str) or len(description) > 1024:
        raise ValueError("tool description is invalid")
    return {"name": name, "description": description, "parameters": parameters}


def _check_schema(schema: Mapping[str, object], depth: int) -> None:
    if depth > 8:
        raise ValueError("tool schema is too deeply nested")
    allowed = {"type", "properties", "required", "additionalProperties", "items", "description", "enum", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"}
    if set(schema) - allowed:
        raise ValueError("tool schema contains unsupported keywords")
    kind = schema.get("type")
    if isinstance(kind, list):
        if not kind or any(item not in ("object", "array", "string", "number", "integer", "boolean", "null") for item in kind):
            raise ValueError("tool schema type is unsupported")
        return
    if kind not in ("object", "array", "string", "number", "integer", "boolean", "null"):
        raise ValueError("tool schema type is unsupported")
    properties = schema.get("properties", {})
    if kind == "object":
        if not isinstance(properties, dict) or len(properties) > 64:
            raise ValueError("tool schema properties are invalid")
        required = schema.get("required", [])
        if not isinstance(required, list) or any(item not in properties for item in required):
            raise ValueError("tool schema required fields are invalid")
        for key, child in properties.items():
            if not isinstance(key, str) or not isinstance(child, dict):
                raise ValueError("tool schema property is invalid")
            _check_schema(child, depth + 1)
    if kind == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            raise ValueError("tool array schema needs items")
        _check_schema(items, depth + 1)


def _validate_arguments(value: object, schema: object, path: str = "arguments") -> None:
    if not isinstance(schema, Mapping):
        raise ValueError("tool schema is invalid")
    kind = schema.get("type")
    if isinstance(kind, list):
        errors = []
        for option in kind:
            try:
                _validate_arguments(value, {**schema, "type": option}, path)
                return
            except ValueError as error:
                errors.append(error)
        raise ValueError(f"{path} has the wrong type")
    matches = {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(str(kind), False)
    if not matches:
        raise ValueError(f"{path} has the wrong type")
    if kind == "object":
        assert isinstance(value, Mapping)
        properties = schema.get("properties", {})
        assert isinstance(properties, Mapping)
        missing = set(schema.get("required", [])) - set(value)
        if missing:
            raise ValueError(f"{path} is missing {sorted(missing)[0]}")
        if schema.get("additionalProperties", True) is False and set(value) - set(properties):
            raise ValueError(f"{path} contains an unknown field")
        for key, item in value.items():
            if key in properties:
                _validate_arguments(item, properties[key], f"{path}.{key}")
    elif kind == "array":
        assert isinstance(value, list)
        for index, item in enumerate(value):
            _validate_arguments(item, schema["items"], f"{path}[{index}]")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is not an allowed value")
