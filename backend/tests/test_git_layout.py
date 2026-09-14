from __future__ import annotations

import json
from pathlib import Path

import pytest

from miniclaw2.domain import GitLayout
from miniclaw2.git_layout import check_git_layout_conflicts
from miniclaw2.migrations.errors import MigrationError
from miniclaw2.migrations.transaction import atomic_json, file_digest
from miniclaw2.restore_git_layout import apply_recovery, recovery_plan


COMMIT = "commit:" + "a" * 40
OTHER = "commit:" + "b" * 40
POSITION = {"x": 10.0, "y": 20.0, "space": "canvas"}


def test_coordinate_is_atomic_in_three_way_sync(tmp_path: Path) -> None:
    base, local, remote = (tmp_path / name for name in ("base", "local", "remote"))
    relative = "projects/project/git-layout.json"
    for root, position in ((base, POSITION), (local, {**POSITION, "x": 80}), (remote, {**POSITION, "y": 90})):
        atomic_json(root / relative, {"schema_version": 1, "nodes": {COMMIT: position}})
    with pytest.raises(MigrationError, match="位置冲突"):
        check_git_layout_conflicts(base, local, remote)
    atomic_json(remote / relative, {"schema_version": 1, "nodes": {COMMIT: POSITION, OTHER: POSITION}})
    check_git_layout_conflicts(base, local, remote)
    atomic_json(remote / relative, {"schema_version": 1, "nodes": {}})
    with pytest.raises(MigrationError, match="位置冲突"):
        check_git_layout_conflicts(base, local, remote)
    atomic_json(remote / relative, json.loads((local / relative).read_text()))
    check_git_layout_conflicts(base, local, remote)


def backup_fixture(root: Path) -> None:
    inventory = {}
    for project_id in ("one", "two", "deleted"):
        if project_id != "deleted":
            atomic_json(root / "projects" / project_id / "project.json", {"id": project_id})
        for host, hints in (("peer", {COMMIT: {"x": 999, "y": 999}, OTHER: {"x": 50, "y": 60}}),
                            ("native", {COMMIT: {"x": 10, "y": 20}, "commit:ghost": {"x": 70, "y": 80}, "agent": {"x": 0, "y": 0}})):
            relative = f"projects/{project_id}/hosts/{host}/layout.json"
            path = root / "migration-backups/backup/0" / relative
            atomic_json(path, {"layout_hints": hints})
            inventory[relative] = file_digest(path)
    atomic_json(root / ".migration-local/transactions/backup/journal.json", {"inputs": [inventory]})


def test_restore_all_projects_is_verified_additive_and_idempotent(tmp_path: Path) -> None:
    backup_fixture(tmp_path)
    originals = {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    plan = recovery_plan(tmp_path, "backup", "native")
    assert {project["project_id"] for project in plan["projects"]} == {"one", "two"}
    assert plan["projects"][0]["additions"][COMMIT] == POSITION
    assert not list((tmp_path / "projects").glob("*/git-layout.json"))
    assert apply_recovery(tmp_path, plan) == {"one": 3, "two": 3}
    assert apply_recovery(tmp_path, recovery_plan(tmp_path, "backup", "native")) == {}
    for path, original in originals.items():
        assert path.read_bytes() == original
    path = tmp_path / "projects/one/git-layout.json"
    payload = json.loads(path.read_text())
    payload["nodes"][COMMIT]["x"] = 123
    atomic_json(path, payload)
    assert recovery_plan(tmp_path, "backup", "native")["projects"][0]["additions"] == {}
    assert json.loads(path.read_text())["nodes"][COMMIT]["x"] == 123


def test_restore_rejects_corruption_before_any_write(tmp_path: Path) -> None:
    backup_fixture(tmp_path)
    source = tmp_path / "migration-backups/backup/0/projects/two/hosts/peer/layout.json"
    source.write_text("{}")
    with pytest.raises(ValueError, match="摘要"):
        recovery_plan(tmp_path, "backup", "native")
    assert not list((tmp_path / "projects").glob("*/git-layout.json"))


def test_restore_never_overwrites_existing_or_concurrent_writes(tmp_path: Path) -> None:
    backup_fixture(tmp_path)
    plan = recovery_plan(tmp_path, "backup", "native")
    path = tmp_path / "projects/one/git-layout.json"
    atomic_json(path, {"schema_version": 1, "nodes": {COMMIT: {**POSITION, "x": 888}}})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        apply_recovery(tmp_path, plan)
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="已有 Git 布局"):
        apply_recovery(tmp_path, recovery_plan(tmp_path, "backup", "native"))
    assert path.read_bytes() == before


@pytest.mark.parametrize("position", [{**POSITION, "x": float("inf")}, {**POSITION, "y": True}, {**POSITION, "space": "planspace:lane"}])
def test_invalid_git_coordinates_are_rejected(position: dict) -> None:
    with pytest.raises(ValueError):
        GitLayout.model_validate({"schema_version": 1, "nodes": {COMMIT: position}})
