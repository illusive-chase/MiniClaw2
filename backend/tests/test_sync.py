from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Event, get_ident
from unittest.mock import patch

import pytest
from anyio import CancelScope
from fastapi.testclient import TestClient

import miniclaw2.sync as sync_module
from miniclaw2.app import create_app
from miniclaw2.domain import Node, NodeState, Project
from miniclaw2.global_config import load_global_config, save_global_config
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store
from miniclaw2.migrations.catalog import MINIMUM_VERSION, marker
from miniclaw2.sync import (
    SchemaConflictError,
    SyncError,
    bootstrap_store,
)


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


class NonNativeProjectApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.project = Project(
            root_path="/path/only/valid/on/source",
            name="Remote project",
            machine_id="remote-machine-id",
            machine_label="alice-mbp",
        )
        self.store.create_project(self.project)
        self.store.create_node(
            Node(
                project_id=self.project.id,
                state=NodeState.RUNNING,
                model_preset_id=self.project.model_preset_id,
                prompt="stale remote state",
            )
        )
        (
            self.store.root
            / "projects"
            / self.project.id
            / "hosts"
            / self.store.machine.id
            / "local.json"
        ).unlink()
        self.registry = ProjectRegistry(self.store)
        self.client = TestClient(create_app(self.registry))

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def test_viewing_is_allowed_but_mutations_are_rejected(self) -> None:
        response = self.client.get(f"/sessions/{self.project.id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["read_only"])
        self.assertFalse(response.json()["bound_here"])
        self.assertEqual(response.json()["created_on_machine_label"], "alice-mbp")

        nodes = self.client.get(f"/sessions/{self.project.id}/nodes")
        self.assertEqual(nodes.status_code, 200, nodes.text)
        self.assertEqual(nodes.json()[0]["state"], "running")

        mutations = [
            self.client.patch(
                f"/sessions/{self.project.id}", json={"name": "changed"}
            ),
            self.client.patch(
                f"/sessions/{self.project.id}/layout-hints",
                json={"updates": {"root": {"x": 1, "y": 2}}},
            ),
            self.client.post(
                f"/sessions/{self.project.id}/virtuals",
                json={"prompt_draft": "should fail"},
            ),
            self.client.delete(f"/sessions/{self.project.id}"),
        ]
        for mutation in mutations:
            self.assertEqual(mutation.status_code, 403, mutation.text)
            self.assertIn("configure its path", mutation.json()["detail"])


class GitMetadataSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.remote = self.base / "remote.git"
        _git("init", "--bare", str(self.remote))
        _git(
            "--git-dir",
            str(self.remote),
            "config",
            "core.hooksPath",
            str(self.remote / "hooks"),
        )
        self.root_a = self.base / "machine-a"
        self.store_a = Store(self.root_a)
        self.project_a = self.store_a.create_project(
            Project(root_path="/machine-a/project", name="Project A")
        )
        self.store_a.sync.setup_existing_store(str(self.remote))
        _git(
            "--git-dir",
            str(self.remote),
            "symbolic-ref",
            "HEAD",
            "refs/heads/main",
        )

        self.root_b = self.base / "machine-b"
        bootstrap_store(self.root_b, str(self.remote))
        self.store_b = Store(self.root_b)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_disjoint_projects_sync_and_remain_single_writer(self) -> None:
        registry_b = ProjectRegistry(self.store_b)
        synced_a = registry_b.get_project(self.project_a.id)
        assert synced_a is not None
        self.assertFalse(registry_b.is_native_project(synced_a))

        project_b = self.store_b.create_project(
            Project(root_path="/machine-b/project", name="Project B")
        )
        self.store_b.sync.sync_now()
        self.store_a.sync.sync_now()

        registry_a = ProjectRegistry(self.store_a)
        synced_b = registry_a.get_project(project_b.id)
        assert synced_b is not None
        self.assertFalse(registry_a.is_native_project(synced_b))
        self.assertTrue(registry_a.is_native_project(registry_a.get_project(self.project_a.id)))  # type: ignore[arg-type]

    def test_remote_status_is_derived_and_check_fetches_without_mutating_local_state(self) -> None:
        self.store_b.create_project(
            Project(root_path="/machine-b/remote-change", name="Remote change")
        )
        self.store_b.sync.sync_now()
        starting_head = _git("rev-parse", "HEAD", cwd=self.root_a).stdout.strip()
        machine_before = (self.root_a / "machine.json").read_text(encoding="utf-8")

        self.assertEqual(self.store_a.sync.status()["remote"]["behind"], 0)

        checked = self.store_a.sync.check_remote()

        self.assertEqual(checked["remote"]["ahead"], 0)
        self.assertGreater(checked["remote"]["behind"], 0)
        self.assertIsNotNone(checked["remote"]["ref_at"])
        self.assertIsNone(checked["remote"]["error"])
        self.assertEqual(
            _git("rev-parse", "HEAD", cwd=self.root_a).stdout.strip(),
            starting_head,
        )
        self.assertEqual(
            (self.root_a / "machine.json").read_text(encoding="utf-8"),
            machine_before,
        )

    def test_remote_status_reports_divergence(self) -> None:
        self.store_b.create_project(
            Project(root_path="/machine-b/remote-change", name="Remote change")
        )
        self.store_b.sync.sync_now()
        self.store_a.create_project(
            Project(root_path="/machine-a/local-change", name="Local change")
        )
        self.store_a.sync.commit_now("local metadata change")

        checked = self.store_a.sync.check_remote()

        self.assertGreater(checked["remote"]["ahead"], 0)
        self.assertGreater(checked["remote"]["behind"], 0)

    def test_remote_check_timeout_releases_the_manager_lock(self) -> None:
        manager = self.store_a.sync
        original_git = sync_module._git

        def timeout_fetch(root: Path, *args: str, **kwargs: object):
            if args and args[0] == "fetch":
                raise SyncError("git command timed out after 30 seconds")
            return original_git(root, *args, **kwargs)

        with patch.object(sync_module, "_git", side_effect=timeout_fetch):
            with self.assertRaisesRegex(SyncError, "timed out"):
                manager.check_remote()

        self.assertTrue(manager._lock.acquire(blocking=False))
        manager._lock.release()
        self.assertIn("remote", manager.status())

    def test_remote_check_endpoint_does_not_reload_the_registry(self) -> None:
        registry = ProjectRegistry(self.store_a)
        client = TestClient(create_app(registry))

        with patch.object(registry, "reload_from_store") as reload_from_store:
            response = client.post("/global-state/sync/check")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("remote", response.json()["sync"])
        reload_from_store.assert_not_called()

    def test_run_raw_normalizes_timeout_as_sync_error(self) -> None:
        timeout = subprocess.TimeoutExpired(["git", "fetch"], 0.01)
        with patch.object(sync_module.subprocess, "run", side_effect=timeout):
            with self.assertRaisesRegex(SyncError, "timed out after 0.01 seconds"):
                sync_module._run_raw(["git", "fetch"], timeout=0.01)

    def test_global_conflict_preserves_both_sides_until_manual_resolution(self) -> None:
        config_a = load_global_config(self.root_a)
        save_global_config(
            config_a.model_copy(
                update={
                    "defaults": config_a.defaults.model_copy(
                        update={"preferred_language": "English"}
                    )
                }
            ),
            self.root_a,
        )
        self.store_a.sync.commit_now("set language on A")

        config_b = load_global_config(self.root_b)
        save_global_config(
            config_b.model_copy(
                update={
                    "defaults": config_b.defaults.model_copy(
                        update={"preferred_language": "Chinese"}
                    )
                }
            ),
            self.root_b,
        )
        self.store_b.sync.commit_now("set language on B")
        self.store_b.sync.sync_now()

        with self.assertRaises(SchemaConflictError):
            self.store_a.sync.sync_now()
        self.assertEqual(
            load_global_config(self.root_a).defaults.preferred_language,
            "English",
        )
        self.store_b.sync.sync_now()
        self.assertEqual(
            load_global_config(self.root_b).defaults.preferred_language,
            "Chinese",
        )

    def test_schema_conflict_is_a_hard_failure(self) -> None:
        schema_b = json.loads((self.root_b / "schema.json").read_text())
        schema_b["contract"] = "fork"
        (self.root_b / "schema.json").write_text(
            json.dumps(schema_b, indent=2) + "\n", encoding="utf-8"
        )
        self._push_unchecked_b()

        with self.assertRaises(SchemaConflictError):
            self.store_a.sync.sync_now()
        self.assertFalse((self.root_a / ".git" / "MERGE_HEAD").exists())
        self.assertEqual(self.store_a.sync.status()["status"], "changed")
        reloaded = Store(self.root_a)
        self.assertEqual(reloaded.sync.status()["status"], "changed")

    def test_tag_conflict_requires_manual_resolution(self) -> None:
        tag_a = self.store_a.create_tag("Machine A", "coral")
        self.store_a.sync.commit_now("create tag on A")

        self.store_b.create_tag("Machine B", "sage")
        self.store_b.sync.commit_now("create tag on B")
        self.store_b.sync.sync_now()

        with self.assertRaisesRegex(SyncError, "tags.json"):
            self.store_a.sync.sync_now()

        self.assertFalse((self.root_a / ".git" / "MERGE_HEAD").exists())
        self.assertEqual(self.store_a.list_tags(), [tag_a])
        self.assertEqual(self.store_a.sync.status()["status"], "changed")

    def test_remote_schema_upgrade_is_refused_without_touching_live_store(self) -> None:
        schema_b = json.loads((self.root_b / "schema.json").read_text())
        schema_b["schema_version"] += 1
        (self.root_b / "schema.json").write_text(
            json.dumps(schema_b, indent=2) + "\n", encoding="utf-8"
        )
        self._push_unchecked_b()

        self.assertIsNone(self.store_a.read_only_reason)
        with self.assertRaisesRegex(SchemaConflictError, "新于程序"):
            self.store_a.sync.sync_now()
        self.assertIsNone(self.store_a.read_only_reason)
        self.store_a.create_project(Project(root_path="/machine-a/allowed", name="Allowed"))

    def _push_unchecked_b(self) -> None:
        _git("add", "-A", cwd=self.root_b)
        _git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "外部客户端写入", cwd=self.root_b)
        _git("push", "origin", "HEAD:main", cwd=self.root_b)

    def test_supported_remote_is_normalized_before_divergent_merge(self) -> None:
        local = self.store_a.create_project(Project(root_path="/a/offline", name="Offline A"))
        remote = self.store_b.create_project(Project(root_path="/b/offline", name="Offline B"))
        (self.root_b / "schema.json").write_text(json.dumps(marker(MINIMUM_VERSION)))
        self._push_unchecked_b()
        self.store_a.sync.sync_now()
        ids = {project.id for project in self.store_a.list_projects()}
        self.assertTrue({local.id, remote.id}.issubset(ids))
        self.assertEqual(json.loads((self.root_a / "schema.json").read_text()), marker())
        parents = _git("rev-list", "--parents", "-n", "1", "HEAD", cwd=self.root_a).stdout.split()
        self.assertEqual(len(parents), 3)

    def test_current_but_corrupt_remote_is_never_published(self) -> None:
        remote = self.store_b.create_project(Project(root_path="/b/corrupt"))
        path = self.root_b / "projects" / remote.id / "project.json"
        payload = json.loads(path.read_text())
        payload["unknown_schema_field"] = True
        path.write_text(json.dumps(payload))
        self._push_unchecked_b()
        original = _git("rev-parse", "HEAD", cwd=self.root_a).stdout
        with self.assertRaises(SchemaConflictError):
            self.store_a.sync.sync_now()
        self.assertEqual(_git("rev-parse", "HEAD", cwd=self.root_a).stdout, original)
        self.assertFalse((self.root_a / "projects" / remote.id).exists())

    def test_read_only_store_does_not_schedule_native_projects(self) -> None:
        registry = ProjectRegistry(self.store_a)
        schema = json.loads((self.root_a / "schema.json").read_text())
        schema["schema_version"] += 1
        (self.root_a / "schema.json").write_text(
            json.dumps(schema, indent=2) + "\n", encoding="utf-8"
        )

        with patch.object(registry, "_schedule_queued") as schedule_queued:
            registry.schedule_all()

        schedule_queued.assert_not_called()

    def test_failed_push_keeps_the_validated_local_merge(self) -> None:
        project_b = self.store_b.create_project(
            Project(root_path="/machine-b/queued", name="Queued on B")
        )
        self.store_b.sync.sync_now()
        self.store_a.create_project(
            Project(root_path="/machine-a/queued", name="Queued on A")
        )
        self.store_a.sync.commit_now("queue local project on A")
        starting_head = _git("rev-parse", "HEAD", cwd=self.root_a).stdout.strip()

        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

        with self.assertRaises(SyncError):
            self.store_a.sync.sync_now()

        self.assertNotEqual(
            _git("rev-parse", "HEAD", cwd=self.root_a).stdout.strip(),
            starting_head,
        )
        self.assertTrue(
            (self.root_a / "projects" / project_b.id / "project.json").exists()
        )
        self.assertEqual(self.store_a.sync.status()["status"], "changed")

    def test_sync_preserves_ignored_files_but_applies_tracked_deletions(self) -> None:
        ignored = self.root_a / "contextspace" / "private-notes.md"
        ignored.parent.mkdir(exist_ok=True)
        ignored.write_text("仅保留在本机\n", encoding="utf-8")
        with (self.root_a / ".gitignore").open("a", encoding="utf-8") as stream:
            stream.write("\ncontextspace/private-notes.md\n")
        tracked = self.root_a / "contextspace" / "obsolete.md"
        tracked.write_text("待删除\n", encoding="utf-8")
        self.store_a.sync.sync_now()
        self.store_b.sync.sync_now()
        (self.root_b / "contextspace" / "obsolete.md").unlink()
        incoming = self.store_b.create_project(Project(root_path="/b/new", name="远端项目"))
        self.store_b.sync.sync_now()

        self.store_a.sync.sync_now()

        self.assertEqual(ignored.read_text(encoding="utf-8"), "仅保留在本机\n")
        self.assertFalse(tracked.exists())
        self.assertIn(incoming.id, {project.id for project in self.store_a.list_projects()})
        self.assertNotIn("contextspace/private-notes.md", _git("ls-files", cwd=self.root_a).stdout)

    def test_remote_cannot_overwrite_ignored_local_file(self) -> None:
        relative = "contextspace/private-notes.md"
        for root in (self.root_a, self.root_b):
            (root / "contextspace").mkdir(exist_ok=True)
        (self.root_a / relative).write_text("本机私有", encoding="utf-8")
        with (self.root_a / ".git" / "info" / "exclude").open("a") as stream:
            stream.write(f"\n{relative}\n")
        (self.root_b / relative).write_text("远端内容", encoding="utf-8")
        self.store_b.sync.sync_now()

        with self.assertRaisesRegex(SchemaConflictError, "本机未跟踪文件冲突"):
            self.store_a.sync.sync_now()

        self.assertEqual((self.root_a / relative).read_text(encoding="utf-8"), "本机私有")
        self.store_a.assert_writable()

    def test_remote_cannot_write_below_ignored_local_file(self) -> None:
        relative = "contextspace/notes"
        ignored = self.root_a / relative
        ignored.parent.mkdir(exist_ok=True)
        ignored.write_text("本机私有", encoding="utf-8")
        with (self.root_a / ".git" / "info" / "exclude").open("a") as stream:
            stream.write(f"\n{relative}\n")
        incoming = self.root_b / relative / "nested" / "doc.md"
        incoming.parent.mkdir(parents=True)
        incoming.write_text("远端内容", encoding="utf-8")
        self.store_b.sync.sync_now()
        starting_head = _git("rev-parse", "HEAD", cwd=self.root_a).stdout
        generation = self.store_a.sync.publication_generation

        with self.assertRaisesRegex(SchemaConflictError, "本机未跟踪文件冲突"):
            self.store_a.sync.sync_now()

        self.assertEqual(ignored.read_text(encoding="utf-8"), "本机私有")
        self.assertEqual(_git("rev-parse", "HEAD", cwd=self.root_a).stdout, starting_head)
        self.assertEqual(self.store_a.sync.publication_generation, generation)
        self.assertFalse((self.root_a / ".migration-local" / "pending.json").exists())
        self.store_a.assert_writable()

        ignored.unlink()
        self.store_a.sync.sync_now()
        self.assertEqual(
            (self.root_a / relative / "nested" / "doc.md").read_text(encoding="utf-8"),
            "远端内容",
        )
        self.store_a.assert_writable()

    def test_failed_push_refreshes_live_projects_and_store_indexes(self) -> None:
        registry = ProjectRegistry(self.store_a)
        self.store_a.list_nodes(self.project_a.id)
        self.store_a._last_activity_index[self.project_a.id] = 0.0
        payload_path = self.root_b / "projects" / self.project_a.id / "project.json"
        payload = json.loads(payload_path.read_text())
        payload["name"] = "远端新名称"
        payload_path.write_text(json.dumps(payload))
        incoming = self.store_b.create_project(Project(root_path="/b/incoming", name="远端新增"))
        node = self.store_b.create_node(Node(project_id=self.project_a.id, state=NodeState.DONE, model_preset_id=self.project_a.model_preset_id))
        self.store_b.sync.sync_now()
        self.store_a.create_tag("本机未推送", "coral")
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

        with patch("miniclaw2.app.install_hooks"), TestClient(create_app(registry)) as client:
            response = client.post("/global-state/sync")
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(registry.get_project(self.project_a.id).name, "远端新名称")
            self.assertIsNotNone(registry.get_project(incoming.id))
            self.assertIsNotNone(self.store_a.load_node(self.project_a.id, node.id))
            self.assertGreater(self.store_a.project_last_activity_at(self.project_a.id), 0.0)
            changed = client.patch(f"/sessions/{self.project_a.id}/preferences", json={"concurrency": 2})
            self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(json.loads((self.root_a / "projects" / self.project_a.id / "project.json").read_text())["name"], "远端新名称")


@pytest.mark.parametrize("endpoint", ["/global-state/sync", "/global-state/sync/setup"])
def test_sync_worker_keeps_loop_responsive_and_gates_storage(tmp_path: Path, endpoint: str) -> None:
    registry = ProjectRegistry(Store(tmp_path))
    project = registry.create_project(name="并发同步", cwd=str(tmp_path))
    entered, release = Event(), Event()
    worker_threads: list[int] = []

    def slow_git(*_arguments: object) -> None:
        worker_threads.append(get_ident())
        entered.set()
        assert release.wait(10)
        raise SyncError("模拟远端失败")

    method = "setup_existing_store" if endpoint.endswith("setup") else "sync_now"
    app = create_app(registry)
    with patch.object(registry.store.sync, method, side_effect=slow_git), patch("miniclaw2.app.install_hooks"):
        with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
            with client.websocket_connect(f"/ws/{project.id}") as websocket:
                syncing = pool.submit(client.post, endpoint, json={"remote_url": "/unused", "privacy_acknowledged": True})
                try:
                    assert entered.wait(5)
                    health = pool.submit(client.get, "/health").result(timeout=2)
                    assert health.json()["status"] == "maintenance"
                    assert client.get("/migrations/status").json()["state"] == "waiting_for_idle"
                    assert client.patch(f"/sessions/{project.id}", json={"name": "不应写入"}).status_code == 503
                    assert client.post("/global-state/sync").status_code == 503
                    websocket.send_json({"type": "user_message", "text": "不应启动"})
                    assert websocket.receive_json()["type"] == "error"
                    assert registry._runtimes[project.id].runner_tasks == {}
                    assert not registry.prepare_self_update()
                    assert client.portal.call(get_ident) not in worker_threads
                finally:
                    release.set()
                assert syncing.result(timeout=5).status_code == 409
            assert client.get("/health").json()["status"] == "ok"
            assert client.get(f"/sessions/{project.id}").json()["name"] == "并发同步"
            assert not registry._storage_sync_pending


@pytest.mark.parametrize("task_done", [False, True])
def test_sync_waits_for_runner_finalizers(tmp_path: Path, task_done: bool) -> None:
    registry = ProjectRegistry(Store(tmp_path))
    project = registry.create_project(name="终结写入", cwd=str(tmp_path))
    task: Future[None] = Future()
    if task_done:
        task.set_result(None)
    runtime = registry._runtimes[project.id]
    with patch.object(runtime, "runner_tasks", {"node": task}), patch.object(registry.store.sync, "sync_now") as sync_now:
        response = TestClient(create_app(registry)).post("/global-state/sync")
    assert response.status_code == 409
    sync_now.assert_not_called()
    assert not registry._storage_sync_pending


def test_sync_waits_for_admitted_http_requests(tmp_path: Path) -> None:
    registry = ProjectRegistry(Store(tmp_path))
    app = create_app(registry)
    entered, release = Event(), Event()

    @app.get("/slow-storage-request")
    def slow_request() -> dict[str, bool]:
        entered.set()
        assert release.wait(10)
        return {"done": True}

    with patch("miniclaw2.app.install_hooks"), patch.object(registry.store.sync, "sync_now") as sync_now:
        with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
            reading = pool.submit(client.get, "/slow-storage-request")
            try:
                assert entered.wait(5)
                assert client.post("/global-state/sync").status_code == 409
                sync_now.assert_not_called()
            finally:
                release.set()
            assert reading.result(timeout=5).status_code == 200


@pytest.mark.parametrize("cancellation", ["once", "repeated", "scope"])
@pytest.mark.parametrize("publish", [False, True])
def test_cancelled_sync_holds_gate_until_worker_finishes(
    tmp_path: Path, cancellation: str, publish: bool,
) -> None:
    registry = ProjectRegistry(Store(tmp_path))
    entered, release, finished = Event(), Event(), Event()
    incoming = Project(root_path=str(tmp_path / "incoming"), name="取消后完成同步的项目")
    app = create_app(registry)
    endpoint = next(route.endpoint for route in app.routes if route.path == "/global-state/sync")
    reload_from_store = registry.reload_from_store

    def slow_sync() -> None:
        entered.set()
        assert release.wait(10)
        if publish:
            registry.store.create_project(incoming)
            registry.store.sync.publication_generation += 1
        finished.set()

    def reload_after_sync() -> None:
        assert finished.is_set()
        assert app.state.storage_syncing
        assert registry._storage_sync_pending
        reload_from_store()

    async def cancel_sync() -> None:
        scope = CancelScope()

        async def scoped_sync() -> None:
            with scope:
                await endpoint()

        syncing = asyncio.create_task(scoped_sync())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            for _attempt in range(3 if cancellation == "repeated" else 1):
                if cancellation == "scope":
                    scope.cancel()
                else:
                    syncing.cancel()
                await asyncio.sleep(0.01)
                assert not syncing.done()
                assert app.state.storage_syncing
                assert registry._storage_sync_pending
        finally:
            release.set()
            try:
                await syncing
            except asyncio.CancelledError:
                pass
            assert await asyncio.to_thread(finished.wait, 5)
        if cancellation == "scope":
            assert scope.cancelled_caught
        else:
            assert syncing.cancelled()
        assert not app.state.storage_syncing
        assert not registry._storage_sync_pending
        if publish:
            assert registry.get_project(incoming.id) is not None
            reload_mock.assert_called_once_with()
        else:
            reload_mock.assert_not_called()

    with (
        patch.object(registry.store.sync, "sync_now", side_effect=slow_sync),
        patch.object(registry, "reload_from_store", side_effect=reload_after_sync) as reload_mock,
    ):
        asyncio.run(cancel_sync())


if __name__ == "__main__":
    unittest.main()
