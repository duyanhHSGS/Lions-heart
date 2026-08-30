from __future__ import annotations

import time
from pathlib import Path
from threading import Event

import pytest

from cor_being import Life
from cor_beings.activity import ActivityBeing
from cor_beings.audio import AudioBeing
from cor_beings.images import ImageBeing
from cor_beings.media_jobs import MediaJobBeing, MediaResult
from cor_beings.projects import ProjectsBeing
from cor_beings.recipes import RecipeBeing
from cor_beings.storage import SCHEMA_VERSION, StorageBeing
from cor_beings.video import VideoBeing
from cor_beings.workspace import WorkspaceBeing


class World:
    name = "phase-test"
    news = None
    alive = True

    def __init__(self, *items): self.items = {type(item): item for item in items}
    def need(self, kind): return self.items[kind]
    def branch(self, name): return World()


def born(being, world):
    life = Life(being.name); being.birth(world, life); return life


def test_schema_and_redacted_activity(tmp_path: Path) -> None:
    storage = StorageBeing(data_root=tmp_path / "data"); storage_life = born(storage, World())
    activity = ActivityBeing(); activity_life = born(activity, World(storage))
    try:
        assert storage.fetchone("PRAGMA user_version")[0] == SCHEMA_VERSION
        item_id = activity.record(provider="openai", model="gpt-test", capability="text", status="completed",
                                  latency_ms=7, input_tokens=2, output_tokens=3, cost_microusd=9, pricing_version="v1")
        assert activity.list()[0]["id"] == item_id
        assert activity.totals()["output_tokens"] == 3
        with pytest.raises(ValueError): activity.record(provider="openai", model="x", capability="text", status="failed", error_kind="raw key: secret")
        assert activity.prune(before=int(time.time()) + 1) == 1
    finally:
        activity_life.die(); storage_life.die()


def test_media_job_streams_output_and_cancel_is_idempotent(tmp_path: Path) -> None:
    storage = StorageBeing(data_root=tmp_path / "data"); storage_life = born(storage, World())
    def runner(request, cancel: Event, progress):
        progress(50)
        return MediaResult([b"hello", b" world"], "image/png", "png", remote_id="remote-safe")
    jobs = MediaJobBeing(runner=runner); jobs_life = born(jobs, World(storage))
    image = ImageBeing(); image_life = born(image, World(jobs))
    audio = AudioBeing(); audio_life = born(audio, World(jobs))
    video = VideoBeing(); video_life = born(video, World(jobs))
    try:
        item = image.generate("a lion", provider="fake", model="fast", count=1)
        for _ in range(100):
            item = jobs.get(str(item["id"]))
            if item["status"] == "completed": break
            time.sleep(.01)
        assert item["status"] == "completed" and item["progress"] == 100
        metadata, path = jobs.download(str(item["id"]))
        assert metadata["output_bytes"] == 11 and path.read_bytes() == b"hello world"
        assert jobs.cancel(str(item["id"]))["status"] == "completed"
        jobs.delete(str(item["id"]))
        assert not path.exists()
        with pytest.raises(ValueError): image.generate("x", provider="p", model="m", count=0)
        with pytest.raises(ValueError): audio.generate("x", provider="p", model="m", duration_seconds=0)
        with pytest.raises(ValueError): video.generate("x", provider="p", model="m", width=1, height=1)
    finally:
        video_life.die(); audio_life.die(); image_life.die(); jobs_life.die(); storage_life.die()


def test_media_provider_refusal_happens_before_a_job_is_created(tmp_path: Path) -> None:
    storage = StorageBeing(data_root=tmp_path / "data"); storage_life = born(storage, World())
    jobs = MediaJobBeing(); life = born(jobs, World(storage))
    try:
        with pytest.raises(RuntimeError, match="not configured"): jobs.submit("image", "p", "m", "hello")
        assert jobs.list() == ()
    finally: life.die(); storage_life.die()


def test_recipe_graph_validation_snapshot_and_history(tmp_path: Path) -> None:
    storage = StorageBeing(data_root=tmp_path / "data"); storage_life = born(storage, World())
    recipes = RecipeBeing(); life = born(recipes, World(storage))
    graph = {"steps": [
        {"id": "hello", "operation": "literal", "value": "Hello"},
        {"id": "name", "operation": "input", "key": "name"},
        {"id": "result", "operation": "concat", "needs": ["hello", "name"], "separator": " "},
    ], "outputs": ["result"]}
    try:
        recipe = recipes.create("Greeting", graph)
        run = recipes.run(str(recipe["id"]), {"name": "Lion"})
        assert run["outputs"] == {"result": "Hello Lion"}
        recipes.update(str(recipe["id"]), "Changed", graph, revision=int(recipe["revision"]))
        assert run["snapshot"]["revision"] == 1
        assert len(recipes.history(str(recipe["id"]))) == 1
        cyclic = {"steps": [{"id": "a", "operation": "concat", "needs": ["b"]}, {"id": "b", "operation": "concat", "needs": ["a"]}]}
        with pytest.raises(ValueError, match="cycle"): recipes.create("Nope", cyclic)
    finally: life.die(); storage_life.die()


def test_project_selection_is_durable_and_changes_workspace(tmp_path: Path) -> None:
    data = tmp_path / "data"; work = tmp_path / "work"; work.mkdir()
    storage = StorageBeing(data_root=data); storage_life = born(storage, World())
    workspace = WorkspaceBeing(root=tmp_path); workspace_life = born(workspace, World())
    projects = ProjectsBeing(); projects_life = born(projects, World(storage, workspace))
    try:
        project_id = projects.create("Lion", workspace=str(work))
        projects.select(project_id)
        assert projects.selected_id == project_id and workspace.root == work.resolve()
    finally: projects_life.die(); workspace_life.die(); storage_life.die()
