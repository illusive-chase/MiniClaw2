from __future__ import annotations

import json
from pathlib import Path

import pytest

from miniclaw2.domain import Node, NodeLayout, NodePosition, Project
from miniclaw2.migrations.errors import MigrationError
from miniclaw2.migrations.transaction import atomic_json
from miniclaw2.store import Store


def test_owner_union_filters_stale_foreign_and_dangling_entries(tmp_path: Path) -> None:
    store = Store(tmp_path)
    project = store.create_project(Project(root_path=str(tmp_path)))
    native = store.create_node(Node(model_preset_id="opus-4-8", project_id=project.id))
    foreign = Node(model_preset_id="opus-4-8", project_id=project.id, planspace_id="remote")
    peer = tmp_path / "projects" / project.id / "hosts/peer"
    atomic_json(peer / "nodes" / foreign.id / "node.json", foreign.model_dump(exclude={"provider", "owner_host_id"}))
    remote_position = {"x": 30, "y": 40, "space": "planspace:remote"}
    local_position = {"x": 10, "y": 20, "space": "canvas"}
    peer_file = peer / "node-layout.json"
    atomic_json(peer_file, {"schema_version": 1, "nodes": {foreign.id: remote_position, native.id: {**local_position, "x": 999}}})
    original_peer = peer_file.read_bytes()
    local_file = tmp_path / "projects" / project.id / "hosts" / store.machine.id / "node-layout.json"
    atomic_json(local_file, {"schema_version": 1, "nodes": {native.id: local_position, foreign.id: remote_position, "gone": local_position}})
    positions = store.read_node_positions(project.id)
    assert {key: value.model_dump() for key, value in positions.items()} == {native.id: local_position, foreign.id: remote_position}
    with pytest.raises(ValueError):
        store.update_node_positions(project.id, {foreign.id: NodePosition(**remote_position)}, [])
    store.update_node_positions(project.id, {}, [])
    assert set(json.loads(local_file.read_text())["nodes"]) == {native.id}
    assert peer_file.read_bytes() == original_peer
    native.planspace_id = "moved"
    store.update_node(native)
    assert native.id not in store.read_node_positions(project.id)
    store.delete_node(project.id, native.id)
    assert native.id not in store.read_node_positions(project.id)
    store.update_node_positions(project.id, {}, [])
    assert json.loads(local_file.read_text())["nodes"] == {}
    (local_file.parent / "local.json").unlink()
    assert store.read_node_positions(project.id)[foreign.id].model_dump() == remote_position
    with pytest.raises(ValueError, match="未绑定"):
        store.update_node_positions(project.id, {}, [])


def test_duplicate_node_owner_rejected_on_single_and_aggregate_reads(tmp_path: Path) -> None:
    store = Store(tmp_path)
    project = store.create_project(Project(root_path=str(tmp_path)))
    node = store.create_node(Node(model_preset_id="opus-4-8", project_id=project.id))
    duplicate = tmp_path / "projects" / project.id / "hosts/peer/nodes" / node.id / "node.json"
    atomic_json(duplicate, node.model_dump(exclude={"provider", "owner_host_id"}))
    with pytest.raises(MigrationError):
        store.load_node(project.id, node.id)
    with pytest.raises(MigrationError):
        store.read_node_positions(project.id)


@pytest.mark.parametrize("payload", [{}, {"nodes": {}}, {"schema_version": True, "nodes": {}}, {"schema_version": 2, "nodes": {}}])
def test_layout_version_is_explicit_and_strict(payload: dict) -> None:
    with pytest.raises(ValueError):
        NodeLayout.model_validate(payload)
