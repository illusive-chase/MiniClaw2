from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.domain import Node, NodeState, Project
from miniclaw2.global_config import load_global_config, save_global_config
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store, StoreReadOnlyError
from miniclaw2.sync import SCHEMA_VERSION, SchemaConflictError, SyncError, bootstrap_store


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


class StoreIdentityMigrationTests(unittest.TestCase):
    def test_legacy_projects_are_stamped_and_backed_up(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = Project(root_path="/tmp/legacy", name="Legacy")
            payload = project.model_dump(
                exclude={"provider", "machine_id", "machine_label"}
            )
            project_file = root / "projects" / project.id / "project.json"
            project_file.parent.mkdir(parents=True)
            project_file.write_text(json.dumps(payload), encoding="utf-8")

            store = Store(root)

            migrated = json.loads(project_file.read_text(encoding="utf-8"))
            self.assertEqual(migrated["machine_id"], store.machine.id)
            self.assertEqual(migrated["machine_label"], store.machine.label)
            self.assertTrue((root / "machine.json").is_file())
            self.assertEqual(
                json.loads((root / "schema.json").read_text())["schema_version"],
                SCHEMA_VERSION,
            )
            self.assertTrue(list((root / "migration-backups").glob("*/projects/*/project.json")))
            ignore = (root / ".gitignore").read_text()
            self.assertIn("machine.json", ignore)
            self.assertIn("migration-backups/", ignore)
            self.assertIn("*.tmp", ignore)


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
        self.registry = ProjectRegistry(self.store)
        self.client = TestClient(create_app(self.registry))

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def test_viewing_is_allowed_but_mutations_are_rejected(self) -> None:
        response = self.client.get(f"/sessions/{self.project.id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["read_only"])
        self.assertEqual(response.json()["native_machine_label"], "alice-mbp")

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
            self.assertIn("alice-mbp", mutation.json()["detail"])


class GitMetadataSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.remote = self.base / "remote.git"
        _git("init", "--bare", str(self.remote))
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

    def test_global_conflict_uses_local_hunks_then_converges(self) -> None:
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

        self.store_a.sync.sync_now()
        self.assertEqual(
            load_global_config(self.root_a).defaults.preferred_language,
            "English",
        )
        self.store_b.sync.sync_now()
        self.assertEqual(
            load_global_config(self.root_b).defaults.preferred_language,
            "English",
        )

    def test_schema_conflict_is_a_hard_failure(self) -> None:
        schema_a = json.loads((self.root_a / "schema.json").read_text())
        schema_a["migration"] = "machine-a"
        (self.root_a / "schema.json").write_text(
            json.dumps(schema_a, indent=2) + "\n", encoding="utf-8"
        )
        self.store_a.sync.commit_now("migrate schema on A")

        schema_b = json.loads((self.root_b / "schema.json").read_text())
        schema_b["migration"] = "machine-b"
        (self.root_b / "schema.json").write_text(
            json.dumps(schema_b, indent=2) + "\n", encoding="utf-8"
        )
        self.store_b.sync.commit_now("migrate schema on B")
        self.store_b.sync.sync_now()

        with self.assertRaises(SchemaConflictError):
            self.store_a.sync.sync_now()
        self.assertFalse((self.root_a / ".git" / "MERGE_HEAD").exists())
        self.assertEqual(self.store_a.sync.status()["status"], "changed")
        reloaded = Store(self.root_a)
        self.assertEqual(reloaded.sync.status()["status"], "changed")

    def test_remote_schema_upgrade_makes_live_store_read_only(self) -> None:
        schema_b = json.loads((self.root_b / "schema.json").read_text())
        schema_b["schema_version"] += 1
        (self.root_b / "schema.json").write_text(
            json.dumps(schema_b, indent=2) + "\n", encoding="utf-8"
        )
        self.store_b.sync.sync_now()

        self.assertIsNone(self.store_a.read_only_reason)
        self.store_a.sync.sync_now()

        self.assertEqual(
            self.store_a.read_only_reason,
            "store schema is newer than this MiniClaw2 version",
        )
        with self.assertRaises(StoreReadOnlyError):
            self.store_a.create_project(
                Project(root_path="/machine-a/blocked", name="Blocked")
            )

    def test_read_only_store_does_not_schedule_native_projects(self) -> None:
        schema = json.loads((self.root_a / "schema.json").read_text())
        schema["schema_version"] += 1
        (self.root_a / "schema.json").write_text(
            json.dumps(schema, indent=2) + "\n", encoding="utf-8"
        )
        registry = ProjectRegistry(self.store_a)

        with patch.object(registry, "_schedule_queued") as schedule_queued:
            registry.schedule_all()

        schedule_queued.assert_not_called()

    def test_failed_push_rolls_back_the_fetched_merge(self) -> None:
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

        self.assertEqual(
            _git("rev-parse", "HEAD", cwd=self.root_a).stdout.strip(),
            starting_head,
        )
        self.assertFalse(
            (self.root_a / "projects" / project_b.id / "project.json").exists()
        )
        self.assertEqual(self.store_a.sync.status()["status"], "changed")


if __name__ == "__main__":
    unittest.main()
