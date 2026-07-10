from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from miniclaw2.migrations import (
    StoreMigrationError,
    check_store,
    migrate_store,
    repair_store,
)
from miniclaw2.store import Store


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ModelPresetMigrationTest(unittest.TestCase):
    def test_importing_app_does_not_migrate_default_store(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            root = home / ".miniclaw2"
            project_path = root / "projects" / "p1" / "project.json"
            _write_json(
                project_path,
                {
                    "id": "p1",
                    "root_path": str(home / "repo"),
                    "provider": "claude",
                    "settings_override": {"model": "opus-4-7"},
                },
            )
            original_project = project_path.read_text(encoding="utf-8")
            env = os.environ.copy()
            env.pop("MINICLAW_HOME", None)
            env.pop("MINICLAW_CONTEXT_HOME", None)
            env["HOME"] = str(home)

            completed = subprocess.run(
                [sys.executable, "-c", "import miniclaw2.app"],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                project_path.read_text(encoding="utf-8"),
                original_project,
            )
            self.assertFalse((root / "schema.json").exists())
            self.assertFalse((root / "migration-backups").exists())
            self.assertFalse((root / "migrations").exists())

    def test_legacy_provider_project_node_preview_and_template_migrate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            context_root = root / "contextspace"
            project_dir = root / "projects" / "p1"
            node_dir = project_dir / "nodes" / "n1"
            template_dir = context_root / "templates" / "legacy"
            _write_json(
                project_dir / "project.json",
                {
                    "id": "p1",
                    "root_path": str(root / "repo"),
                    "provider": "claude",
                    "settings_override": {
                        "model": "opus-4-7",
                        "active_planspace_id": "planspaces.one",
                    },
                },
            )
            _write_json(
                node_dir / "node.json",
                {
                    "id": "n1",
                    "project_id": "p1",
                    "kind": "agent",
                    "state": "virtual",
                    "provider": "claude",
                    "planspace_id": "planspaces.one",
                    "prompt_draft": "Do work.",
                    "settings_snapshot": {"model": "opus-4-7"},
                },
            )
            _write_json(
                node_dir / "preview.json",
                {
                    "id": "n1",
                    "kind": "agent",
                    "category": "regular",
                    "state": "virtual",
                    "lane": "planspaces.one",
                    "prompt_draft": "Do work.",
                    "motivation": "legacy preview",
                    "scheduled_deps": [],
                },
            )
            template_dir.mkdir(parents=True)
            (template_dir / "template.yaml").write_text(
                yaml.safe_dump(
                    {
                        "name": "legacy",
                        "brief": "legacy",
                        "providers": ["claude", "codex"],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {"MINICLAW_CONTEXT_HOME": str(context_root)},
            ):
                migrate_store(root)

            project = _read_json(project_dir / "project.json")
            self.assertEqual(project["model_preset_id"], "opus-4-7")
            self.assertEqual(project["provider"], "claude")
            self.assertNotIn("model", project["settings_override"])
            self.assertEqual(
                project["settings_override"]["active_planspace_id"],
                "planspaces.one",
            )

            node = _read_json(node_dir / "node.json")
            self.assertEqual(node["model_preset_id"], "opus-4-7")
            self.assertEqual(node["provider"], "claude")
            self.assertEqual(node["settings_snapshot"]["model_preset_id"], "opus-4-7")
            self.assertEqual(node["settings_snapshot"]["model"], "opus-4-7")

            preview = _read_json(node_dir / "preview.json")
            self.assertEqual(preview["model_preset_id"], "opus-4-7")

            template = yaml.safe_load(
                (template_dir / "template.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(
                template["allowed_model_preset_ids"],
                ["opus-4-7", "gpt-5.5"],
            )
            self.assertNotIn("providers", template)

            schema = _read_json(root / "schema.json")
            self.assertEqual(schema["schema_version"], 2)
            self.assertTrue((root / "migrations" / "model-presets-v2.jsonl").exists())
            backups = list((root / "migration-backups").glob("model-presets-v2-*"))
            self.assertEqual(len(backups), 1)

    def test_unknown_legacy_model_combo_fails_without_defaulting(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _write_json(
                root / "projects" / "p1" / "project.json",
                {
                    "id": "p1",
                    "root_path": str(root / "repo"),
                    "provider": "codex",
                    "settings_override": {"model": "not-a-preset"},
                },
            )

            with self.assertRaisesRegex(
                StoreMigrationError,
                "cannot map legacy provider/model settings",
            ):
                migrate_store(root)

    def test_legacy_reasoning_effort_must_match_project_preset(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project_path = root / "projects" / "p1" / "project.json"
            _write_json(
                project_path,
                {
                    "id": "p1",
                    "root_path": str(root / "repo"),
                    "provider": "codex",
                    "settings_override": {
                        "model": "gpt-5.5",
                        "reasoning_effort": "high",
                    },
                },
            )

            with self.assertRaisesRegex(
                StoreMigrationError,
                "cannot map legacy provider/model settings",
            ):
                migrate_store(root)

            self.assertEqual(
                _read_json(project_path)["settings_override"]["reasoning_effort"],
                "high",
            )
            self.assertFalse((root / "schema.json").exists())

    def test_legacy_reasoning_effort_must_match_node_preset(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project_dir = root / "projects" / "p1"
            node_path = project_dir / "nodes" / "n1" / "node.json"
            _write_json(
                project_dir / "project.json",
                {
                    "id": "p1",
                    "root_path": str(root / "repo"),
                    "provider": "codex",
                    "settings_override": {},
                },
            )
            _write_json(
                node_path,
                {
                    "id": "n1",
                    "project_id": "p1",
                    "kind": "agent",
                    "state": "done",
                    "provider": "codex",
                    "settings_snapshot": {"reasoning_effort": "low"},
                },
            )

            with self.assertRaisesRegex(
                StoreMigrationError,
                "cannot map legacy provider/model settings",
            ):
                migrate_store(root)

            self.assertEqual(
                _read_json(node_path)["settings_snapshot"]["reasoning_effort"],
                "low",
            )
            self.assertFalse((root / "schema.json").exists())

    def test_missing_legacy_provider_fails_without_defaulting(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _write_json(
                root / "projects" / "p1" / "project.json",
                {
                    "id": "p1",
                    "root_path": str(root / "repo"),
                    "settings_override": {},
                },
            )

            with self.assertRaisesRegex(
                StoreMigrationError,
                "unknown legacy provider",
            ):
                migrate_store(root)

    def test_external_context_home_template_backup_stays_under_store(self) -> None:
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as ctx:
            root = Path(raw)
            context_root = Path(ctx)
            template_dir = context_root / "templates" / "legacy"
            template_dir.mkdir(parents=True)
            (template_dir / "template.yaml").write_text(
                yaml.safe_dump(
                    {
                        "name": "legacy",
                        "brief": "legacy",
                        "providers": ["claude"],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {"MINICLAW_CONTEXT_HOME": str(context_root)},
            ):
                migrate_store(root)

            migrated = yaml.safe_load(
                (template_dir / "template.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(migrated["allowed_model_preset_ids"], ["opus-4-7"])
            backups = list((root / "migration-backups").glob("model-presets-v2-*"))
            self.assertEqual(len(backups), 1)
            backup_templates = list(backups[0].rglob("template.yaml"))
            self.assertEqual(len(backup_templates), 1)
            backup = yaml.safe_load(backup_templates[0].read_text(encoding="utf-8"))
            self.assertEqual(backup["providers"], ["claude"])

    def test_current_schema_missing_agent_preset_fails_in_migration_layer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _write_json(root / "schema.json", {"schema_version": 2})
            project_dir = root / "projects" / "p1"
            node_dir = project_dir / "nodes" / "n1"
            _write_json(
                project_dir / "project.json",
                {
                    "id": "p1",
                    "root_path": str(root / "repo"),
                    "model_preset_id": "gpt-5.5",
                    "provider": "codex",
                    "settings_override": {},
                },
            )
            _write_json(
                node_dir / "node.json",
                {
                    "id": "n1",
                    "project_id": "p1",
                    "kind": "agent",
                    "state": "done",
                    "provider": "codex",
                },
            )

            with self.assertRaisesRegex(
                StoreMigrationError,
                "node requires model_preset_id",
            ):
                Store(root=root)

    def test_repair_current_schema_with_legacy_project_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project_path = root / "projects" / "p1" / "project.json"
            _write_json(root / "schema.json", {"schema_version": 2})
            _write_json(
                project_path,
                {
                    "id": "p1",
                    "root_path": str(root / "repo"),
                    "provider": "claude",
                    "settings_override": {},
                },
            )

            with self.assertRaisesRegex(
                StoreMigrationError,
                "--repair-store",
            ):
                migrate_store(root)

            report = repair_store(root)

            self.assertTrue(report.repaired)
            self.assertEqual(report.version_before, 2)
            self.assertEqual(report.version_after, 2)
            self.assertEqual(_read_json(project_path)["model_preset_id"], "opus-4-7")
            self.assertIsNotNone(report.backup_root)
            self.assertTrue((report.backup_root / "projects/p1/project.json").exists())
            check_store(root)

            backups_before = list((root / "migration-backups").iterdir())
            second = repair_store(root)
            backups_after = list((root / "migration-backups").iterdir())
            self.assertFalse(second.repaired)
            self.assertEqual(second.changed_files, ())
            self.assertEqual(backups_after, backups_before)

    def test_validation_failure_does_not_mark_migration_complete(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project_dir = root / "projects" / "p1"
            node_dir = project_dir / "nodes" / "n1"
            _write_json(root / "schema.json", {"schema_version": 1})
            _write_json(
                project_dir / "project.json",
                {
                    "id": "p1",
                    "root_path": str(root / "repo"),
                    "provider": "claude",
                    "settings_override": {"model": "opus-4-7"},
                },
            )
            _write_json(
                node_dir / "node.json",
                {
                    "id": "n1",
                    "project_id": "p1",
                    "kind": "agent",
                    "state": "virtual",
                    "provider": "claude",
                    "settings_snapshot": {"model": "opus-4-7"},
                },
            )
            _write_json(
                node_dir / "preview.json",
                {
                    "id": "n1",
                    "kind": "agent",
                    "state": "virtual",
                    "model_preset_id": "gpt-5.5",
                },
            )

            with self.assertRaisesRegex(
                StoreMigrationError,
                "does not match node model_preset_id",
            ):
                migrate_store(root)

            self.assertEqual(_read_json(root / "schema.json")["schema_version"], 1)
            project = _read_json(project_dir / "project.json")
            self.assertNotIn("model_preset_id", project)
            self.assertEqual(project["settings_override"]["model"], "opus-4-7")
            node = _read_json(node_dir / "node.json")
            self.assertNotIn("model_preset_id", node)
            self.assertEqual(node["settings_snapshot"]["model"], "opus-4-7")
            audit_path = root / "migrations" / "model-presets-v2.jsonl"
            audit_records = [
                json.loads(line)
                for line in audit_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertNotIn(
                "migration_complete",
                {record["action"] for record in audit_records},
            )
            self.assertIn(
                "migration_rolled_back",
                {record["action"] for record in audit_records},
            )

    def test_current_schema_template_providers_field_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            context_root = root / "contextspace"
            template_dir = context_root / "templates" / "legacy"
            _write_json(root / "schema.json", {"schema_version": 2})
            template_dir.mkdir(parents=True)
            (template_dir / "template.yaml").write_text(
                yaml.safe_dump(
                    {
                        "name": "legacy",
                        "brief": "legacy",
                        "providers": ["claude"],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {"MINICLAW_CONTEXT_HOME": str(context_root)},
            ):
                with self.assertRaisesRegex(
                    StoreMigrationError,
                    "providers is obsolete",
                ):
                    Store(root=root)


if __name__ == "__main__":
    unittest.main()
