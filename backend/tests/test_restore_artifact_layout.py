from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.domain import ArtifactRef, Node, NodeKind, NodePosition, Project
from miniclaw2.migrations.transaction import atomic_json, file_digest
from miniclaw2.node_layout import node_layout_owners
from miniclaw2.restore_artifact_layout import apply_recovery, recovery_plan
from miniclaw2.store import Store


def backup_fixture(root: Path) -> tuple[Store, str, Node, str]:
    store = Store(root)
    project = store.create_project(Project(root_path=str(root)))
    owner = store.create_node(Node(
        model_preset_id="opus-4-8", project_id=project.id, state="done",
        settings_snapshot={"active_planspace_id": "history"},
        artifacts=[ArtifactRef(name=name, bytes=42, mtime=1, sha256="hash", status="published")
                   for name in ["设计 !'()*:/%?#.svg", "report.md", "page.html", "data.json", "last.md"]],
    ))
    tile_id = next(key for key in node_layout_owners([owner]) if key.startswith("artifact:"))
    inventory = {}
    node_relative = f"projects/{project.id}/hosts/{store.machine.id}/nodes/{owner.id}/node.json"
    backup = root / "migration-backups/backup/0"
    atomic_json(backup / node_relative, owner.model_dump(exclude={"provider", "owner_host_id"}))
    inventory[node_relative] = file_digest(backup / node_relative)
    for host, offset in (("peer", 900), (store.machine.id, 0)):
        relative = f"projects/{project.id}/hosts/{host}/layout.json"
        atomic_json(backup / relative, {"layout_hints": {
            tile_id: {"x": 100 + offset, "y": 200 + offset},
            f"artifact-overflow:{owner.id}": {"x": 300 + offset, "y": 400 + offset},
            "artifact:deleted:report.md": {"x": 0, "y": 0},
            owner.id: {"x": 0, "y": 0}, "commit:ghost": {"x": 0, "y": 0},
        }})
        inventory[relative] = file_digest(backup / relative)
    atomic_json(root / ".migration-local/transactions/backup/journal.json", {"phase": "ready", "inputs": [inventory]})
    return store, project.id, owner, tile_id


def test_plan_restores_only_valid_owned_artifacts_without_writes(tmp_path: Path) -> None:
    store, project_id, owner, tile_id = backup_fixture(tmp_path)
    originals = {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    plan = recovery_plan(tmp_path, "backup")
    project = plan["projects"][0]
    assert project["project_id"] == project_id
    assert project["additions"] == {
        tile_id: {"x": 100, "y": 200, "space": "planspace:history"},
        f"artifact-overflow:{owner.id}": {"x": 300, "y": 400, "space": "planspace:history"},
    }
    assert project["sources"][tile_id].endswith(f"hosts/{store.machine.id}/layout.json")
    assert recovery_plan(tmp_path, "backup", project_id="missing")["projects"] == []
    for path, before in originals.items():
        assert path.read_bytes() == before


def test_existing_and_concurrent_positions_win_and_recovery_is_idempotent(tmp_path: Path) -> None:
    store, project_id, owner, tile_id = backup_fixture(tmp_path)
    plan = recovery_plan(tmp_path, "backup")
    manual = NodePosition(x=999, y=888, space="planspace:history")
    store.update_node_positions(project_id, {tile_id: manual, owner.id: manual}, [])
    updates = {key: NodePosition.model_validate(value) for key, value in plan["projects"][0]["additions"].items()}
    positions = store.update_node_positions(project_id, updates, [], only_missing=True)
    assert positions[tile_id] == manual
    assert positions[owner.id] == manual
    assert positions[f"artifact-overflow:{owner.id}"].x == 300
    assert recovery_plan(tmp_path, "backup")["projects"][0]["additions"] == {}
    assert Store(tmp_path).read_node_positions(project_id) == positions
    with pytest.raises(ValueError, match="不能删除"):
        store.update_node_positions(project_id, {}, [tile_id], only_missing=True)
    assert store.read_node_positions(project_id) == positions


@pytest.mark.parametrize("change", ["moved", "dropped", "deleted", "foreign", "unbound", "op"])
def test_obsolete_or_foreign_artifact_positions_are_not_restored(tmp_path: Path, change: str) -> None:
    store, project_id, owner, tile_id = backup_fixture(tmp_path)
    if change == "moved":
        owner.planspace_id = "other"
    elif change == "dropped":
        owner.artifacts = []
    elif change == "op":
        owner.kind = NodeKind.OP
        owner.category = None
    if change in {"moved", "dropped", "op"}:
        store.update_node(owner)
    elif change == "deleted":
        store.delete_node(project_id, owner.id)
    elif change == "foreign":
        source = tmp_path / f"projects/{project_id}/hosts/{store.machine.id}/nodes/{owner.id}"
        target = tmp_path / f"projects/{project_id}/hosts/peer/nodes/{owner.id}"
        target.parent.mkdir(parents=True)
        source.rename(target)
    else:
        (tmp_path / f"projects/{project_id}/hosts/{store.machine.id}/local.json").unlink()
    assert all(not project["additions"] for project in recovery_plan(tmp_path, "backup")["projects"])


@pytest.mark.parametrize("source_kind", ["layout", "node"])
def test_corrupt_backup_is_rejected(tmp_path: Path, source_kind: str) -> None:
    store, project_id, owner, tile_id = backup_fixture(tmp_path)
    relative = f"projects/{project_id}/hosts/{store.machine.id}/"
    relative += "layout.json" if source_kind == "layout" else f"nodes/{owner.id}/node.json"
    (tmp_path / "migration-backups/backup/0" / relative).write_text("{}")
    with pytest.raises(ValueError, match="摘要"):
        recovery_plan(tmp_path, "backup")
    assert store.read_node_positions(project_id) == {}


def test_peer_backup_fills_only_missing_local_hints(tmp_path: Path) -> None:
    store, project_id, owner, tile_id = backup_fixture(tmp_path)
    relative = f"projects/{project_id}/hosts/{store.machine.id}/layout.json"
    source = tmp_path / "migration-backups/backup/0" / relative
    payload = json.loads(source.read_text())
    del payload["layout_hints"][tile_id]
    atomic_json(source, payload)
    journal_path = tmp_path / ".migration-local/transactions/backup/journal.json"
    journal = json.loads(journal_path.read_text())
    journal["inputs"][0][relative] = file_digest(source)
    atomic_json(journal_path, journal)
    project = recovery_plan(tmp_path, "backup")["projects"][0]
    assert project["additions"][tile_id]["x"] == 1000
    assert project["additions"][f"artifact-overflow:{owner.id}"]["x"] == 300


def test_recovery_checks_historical_parent_coordinate_space(tmp_path: Path) -> None:
    store, project_id, owner, tile_id = backup_fixture(tmp_path)
    parent = store.create_node(Node(model_preset_id="opus-4-8", project_id=project_id, planspace_id="history"))
    owner.parent_node_id = parent.id
    owner.settings_snapshot = {}
    store.update_node(owner)
    journal_path = tmp_path / ".migration-local/transactions/backup/journal.json"
    journal = json.loads(journal_path.read_text())
    for node in [owner, parent]:
        relative = f"projects/{project_id}/hosts/{store.machine.id}/nodes/{node.id}/node.json"
        source = tmp_path / "migration-backups/backup/0" / relative
        atomic_json(source, node.model_dump(exclude={"provider", "owner_host_id"}))
        journal["inputs"][0][relative] = file_digest(source)
    atomic_json(journal_path, journal)
    assert recovery_plan(tmp_path, "backup")["projects"][0]["additions"][tile_id]["space"] == "planspace:history"
    parent.planspace_id = "moved"
    store.update_node(parent)
    assert recovery_plan(tmp_path, "backup")["projects"][0]["additions"] == {}


@pytest.mark.parametrize("coordinate", [True, float("inf"), "100"])
def test_invalid_historical_coordinates_are_rejected(tmp_path: Path, coordinate: object) -> None:
    store, project_id, owner, tile_id = backup_fixture(tmp_path)
    relative = f"projects/{project_id}/hosts/{store.machine.id}/layout.json"
    source = tmp_path / "migration-backups/backup/0" / relative
    payload = json.loads(source.read_text())
    payload["layout_hints"][tile_id]["x"] = coordinate
    atomic_json(source, payload)
    journal_path = tmp_path / ".migration-local/transactions/backup/journal.json"
    journal = json.loads(journal_path.read_text())
    journal["inputs"][0][relative] = file_digest(source)
    atomic_json(journal_path, journal)
    with pytest.raises(ValueError):
        recovery_plan(tmp_path, "backup")
    assert store.read_node_positions(project_id) == {}


def test_recovery_uses_live_api_and_preserves_a_drag_after_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, project_id, owner, tile_id = backup_fixture(tmp_path)
    monkeypatch.setenv("MINICLAW_HOME", str(tmp_path))
    plan = recovery_plan(tmp_path, "backup")
    with TestClient(create_app()) as client:
        def request(server: str, path: str, payload: dict | None = None) -> dict:
            if payload is None:
                response = client.get(path)
            else:
                dragged = client.patch(path, json={"updates": {
                    tile_id: {"x": 999, "y": 888, "space": "planspace:history"},
                }})
                assert dragged.status_code == 200, dragged.text
                response = client.patch(path, json=payload)
            assert response.status_code == 200, response.text
            return response.json()

        monkeypatch.setattr("miniclaw2.restore_artifact_layout._request_json", request)
        assert apply_recovery(plan, "http://testserver") == {project_id: 1}
        fetched = client.get(f"/sessions/{project_id}").json()["node_positions"]
        assert fetched[tile_id]["x"] == 999
        assert fetched[f"artifact-overflow:{owner.id}"]["x"] == 300
        assert apply_recovery(plan, "http://testserver") == {}
        assert client.patch(f"/sessions/{project_id}/node-layout", json={
            "only_missing": True, "remove": [tile_id],
        }).status_code == 409
    with TestClient(create_app()) as restarted:
        assert restarted.get(f"/sessions/{project_id}").json()["node_positions"] == fetched


@pytest.mark.parametrize("field,value", [("local_machine_id", "wrong"), ("root_path", "wrong"), ("read_only", True), ("bound_here", False)])
def test_recovery_refuses_wrong_or_readonly_server_before_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object) -> None:
    store, project_id, owner, tile_id = backup_fixture(tmp_path)
    plan = recovery_plan(tmp_path, "backup")
    session = {"local_machine_id": store.machine.id, "root_path": str(tmp_path), "read_only": False, "bound_here": True}
    session[field] = value

    def request(server: str, path: str, payload: dict | None = None) -> dict:
        assert payload is None
        return session

    monkeypatch.setattr("miniclaw2.restore_artifact_layout._request_json", request)
    with pytest.raises(ValueError):
        apply_recovery(plan, "http://testserver")
