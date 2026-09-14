from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
