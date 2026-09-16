from __future__ import annotations

from pathlib import Path

import pytest

from miniclaw2.domain import ContextLayout, Node, NodePosition, Project
from miniclaw2.git_layout import check_context_layout_conflicts
from miniclaw2.migrations.errors import MigrationError
from miniclaw2.migrations.transaction import atomic_json
from miniclaw2.migrations.validation import validate
from miniclaw2.store import Store


CONTEXT = "ctx:project-root::context::CONTEXT.md"
LANE_CONTEXT = "ctx:contextspace::planspace::planspaces/lane/CONTEXT.md"
POSITION = {"x": 400.0, "y": 800.0, "space": "canvas"}


def test_context_positions_are_shared_and_retained_without_owners(tmp_path: Path) -> None:
    store = Store(tmp_path)
    project = store.create_project(Project(root_path=str(tmp_path)))
    owner = store.create_node(Node(model_preset_id="opus-4-8", project_id=project.id))
    expected = {CONTEXT: NodePosition(**POSITION)}
    assert store.read_context_positions(project.id) == {}
    assert store.update_context_positions(project.id, expected, []) == expected
    store.delete_node(project.id, owner.id)
    assert store.update_context_positions(project.id, {}, []) == expected
    lane_position = NodePosition(x=700, y=900, space="planspace:lane")
    expected[LANE_CONTEXT] = lane_position
    assert store.update_context_positions(project.id, {LANE_CONTEXT: lane_position}, []) == expected
    assert Store(tmp_path).read_context_positions(project.id) == expected
    assert store.read_node_positions(project.id) == {}
    assert store.update_context_positions(project.id, {}, [CONTEXT]) == {LANE_CONTEXT: lane_position}
    binding = tmp_path / "projects" / project.id / "hosts" / store.machine.id / "local.json"
    binding.unlink()
    assert store.read_context_positions(project.id) == {LANE_CONTEXT: lane_position}
    with pytest.raises(ValueError, match="未绑定"):
        store.update_context_positions(project.id, {}, [LANE_CONTEXT])


@pytest.mark.parametrize("payload", [
    {"schema_version": 2, "nodes": {}},
    {"schema_version": True, "nodes": {}},
    {"schema_version": 1, "nodes": {"ctx:bad": POSITION}},
    {"schema_version": 1, "nodes": {"err:owner": POSITION}},
    *({"schema_version": 1, "nodes": {CONTEXT: position}} for position in [
        {**POSITION, "x": float("nan")}, {**POSITION, "y": float("inf")},
        {**POSITION, "x": True}, {**POSITION, "space": "planspace:"},
        {**POSITION, "space": "other"}, {**POSITION, "unknown": 1},
    ]),
])
def test_context_schema_rejects_invalid_positions(payload: dict) -> None:
    with pytest.raises(ValueError):
        ContextLayout.model_validate(payload)


def test_context_layout_is_validated_in_shared_tree(tmp_path: Path) -> None:
    store = Store(tmp_path)
    project = store.create_project(Project(root_path=str(tmp_path)))
    store.update_context_positions(project.id, {CONTEXT: NodePosition(**POSITION)}, [])
    validate(tmp_path)
    path = tmp_path / "projects" / project.id / "context-layout.json"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        store.update_context_positions(project.id, {CONTEXT: NodePosition(x=1, y=2, space="canvas")}, ["err:owner"])
    assert path.read_bytes() == before
    atomic_json(path, {"schema_version": 2, "nodes": {}})
    with pytest.raises(MigrationError):
        validate(tmp_path)


def test_context_sync_coordinates_are_atomic(tmp_path: Path) -> None:
    roots = [tmp_path / name for name in ("base", "local", "remote")]
    relative = "projects/project/context-layout.json"
    for root, position in zip(roots, [POSITION, {**POSITION, "x": 90}, {**POSITION, "y": 100}]):
        atomic_json(root / "projects/project/project.json", {"id": "project"})
        atomic_json(root / relative, {"schema_version": 1, "nodes": {CONTEXT: position}})
    with pytest.raises(MigrationError, match="上下文位置冲突"):
        check_context_layout_conflicts(*roots)
    atomic_json(roots[2] / relative, {"schema_version": 1, "nodes": {CONTEXT: POSITION, LANE_CONTEXT: POSITION}})
    check_context_layout_conflicts(*roots)
    atomic_json(roots[2] / relative, {"schema_version": 1, "nodes": {CONTEXT: {**POSITION, "x": 90}}})
    check_context_layout_conflicts(*roots)


@pytest.mark.parametrize("deleted_side", ["local", "remote", "both"])
@pytest.mark.parametrize("whole_file", [False, True])
def test_context_deletion_requires_agreement(tmp_path: Path, deleted_side: str, whole_file: bool) -> None:
    roots = {name: tmp_path / name for name in ("base", "local", "remote")}
    for name, root in roots.items():
        atomic_json(root / "projects/project/project.json", {"id": "project"})
        deleted = name != "base" and deleted_side in (name, "both")
        if not (deleted and whole_file):
            atomic_json(root / "projects/project/context-layout.json", {
                "schema_version": 1, "nodes": {} if deleted else {CONTEXT: POSITION},
            })
    if deleted_side == "both":
        check_context_layout_conflicts(roots["base"], roots["local"], roots["remote"])
    else:
        with pytest.raises(MigrationError, match="上下文位置冲突"):
            check_context_layout_conflicts(roots["base"], roots["local"], roots["remote"])
