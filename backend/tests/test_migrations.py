from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from miniclaw2.domain import Node, Project
from miniclaw2.migrations.catalog import CURRENT_VERSION, MINIMUM_VERSION, check_manifest, marker, steps, version_of
from miniclaw2.migrations.coordinator import coordinator, open_storage
from miniclaw2.migrations.errors import MigrationError
from miniclaw2.migrations.inventory import files
from miniclaw2.migrations.transaction import Transaction, atomic_json, recover
from miniclaw2.migrations.validation import validate
from miniclaw2.store import Store




def test_manifest_window() -> None:
    check_manifest()
    assert MINIMUM_VERSION == max(14, CURRENT_VERSION - 3)


def test_release_prunes_fourth_edge_and_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from miniclaw2.migrations import catalog
    from miniclaw2.migrations.sdk import Migration

    package = tmp_path / "miniclaw2" / "migrations"
    (package / "steps").mkdir(parents=True)
    fixtures = tmp_path / "tests" / "migrations"
    fixtures.mkdir(parents=True)
    modules = {}
    for target in range(15, 19):
        name = f"v{target:04d}_example"
        (package / "steps" / (name + ".py")).write_text(f"target = {target}\n")
        (fixtures / ("test_" + name + ".py")).write_text("fixture\n")
        modules[name] = SimpleNamespace(MIGRATION=Migration(target - 1, target, ("shared",), "用例", str(target), lambda context: None, lambda context: None))
    monkeypatch.setattr(catalog, "DIRECTORY", package)
    with patch.object(catalog.importlib, "import_module", side_effect=lambda name: modules[name.rsplit(".", 1)[-1]]):
        manifest = catalog.generate(prune=True)
        assert manifest["target"] == 18
        assert manifest["minimum"] == 15
        assert not (package / "steps" / "v0015_example.py").exists()
        assert not (fixtures / "test_v0015_example.py").exists()
        catalog.check_manifest()
        with pytest.raises(MigrationError) as error:
            catalog.version_of({"schema_version": 14}, tmp_path)
        assert error.value.state == "schema_too_old"
        (package / "steps" / "v0018_example.py").write_text("modified\n")
        with pytest.raises(ValueError, match="不可修改"):
            catalog.generate(prune=True)


def test_build_removes_retired_modules_from_reused_build_directory(tmp_path: Path) -> None:
    build = tmp_path / "build"
    stale = build / "miniclaw2" / "migrations" / "steps" / "v0001_retired.py"
    stale.parent.mkdir(parents=True)
    stale.write_text("retired = True\n")
    result = subprocess.run(
        [os.sys.executable, "setup.py", "build_py", "--build-lib", str(build)],
        cwd=Path(__file__).parents[1], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not stale.exists()


@pytest.mark.parametrize("payload,state", [
    ({}, "migration_failed"), ({"schema_version": True}, "migration_failed"),
    ({"schema_version": "14"}, "migration_failed"), ({"schema_version": 14.5}, "migration_failed"),
    ({"schema_version": 13}, "schema_too_old"), ({"schema_version": 100}, "schema_too_new"),
    ({**marker(), "contract": "fork"}, "schema_conflict"),
])
def test_schema_refusal_precedes_business_writes(tmp_path: Path, payload: dict, state: str) -> None:
    atomic_json(tmp_path / "schema.json", payload)
    before = (tmp_path / "schema.json").read_bytes()
    with pytest.raises(MigrationError) as error:
        Store(tmp_path)
    assert error.value.state == state
    assert (tmp_path / "schema.json").read_bytes() == before
    assert not (tmp_path / "config.json").exists()
    assert not (tmp_path / "machine.json").exists()


def test_nonempty_unversioned_store_is_not_new(tmp_path: Path) -> None:
    atomic_json(tmp_path / "config.json", {})
    with pytest.raises(MigrationError, match="非空存储"):
        Store(tmp_path)


def test_empty_and_repeat_startup_do_not_reapply(tmp_path: Path) -> None:
    store = Store(tmp_path)
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with patch("miniclaw2.migrations.coordinator.Transaction", side_effect=AssertionError("重复事务")):
        Store(tmp_path)
    assert before == {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert store.list_projects() == []


@pytest.mark.parametrize("name", ["node.json", "project.json", "local.json", "layout.json", "host.json", "head.json", "git_aliases.json"])
def test_artifact_metadata_names_survive_validation_and_migration(tmp_path: Path, name: str) -> None:
    from miniclaw2.artifacts import publish_artifacts, stored_artifact_path, workspace_artifacts_dir

    store = Store(tmp_path / "store")
    project = store.create_project(Project(root_path=str(tmp_path / "workspace")))
    node = store.create_node(Node(project_id=project.id, model_preset_id=project.model_preset_id))
    source = workspace_artifacts_dir(project, node.id)
    source.mkdir(parents=True)
    (source / name).write_text('["这是产物，不是元数据"]', encoding="utf-8")
    assert publish_artifacts(project, node, [name], store)[0].status == "published"
    validate(store.root)
    atomic_json(store.root / "schema.json", marker(MINIMUM_VERSION))
    store.coordinator.ready = False
    store.coordinator.apply(store.machine.id, accept_data_loss=True)
    migrated = Store(store.root)
    assert stored_artifact_path(migrated, project.id, node.id, name).read_text(encoding="utf-8") == '["这是产物，不是元数据"]'


@pytest.mark.parametrize("name,payload", [("local.json", {}), ("node.json", {"id": "错误标识"})])
def test_metadata_records_still_require_valid_content(tmp_path: Path, name: str, payload: dict) -> None:
    store = Store(tmp_path)
    project = store.create_project(Project(root_path="/tmp/metadata"))
    node = store.create_node(Node(project_id=project.id, model_preset_id=project.model_preset_id))
    host = tmp_path / "projects" / project.id / "hosts" / store.machine.id
    path = host / name if name == "local.json" else host / "nodes" / node.id / name
    atomic_json(path, payload)
    with pytest.raises(MigrationError):
        validate(tmp_path)




def test_local_cursor_is_not_shared_cursor(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.create_project(Project(root_path="/tmp/local-cursor"))
    receipt_path = tmp_path / ".migration-local" / "state.json"
    receipt = json.loads(receipt_path.read_text())
    receipt.update(marker(MINIMUM_VERSION))
    atomic_json(receipt_path, receipt)
    scopes = []

    def tracked_steps(source: int):
        def track(context, migration):
            scopes.append(context.scope)
            migration.upgrade(context)

        return [replace(migration, upgrade=lambda context, migration=migration: track(context, migration)) for migration in steps(source)]

    with patch("miniclaw2.migrations.coordinator.steps", side_effect=tracked_steps):
        store.coordinator.apply(store.machine.id, accept_data_loss=True)
    assert scopes == ["local"] * sum("local" in migration.scopes for migration in steps(MINIMUM_VERSION))
    assert version_of(json.loads(receipt_path.read_text()), receipt_path) == CURRENT_VERSION
    latest = sorted((tmp_path / ".migration-local" / "transactions").glob("*/journal.json"), key=lambda path: path.stat().st_mtime)[-1]
    assert any(change["path"] == ".migration-local/state.json" for change in json.loads(latest.read_text())["changes"])






def test_current_bad_record_is_not_silently_hidden(tmp_path: Path) -> None:
    store = Store(tmp_path)
    project = store.create_project(Project(root_path="/tmp/current-bad"))
    (tmp_path / "projects" / project.id / "project.json").write_text("{}")
    with pytest.raises(MigrationError):
        store.list_projects()


@pytest.mark.parametrize("failure_at", [1, 2, 3])
def test_publish_recovers_at_each_file_boundary(tmp_path: Path, failure_at: int) -> None:
    from miniclaw2.migrations import transaction as module

    storage = coordinator(tmp_path)
    atomic_json(tmp_path / "config.json", {"old": True})
    atomic_json(tmp_path / "tags.json", {"old": True})
    atomic_json(tmp_path / "schema.json", marker(MINIMUM_VERSION))
    transaction = Transaction(tmp_path, [tmp_path])
    for filename in ("config.json", "tags.json"):
        atomic_json(transaction.stage(0) / filename, {"new": True})
    atomic_json(transaction.stage(0) / "schema.json", marker())
    transaction.decide()
    copies = 0
    original = module.durable_copy

    def fail_copy(source: Path, destination: Path) -> None:
        nonlocal copies
        copies += 1
        if copies == failure_at:
            raise OSError("注入中断")
        original(source, destination)

    with patch.object(module, "durable_copy", side_effect=fail_copy), pytest.raises(OSError):
        transaction.publish()
    with pytest.raises(MigrationError, match="尚未完成"):
        storage.assert_current()
    recover(tmp_path, [tmp_path])
    assert json.loads((tmp_path / "config.json").read_text()) == {"new": True}
    assert json.loads((tmp_path / "schema.json").read_text()) == marker()
    assert not (tmp_path / ".migration-local" / "pending.json").exists()


def test_prepare_failure_leaves_source_and_recovery_discards_staging(tmp_path: Path) -> None:
    atomic_json(tmp_path / "config.json", {"old": True})
    transaction = Transaction(tmp_path, [tmp_path])
    atomic_json(transaction.stage(0) / "config.json", {"new": True})
    recover(tmp_path, [tmp_path])
    assert json.loads((tmp_path / "config.json").read_text()) == {"old": True}
    assert json.loads((transaction.directory / "journal.json").read_text())["phase"] == "aborted"


def test_external_write_after_decision_is_not_overwritten(tmp_path: Path) -> None:
    atomic_json(tmp_path / "config.json", {"old": True})
    transaction = Transaction(tmp_path, [tmp_path])
    atomic_json(transaction.stage(0) / "config.json", {"new": True})
    transaction.decide()
    atomic_json(tmp_path / "config.json", {"human": True})
    with pytest.raises(MigrationError, match="外部修改"):
        recover(tmp_path, [tmp_path])
    assert json.loads((tmp_path / "config.json").read_text()) == {"human": True}


def test_untouched_input_is_checked_again_before_publish(tmp_path: Path) -> None:
    atomic_json(tmp_path / "config.json", {"old": True})
    atomic_json(tmp_path / "tags.json", {"untouched": True})
    transaction = Transaction(tmp_path, [tmp_path])
    atomic_json(transaction.stage(0) / "config.json", {"new": True})
    transaction.decide()
    atomic_json(tmp_path / "tags.json", {"external": True})
    with pytest.raises(MigrationError, match="外部修改"):
        transaction.publish()
    assert json.loads((tmp_path / "config.json").read_text()) == {"old": True}


def test_isolated_restore_never_rolls_back_live_data(tmp_path: Path) -> None:
    from miniclaw2.migrations.cli import restore_isolated

    root = tmp_path / "store"
    atomic_json(root / "config.json", {"old": True})
    transaction = Transaction(root, [root])
    atomic_json(transaction.stage(0) / "config.json", {"new": True})
    transaction.decide()
    transaction.publish()
    restore_isolated(root, transaction.identifier, tmp_path / "recovered")
    assert json.loads((root / "config.json").read_text()) == {"new": True}
    assert json.loads((tmp_path / "recovered" / "store" / "config.json").read_text()) == {"old": True}


def test_other_process_cannot_open_held_store(tmp_path: Path) -> None:
    store = Store(tmp_path)
    result = subprocess.run(
        [os.sys.executable, "-c", "from pathlib import Path; from miniclaw2.store import Store; import sys; Store(Path(sys.argv[1]))", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "另一个进程" in result.stderr
    assert store.list_projects() == []


def test_symlink_cannot_escape_inventory(tmp_path: Path) -> None:
    (tmp_path / "contextspace").mkdir()
    (tmp_path / "contextspace" / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(MigrationError, match="符号链接"):
        files(tmp_path)


def test_maintenance_api_does_not_load_incompatible_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from miniclaw2.app import create_app

    monkeypatch.setenv("MINICLAW_HOME", str(tmp_path))
    atomic_json(tmp_path / "schema.json", {"schema_version": 999})
    with TestClient(create_app()) as client:
        assert client.get("/health").json()["status"] == "maintenance"
        assert client.get("/migrations/status").json()["state"] == "schema_too_new"
        assert client.get("/sessions").status_code == 503
    assert not (tmp_path / "config.json").exists()


def test_context_root_change_does_not_reuse_previous_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "store"
    first, second = tmp_path / "first", tmp_path / "second"
    monkeypatch.setenv("MINICLAW_CONTEXT_HOME", str(first))
    storage = open_storage(root)
    first_receipt = (first / ".migration-local" / "state.json").read_text()
    monkeypatch.setenv("MINICLAW_CONTEXT_HOME", str(second))
    assert open_storage(root) is storage
    second_receipt = (second / ".migration-local" / "state.json").read_text()
    assert first_receipt != second_receipt
    assert storage._context.root == second.resolve()


def test_current_corrupt_config_enters_maintenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from miniclaw2.app import create_app

    store = Store(tmp_path)
    (tmp_path / "config.json").write_text("{broken")
    monkeypatch.setenv("MINICLAW_HOME", str(tmp_path))
    with TestClient(create_app()) as client:
        assert client.get("/migrations/status").json()["state"] == "migration_failed"
    assert store.coordinator.root == tmp_path
