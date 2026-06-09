from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.contextspace import add_planspace_to_binding


def _write_contextspace(home: Path, project_root: Path) -> None:
    ctx = home / "contextspace"
    (ctx / "bindings" / "projects").mkdir(parents=True)
    (ctx / "plugs" / "planspaces" / "memory").mkdir(parents=True)
    (ctx / "plugs" / "planspaces" / "other").mkdir(parents=True)
    (ctx / "plugs" / "skills" / "python-testing").mkdir(parents=True)

    (ctx / "bindings" / "projects" / "project.test.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "id: project.test",
                "project:",
                "  name: Test Project",
                "  local_paths:",
                f"    - {project_root}",
                "plugs:",
                "  - id: planspaces.memory",
                "    role: status-plan",
                "    enabled: true",
                "    auto_update: true",
                "  - id: planspaces.other",
                "    role: status-plan",
                "    enabled: true",
                "    auto_update: true",
                "  - id: skills.python-testing",
                "    role: skill",
                "    enabled: true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    for slug, title in (("memory", "Memory"), ("other", "Other")):
        (ctx / "plugs" / "planspaces" / slug / "manifest.yaml").write_text(
            "\n".join(
                [
                    "version: 1",
                    f"id: planspaces.{slug}",
                    "kind: planspace",
                    f"title: {title}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    (ctx / "plugs" / "skills" / "python-testing" / "manifest.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "id: skills.python-testing",
                "kind: skill",
                "title: Python Testing",
                "",
            ]
        ),
        encoding="utf-8",
    )


class ContextSpaceApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self._cwd = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        _write_contextspace(Path(self._home.name), Path(self._cwd.name))
        self.app = create_app()
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self._cwd.cleanup()
        self._home.cleanup()

    def _create_session(self) -> str:
        res = self.client.post(
            "/sessions",
            json={"cwd": self._cwd.name, "provider": "claude"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["id"]

    def test_get_contextspace_resolves_path_matched_binding(self) -> None:
        sid = self._create_session()

        res = self.client.get(f"/sessions/{sid}/contextspace")

        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["exists"])
        self.assertIsNone(body["project_context_binding_id"])
        self.assertEqual(body["resolved_binding_id"], "project.test")
        self.assertIsNone(body["active_planspace_id"])
        self.assertEqual(len(body["bindings"]), 1)
        binding = body["bindings"][0]
        self.assertTrue(binding["matches_project_path"])
        plug_ids = {plug["id"] for plug in binding["plugs"]}
        self.assertIn("planspaces.memory", plug_ids)
        self.assertIn("planspaces.other", plug_ids)
        self.assertIn("skills.python-testing", plug_ids)

    def test_patch_contextspace_sets_binding_and_active_planspace(self) -> None:
        sid = self._create_session()

        res = self.client.patch(
            f"/sessions/{sid}/contextspace",
            json={
                "project_context_binding_id": "project.test",
                "active_planspace_id": "planspaces.other",
            },
        )

        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["project_context_binding_id"], "project.test")
        self.assertEqual(body["project_active_planspace_id"], "planspaces.other")
        self.assertEqual(body["active_planspace_id"], "planspaces.other")

        listed = self.client.get("/sessions").json()
        match = next(item for item in listed if item["id"] == sid)
        self.assertEqual(match["project_context_binding_id"], "project.test")

    def test_patch_contextspace_null_clears_project_overrides(self) -> None:
        sid = self._create_session()
        self.client.patch(
            f"/sessions/{sid}/contextspace",
            json={
                "project_context_binding_id": "project.test",
                "active_planspace_id": "planspaces.other",
            },
        )

        res = self.client.patch(
            f"/sessions/{sid}/contextspace",
            json={
                "project_context_binding_id": None,
                "active_planspace_id": None,
            },
        )

        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertIsNone(body["project_context_binding_id"])
        self.assertIsNone(body["project_active_planspace_id"])
        self.assertEqual(body["resolved_binding_id"], "project.test")


class ContextSpaceBootstrapApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self._cwd = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        self.app = create_app()
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self._cwd.cleanup()
        self._home.cleanup()

    def test_bootstrap_creates_contextspace_and_binds_project(self) -> None:
        create = self.client.post(
            "/sessions",
            json={
                "cwd": self._cwd.name,
                "provider": "claude",
                "name": "Alpha Project",
            },
        )
        self.assertEqual(create.status_code, 200, create.text)
        sid = create.json()["id"]

        before = self.client.get(f"/sessions/{sid}/contextspace")
        self.assertEqual(before.status_code, 200, before.text)
        self.assertFalse(before.json()["exists"])

        res = self.client.post(
            f"/sessions/{sid}/contextspace/bootstrap",
            json={"title": "Alpha Track"},
        )

        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["exists"])
        self.assertEqual(body["project_context_binding_id"], "project.alpha-track")
        self.assertEqual(body["project_active_planspace_id"], "planspaces.alpha-track")
        self.assertEqual(body["resolved_binding_id"], "project.alpha-track")
        self.assertEqual(body["active_planspace_id"], "planspaces.alpha-track")
        self.assertIn("contextspace.yaml", body["bootstrap"]["created"])
        self.assertIn(
            "plugs/planspaces/alpha-track/manifest.yaml",
            body["bootstrap"]["created"],
        )
        self.assertTrue(
            (Path(self._home.name) / "contextspace" / "contextspace.yaml").exists()
        )
        self.assertTrue(
            (
                Path(self._home.name)
                / "contextspace"
                / "bindings"
                / "projects"
                / "project.alpha-track.yaml"
            ).exists()
        )

        listed = self.client.get("/sessions").json()
        match = next(item for item in listed if item["id"] == sid)
        self.assertEqual(match["project_context_binding_id"], "project.alpha-track")

    def test_delete_session_removes_owned_binding_and_planspaces(self) -> None:
        create = self.client.post(
            "/sessions",
            json={
                "cwd": self._cwd.name,
                "provider": "claude",
                "name": "Alpha Project",
            },
        )
        self.assertEqual(create.status_code, 200, create.text)
        sid = create.json()["id"]

        boot = self.client.post(
            f"/sessions/{sid}/contextspace/bootstrap",
            json={"title": "Alpha Track"},
        )
        self.assertEqual(boot.status_code, 200, boot.text)
        add_planspace_to_binding(
            "project.alpha-track",
            title="Second Track",
            planspace_slug="second-track",
            store_root=Path(self._home.name),
        )

        context_root = Path(self._home.name) / "contextspace"
        binding_path = (
            context_root / "bindings" / "projects" / "project.alpha-track.yaml"
        )
        first_planspace = context_root / "plugs" / "planspaces" / "alpha-track"
        second_planspace = context_root / "plugs" / "planspaces" / "second-track"
        self.assertTrue(binding_path.exists())
        self.assertTrue(first_planspace.exists())
        self.assertTrue(second_planspace.exists())

        delete = self.client.delete(f"/sessions/{sid}")

        self.assertEqual(delete.status_code, 200, delete.text)
        self.assertFalse((Path(self._home.name) / "projects" / sid).exists())
        self.assertFalse(binding_path.exists())
        self.assertFalse(first_planspace.exists())
        self.assertFalse(second_planspace.exists())


if __name__ == "__main__":
    unittest.main()
