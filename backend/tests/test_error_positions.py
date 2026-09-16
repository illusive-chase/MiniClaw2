from __future__ import annotations

import json
from pathlib import Path

import pytest

from miniclaw2.domain import Node, NodeKind, NodePosition, NodeState, Project
from miniclaw2.migrations.transaction import atomic_json
from miniclaw2.store import Store


@pytest.mark.parametrize("change", ["success", "empty_error", "op", "lane"])
@pytest.mark.parametrize("only_missing", [False, True])
def test_error_position_survives_hidden_state_and_unrelated_writes(
    tmp_path: Path, change: str, only_missing: bool,
) -> None:
    store = Store(tmp_path)
    project = store.create_project(Project(root_path=str(tmp_path)))
    owner = store.create_node(Node(
        model_preset_id="opus-4-8", project_id=project.id, state="error", error="测试失败",
        settings_snapshot={"active_planspace_id": "history"},
    ))
    original = owner.model_copy(deep=True)
    other = store.create_node(Node(model_preset_id="opus-4-8", project_id=project.id))
    terminal_id = f"err:{owner.id}"
    position = NodePosition(x=500, y=800, space="planspace:history")
    store.update_node_positions(project.id, {terminal_id: position}, [])
    if change == "success":
        owner.state = NodeState.DONE
    elif change == "empty_error":
        owner.error = ""
    elif change == "op":
        owner.kind = NodeKind.OP
        owner.category = None
    else:
        owner.planspace_id = "new"
    store.update_node(owner)
    assert store.read_node_positions(project.id) == {}
    with pytest.raises(ValueError):
        store.update_node_positions(project.id, {terminal_id: position}, [])
    other_position = NodePosition(x=1, y=2, space="canvas")
    store.update_node_positions(project.id, {other.id: other_position}, [], only_missing=only_missing)
    local_path = tmp_path / "projects" / project.id / "hosts" / store.machine.id / "node-layout.json"
    assert json.loads(local_path.read_text())["nodes"][terminal_id] == position.model_dump()
    store.update_node(original)
    assert Store(tmp_path).read_node_positions(project.id) == {terminal_id: position, other.id: other_position}
    assert store.update_node_positions(project.id, {}, [terminal_id]) == {other.id: other_position}


def test_error_positions_respect_owner_host(tmp_path: Path) -> None:
    store = Store(tmp_path)
    project = store.create_project(Project(root_path=str(tmp_path)))
    foreign = Node(model_preset_id="opus-4-8", project_id=project.id, state="error", error="远端失败")
    peer = tmp_path / "projects" / project.id / "hosts/peer"
    atomic_json(peer / "nodes" / foreign.id / "node.json", foreign.model_dump(exclude={"provider", "owner_host_id"}))
    terminal_id = f"err:{foreign.id}"
    position = NodePosition(x=30, y=40, space="canvas")
    atomic_json(peer / "node-layout.json", {"schema_version": 1, "nodes": {terminal_id: position.model_dump()}})
    assert store.read_node_positions(project.id) == {terminal_id: position}
    for updates, remove in [({terminal_id: position}, []), ({}, [terminal_id])]:
        with pytest.raises(ValueError):
            store.update_node_positions(project.id, updates, remove)
