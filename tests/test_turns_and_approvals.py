"""Race, cleanup, persistence, and workspace tests for Lion turns and approvals."""

from __future__ import annotations

from pathlib import Path
from threading import Event, Thread

import pytest

from cor_being import Life
from cor_beings.agent_loop import AgentLoopBeing
from cor_beings.approval import ApprovalBeing
from cor_beings.bash import BashBeing
from cor_beings.edit import EditBeing
from cor_beings.providers import ProviderEvent
from cor_beings.projects import ProjectsBeing
from cor_beings.read import ReadBeing
from cor_beings.session import SessionBeing
from cor_beings.storage import StorageBeing
from cor_beings.tool_shelf import ToolShelfBeing
from cor_beings.turn_manager import TurnManagerBeing
from cor_beings.workspace import WorkspaceBeing


class World:
    name = "turn-tests"
    news = None

    def __init__(self, *instances: object, agent: object | None = None) -> None:
        self.instances = {type(item): item for item in instances}
        self.agent = agent

    def need(self, kind):
        if kind is AgentLoopBeing and self.agent is not None:
            return self.agent
        try:
            return self.instances[kind]
        except KeyError as error:
            raise LookupError(kind) from error


class Platform:
    def __init__(self, root: Path, *, expiry_seconds: int = 300) -> None:
        self.storage = StorageBeing(data_root=root / "runtime")
        self.workspace = WorkspaceBeing(root=root)
        self.projects = ProjectsBeing()
        self.read = ReadBeing()
        self.edit = EditBeing()
        self.bash = BashBeing()
        self.tools = ToolShelfBeing()
        self.session = SessionBeing()
        self.approval = ApprovalBeing(expiry_seconds=expiry_seconds)
        self.beings = (
            self.storage,
            self.workspace,
            self.projects,
            self.read,
            self.edit,
            self.bash,
            self.tools,
            self.session,
            self.approval,
        )
        self.world = World(*self.beings)
        self.lives: list[Life] = []
        for being in self.beings:
            life = Life(being.name)
            being.birth(self.world, life)
            self.lives.append(life)

    def close(self) -> None:
        for life in reversed(self.lives):
            life.die()


@pytest.fixture
def platform(tmp_path: Path):
    value = Platform(tmp_path)
    try:
        yield value
    finally:
        value.close()


def test_workspace_allows_inside_and_rejects_parent_escape(platform: Platform, tmp_path: Path) -> None:
    inside = tmp_path / "hello.txt"
    inside.write_text("lion", encoding="utf-8")
    assert platform.read.run({"path": "hello.txt"}) == "lion"
    with pytest.raises(PermissionError, match="escapes"):
        platform.read.run({"path": "../outside.txt"})


def test_workspace_rejects_absolute_outside_write(platform: Platform, tmp_path: Path) -> None:
    outside = tmp_path.parent / "lion-outside.txt"
    with pytest.raises(PermissionError, match="escapes"):
        platform.edit.run({"path": str(outside), "content": "nope"})
    assert not outside.exists()


def test_approval_executes_once_after_owner_decision(platform: Platform, tmp_path: Path) -> None:
    target = tmp_path / "food.txt"
    target.write_text("MEAT", encoding="utf-8")
    approval_id = platform.approval.create("blocking-test", "read", {"path": "food.txt"})
    result: list[object] = []
    worker = Thread(
        target=lambda: result.append(platform.approval.wait_and_execute(approval_id, cancel=Event()))
    )
    worker.start()
    platform.approval.decide(approval_id, approved=True)
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result[0].approved is True
    assert result[0].result == "MEAT"
    with pytest.raises(RuntimeError, match="already decided"):
        platform.approval.decide(approval_id, approved=True)


def test_rejected_approval_returns_structured_denial(platform: Platform) -> None:
    approval_id = platform.approval.create("blocking-reject", "read", {"path": "missing"})
    platform.approval.decide(approval_id, approved=False)
    result = platform.approval.wait_and_execute(approval_id, cancel=Event())
    assert result.approved is False
    assert "denied" in result.result


def test_cancelled_approval_never_runs_tool(platform: Platform, tmp_path: Path) -> None:
    target = tmp_path / "untouched.txt"
    cancel = Event()
    approval_id = platform.approval.create(
        "blocking-cancel", "edit", {"path": "untouched.txt", "content": "bad"}
    )
    cancel.set()
    result = platform.approval.wait_and_execute(approval_id, cancel=cancel)
    assert result.approved is False
    assert not target.exists()


def test_unknown_tool_never_creates_approval(platform: Platform) -> None:
    with pytest.raises(LookupError, match="unknown tool"):
        platform.approval.create("blocking-bad", "teleport", {})
    assert platform.storage.fetchone("SELECT id FROM approvals") is None


def test_projects_crud_search_and_conversation_assignment(platform: Platform, tmp_path: Path) -> None:
    project_id = platform.projects.create("Lion Lab", workspace=str(tmp_path))
    assert platform.projects.search("Lion")[0]["id"] == project_id
    platform.projects.rename(project_id, "Roar Lab")
    assert platform.projects.list()[0]["name"] == "Roar Lab"
    conversation_id = platform.session.conversation_id
    platform.session.assign_project(conversation_id, project_id)
    assert platform.session.list_conversations()[0]["project_id"] == project_id
    platform.projects.delete(project_id)
    assert platform.session.list_conversations()[0]["project_id"] is None


def test_conversation_fork_search_pin_archive_and_exports(platform: Platform) -> None:
    source = platform.session.conversation_id
    platform.session.append("user", text="rare pineapple")
    platform.session.append("assistant", text="found it")
    platform.session.pin_conversation(source)
    assert platform.session.list_conversations()[0]["pinned"] == 1
    assert platform.session.search("pineapple")[0]["id"] == source
    fork = platform.session.fork_conversation(source)
    assert [event.kind for event in platform.session.events_for(fork)] == ["user", "assistant"]
    assert "rare pineapple" in platform.session.export_conversation(fork, format="markdown")
    assert '"kind": "user"' in platform.session.export_conversation(fork, format="json")
    platform.session.archive_conversation(source)
    assert all(item["id"] != source for item in platform.session.list_conversations())
    with pytest.raises(ValueError, match="markdown or json"):
        platform.session.export_conversation(fork, format="pdf")


class StreamingAgent:
    def __init__(self, gate: Event | None = None) -> None:
        self.gate = gate

    def stream_turn(
        self, message: str, *, turn_id: str, cancel: Event, emit, conversation_id: str
    ):
        assert conversation_id
        emit(ProviderEvent.make("start", turn_id=turn_id))
        emit(ProviderEvent.make("text_delta", text=message.upper()))
        if self.gate is not None:
            while not self.gate.wait(0.01) and not cancel.is_set():
                pass
        emit(ProviderEvent.make("cancelled" if cancel.is_set() else "completed"))
        return ()


def _turn_manager(platform: Platform, agent: StreamingAgent) -> tuple[TurnManagerBeing, Life]:
    manager = TurnManagerBeing()
    world = World(*platform.beings, agent=agent)
    life = Life("turn_manager")
    manager.birth(world, life)
    return manager, life


def _wait_terminal(manager: TurnManagerBeing, turn_id: str) -> tuple[dict[str, object], ...]:
    for _ in range(100):
        events, status = manager.events_after(turn_id, 0)
        if status in {"completed", "cancelled", "failed"}:
            return events
        manager.wait_for_events(turn_id, len(events), 0.05)
    raise AssertionError("turn did not finish")


def test_turn_events_are_ordered_resumable_and_persisted(platform: Platform) -> None:
    manager, life = _turn_manager(platform, StreamingAgent())
    turn_id = manager.create(platform.session.conversation_id, "roar")
    events = _wait_terminal(manager, turn_id)
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert any(event["kind"] == "text_delta" for event in events)
    tail, status = manager.events_after(turn_id, 2)
    assert status == "completed"
    assert all(event["sequence"] > 2 for event in tail)
    life.die()

    replacement, replacement_life = _turn_manager(platform, StreamingAgent())
    persisted, status = replacement.events_after(turn_id, 0)
    assert status == "completed"
    assert persisted == events
    replacement_life.die()


def test_one_active_turn_per_conversation_and_cancellation(platform: Platform) -> None:
    gate = Event()
    manager, life = _turn_manager(platform, StreamingAgent(gate))
    turn_id = manager.create(platform.session.conversation_id, "slow")
    with pytest.raises(RuntimeError, match="active turn"):
        manager.create(platform.session.conversation_id, "duplicate")
    manager.cancel(turn_id)
    events = _wait_terminal(manager, turn_id)
    assert events[-1]["kind"] == "turn_cancelled"
    life.die()


def test_different_conversations_can_run_concurrently(platform: Platform) -> None:
    gate = Event()
    manager, life = _turn_manager(platform, StreamingAgent(gate))
    first_conversation = platform.session.conversation_id
    second_conversation = platform.session.new_conversation(title="Second")
    first = manager.create(first_conversation, "one")
    second = manager.create(second_conversation, "two")
    assert first != second
    manager.cancel(first)
    manager.cancel(second)
    assert _wait_terminal(manager, first)[-1]["kind"] == "turn_cancelled"
    assert _wait_terminal(manager, second)[-1]["kind"] == "turn_cancelled"
    life.die()


def test_turn_shutdown_cancels_and_joins_worker(platform: Platform) -> None:
    manager, life = _turn_manager(platform, StreamingAgent(Event()))
    turn_id = manager.create(platform.session.conversation_id, "shutdown")
    life.die()
    row = platform.storage.fetchone("SELECT status FROM turns WHERE id=?", (turn_id,))
    assert row is not None
    assert row["status"] == "cancelled"
