from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pytest

from miniclaw2.domain import Node, Project
from miniclaw2.migrations.errors import MigrationError
from miniclaw2.migrations.transaction import atomic_json
from miniclaw2.store import Store
from miniclaw2.migrations.catalog import marker


def test_v14_code_review_default_uses_remaining_preset(tmp_path: Path) -> None:
    store = Store(tmp_path)
    path = tmp_path / "config.json"
    payload = json.loads(path.read_text())
    payload.pop("code_review")
    payload["defaults"]["default_model_preset_id"] = "opus-4-8"
    payload["model_presets"] = [preset for preset in payload["model_presets"] if preset["id"] != "gpt-5.6"]
    atomic_json(path, payload)
    baseline(tmp_path)
    store.coordinator.apply(store.machine.id)
    assert json.loads(path.read_text())["code_review"] == {"model_preset_id": "opus-4-8"}


def baseline(root: Path) -> None:
    atomic_json(root / "schema.json", {"schema": "node-revision-v9", "schema_version": 14})
    receipt = root / ".migration-local" / "state.json"
    if receipt.exists():
        receipt.unlink()


def test_baseline_converges_config_project_and_events(tmp_path: Path) -> None:
    store = Store(tmp_path)
    project = store.create_project(Project(root_path="/tmp/migration-project"))
    node = store.create_node(Node(project_id=project.id, model_preset_id=project.model_preset_id))
    project_file = tmp_path / "projects" / project.id / "project.json"
    payload = json.loads(project_file.read_text())
    payload.update(active_planspace_id="old", planspace_selection_explicit=True, sharing={})
    atomic_json(project_file, payload)
    config_file = tmp_path / "config.json"
    config = json.loads(config_file.read_text())
    config.pop("code_review")
    config["updates"] = {"enabled": True}
    atomic_json(config_file, config)
    event_file = store.node_dir(project.id, node.id) / "events.jsonl"
    original = json.dumps({"seq": 1, "event": {"type": "interaction_request", "interaction_type": "checkpoint_review"}}) + "\n"
    event_file.write_text(original)
    baseline(tmp_path)
    store.coordinator.ready = False
    migrated = Store(tmp_path)
    assert migrated.list_projects()[0].id == project.id
    assert "updates" not in json.loads(config_file.read_text())
    assert "code_review" in json.loads(config_file.read_text())
    assert migrated.replay_events(project.id, node.id)[0]["event"]["interaction_type"] == "human_review_prose"
    assert event_file.with_name("events.jsonl.original").read_text() == original
    assert any(path.read_text() == original for path in (tmp_path / "migration-backups").rglob("events.jsonl"))


def test_external_context_has_its_own_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, external = tmp_path / "store", tmp_path / "context"
    external.mkdir()
    (external / "bindings" / "projects").mkdir(parents=True)
    binding = external / "bindings" / "projects" / "project.test.yaml"
    binding.write_text("version: 1\nproject:\n  local_paths: [/private/checkout]\n")
    monkeypatch.setenv("MINICLAW_CONTEXT_HOME", str(external))
    store = Store(root)
    assert "local_paths" not in binding.read_text()
    assert (external / ".migration-local" / "state.json").is_file()
    assert store.coordinator._context is not None


def test_bad_records_do_not_advance_cursor(tmp_path: Path) -> None:
    baseline(tmp_path)
    path = tmp_path / "projects" / "broken" / "project.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken")
    with pytest.raises(MigrationError):
        Store(tmp_path)
    assert json.loads((tmp_path / "schema.json").read_text())["schema_version"] == 14
    assert path.read_text() == "{broken"
    assert not (tmp_path / ".migration-local" / "state.json").exists()


def test_bad_yaml_is_a_maintenance_failure(tmp_path: Path) -> None:
    baseline(tmp_path)
    path = tmp_path / "contextspace" / "bindings" / "projects" / "project.bad.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("project: [")
    with pytest.raises(MigrationError) as error:
        Store(tmp_path)
    assert error.value.path == path or error.value.path.name == path.name
    assert json.loads((tmp_path / "schema.json").read_text())["schema_version"] == 14


def test_v14_partial_partition_preserves_owned_binding_and_artifacts(tmp_path: Path) -> None:
    store = Store(tmp_path)
    project = Project(root_path="/tmp/source-checkout", machine_id=store.machine.id, name="legacy")
    project_file = tmp_path / "projects" / project.id / "project.json"
    atomic_json(project_file, project.model_dump(exclude={"provider"}))
    node = Node(project_id=project.id, model_preset_id=project.model_preset_id)
    old = project_file.parent / "nodes" / node.id
    atomic_json(old / "node.json", node.model_dump(exclude={"provider", "owner_host_id"}))
    artifact = old / "artifacts" / "nested" / "raw.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"unaltered evidence\n")
    baseline(tmp_path)
    store.coordinator.apply(store.machine.id)
    loaded = store.list_projects()[0]
    assert loaded.root_path == project.root_path
    assert store.load_node(project.id, node.id).id == node.id
    assert (store.node_dir(project.id, node.id) / "artifacts" / "nested" / "raw.md").read_bytes() == b"unaltered evidence\n"
    assert "root_path" not in json.loads(project_file.read_text())


def test_v14_broken_jsonl_never_advances_schema(tmp_path: Path) -> None:
    store = Store(tmp_path)
    project = store.create_project(Project(root_path="/tmp/events"))
    node = store.create_node(Node(project_id=project.id, model_preset_id=project.model_preset_id))
    events = store.node_dir(project.id, node.id) / "events.jsonl"
    events.write_text('{"event": {"type": "text"}, "seq": 1}')
    baseline(tmp_path)
    with pytest.raises(MigrationError, match="未完整落盘"):
        store.coordinator.apply(store.machine.id)
    assert json.loads((tmp_path / "schema.json").read_text())["schema_version"] == 14


class RetiredProjectKeyTests(unittest.TestCase):
    def _migrate(self) -> None:
        (self.store.root / "schema.json").write_text(json.dumps(marker(14)))
        self.store.coordinator.apply(self.store.machine.id)

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(root=Path(self.tmp.name) / "store")

    def _write_legacy_keys(self, pid: str, **extra: object) -> None:
        """Put the retired keys back into an already-written project.json."""
        path = self.store.root / "projects" / pid / "project.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(extra)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def test_list_projects_keeps_a_record_carrying_the_retired_keys(self) -> None:
        project = Project(root_path=str(Path(self.tmp.name) / "repo"), name="legacy")
        self.store.create_project(project)
        self._write_legacy_keys(
            project.id,
            active_planspace_id="planspaces.some-binding.a-lane",
            planspace_selection_explicit=True,
        )

        self._migrate()
        listed = self.store.list_projects()

        self.assertEqual([p.id for p in listed], [project.id])
        self.assertEqual(listed[0].name, "legacy")
        # The keys are dropped, not mapped onto some surviving attribute: the
        # project-level cursor has no successor, and a node's lane is the only
        # lane anything reads now.
        self.assertFalse(hasattr(listed[0], "active_planspace_id"))
        self.assertFalse(hasattr(listed[0], "planspace_selection_explicit"))

    def test_rewriting_a_legacy_record_drops_the_retired_keys(self) -> None:
        """The keys are not merely tolerated on read — they stop being stored."""
        project = Project(root_path=str(Path(self.tmp.name) / "repo"))
        self.store.create_project(project)
        self._write_legacy_keys(
            project.id, active_planspace_id="planspaces.b.lane"
        )

        self._migrate()
        reloaded = self.store.list_projects()[0]
        reloaded.name = "renamed"
        self.store.update_project(reloaded)

        payload = json.loads(
            (self.store.root / "projects" / project.id / "project.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("active_planspace_id", payload)
        self.assertNotIn("planspace_selection_explicit", payload)
        self.assertEqual(payload["name"], "renamed")

    def test_an_unrecognized_key_is_still_rejected(self) -> None:
        """正常读取不再包含旧格式清理，也不能跳过损坏项目。"""
        project = Project(root_path=str(Path(self.tmp.name) / "repo"))
        self.store.create_project(project)
        self._write_legacy_keys(project.id, totally_unknown_field="x")

        with self.assertRaises(MigrationError):
            self.store.list_projects()
