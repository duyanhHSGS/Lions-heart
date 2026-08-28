"""Indexed tool shelf for the first tiny Lions-heart harness."""

from __future__ import annotations

from collections.abc import Mapping

from cor_being import Being, Life, World
from cor_beings.bash import BashBeing
from cor_beings.edit import EditBeing
from cor_beings.read import ReadBeing

ToolBeing = ReadBeing | EditBeing | BashBeing


class ToolShelfBeing(Being):
    """Own O(1) lookup for the three concrete starter tools."""

    name = "tool_shelf"
    needs = (ReadBeing, EditBeing, BashBeing)

    def __init__(self) -> None:
        self._tools: dict[str, ToolBeing] = {}

    def birth(self, world: World, life: Life) -> None:
        tools = tuple(world.need(tool_type) for tool_type in self.needs)
        by_name = {tool.name: tool for tool in tools}
        if len(by_name) != len(tools):
            raise ValueError("tool names must be unique")

        self._tools = by_name
        life.on_death(self._tools.clear)
        # TODO: Replace direct concrete dependencies with Life-owned dynamic registration when needed.

    @property
    def names(self) -> tuple[str, ...]:
        """Return tool names in deterministic starter-composition order."""
        return tuple(self._tools)

    @property
    def schemas(self) -> tuple[dict[str, object], ...]:
        """Return cached-size provider-neutral schemas in deterministic order."""
        schemas = {
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
        return tuple(schemas[name] for name in self._tools)

    def get(self, name: str) -> ToolBeing:
        try:
            return self._tools[name]
        except KeyError as error:
            raise LookupError(f"unknown tool: {name}") from error

    def execute(self, name: str, arguments: Mapping[str, object]) -> str:
        """Resolve one tool by indexed name and execute it."""
        return self.get(name).run(arguments)
