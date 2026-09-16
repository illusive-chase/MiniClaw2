from __future__ import annotations

import json
from pathlib import Path

import pytest

from miniclaw2.domain import LaneLayout
from miniclaw2.git_layout import check_lane_layout_conflicts
from miniclaw2.migrations.errors import MigrationError
from miniclaw2.migrations.transaction import atomic_json, file_digest
from miniclaw2.migrations.validation import validate
from miniclaw2.restore_git_layout import apply_recovery, recovery_plan


POSITION = {"x": -1704.0, "y": 3480.0, "space": "canvas"}


def test_lane_coordinate_is_atomic_in_sync(tmp_path: Path) -> None:
    base, local, remote = (tmp_path / name for name in ("base", "local", "remote"))
    relative = "projects/project/lane-layout.json"
    for root, position in ((base, POSITION), (local, {**POSITION, "x": 80}), (remote, {**POSITION, "y": 90})):
        atomic_json(root / "projects/project/project.json", {"id": "project"})
        atomic_json(root / relative, {"schema_version": 1, "nodes": {"planspace:lane": position}})
    with pytest.raises(MigrationError, match="方向位置冲突"):
        check_lane_layout_conflicts(base, local, remote)
    atomic_json(remote / relative, {"schema_version": 1, "nodes": {"planspace:lane": POSITION, "planspace:other": POSITION}})
    check_lane_layout_conflicts(base, local, remote)
    atomic_json(remote / relative, {"schema_version": 1, "nodes": {}})
    with pytest.raises(MigrationError, match="方向位置冲突"):
        check_lane_layout_conflicts(base, local, remote)


@pytest.mark.parametrize("deleted_side", ["local", "remote", "both"])
@pytest.mark.parametrize("whole_file", [False, True])
def test_lane_deletion_requires_agreement(tmp_path: Path, deleted_side: str, whole_file: bool) -> None:
    roots = {name: tmp_path / name for name in ("base", "local", "remote")}
    for name, root in roots.items():
        atomic_json(root / "projects/project/project.json", {"id": "project"})
        deleted = name != "base" and deleted_side in (name, "both")
        if not (deleted and whole_file):
            atomic_json(root / "projects/project/lane-layout.json", {
                "schema_version": 1, "nodes": {} if deleted else {"planspace:lane": POSITION},
            })
    if deleted_side == "both":
        check_lane_layout_conflicts(roots["base"], roots["local"], roots["remote"])
    else:
        with pytest.raises(MigrationError, match="planspace:lane") as error:
            check_lane_layout_conflicts(roots["base"], roots["local"], roots["remote"])
        assert error.value.state == "schema_conflict"


@pytest.mark.parametrize("deleted_side", ["local", "remote"])
def test_deleted_project_does_not_block_lane_merge(tmp_path: Path, deleted_side: str) -> None:
    for name in ("base", "local", "remote"):
        if name != deleted_side:
            atomic_json(tmp_path / name / "projects/project/project.json", {"id": "project"})
            atomic_json(tmp_path / name / "projects/project/lane-layout.json", {"schema_version": 1, "nodes": {"planspace:lane": POSITION}})
    check_lane_layout_conflicts(tmp_path / "base", tmp_path / "local", tmp_path / "remote")


def test_lane_recovery_preserves_git_and_owner_records(tmp_path: Path) -> None:
    inventory = {}
    for project_id in ("one", "two", "deleted"):
        if project_id != "deleted":
            atomic_json(tmp_path / f"projects/{project_id}/project.json", {"id": project_id})
            atomic_json(tmp_path / f"projects/{project_id}/git-layout.json", {"schema_version": 1, "nodes": {"commit:ghost": POSITION}})
            atomic_json(tmp_path / f"projects/{project_id}/hosts/native/node-layout.json", {"schema_version": 1, "nodes": {"agent": {"x": 40, "y": 160, "space": "planspace:lane"}}})
        for host in ("native", "peer"):
            relative = f"projects/{project_id}/hosts/{host}/layout.json"
            path = tmp_path / "migration-backups/backup/0" / relative
            hints = {"planspace:lane": {"x": POSITION["x"] if host == "native" else 999, "y": POSITION["y"]}, "commit:ghost": {"x": 0, "y": 0}}
            if host == "peer":
                hints["planspace:extra"] = {"x": 4000, "y": 0}
            atomic_json(path, {"layout_hints": hints})
            inventory[relative] = file_digest(path)
    atomic_json(tmp_path / ".migration-local/transactions/backup/journal.json", {"inputs": [inventory]})
    originals = {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    plan = recovery_plan(tmp_path, "backup", "native", kind="lane")
    assert {project["project_id"] for project in plan["projects"]} == {"one", "two"}
    assert plan["projects"][0]["additions"]["planspace:lane"] == POSITION
    assert apply_recovery(tmp_path, plan) == {"one": 2, "two": 2}
    assert apply_recovery(tmp_path, recovery_plan(tmp_path, "backup", "native", kind="lane")) == {}
    assert all(path.read_bytes() == before for path, before in originals.items())
    destination = tmp_path / "projects/one/lane-layout.json"
    payload = json.loads(destination.read_text())
    assert "commit:ghost" not in payload["nodes"]
    payload["nodes"]["planspace:lane"]["x"] = 888
    atomic_json(destination, payload)
    assert recovery_plan(tmp_path, "backup", "native", kind="lane")["projects"][0]["additions"] == {}
    with pytest.raises(FileExistsError):
        apply_recovery(tmp_path, plan)
    assert json.loads(destination.read_text())["nodes"]["planspace:lane"]["x"] == 888


@pytest.mark.parametrize("position", [{**POSITION, "y": float("nan")}, {**POSITION, "x": True}, {**POSITION, "space": "planspace:lane"}])
def test_invalid_lane_coordinates_are_rejected(position: dict) -> None:
    with pytest.raises(ValueError):
        LaneLayout.model_validate({"schema_version": 1, "nodes": {"planspace:lane": position}})


def test_shared_tree_validates_lane_layout(tmp_path: Path) -> None:
    from miniclaw2.domain import Project
    from miniclaw2.store import Store

    store = Store(tmp_path)
    project = store.create_project(Project(root_path=str(tmp_path)))
    atomic_json(tmp_path / f"projects/{project.id}/lane-layout.json", {"schema_version": 1, "nodes": {"planspace:lane": POSITION}})
    validate(tmp_path)
    atomic_json(tmp_path / f"projects/{project.id}/lane-layout.json", {"schema_version": 2, "nodes": {}})
    with pytest.raises(MigrationError):
        validate(tmp_path)
