from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from miniclaw2.domain import Node, Project
from miniclaw2.migrations.catalog import marker
from miniclaw2.migrations.coordinator import coordinator
from miniclaw2.migrations.errors import MigrationError
from miniclaw2.migrations.impact import layout_impact
from miniclaw2.migrations.steps.v0016_node_layout import MIGRATION
from miniclaw2.migrations.sync_tree import merge_remote, normalize
from miniclaw2.migrations.transaction import atomic_json
from miniclaw2.store import Store
from miniclaw2.sync import ensure_machine_identity


def seed(root: Path, version: int = 15) -> tuple[str, dict[str, Path]]:
    project = Project(id="project", root_path="/tmp/layout-test")
    atomic_json(root / "schema.json", marker(version))
    atomic_json(root / "projects/project/project.json", project.model_dump(exclude={"provider", "root_path", "node_positions"}))
    hosts = {owner: root / "projects/project/hosts" / owner for owner in ("a", "b")}
    for owner, host in hosts.items():
        node = Node(model_preset_id="opus-4-8", id=owner, project_id=project.id)
        if owner == "a":
            node.settings_snapshot = {"active_planspace_id": "history"}
        else:
            node.parent_node_id = "a"
        atomic_json(host / "host.json", {"label": owner, "bound_at": 1, "repo": {}})
        atomic_json(host / "nodes" / owner / "node.json", node.model_dump(exclude={"provider", "owner_host_id"}))
        atomic_json(host / "layout.json", {
            "layout_hints": {"a": {"x": 10, "y": 20}, "b": {"x": 30, "y": 40}, "commit:ghost": {"x": 0, "y": 0}},
            "layout_viewport": {"x": 1, "y": 2, "zoom": 1},
        })
    return project.id, hosts


@pytest.mark.parametrize("version", [14, 15])
def test_confirmation_backup_and_historical_spaces(tmp_path: Path, version: int) -> None:
    project_id, hosts = seed(tmp_path, version)
    original = (hosts["a"] / "layout.json").read_bytes()
    (hosts["a"] / "layout.json").chmod(0o640)
    with pytest.raises(MigrationError) as error:
        Store(tmp_path)
    assert error.value.state == "migration_required"
    assert (hosts["a"] / "layout.json").read_bytes() == original
    storage = coordinator(tmp_path)
    storage.apply(ensure_machine_identity(tmp_path).id, accept_data_loss=True)
    store = Store(tmp_path)
    positions = store.read_node_positions(project_id)
    assert positions["a"].model_dump() == {"x": 10, "y": 20, "space": "planspace:history"}
    assert positions["b"].model_dump() == {"x": 30, "y": 40, "space": "planspace:history"}
    for owner, host in hosts.items():
        assert not (host / "layout.json").exists()
        assert set(json.loads((host / "node-layout.json").read_text())["nodes"]) == {owner}
    backups = list((tmp_path / "migration-backups").rglob("hosts/a/layout.json"))
    assert backups and all(path.read_bytes() == original and path.stat().st_mode & 0o777 == 0o640 for path in backups)
    receipt = json.loads((tmp_path / ".migration-local/state.json").read_text())
    assert MIGRATION.contract in receipt["accepted_migration_contracts"]


def test_ambiguous_owner_aborts_without_publishing(tmp_path: Path) -> None:
    _, hosts = seed(tmp_path)
    node = json.loads((hosts["a"] / "nodes/a/node.json").read_text())
    atomic_json(hosts["b"] / "nodes/a/node.json", node)
    with pytest.raises(MigrationError, match="唯一性"):
        coordinator(tmp_path).apply(ensure_machine_identity(tmp_path).id, accept_data_loss=True)
    assert json.loads((tmp_path / "schema.json").read_text())["schema_version"] == 15
    assert all((host / "layout.json").exists() for host in hosts.values())


def git(root: Path, *arguments: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.mark.parametrize("ancestor_version", [14, 15])
@pytest.mark.parametrize("peer_upgraded", [False, True])
def test_three_way_sync_preserves_each_owners_offline_move(tmp_path: Path, ancestor_version: int, peer_upgraded: bool) -> None:
    root = tmp_path / "local"
    peer = tmp_path / "peer"
    root.mkdir()
    _, hosts = seed(root, ancestor_version)
    git(root, "init", "-q")
    git(root, "config", "user.name", "test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-qm", "old common ancestor")
    ancestor = git(root, "rev-parse", "HEAD")
    git(root, "worktree", "add", "-qb", "peer", str(peer))
    accepted = frozenset({MIGRATION.contract})
    normalize(root, accepted)
    position_a = hosts["a"] / "node-layout.json"
    payload = json.loads(position_a.read_text())
    payload["nodes"]["a"]["x"] = 111
    atomic_json(position_a, payload)
    git(root, "add", ".")
    git(root, "commit", "-qm", "local upgrade and move")
    if peer_upgraded:
        normalize(peer, accepted)
    position_b = peer / "projects/project/hosts/b" / ("node-layout.json" if peer_upgraded else "layout.json")
    payload = json.loads(position_b.read_text())
    payload["nodes" if peer_upgraded else "layout_hints"]["b"]["y"] = 222
    atomic_json(position_b, payload)
    git(peer, "add", ".")
    git(peer, "commit", "-qm", "peer offline move")
    atomic_json(root / ".migration-local/state.json", {**marker(), "accepted_migration_contracts": list(accepted)})
    assert merge_remote(root, "peer")
    assert json.loads(position_a.read_text())["nodes"]["a"]["x"] == 111
    assert json.loads((hosts["b"] / "node-layout.json").read_text())["nodes"]["b"]["y"] == 222
    assert not list(root.glob("projects/*/hosts/*/layout.json"))
    assert git(root, "show", f"{ancestor}:projects/project/hosts/a/layout.json")


def test_sync_normalization_requires_local_confirmation(tmp_path: Path) -> None:
    seed(tmp_path)
    with pytest.raises(MigrationError, match="确认"):
        normalize(tmp_path)


def test_plan_counts_losses_without_modifying_sources(tmp_path: Path) -> None:
    _, hosts = seed(tmp_path)
    before = {host: (path / "layout.json").read_bytes() for host, path in hosts.items()}
    impact = layout_impact(tmp_path)
    assert len(impact) == 2
    assert all(item["retained"] == 1 and item["discarded_foreign"] == 1 and item["discarded_synthetic_or_missing"] == 1 and item["viewport_discarded"] for item in impact)
    assert before == {host: (path / "layout.json").read_bytes() for host, path in hosts.items()}
    (hosts["a"] / "layout.json").unlink()
    atomic_json(hosts["b"] / "layout.json", {"layout_hints": {}})
    assert [item["source"] for item in layout_impact(tmp_path)] == ["missing", "empty"]


def test_layout_publish_recovers_after_partial_replacement(tmp_path: Path) -> None:
    from miniclaw2.migrations import transaction as module

    _, hosts = seed(tmp_path)
    storage = coordinator(tmp_path)
    machine_id = ensure_machine_identity(tmp_path).id
    copy = module.durable_copy

    def interrupt(source: Path, destination: Path) -> None:
        if destination == hosts["b"] / "node-layout.json":
            raise OSError("注入布局发布中断")
        copy(source, destination)

    with patch.object(module, "durable_copy", side_effect=interrupt), pytest.raises(MigrationError):
        storage.apply(machine_id, accept_data_loss=True)
    assert json.loads((tmp_path / "schema.json").read_text())["schema_version"] == 15
    storage.apply(machine_id)
    assert json.loads((tmp_path / "schema.json").read_text()) == marker()
    assert all((host / "node-layout.json").is_file() and not (host / "layout.json").exists() for host in hosts.values())
    assert not (tmp_path / ".migration-local/pending.json").exists()


def test_copy_does_not_inherit_loss_confirmation(tmp_path: Path) -> None:
    source, copied = tmp_path / "source", tmp_path / "copied"
    seed(source)
    coordinator(source).apply(ensure_machine_identity(source).id, accept_data_loss=True)
    shutil.copytree(source, copied)
    store = Store(copied)
    receipt = json.loads((copied / ".migration-local/state.json").read_text())
    assert receipt["accepted_migration_contracts"] == []
    store.coordinator.apply(store.machine.id, accept_data_loss=True)
    receipt = json.loads((copied / ".migration-local/state.json").read_text())
    assert receipt["accepted_migration_contracts"] == [MIGRATION.contract]


@pytest.mark.parametrize("value", [True, "12", float("inf"), None])
def test_invalid_owned_coordinate_does_not_publish(tmp_path: Path, value: object) -> None:
    _, hosts = seed(tmp_path)
    original = json.loads((hosts["a"] / "layout.json").read_text())
    original["layout_hints"]["a"]["x"] = value
    atomic_json(hosts["a"] / "layout.json", original)
    with pytest.raises(MigrationError, match="有限数值"):
        coordinator(tmp_path).apply(ensure_machine_identity(tmp_path).id, accept_data_loss=True)
    assert json.loads((tmp_path / "schema.json").read_text())["schema_version"] == 15
