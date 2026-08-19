from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.domain import Node, NodeState, Project
from miniclaw2.global_config import load_global_config, save_global_config
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store, StoreReadOnlyError
from miniclaw2.sync import SCHEMA_VERSION, SchemaConflictError, SyncError, bootstrap_store
from miniclaw2.sync import current_hostname
from miniclaw2.templates import load_user_template


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _write_user_template(
    root: Path,
    slug: str,
    *,
    template_text: str,
    prompt: str = "Do the work.",
) -> Path:
    template_root = root / "contextspace" / "templates" / slug
    (template_root / "prompts").mkdir(parents=True)
    (template_root / "template.yaml").write_text(template_text, encoding="utf-8")
    (template_root / "lane.yaml").write_text(
        yaml.safe_dump(
            {
                "nodes": [
                    {
                        "id": "work",
                        "kind": "agent",
                        "category": "regular",
                        "prompt_file": "prompts/work.md",
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (template_root / "prompts" / "work.md").write_text(prompt, encoding="utf-8")
    return template_root


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

    def test_v12_prepartitions_only_owned_durable_projects_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.mkdir(parents=True, exist_ok=True)
            machine_id = "local-machine"
            (root / "machine.json").write_text(
                json.dumps(
                    {
                        "id": machine_id,
                        "hostname": current_hostname(),
                        "label": "Local",
                    }
                ),
                encoding="utf-8",
            )
            (root / "schema.json").write_text(
                json.dumps({"schema": "node-revision-v9", "schema_version": 11}),
                encoding="utf-8",
            )
            repo = root / "repo"
            repo.mkdir()
            _git("init", "-q", "--initial-branch=main", cwd=repo)
            (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
            _git("add", "seed.txt", cwd=repo)
            _git(
                "-c", "user.name=Test", "-c", "user.email=test@example.com",
                "commit", "-q", "-m", "seed", cwd=repo,
            )

            owned = Project(
                root_path=str(repo),
                machine_id=machine_id,
                machine_label="Local",
                layout_hints={"node-a": {"x": 1, "y": 2}},
            )
            foreign = Project(
                root_path="/remote/checkout",
                machine_id="remote-machine",
                machine_label="Remote",
            )
            temporary = Project(
                root_path=str(root / "temporary"),
                machine_id=machine_id,
                machine_label="Local",
                temporary=True,
            )
            for project in (owned, foreign, temporary):
                project_dir = root / "projects" / project.id
                (project_dir / "nodes").mkdir(parents=True)
                (project_dir / "project.json").write_text(
                    json.dumps(project.model_dump(exclude={"provider"})),
                    encoding="utf-8",
                )
            owned_node = Node(
                id="node-a",
                project_id=owned.id,
                model_preset_id=owned.model_preset_id,
            )
            owned_node_file = root / "projects" / owned.id / "nodes" / owned_node.id / "node.json"
            owned_node_file.parent.mkdir(parents=True)
            owned_node_file.write_text(
                json.dumps(owned_node.model_dump(exclude={"provider", "owner_host_id"})),
                encoding="utf-8",
            )

            store = Store(root)

            owned_dir = root / "projects" / owned.id
            host_dir = owned_dir / "hosts" / machine_id
            payload = json.loads((owned_dir / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["sharing"], "device-native")
            self.assertNotIn("root_path", payload)
            self.assertNotIn("layout_hints", payload)
            self.assertFalse((owned_dir / "nodes").exists())
            self.assertTrue((host_dir / "nodes" / "node-a" / "node.json").is_file())
            self.assertEqual(
                json.loads((host_dir / "local.json").read_text(encoding="utf-8"))["root_path"],
                str(repo),
            )
            self.assertTrue(
                json.loads((host_dir / "host.json").read_text(encoding="utf-8"))["repo"]["root_commit"]
            )
            self.assertTrue((root / "projects" / foreign.id / "nodes").is_dir())
            self.assertTrue((root / "projects" / temporary.id / "nodes").is_dir())
            backups = sorted((root / "migration-backups").glob("native-prepartition-v12-*"))
            self.assertEqual(len(backups), 1)

            Store(root)
            self.assertEqual(
                sorted((root / "migration-backups").glob("native-prepartition-v12-*")),
                backups,
            )
            loaded = next(project for project in store.list_projects() if project.id == owned.id)
            self.assertEqual(loaded.sharing, "device-native")
            self.assertEqual(loaded.root_path, str(repo))


class UserTemplateSchemaMigrationTests(unittest.TestCase):
    def _write_store_schema_v7(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "schema.json").write_text(
            json.dumps({"schema": "host-partition-v7", "schema_version": 7}),
            encoding="utf-8",
        )

    def test_legacy_template_is_backed_up_migrated_loadable_and_warned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_store_schema_v7(root)
            template_root = _write_user_template(
                root,
                "legacy",
                template_text=(
                    "name: legacy\n"
                    "brief: Legacy template\n"
                    "allowed_model_preset_ids: [opus-4-8]\n"
                    "lane_mode: manual\n"
                ),
                prompt="Discuss {{topic}} while preserving {{Not-An-Arg}}.\n",
            )

            with self.assertLogs("miniclaw2.sync", level="WARNING") as captured:
                Store(root)

            migrated = yaml.safe_load(
                (template_root / "template.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(migrated["schema_version"], 2)
            self.assertEqual(migrated["arguments"], [])
            self.assertEqual(migrated["inputs"], [])
            loaded = load_user_template("legacy", store_root=root)
            self.assertEqual([argument.name for argument in loaded.arguments], ["topic"])

            warning_text = "\n".join(captured.output)
            self.assertIn("prompts/work.md:1: {{topic}}", warning_text)
            self.assertNotIn("Not-An-Arg", warning_text)

            backups = list(
                (root / "migration-backups").glob(
                    "node-revision-v9-*/contextspace/templates/legacy/template.yaml"
                )
            )
            self.assertEqual(len(backups), 1)
            self.assertNotIn(
                "schema_version",
                yaml.safe_load(backups[0].read_text(encoding="utf-8")),
            )

            first_result = (template_root / "template.yaml").read_text(encoding="utf-8")
            first_backups = sorted((root / "migration-backups").iterdir())
            Store(root)
            self.assertEqual(
                (template_root / "template.yaml").read_text(encoding="utf-8"),
                first_result,
            )
            self.assertEqual(
                sorted((root / "migration-backups").iterdir()),
                first_backups,
            )

    def test_schema_v2_template_is_skipped_without_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_store_schema_v7(root)
            original = (
                "# preserve this exact v2 file\n"
                "schema_version: 2\n"
                "name: current\n"
                "brief: Current template\n"
                "allowed_model_preset_ids: [opus-4-8]\n"
                "lane_mode: manual\n"
            )
            template_root = _write_user_template(
                root,
                "current",
                template_text=original,
                prompt="Discuss {{topic}}.\n",
            )

            Store(root)

            self.assertEqual(
                (template_root / "template.yaml").read_text(encoding="utf-8"),
                original,
            )
            self.assertTrue(
                list(
                    (root / "migration-backups").glob(
                        "node-revision-v9-*/contextspace/templates/current/template.yaml"
                    )
                )
            )

    def test_bad_template_does_not_block_other_template_migrations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_store_schema_v7(root)
            bad_root = _write_user_template(
                root,
                "bad",
                template_text="name: [\n",
            )
            good_root = _write_user_template(
                root,
                "good",
                template_text=(
                    "name: good\n"
                    "brief: Good template\n"
                    "allowed_model_preset_ids: [opus-4-8]\n"
                    "lane_mode: manual\n"
                ),
            )
            bad_original = (bad_root / "template.yaml").read_text(encoding="utf-8")

            with self.assertLogs("miniclaw2.sync", level="ERROR") as captured:
                Store(root)

            self.assertIn("用户模板 'bad' 迁移失败", "\n".join(captured.output))
            self.assertEqual(
                (bad_root / "template.yaml").read_text(encoding="utf-8"),
                bad_original,
            )
            self.assertEqual(
                yaml.safe_load(
                    (good_root / "template.yaml").read_text(encoding="utf-8")
                )["schema_version"],
                2,
            )
            load_user_template("good", store_root=root)
            self.assertEqual(
                json.loads((root / "schema.json").read_text(encoding="utf-8"))[
                    "schema_version"
                ],
                SCHEMA_VERSION,
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

    def test_tag_conflict_requires_manual_resolution(self) -> None:
        tag_a = self.store_a.create_tag("Machine A", "coral")
        self.store_a.sync.commit_now("create tag on A")

        self.store_b.create_tag("Machine B", "sage")
        self.store_b.sync.commit_now("create tag on B")
        self.store_b.sync.sync_now()

        with self.assertRaisesRegex(
            SyncError,
            "tags.json changed independently.*resolve it manually",
        ):
            self.store_a.sync.sync_now()

        self.assertFalse((self.root_a / ".git" / "MERGE_HEAD").exists())
        self.assertEqual(self.store_a.list_tags(), [tag_a])
        self.assertEqual(self.store_a.sync.status()["status"], "changed")

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
