"""Tests for the planspace plug helpers added in step 8."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import yaml

from miniclaw2.contextspace import (
    add_planspace_to_binding,
    create_planspace,
    ensure_project_binding,
    read_planspace_mode,
    resolve_project_binding,
)
from miniclaw2.domain import Project


class PlanspaceCreationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store_root = Path(self.tmp.name) / "store"
        os.environ["MINICLAW_CONTEXT_HOME"] = str(self.store_root / "ctx")
        self.project = Project(
            root_path=str(Path(self.tmp.name) / "repo"),
            name="auth-flow",
        )
        Path(self.project.root_path).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def test_create_planspace_creates_manifest_and_appends_to_binding(self) -> None:
        plug_id = create_planspace(
            self.project,
            title="Auth flow",
            mode="manual",
            seed_text="Build the auth flow",
        )
        self.assertEqual(plug_id, "planspaces.auth-flow")

        root = Path(os.environ["MINICLAW_CONTEXT_HOME"])
        manifest_path = root / "plugs" / "planspaces" / "auth-flow" / "manifest.yaml"
        self.assertTrue(manifest_path.exists())
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["kind"], "planspace")
        self.assertEqual(manifest["mode"], "manual")
        self.assertEqual(manifest["title"], "Auth flow")
        self.assertEqual(manifest["seed"], "Build the auth flow")
        self.assertEqual(manifest["id"], plug_id)

        binding = resolve_project_binding(self.project, root)
        assert binding is not None
        plug_ids = [ref.id for ref in binding.plugs]
        self.assertIn(plug_id, plug_ids)

    def test_create_planspace_idempotent_unique_slug(self) -> None:
        first = create_planspace(self.project, title="Direction", mode="manual")
        second = create_planspace(self.project, title="Direction", mode="manual")
        self.assertNotEqual(first, second)
        self.assertEqual(first, "planspaces.direction")
        self.assertTrue(second.startswith("planspaces.direction-"))

    def test_create_planspace_rejects_unknown_mode(self) -> None:
        with self.assertRaises(ValueError):
            create_planspace(self.project, title="x", mode="weird")

    def test_add_planspace_to_binding_is_idempotent(self) -> None:
        root = Path(os.environ["MINICLAW_CONTEXT_HOME"])
        binding = ensure_project_binding(self.project)
        add_planspace_to_binding(binding, "planspaces.foo")
        add_planspace_to_binding(binding, "planspaces.foo")
        reloaded = resolve_project_binding(self.project, root)
        assert reloaded is not None
        plug_ids = [ref.id for ref in reloaded.plugs]
        self.assertEqual(plug_ids.count("planspaces.foo"), 1)


class ReadPlanspaceModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.project = Project(root_path=str(Path(self.tmp.name) / "repo"))
        Path(self.project.root_path).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def test_defaults_to_manual_when_manifest_missing(self) -> None:
        self.assertEqual(
            read_planspace_mode(self.project, "planspaces.foo"),
            "manual",
        )

    def test_defaults_to_manual_when_lane_id_empty(self) -> None:
        self.assertEqual(read_planspace_mode(self.project, ""), "manual")

    def test_reads_auto_mode_from_manifest(self) -> None:
        plug_id = create_planspace(
            self.project, title="auto-lane", mode="auto"
        )
        self.assertEqual(read_planspace_mode(self.project, plug_id), "auto")

    def test_reads_manual_mode_from_manifest(self) -> None:
        plug_id = create_planspace(
            self.project, title="manual-lane", mode="manual"
        )
        self.assertEqual(read_planspace_mode(self.project, plug_id), "manual")


if __name__ == "__main__":
    unittest.main()
