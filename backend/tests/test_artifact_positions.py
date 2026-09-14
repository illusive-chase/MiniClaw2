from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest

from miniclaw2.domain import ArtifactRef, Node, NodeKind, NodePosition, Project
from miniclaw2.migrations.transaction import atomic_json
from miniclaw2.store import Store


def published_artifacts() -> list[ArtifactRef]:
    return [
        ArtifactRef(name=name, bytes=42, mtime=1, sha256="hash", status="published")
        for name in ["设计 /draft:100%?#.svg", "report.md", "page.html", "data.json", "last.md"]
    ]


def artifact_id(node: Node) -> str:
    return f"artifact:{node.id}:{quote(node.artifacts[0].name, safe='')}"


def test_artifact_layout_owner_union_and_atomic_validation(tmp_path: Path) -> None:
    store = Store(tmp_path)
    project = store.create_project(Project(root_path=str(tmp_path)))
    native = store.create_node(Node(
        model_preset_id="opus-4-8", project_id=project.id,
        settings_snapshot={"active_planspace_id": "history"}, artifacts=published_artifacts(),
    ))
    foreign = Node(
        model_preset_id="opus-4-8", project_id=project.id,
        parent_node_id=native.id, artifacts=published_artifacts(),
    )
    peer = tmp_path / "projects" / project.id / "hosts/peer"
    atomic_json(peer / "nodes" / foreign.id / "node.json", foreign.model_dump(exclude={"provider", "owner_host_id"}))
    position = NodePosition(x=830, y=760, space="planspace:history")
    tile_id = artifact_id(native)
    overflow_id = f"artifact-overflow:{native.id}"
    foreign_id = artifact_id(foreign)
    peer_layout = peer / "node-layout.json"
    atomic_json(peer_layout, {"schema_version": 1, "nodes": {
        foreign_id: position.model_dump(), tile_id: {**position.model_dump(), "x": 999},
    }})
    peer_before = peer_layout.read_bytes()
    expected = {tile_id: position, overflow_id: position, foreign_id: position}
    assert store.update_node_positions(project.id, {tile_id: position, overflow_id: position}, []) == expected
    assert Store(tmp_path).read_node_positions(project.id) == expected
    local_layout = tmp_path / "projects" / project.id / "hosts" / store.machine.id / "node-layout.json"
    before = local_layout.read_bytes()
    for invalid_id in [foreign_id, f"artifact:{native.id}:missing.md", "artifact:missing:report.md", "context:fake"]:
        with pytest.raises(ValueError):
            store.update_node_positions(project.id, {native.id: position, invalid_id: position}, [])
        with pytest.raises(ValueError):
            store.update_node_positions(project.id, {}, [invalid_id])
        assert local_layout.read_bytes() == before
    with pytest.raises(ValueError, match="坐标空间"):
        store.update_node_positions(project.id, {tile_id: NodePosition(x=1, y=2, space="canvas")}, [])
    assert store.update_node_positions(project.id, {}, [overflow_id]) == {tile_id: position, foreign_id: position}
    assert peer_layout.read_bytes() == peer_before
    native.planspace_id = "moved"
    store.update_node(native)
    assert store.read_node_positions(project.id) == {}
    store.update_node_positions(project.id, {}, [])
    assert json.loads(local_layout.read_text())["nodes"] == {tile_id: position.model_dump()}
    native.planspace_id = None
    store.update_node(native)
    assert store.read_node_positions(project.id) == {tile_id: position, foreign_id: position}


@pytest.mark.parametrize("change", ["dropped", "removed", "deleted", "overflow", "op"])
@pytest.mark.parametrize("only_missing", [False, True])
def test_artifact_layout_retains_temporarily_hidden_tiles(
    tmp_path: Path, change: str, only_missing: bool,
) -> None:
    store = Store(tmp_path)
    project = store.create_project(Project(root_path=str(tmp_path)))
    owner = store.create_node(Node(model_preset_id="opus-4-8", project_id=project.id, artifacts=published_artifacts()))
    original = owner.model_copy(deep=True)
    unrelated = store.create_node(Node(model_preset_id="opus-4-8", project_id=project.id))
    tile_id = f"artifact-overflow:{owner.id}" if change == "overflow" else artifact_id(owner)
    position = NodePosition(x=50, y=60, space="canvas")
    store.update_node_positions(project.id, {tile_id: position}, [])
    if change == "deleted":
        store.delete_node(project.id, owner.id)
    else:
        if change == "dropped":
            owner.artifacts[0].status = "dropped"
        elif change == "removed":
            owner.artifacts = []
        elif change == "overflow":
            owner.artifacts = owner.artifacts[:4]
        else:
            owner.kind = NodeKind.OP
            owner.category = None
        store.update_node(owner)
    assert store.read_node_positions(project.id) == {}
    with pytest.raises(ValueError):
        store.update_node_positions(project.id, {tile_id: position}, [])
    assert store.update_node_positions(
        project.id, {unrelated.id: position}, [], only_missing=only_missing,
    ) == {unrelated.id: position}
    local_layout = tmp_path / "projects" / project.id / "hosts" / store.machine.id / "node-layout.json"
    assert json.loads(local_layout.read_text())["nodes"][tile_id] == position.model_dump()
    if change == "deleted":
        store.create_node(original)
    else:
        store.update_node(original)
    assert Store(tmp_path).read_node_positions(project.id) == {tile_id: position, unrelated.id: position}
    assert store.update_node_positions(project.id, {}, [tile_id]) == {unrelated.id: position}
    assert tile_id not in json.loads(local_layout.read_text())["nodes"]
