from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.artifacts import (
    INLINE_TEXT_CAP,
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACTS_PER_NODE,
    publish_artifacts,
    stored_artifacts_dir,
    workspace_artifacts_dir,
)
from miniclaw2.domain import Category, Node, NodeState, Project
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


class ArtifactPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = Store(root=root / "store")
        self.project = Project(
            id="p1",
            root_path=str(root / "project"),
            model_preset_id="gpt-5.5",
        )
        Path(self.project.root_path).mkdir()
        self.store.create_project(self.project)
        self.node = Node(
            id="n1",
            project_id=self.project.id,
            category=Category.REGULAR,
            state=NodeState.DONE,
            model_preset_id="gpt-5.5",
        )
        self.store.create_node(self.node)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_publishes_allowed_files_and_records_drops(self) -> None:
        outputs = workspace_artifacts_dir(self.project, self.node.id)
        outputs.mkdir(parents=True)
        (outputs / "report.md").write_text("# Report\n", encoding="utf-8")
        (outputs / "scratch.txt").write_text("hidden", encoding="utf-8")

        refs = publish_artifacts(
            self.project,
            self.node,
            ["report.md", "scratch.txt", "missing.json", "../escape.md"],
            self.store,
        )

        self.assertEqual([ref.status for ref in refs], ["published", "dropped", "dropped", "dropped"])
        self.assertEqual(refs[0].name, "report.md")
        self.assertEqual(len(refs[0].sha256), 64)
        self.assertIn("suffix", refs[1].reason or "")
        self.assertIn("does not exist", refs[2].reason or "")
        self.assertIn("bare filename", refs[3].reason or "")
        durable = stored_artifacts_dir(self.store, self.project.id, self.node.id)
        self.assertEqual((durable / "report.md").read_text(encoding="utf-8"), "# Report\n")
        self.assertFalse((durable / "scratch.txt").exists())

    def test_republication_replaces_stale_files_and_enforces_caps(self) -> None:
        outputs = workspace_artifacts_dir(self.project, self.node.id)
        outputs.mkdir(parents=True)
        (outputs / "old.md").write_text("old", encoding="utf-8")
        publish_artifacts(self.project, self.node, ["old.md"], self.store)
        (outputs / "large.md").write_bytes(b"x" * (MAX_ARTIFACT_BYTES + 1))
        declarations = ["large.md"] * MAX_ARTIFACTS_PER_NODE + ["overflow.md"]

        refs = publish_artifacts(self.project, self.node, declarations, self.store)

        durable = stored_artifacts_dir(self.store, self.project.id, self.node.id)
        self.assertFalse((durable / "old.md").exists())
        self.assertTrue(all(ref.status == "dropped" for ref in refs))
        self.assertIn("artifact limit", refs[-1].reason or "")


class ArtifactApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.cwd = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self.home.name
        self.registry = ProjectRegistry(initialize=False)
        self.client = TestClient(create_app(self.registry))
        response = self.client.post(
            "/sessions",
            json={"cwd": self.cwd.name, "model_preset_id": "opus-4-8"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.sid = response.json()["id"]
        self.node = Node(
            id="artifact-node",
            project_id=self.sid,
            category=Category.REGULAR,
            state=NodeState.DONE,
            model_preset_id="opus-4-8",
        )
        self.registry.store.create_node(self.node)
        project = self.registry.get_project(self.sid)
        assert project is not None
        outputs = workspace_artifacts_dir(project, self.node.id)
        outputs.mkdir(parents=True)
        (outputs / "demo.html").write_text(
            "<script>document.body.textContent='ok'</script>",
            encoding="utf-8",
        )
        (outputs / "large.md").write_text(
            "x" * (INLINE_TEXT_CAP + 10),
            encoding="utf-8",
        )
        (outputs / "undeclared.md").write_text("hidden", encoding="utf-8")
        publish_artifacts(
            project,
            self.node,
            ["demo.html", "large.md"],
            self.registry.store,
        )
        self.registry.store.update_node(self.node)

    def tearDown(self) -> None:
        self.client.close()
        self.cwd.cleanup()
        self.home.cleanup()

    def test_json_and_sandboxed_raw_modes(self) -> None:
        url = f"/sessions/{self.sid}/nodes/{self.node.id}/artifacts/demo.html"

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["name"], "demo.html")
        self.assertFalse(response.json()["truncated"])

        raw = self.client.get(f"{url}?raw=1")
        self.assertEqual(raw.status_code, 200, raw.text)
        self.assertEqual(raw.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(
            raw.headers["content-security-policy"],
            "sandbox allow-scripts; connect-src 'none'",
        )
        self.assertEqual(raw.headers["x-content-type-options"], "nosniff")

    def test_undeclared_and_dropped_names_are_not_served(self) -> None:
        for name in ["missing.md", "undeclared.md", "demo.html%2Fother"]:
            response = self.client.get(
                f"/sessions/{self.sid}/nodes/{self.node.id}/artifacts/{name}"
            )
            self.assertEqual(response.status_code, 404)

    def test_inline_mode_truncates_but_raw_mode_does_not(self) -> None:
        url = f"/sessions/{self.sid}/nodes/{self.node.id}/artifacts/large.md"

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["truncated"])
        self.assertEqual(len(response.json()["text"]), INLINE_TEXT_CAP)

        raw = self.client.get(f"{url}?raw=1")
        self.assertEqual(len(raw.content), INLINE_TEXT_CAP + 10)
        self.assertEqual(raw.headers["content-type"], "text/plain; charset=utf-8")


if __name__ == "__main__":
    unittest.main()
