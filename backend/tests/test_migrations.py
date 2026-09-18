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
from miniclaw2.migrations.plan import migration_plan
from miniclaw2.migrations.transaction import Transaction, atomic_json, backup_payload, recover
from miniclaw2.migrations.validation import read_object, validate
from miniclaw2.store import Store




def test_manifest_window() -> None:
    check_manifest()
    assert MINIMUM_VERSION == max(14, CURRENT_VERSION - 3)


def legacy_layout_store(root: Path, source: int = 16, *, hints: bool = True) -> tuple[Store, str, str]:
    store = Store(root)
    project = store.create_project(Project(root_path=str(root / "workspace")))
    node = store.create_node(Node(project_id=project.id, model_preset_id=project.model_preset_id))
    (root / f"projects/{project.id}/hosts/{store.machine.id}/node-layout.json").unlink(missing_ok=True)
    if hints:
        atomic_json(root / f"projects/{project.id}/hosts/{store.machine.id}/layout.json", {
            "layout_hints": {node.id: {"x": 10, "y": 20}, "planspace:lane": {"x": 30, "y": 40}},
        })
    atomic_json(root / "schema.json", marker(source))
    receipt_path = root / ".migration-local/state.json"
    receipt = read_object(receipt_path)
    receipt.update(marker(source))
    receipt["accepted_migration_contracts"] = []
    atomic_json(receipt_path, receipt)
    store.coordinator.ready = False
    return store, project.id, node.id


def synthetic_repair_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniclaw2.migrations.repairs import V16
    from miniclaw2.migrations.sdk import Migration
    from miniclaw2.migrations.steps.v0017_layout_recovery import MIGRATION

    def discard_legacy(context):
        for path in context.paths("projects/*/hosts/*/layout.json"):
            context.delete(path)

    loss = Migration(16, 17, ("shared",), "有损测试步骤", V16, discard_legacy, lambda context: None, True)
    chain = [loss, replace(MIGRATION, source=17, target=18), *steps(18)]
    for module in ("coordinator", "sync_tree", "plan"):
        monkeypatch.setattr(f"miniclaw2.migrations.{module}.steps", lambda source: [step for step in chain if step.source >= source])


@pytest.mark.parametrize("source", [16])
@pytest.mark.parametrize("entrypoint", ["startup", "sync"])
def test_repaired_chain_automatically_preserves_layout(tmp_path: Path, source: int, entrypoint: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from miniclaw2.migrations.catalog import DIRECTORY
    from miniclaw2.migrations.sync_tree import normalize

    manifest_before = (DIRECTORY / "manifest.json").read_bytes()
    store, project_id, node_id = legacy_layout_store(tmp_path, source)
    synthetic_repair_chain(monkeypatch)
    if entrypoint == "startup":
        assert open_storage(tmp_path) is store.coordinator
    else:
        normalize(tmp_path)
    host = tmp_path / f"projects/{project_id}/hosts/{store.machine.id}"
    assert read_object(host / "node-layout.json")["nodes"][node_id] == {"x": 10, "y": 20, "space": "canvas"}
    assert read_object(tmp_path / f"projects/{project_id}/lane-layout.json")["nodes"]["planspace:lane"]["x"] == 30
    assert not (host / "layout.json").exists()
    assert read_object(tmp_path / "schema.json") == marker()
    assert read_object(tmp_path / ".migration-local/state.json")["accepted_migration_contracts"] == []
    check_manifest()
    assert (DIRECTORY / "manifest.json").read_bytes() == manifest_before


@pytest.mark.parametrize("entrypoint", ["startup", "sync"])
@pytest.mark.parametrize("guard", ["undeclared", "outside_chain", "repair_outside_chain", "no_input", "empty_impact"])
def test_repair_exemption_never_replaces_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str, guard: str) -> None:
    from miniclaw2.migrations import repairs
    from miniclaw2.migrations.sync_tree import normalize

    store, project_id, _node_id = legacy_layout_store(tmp_path, hints=guard != "no_input")
    synthetic_repair_chain(monkeypatch)
    if guard == "undeclared":
        monkeypatch.setattr(repairs, "REPAIRS", {})
    elif guard == "outside_chain":
        monkeypatch.setattr(repairs, "REPAIRS", {repairs.V17: ("不在本条链中的契约",)})
    elif guard == "repair_outside_chain":
        monkeypatch.setattr(repairs, "REPAIRS", {"不在本条链中的修复者": (repairs.V16,)})
    elif guard == "empty_impact":
        atomic_json(tmp_path / f"projects/{project_id}/hosts/{store.machine.id}/layout.json", {"layout_hints": {}})
    before = (tmp_path / "schema.json").read_bytes()
    with pytest.raises(MigrationError) as error:
        open_storage(tmp_path) if entrypoint == "startup" else normalize(tmp_path)
    assert error.value.state == "migration_required"
    assert (tmp_path / "schema.json").read_bytes() == before


@pytest.mark.parametrize("entrypoint", ["startup", "sync"])
def test_new_destructive_step_cannot_borrow_retired_repair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str) -> None:
    from miniclaw2.migrations import repairs
    from miniclaw2.migrations.sdk import Migration
    from miniclaw2.migrations.sync_tree import normalize

    store, _project_id, _node_id = legacy_layout_store(tmp_path, 16, hints=False)
    destructive = Migration(16, 17, ("shared",), "新的有损步骤", "new-loss", lambda context: None, lambda context: None, True)
    chain = [destructive, *steps(16)]
    monkeypatch.setattr(repairs, "REPAIRS", {repairs.V17: (repairs.V16, "new-loss")})
    module = "coordinator" if entrypoint == "startup" else "sync_tree"
    monkeypatch.setattr(f"miniclaw2.migrations.{module}.steps", lambda source: chain)
    with pytest.raises(MigrationError) as error:
        open_storage(tmp_path) if entrypoint == "startup" else normalize(tmp_path)
    assert error.value.state == "migration_required"
    assert not store.coordinator.ready


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


def test_migration_plan_rejects_nonempty_unversioned_store(tmp_path: Path) -> None:
    assert migration_plan(tmp_path)["source"] == CURRENT_VERSION
    atomic_json(tmp_path / "config.json", {})
    with pytest.raises(MigrationError, match="非空存储") as error:
        migration_plan(tmp_path)
    assert error.value.state == "migration_failed"
    assert error.value.path == tmp_path / "schema.json"


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






def test_pristine_snapshot_is_hydrated_once_per_root(tmp_path: Path) -> None:
    """Shared and local read the same root, so one overlay serves both."""
    from miniclaw2.migrations.transaction import hydrated_backup

    store = Store(tmp_path)
    store.create_project(Project(root_path="/tmp/one-hydration"))
    _committed_store(tmp_path)
    atomic_json(tmp_path / "schema.json", marker(MINIMUM_VERSION))
    receipt_path = tmp_path / ".migration-local" / "state.json"
    receipt = json.loads(receipt_path.read_text())
    receipt.update(marker(MINIMUM_VERSION))
    atomic_json(receipt_path, receipt)
    store.coordinator.ready = False
    hydrated = []

    def tracked(root, journal, index, **keywords):
        hydrated.append(index)
        return hydrated_backup(root, journal, index, **keywords)

    with patch("miniclaw2.migrations.coordinator.hydrated_backup", side_effect=tracked):
        store.coordinator.apply(store.machine.id, accept_data_loss=True)
    assert hydrated == [0], "同一数据根只应水化一次原始快照"
    assert version_of(json.loads((tmp_path / "schema.json").read_text()), tmp_path / "schema.json") == CURRENT_VERSION


def test_current_store_records_confirmation_without_hydrating(tmp_path: Path) -> None:
    """`apply --accept-data-loss` on a current store has no steps, so no snapshot."""
    store = Store(tmp_path)
    store.create_project(Project(root_path="/tmp/no-hydration"))
    _committed_store(tmp_path)
    store.coordinator.ready = False
    with patch("miniclaw2.migrations.coordinator.hydrated_backup",
               side_effect=AssertionError("无迁移步骤时不应水化备份")):
        store.coordinator.apply(store.machine.id, accept_data_loss=True)
    accepted = json.loads((tmp_path / ".migration-local" / "state.json").read_text())
    assert accepted["accepted_migration_contracts"] == sorted(
        migration.contract for migration in steps(MINIMUM_VERSION) if migration.destructive)


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


def _committed_store(root: Path) -> str:
    """A store whose managed files are committed, as a synced store's are."""
    environment = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_COMMITTER_NAME": "T",
                   "GIT_AUTHOR_EMAIL": "t@localhost", "GIT_COMMITTER_EMAIL": "t@localhost"}
    for arguments in (["init", "-b", "main"], ["add", "-A"], ["commit", "-m", "baseline"]):
        subprocess.run(["git", "-C", str(root), *arguments], check=True,
                       capture_output=True, env=environment)
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True, env=environment).stdout.strip()


def test_backup_references_git_instead_of_copying_committed_bytes(tmp_path: Path) -> None:
    atomic_json(tmp_path / "config.json", {"old": True})
    atomic_json(tmp_path / "tags.json", {"old": True})
    _committed_store(tmp_path)
    atomic_json(tmp_path / "tags.json", {"uncommitted": True})
    transaction = Transaction(tmp_path, [tmp_path])
    references = transaction.journal["backup_refs"][0]
    assert "config.json" in references, "已提交且字节一致的文件应引用 Git 对象"
    assert "tags.json" not in references, "Git 未持有的字节必须真拷贝"
    assert not (transaction.backup / "0" / "config.json").exists()
    assert (transaction.backup / "0" / "tags.json").is_file()
    payload = backup_payload(tmp_path, transaction.journal, 0, "config.json")
    assert payload.record == {"old": True}
    assert payload.digest == transaction.journal["inputs"][0]["config.json"]


def test_backup_copies_bytes_git_would_normalize(tmp_path: Path) -> None:
    """A filtered file is copied, because its committed blob is not its bytes."""
    from miniclaw2.migrations.cli import restore_isolated

    root = tmp_path / "store"
    crlf = b'{\r\n  "old": true\r\n}\r\n'
    root.mkdir()
    (root / "config.json").write_bytes(crlf)
    atomic_json(root / "schema.json", marker())
    environment = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_COMMITTER_NAME": "T",
                   "GIT_AUTHOR_EMAIL": "t@localhost", "GIT_COMMITTER_EMAIL": "t@localhost"}
    for arguments in (["init", "-b", "main"], ["config", "core.autocrlf", "true"],
                      ["add", "-A"], ["commit", "-m", "baseline"]):
        subprocess.run(["git", "-C", str(root), *arguments], check=True,
                       capture_output=True, env=environment)
    committed = subprocess.run(["git", "-C", str(root), "cat-file", "blob", "HEAD:config.json"],
                               check=True, capture_output=True, env=environment).stdout
    assert committed != crlf, "此用例要求 Git 确实规范化了换行，否则没有验证到该风险"

    transaction = Transaction(root, [root])

    assert "config.json" not in transaction.journal["backup_refs"][0], "字节与提交对象不同的文件不能只记引用"
    assert (transaction.backup / "0" / "config.json").read_bytes() == crlf
    atomic_json(transaction.stage(0) / "config.json", {"new": True})
    transaction.decide()
    transaction.publish()
    restore_isolated(root, transaction.identifier, tmp_path / "recovered")
    assert (tmp_path / "recovered" / "store" / "config.json").read_bytes() == crlf


@pytest.mark.parametrize("committed", [True, False])
def test_large_backup_entry_is_verified_and_exported_by_streaming(tmp_path: Path, committed: bool) -> None:
    """Neither verifying nor exporting a backup may scale with the largest file."""
    import tracemalloc

    from miniclaw2.migrations.cli import restore_isolated
    from miniclaw2.migrations.transaction import backup_digest, backup_extract

    root = tmp_path / "store"
    relative = "projects/p/hosts/h/nodes/n/events.jsonl"
    events = root / relative
    events.parent.mkdir(parents=True)
    events.write_bytes(b'{"seq":1}\n' * (1024 * 1024))
    atomic_json(root / "schema.json", marker())
    if committed:
        _committed_store(root)
    transaction = Transaction(root, [root])
    assert (relative in transaction.journal["backup_refs"][0]) is committed
    recorded = transaction.journal["inputs"][0][relative]
    budget = events.stat().st_size // 2

    tracemalloc.start()
    try:
        assert backup_digest(root, transaction.journal, 0, relative,
                             identifier=transaction.identifier) == recorded
        assert tracemalloc.get_traced_memory()[1] < budget, "校验备份摘要不应整块读入文件"
        tracemalloc.reset_peak()
        assert backup_extract(root, transaction.journal, 0, relative, tmp_path / "copy.jsonl",
                              identifier=transaction.identifier) == recorded
        assert tracemalloc.get_traced_memory()[1] < budget, "导出备份不应整块读入文件"
    finally:
        tracemalloc.stop()
    assert (tmp_path / "copy.jsonl").read_bytes() == events.read_bytes()

    atomic_json(transaction.stage(0) / "config.json", {"new": True})
    transaction.decide()
    transaction.publish()
    restore_isolated(root, transaction.identifier, tmp_path / "recovered")
    assert (tmp_path / "recovered" / "store" / relative).read_bytes() == events.read_bytes()


def test_backup_copies_everything_without_git_history(tmp_path: Path) -> None:
    atomic_json(tmp_path / "config.json", {"old": True})
    transaction = Transaction(tmp_path, [tmp_path])
    assert transaction.journal["backup_refs"][0] == {}
    assert (transaction.backup / "0" / "config.json").is_file()


def test_published_transaction_keeps_backup_and_drops_stage(tmp_path: Path) -> None:
    atomic_json(tmp_path / "config.json", {"old": True})
    _committed_store(tmp_path)
    transaction = Transaction(tmp_path, [tmp_path])
    atomic_json(transaction.stage(0) / "config.json", {"new": True})
    transaction.decide()
    transaction.publish()
    assert not (transaction.directory / "stage").exists(), "发布后暂存树没有读取方，应回收"
    assert (transaction.directory / "journal.json").is_file()
    assert backup_payload(tmp_path, transaction.journal, 0, "config.json").record == {"old": True}


def test_isolated_restore_reads_referenced_backup(tmp_path: Path) -> None:
    from miniclaw2.migrations.cli import restore_isolated

    root = tmp_path / "store"
    atomic_json(root / "config.json", {"old": True})
    _committed_store(root)
    transaction = Transaction(root, [root])
    atomic_json(transaction.stage(0) / "config.json", {"new": True})
    transaction.decide()
    transaction.publish()
    assert transaction.journal["backup_refs"][0], "此前提交过的文件应为引用"
    restore_isolated(root, transaction.identifier, tmp_path / "recovered")
    assert json.loads((tmp_path / "recovered" / "store" / "config.json").read_text()) == {"old": True}
    assert json.loads((root / "config.json").read_text()) == {"new": True}


def test_referenced_backup_reports_lost_git_object(tmp_path: Path) -> None:
    atomic_json(tmp_path / "config.json", {"old": True})
    _committed_store(tmp_path)
    transaction = Transaction(tmp_path, [tmp_path])
    blob = transaction.journal["backup_refs"][0]["config.json"]
    transaction.journal["backup_refs"][0]["config.json"] = "0" * 40
    with pytest.raises(MigrationError, match="不再持有"):
        backup_payload(tmp_path, transaction.journal, 0, "config.json")
    transaction.journal["backup_refs"][0]["config.json"] = blob
    assert backup_payload(tmp_path, transaction.journal, 0, "config.json").record == {"old": True}


def test_referenced_backup_survives_history_rewrite_and_gc(tmp_path: Path) -> None:
    atomic_json(tmp_path / "config.json", {"old": True})
    _committed_store(tmp_path)
    transaction = Transaction(tmp_path, [tmp_path])
    assert transaction.journal["backup_refs"][0], "已提交文件应为引用"
    environment = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_COMMITTER_NAME": "T",
                   "GIT_AUTHOR_EMAIL": "t@localhost", "GIT_COMMITTER_EMAIL": "t@localhost"}
    # Discard the branch that held those bytes, then collect aggressively:
    # only the backup's own ref can still keep the blob reachable.
    atomic_json(tmp_path / "config.json", {"rewritten": True})
    for arguments in (["add", "-A"], ["commit", "--amend", "-m", "rewritten"],
                      ["reflog", "expire", "--expire=now", "--all"],
                      ["gc", "--prune=now", "--aggressive"]):
        subprocess.run(["git", "-C", str(tmp_path), *arguments], check=True,
                       capture_output=True, env=environment)
    assert backup_payload(tmp_path, transaction.journal, 0, "config.json").record == {"old": True}


def test_prune_keeps_recent_backups_and_spares_unfinished(tmp_path: Path) -> None:
    from miniclaw2.migrations.transaction import prune_transactions

    atomic_json(tmp_path / "config.json", {"n": 0})
    finished = []
    for step in range(3):
        transaction = Transaction(tmp_path, [tmp_path])
        atomic_json(transaction.stage(0) / "config.json", {"n": step + 1})
        transaction.decide()
        transaction.publish()
        finished.append(transaction.identifier)
    unfinished = Transaction(tmp_path, [tmp_path])
    atomic_json(unfinished.stage(0) / "config.json", {"n": 9})

    result = prune_transactions(tmp_path, keep=2)

    assert result["removed"] == finished[:1], "只应回收保留窗口之外的已完结事务"
    assert not (tmp_path / "migration-backups" / finished[0]).exists()
    for identifier in finished[1:]:
        assert (tmp_path / "migration-backups" / identifier).is_dir(), "窗口内的备份必须保留"
        assert not (tmp_path / ".migration-local/transactions" / identifier / "stage").exists()
    assert (unfinished.directory / "stage").is_dir(), "未完结事务仍要能被 recover 使用"
    recover(tmp_path, [tmp_path])
    assert json.loads((tmp_path / "config.json").read_text()) == {"n": 3}


def test_prune_spares_backup_still_needed_for_recovery(tmp_path: Path) -> None:
    from miniclaw2.migrations.transaction import prune_transactions

    atomic_json(tmp_path / "config.json", {"n": 0})
    identifiers = []
    for step in range(3):
        transaction = Transaction(tmp_path, [tmp_path])
        atomic_json(transaction.stage(0) / "config.json", {"n": step + 1})
        transaction.decide()
        transaction.publish()
        identifiers.append(transaction.identifier)

    result = prune_transactions(tmp_path, keep=1, protected={identifiers[0]})

    assert result["kept_protected"] == [identifiers[0]]
    assert (tmp_path / "migration-backups" / identifiers[0]).is_dir(), "仍被恢复引用的备份不能因为旧而删除"
    assert identifiers[1] in result["removed"]
    assert not (tmp_path / "migration-backups" / identifiers[1]).exists()


def test_other_process_cannot_prune_held_store(tmp_path: Path) -> None:
    """Reclaiming a transaction under a running backend would race publication."""
    store = Store(tmp_path)
    for step in range(3):
        transaction = Transaction(tmp_path, [tmp_path])
        atomic_json(transaction.stage(0) / "config.json", {"n": step + 1})
        transaction.decide()
        transaction.publish()
    backups = {path.name for path in (tmp_path / "migration-backups").iterdir()}

    result = subprocess.run(
        [os.sys.executable, "-m", "miniclaw2", "migrations", "prune", "--root", str(tmp_path), "--keep", "0"],
        capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert "另一个进程" in result.stderr
    assert {path.name for path in (tmp_path / "migration-backups").iterdir()} == backups, "未取得存储协调权时不能删除任何备份"
    assert store.list_projects() == []


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


def test_plan_endpoint_stays_reachable_while_storage_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The maintenance page is the only surface a blocked user has, so the plan
    it reads must survive the admission middleware that 503s everything else."""
    from miniclaw2.app import create_app

    store, project_id, _node_id = legacy_layout_store(tmp_path)
    synthetic_repair_chain(monkeypatch)
    for host in (tmp_path / f"projects/{project_id}/hosts").iterdir():
        atomic_json(host / "layout.json", {"layout_hints": {}})
    monkeypatch.setenv("MINICLAW_HOME", str(tmp_path))
    with TestClient(create_app()) as client:
        assert client.get("/sessions").status_code == 503
        assert client.get("/migrations/status").json()["state"] == "migration_required"
        response = client.get("/migrations/plan")
        assert response.status_code == 200
        plan = response.json()
        assert plan["source"] == 16 and plan["target"] == CURRENT_VERSION
        assert [step["destructive"] for step in plan["steps"]] == [True, False, False]
        assert plan["sync_confirmation_contracts"] and plan["sync_confirmation_note"]
        assert any(host["local"] for host in plan["confirmation_hosts"])
        assert "layout_impact" in plan and "layout_recovery" in plan
    # Reading the plan must not confirm anything, nor advance the storage.
    assert read_object(tmp_path / "schema.json") == marker(16)
    assert read_object(tmp_path / ".migration-local/state.json")["accepted_migration_contracts"] == []
    assert store.coordinator.root == tmp_path


def test_unexpected_failure_answers_structured_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a fallback handler an unhandled error reaches the client as the
    plain text `Internal Server Error`, which the UI cannot branch on."""
    from miniclaw2.app import create_app
    from miniclaw2.registry import ProjectRegistry

    monkeypatch.setenv("MINICLAW_HOME", str(tmp_path))
    registry = ProjectRegistry(Store(tmp_path))
    monkeypatch.setattr(type(registry), "list_projects",
                        lambda self: (_ for _ in ()).throw(KeyError("注入的意外故障")))
    with TestClient(create_app(registry), raise_server_exceptions=False) as client:
        response = client.get("/sessions")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["state"] == "internal_error"
    assert body["detail"] and "Internal Server Error" not in body["detail"]


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
