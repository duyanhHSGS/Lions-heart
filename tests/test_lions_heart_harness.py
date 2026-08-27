from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from cor_being import Being, Life
from cor_beings import (
    AgentLoopBeing,
    BashBeing,
    CliBeing,
    EditBeing,
    LionBeing,
    PromptBeing,
    ReadBeing,
    SessionBeing,
    ToolShelfBeing,
    get_beings,
)
from cor_beings.lion import ModelReply, ToolCall
from cor_beings.prompt import PromptSnapshot

ROOT = Path(__file__).resolve().parents[1]


class FakeWorld:
    """Tiny public World fake for Lions-heart-owned Being tests."""

    def __init__(self, *instances: Being, name: str = "test-world") -> None:
        self.name = name
        self.news = None
        self._instances = {type(instance): instance for instance in instances}

    def need(self, being_type: type[Being]):
        try:
            return self._instances[being_type]
        except KeyError as error:
            raise LookupError(f"being is not alive: {being_type.__name__}") from error

    def branch(self, name: str) -> FakeWorld:
        return FakeWorld(name=name)


class Harness:
    def __init__(self) -> None:
        self.instances = {being_type: being_type() for being_type in get_beings()}
        self.world = FakeWorld(*self.instances.values())
        self.lives: dict[type[Being], Life] = {}
        for being_type in get_beings():
            life = Life(being_type.name)
            self.lives[being_type] = life
            self.instances[being_type].birth(self.world, life)

    def get(self, being_type):
        return self.instances[being_type]

    def die(self) -> None:
        for being_type in reversed(get_beings()):
            self.lives[being_type].die()


@pytest.fixture
def harness() -> Harness:
    value = Harness()
    try:
        yield value
    finally:
        value.die()


def test_composition_is_the_nine_tiny_harness_beings() -> None:
    assert get_beings() == (
        SessionBeing,
        LionBeing,
        ReadBeing,
        EditBeing,
        BashBeing,
        ToolShelfBeing,
        PromptBeing,
        AgentLoopBeing,
        CliBeing,
    )


def test_composition_contains_only_beings_with_unique_lowercase_names() -> None:
    beings = get_beings()
    names = [being_type.name for being_type in beings]
    assert all(issubclass(being_type, Being) for being_type in beings)
    assert len(names) == len(set(names))
    assert all(name == name.lower() for name in names)


def test_dependency_graph_is_explicit_and_tiny() -> None:
    assert ToolShelfBeing.needs == (ReadBeing, EditBeing, BashBeing)
    assert PromptBeing.needs == (SessionBeing, ToolShelfBeing)
    assert AgentLoopBeing.needs == (SessionBeing, PromptBeing, ToolShelfBeing, LionBeing)
    assert CliBeing.needs == (AgentLoopBeing,)


def test_session_appends_events_in_order_and_returns_snapshot() -> None:
    session = SessionBeing()
    first = session.append("user", text="hello")
    second = session.append("assistant", text="hi")
    assert session.events == (first, second)
    assert session.events is not session.events
    assert first.data == {"text": "hello"}


def test_session_rejects_blank_event_kind() -> None:
    session = SessionBeing()
    with pytest.raises(ValueError, match="non-empty"):
        session.append("   ")
    assert session.events == ()


def test_session_event_data_cannot_be_mutated() -> None:
    event = SessionBeing().append("user", text="hello")
    with pytest.raises(TypeError):
        event.data["text"] = "mutated"  # type: ignore[index]


def test_read_reads_utf8_text(tmp_path: Path) -> None:
    path = tmp_path / "hello.txt"
    path.write_text("lion 🦁", encoding="utf-8")
    assert ReadBeing().run({"path": str(path)}) == "lion 🦁"


@pytest.mark.parametrize("arguments", [{}, {"path": ""}, {"path": 123}])
def test_read_rejects_bad_path_arguments(arguments: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="path"):
        ReadBeing().run(arguments)


def test_read_propagates_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ReadBeing().run({"path": str(tmp_path / "missing.txt")})


def test_edit_writes_and_replaces_utf8_text(tmp_path: Path) -> None:
    path = tmp_path / "edit.txt"
    edit = EditBeing()
    assert edit.run({"path": str(path), "content": "first 🦁"}).startswith("wrote ")
    assert path.read_text(encoding="utf-8") == "first 🦁"
    edit.run({"path": str(path), "content": "second"})
    assert path.read_text(encoding="utf-8") == "second"


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"path": ""},
        {"path": 3, "content": "x"},
        {"path": "x"},
        {"path": "x", "content": 4},
    ],
)
def test_edit_rejects_bad_arguments(arguments: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        EditBeing().run(arguments)


def test_edit_does_not_secretly_create_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "file.txt"
    with pytest.raises(FileNotFoundError):
        EditBeing().run({"path": str(path), "content": "x"})


def test_bash_runs_argv_command_and_captures_stdout() -> None:
    result = BashBeing().run(
        {"command": [sys.executable, "-c", "print('ROAR')"]}
    )
    assert "exit=0" in result
    assert "stdout=ROAR\n" in result
    assert "stderr=" in result


def test_bash_keeps_nonzero_exit_as_a_result() -> None:
    result = BashBeing().run(
        {
            "command": [
                sys.executable,
                "-c",
                "import sys; print('bad', file=sys.stderr); raise SystemExit(7)",
            ]
        }
    )
    assert "exit=7" in result
    assert "stderr=bad\n" in result


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"command": "echo nope"},
        {"command": []},
        {"command": [""]},
        {"command": [123]},
        {"command": [sys.executable], "cwd": 123},
        {"command": [sys.executable], "timeout": 0},
        {"command": [sys.executable], "timeout": True},
    ],
)
def test_bash_rejects_bad_arguments(arguments: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        BashBeing().run(arguments)


def test_bash_timeout_is_not_swallowed() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        BashBeing().run(
            {
                "command": [sys.executable, "-c", "import time; time.sleep(1)"],
                "timeout": 0.01,
            }
        )


def test_tool_shelf_directly_indexes_the_three_tools() -> None:
    read = ReadBeing()
    edit = EditBeing()
    bash = BashBeing()
    shelf = ToolShelfBeing()
    life = Life("tool_shelf")
    shelf.birth(FakeWorld(read, edit, bash), life)
    try:
        assert shelf.names == ("read", "edit", "bash")
        assert shelf.get("read") is read
        assert shelf.get("edit") is edit
        assert shelf.get("bash") is bash
    finally:
        life.die()


def test_tool_shelf_unknown_name_fails_loudly(harness: Harness) -> None:
    shelf = harness.get(ToolShelfBeing)
    with pytest.raises(LookupError, match="unknown tool: missing"):
        shelf.get("missing")


def test_tool_shelf_rejects_duplicate_tool_names(monkeypatch) -> None:
    monkeypatch.setattr(EditBeing, "name", "read")
    shelf = ToolShelfBeing()
    with pytest.raises(ValueError, match="unique"):
        shelf.birth(
            FakeWorld(ReadBeing(), EditBeing(), BashBeing()),
            Life("tool_shelf"),
        )


def test_tool_shelf_death_removes_its_index() -> None:
    shelf = ToolShelfBeing()
    life = Life("tool_shelf")
    shelf.birth(FakeWorld(ReadBeing(), EditBeing(), BashBeing()), life)
    assert shelf.names == ("read", "edit", "bash")
    life.die()
    assert shelf.names == ()
    with pytest.raises(LookupError):
        shelf.get("read")


def test_prompt_requires_birth_before_build() -> None:
    with pytest.raises(RuntimeError, match="not alive"):
        PromptBeing().build()


def test_prompt_snapshots_session_and_tools(harness: Harness) -> None:
    session = harness.get(SessionBeing)
    prompt = harness.get(PromptBeing)
    session.append("user", text="hello")
    snapshot = prompt.build()
    session.append("assistant", text="later")
    assert snapshot.tools == ("read", "edit", "bash")
    assert [event.kind for event in snapshot.events] == ["user"]
    assert [event.kind for event in prompt.build().events] == ["user", "assistant"]


def test_lion_default_reply_is_deterministic_echo() -> None:
    session = SessionBeing()
    session.append("user", text="hello")
    prompt = PromptSnapshot(events=session.events, tools=("read",))
    lion = LionBeing()
    assert lion.respond(prompt) == ModelReply(text="Lion heard: hello")
    assert lion.respond(prompt) == ModelReply(text="Lion heard: hello")
    assert lion.seen_prompts == (prompt, prompt)


def test_lion_scripted_replies_are_fifo() -> None:
    lion = LionBeing()
    prompt = PromptSnapshot(events=(), tools=())
    first = ModelReply(text="one")
    second = ModelReply(text="two")
    lion.queue_reply(first)
    lion.queue_reply(second)
    assert lion.respond(prompt) is first
    assert lion.respond(prompt) is second


def test_lion_rejects_non_reply_script_item() -> None:
    with pytest.raises(TypeError, match="ModelReply"):
        LionBeing().queue_reply("nope")  # type: ignore[arg-type]


def test_agent_loop_requires_birth() -> None:
    with pytest.raises(RuntimeError, match="not alive"):
        AgentLoopBeing().run_turn("hello")


def test_agent_loop_plain_turn_records_user_and_assistant(harness: Harness) -> None:
    agent = harness.get(AgentLoopBeing)
    session = harness.get(SessionBeing)
    assert agent.run_turn("hello") == "Lion heard: hello"
    assert [event.kind for event in session.events] == ["user", "assistant"]
    assert session.events[-1].data["text"] == "Lion heard: hello"


def test_agent_loop_executes_tool_then_asks_model_again(harness: Harness, tmp_path: Path) -> None:
    path = tmp_path / "read.txt"
    path.write_text("MEAT", encoding="utf-8")
    lion = harness.get(LionBeing)
    lion.queue_reply(
        ModelReply(tool_calls=(ToolCall("read", {"path": str(path)}),))
    )
    lion.queue_reply(ModelReply(text="ate it"))

    assert harness.get(AgentLoopBeing).run_turn("read it") == "ate it"
    session = harness.get(SessionBeing)
    assert [event.kind for event in session.events] == [
        "user",
        "assistant",
        "tool_result",
        "assistant",
    ]
    assert session.events[2].data == {"name": "read", "result": "MEAT"}
    assert len(lion.seen_prompts) == 2
    assert [event.kind for event in lion.seen_prompts[1].events] == [
        "user",
        "assistant",
        "tool_result",
    ]


def test_agent_loop_executes_multiple_tool_calls_in_order(harness: Harness, tmp_path: Path) -> None:
    path = tmp_path / "multi.txt"
    lion = harness.get(LionBeing)
    lion.queue_reply(
        ModelReply(
            tool_calls=(
                ToolCall("edit", {"path": str(path), "content": "ROAR"}),
                ToolCall("read", {"path": str(path)}),
            )
        )
    )
    lion.queue_reply(ModelReply(text="done"))

    assert harness.get(AgentLoopBeing).run_turn("do both") == "done"
    results = [
        event for event in harness.get(SessionBeing).events if event.kind == "tool_result"
    ]
    assert [event.data["name"] for event in results] == ["edit", "read"]
    assert results[-1].data["result"] == "ROAR"


def test_agent_loop_logs_unknown_tool_error_then_raises(harness: Harness) -> None:
    lion = harness.get(LionBeing)
    lion.queue_reply(ModelReply(tool_calls=(ToolCall("missing", {}),)))
    with pytest.raises(LookupError, match="unknown tool"):
        harness.get(AgentLoopBeing).run_turn("boom")
    session = harness.get(SessionBeing)
    assert [event.kind for event in session.events] == ["user", "assistant", "tool_error"]
    assert session.events[-1].data["error"] == "LookupError"


def test_agent_loop_logs_tool_failure_then_raises(harness: Harness, tmp_path: Path) -> None:
    lion = harness.get(LionBeing)
    lion.queue_reply(
        ModelReply(
            tool_calls=(ToolCall("read", {"path": str(tmp_path / "missing")}),)
        )
    )
    with pytest.raises(FileNotFoundError):
        harness.get(AgentLoopBeing).run_turn("boom")
    assert harness.get(SessionBeing).events[-1].kind == "tool_error"
    assert harness.get(SessionBeing).events[-1].data["error"] == "FileNotFoundError"


def test_agent_loop_step_limit_stops_endless_tool_rounds(harness: Harness, tmp_path: Path) -> None:
    path = tmp_path / "loop.txt"
    path.write_text("x", encoding="utf-8")
    harness.get(LionBeing).queue_reply(
        ModelReply(tool_calls=(ToolCall("read", {"path": str(path)}),))
    )
    with pytest.raises(RuntimeError, match="exceeded 1 model steps"):
        harness.get(AgentLoopBeing).run_turn("loop", max_steps=1)
    assert harness.get(SessionBeing).events[-1].kind == "agent_error"


@pytest.mark.parametrize("max_steps", [0, -1, True, 1.5])
def test_agent_loop_rejects_bad_step_limit(harness: Harness, max_steps: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        harness.get(AgentLoopBeing).run_turn("hello", max_steps=max_steps)  # type: ignore[arg-type]


def test_agent_loop_rejects_non_string_message(harness: Harness) -> None:
    with pytest.raises(TypeError, match="string"):
        harness.get(AgentLoopBeing).run_turn(123)  # type: ignore[arg-type]


def test_cli_requires_birth() -> None:
    with pytest.raises(RuntimeError, match="not alive"):
        CliBeing().run_once("hello")


def test_cli_runs_one_turn_prints_once_and_returns_reply(harness: Harness) -> None:
    written: list[str] = []
    cli = harness.get(CliBeing)
    assert cli.run_once("hello", write=written.append) == "Lion heard: hello"
    assert written == ["Lion heard: hello"]
    assert [event.kind for event in harness.get(SessionBeing).events] == ["user", "assistant"]


def test_cor_beings_does_not_import_private_host_package() -> None:
    forbidden_imports: list[str] = []
    for source_path in sorted((ROOT / "cor_beings").rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path.as_posix())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            forbidden_imports.extend(
                name for name in names if name == "cor_runtime" or name.startswith("cor_runtime.")
            )
    assert forbidden_imports == []


# TODO: Add real-provider integration tests when Lions-heart gains a non-fake model Being.
