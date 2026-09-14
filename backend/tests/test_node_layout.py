from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from miniclaw2.domain import Node, NodePosition, Project
from miniclaw2.migrations.errors import MigrationError
from miniclaw2.migrations.transaction import atomic_json
from miniclaw2.store import Store


@pytest.mark.parametrize("only_missing", [False, True])
def test_node_position_survives_lane_round_trip(tmp_path: Path, only_missing: bool) -> None:
    store = Store(tmp_path)
    project = store.create_project(Project(root_path=str(tmp_path)))
    owner = store.create_node(Node(model_preset_id="opus-4-8", project_id=project.id, planspace_id="original"))
    unrelated = store.create_node(Node(model_preset_id="opus-4-8", project_id=project.id))
    saved = NodePosition(x=70, y=80, space="planspace:original")
    moved = NodePosition(x=90, y=100, space="canvas")
    store.update_node_positions(project.id, {owner.id: saved}, [])
    owner.planspace_id = "other"
    store.update_node(owner)
    assert store.read_node_positions(project.id) == {}
    assert store.update_node_positions(
        project.id, {unrelated.id: moved}, [], only_missing=only_missing,
    ) == {unrelated.id: moved}
    owner.planspace_id = "original"
    store.update_node(owner)
    assert Store(tmp_path).read_node_positions(project.id) == {owner.id: saved, unrelated.id: moved}


def test_only_missing_replaces_stale_space_but_keeps_current_position(tmp_path: Path) -> None:
    store = Store(tmp_path)
    project = store.create_project(Project(root_path=str(tmp_path)))
    owner = store.create_node(Node(model_preset_id="opus-4-8", project_id=project.id))
    saved = NodePosition(x=70, y=80, space="canvas")
    store.update_node_positions(project.id, {owner.id: saved}, [])
    owner.planspace_id = "other"
    store.update_node(owner)
    recovered = NodePosition(x=90, y=100, space="planspace:other")
    assert store.update_node_positions(project.id, {owner.id: recovered}, [], only_missing=True) == {owner.id: recovered}
    alternative = NodePosition(x=1, y=2, space="planspace:other")
    assert store.update_node_positions(project.id, {owner.id: alternative}, [], only_missing=True) == {owner.id: recovered}


@pytest.mark.parametrize("failure", ["invalid_json", "invalid_position", "future_schema", "unreadable"])
@pytest.mark.parametrize("local", [False, True])
def test_bad_layout_is_fatal_only_for_local_shard(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, failure: str, local: bool,
) -> None:
    store = Store(tmp_path)
    project = store.create_project(Project(root_path=str(tmp_path)))
    owner = store.create_node(Node(model_preset_id="opus-4-8", project_id=project.id))
    saved = NodePosition(x=70, y=80, space="canvas")
    store.update_node_positions(project.id, {owner.id: saved}, [])
    host_id = store.machine.id if local else "peer"
    layout_path = tmp_path / "projects" / project.id / "hosts" / host_id / "node-layout.json"
    payload: dict[str, Any] = {"schema_version": 1, "nodes": {owner.id: saved.model_dump()}}
    if failure == "future_schema":
        payload["schema_version"] = 2
    elif failure == "invalid_position":
        payload["nodes"][owner.id]["x"] = "invalid"
    atomic_json(layout_path, payload)
    if failure == "invalid_json":
        layout_path.write_text("{", encoding="utf-8")
    before = layout_path.read_bytes()
    read_json = store._read_json

    def read_with_failure(path: Path) -> dict[str, Any]:
        if path == layout_path and failure == "unreadable":
            raise OSError("模拟布局读取失败")
        return read_json(path)

    updated = NodePosition(x=90, y=100, space="canvas")
    with patch.object(store, "_read_json", side_effect=read_with_failure), caplog.at_level(logging.WARNING):
        if local:
            with pytest.raises(MigrationError) as error:
                store.read_node_positions(project.id)
            assert error.value.path == layout_path
            with pytest.raises(MigrationError):
                store.update_node_positions(project.id, {owner.id: updated}, [])
        else:
            assert store.read_node_positions(project.id) == {owner.id: saved}
            assert store.list_projects()[0].node_positions == {owner.id: saved}
            assert store.update_node_positions(project.id, {owner.id: updated}, []) == {owner.id: updated}
            assert str(layout_path) in caplog.text
    assert layout_path.read_bytes() == before
    if not local:
        peer_node = Node(model_preset_id="opus-4-8", project_id=project.id)
        atomic_json(layout_path.parent / "nodes" / peer_node.id / "node.json", peer_node.model_dump(exclude={"provider", "owner_host_id"}))
        atomic_json(layout_path, {"schema_version": 1, "nodes": {peer_node.id: saved.model_dump()}})
        assert store.read_node_positions(project.id) == {owner.id: updated, peer_node.id: saved}
