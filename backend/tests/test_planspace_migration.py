from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

from miniclaw2.migrate_planspaces import apply_plan, build_plan


class ProjectScopedPlanspaceMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.context_root = Path(self.tmp.name) / "context"
        self.project_dir = self.home / "projects" / "p1"
        self.node_dir = self.project_dir / "nodes" / "n1"
        self.binding_path = (
            self.context_root
            / "bindings"
            / "projects"
            / "project.alpha.yaml"
        )
        self.old_plug_dir = (
            self.context_root / "plugs" / "planspaces" / "direction-2"
        )
        self.node_dir.mkdir(parents=True)
        self.binding_path.parent.mkdir(parents=True)
        self.old_plug_dir.mkdir(parents=True)
        self._write_json(
            self.project_dir / "project.json",
            {
                "id": "p1",
                "project_context_binding_id": "project.alpha",
                "active_planspace_id": "planspaces.direction-2",
                "planspace_view": {
                    "planspaces.direction-2": {"hidden": True},
                },
            },
        )
        self._write_json(
            self.node_dir / "node.json",
            {
                "id": "n1",
                "planspace_id": "planspaces.direction-2",
                "settings_snapshot": {
                    "active_planspace_id": "planspaces.direction-2",
                },
            },
        )
        self._write_json(
            self.node_dir / "preview.json",
            {"id": "n1", "lane": "planspaces.direction-2"},
        )
        self.binding_path.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "id": "project.alpha",
                    "project": {"miniclaw_project_id": "p1"},
                    "plugs": [
                        {"id": "planspaces.direction-2", "enabled": True},
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (self.old_plug_dir / "manifest.yaml").write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "kind": "planspace",
                    "id": "planspaces.direction-2",
                    "title": "Direction",
                    "mode": "manual",
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_plan_uses_manifest_title_to_remove_global_collision_suffix(self) -> None:
        plan = build_plan(self.home, self.context_root)

        self.assertEqual(plan.changed_lane_ids, 1)
        self.assertEqual(
            plan.binding_maps["project.alpha"]["planspaces.direction-2"],
            "planspaces.alpha.direction",
        )
        self.assertTrue(self.old_plug_dir.exists())

    def test_apply_rewrites_all_current_references_and_creates_backup(self) -> None:
        backup = apply_plan(build_plan(self.home, self.context_root))

        assert backup is not None
        self.assertTrue((backup / "projects" / "p1" / "project.json").exists())
        self.assertFalse(self.old_plug_dir.exists())
        new_plug_dir = (
            self.context_root / "plugs" / "planspaces" / "alpha.direction"
        )
        manifest = yaml.safe_load(
            (new_plug_dir / "manifest.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["id"], "planspaces.alpha.direction")
        binding = yaml.safe_load(self.binding_path.read_text(encoding="utf-8"))
        self.assertEqual(
            binding["plugs"][0]["id"], "planspaces.alpha.direction"
        )
        project = self._read_json(self.project_dir / "project.json")
        self.assertEqual(
            project["active_planspace_id"], "planspaces.alpha.direction"
        )
        self.assertEqual(
            list(project["planspace_view"]), ["planspaces.alpha.direction"]
        )
        node = self._read_json(self.node_dir / "node.json")
        self.assertEqual(node["planspace_id"], "planspaces.alpha.direction")
        self.assertEqual(
            node["settings_snapshot"]["active_planspace_id"],
            "planspaces.alpha.direction",
        )
        preview = self._read_json(self.node_dir / "preview.json")
        self.assertEqual(preview["lane"], "planspaces.alpha.direction")
        self.assertEqual(
            build_plan(self.home, self.context_root).changed_lane_ids,
            0,
        )

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def _read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
