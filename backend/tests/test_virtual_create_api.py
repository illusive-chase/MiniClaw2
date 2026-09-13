from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.contextspace import create_planspace
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store
from miniclaw2.workspace import remove_temporary_root


class VirtualCreateApiTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = Store(Path(temporary.name))
        self.registry = ProjectRegistry(store)
        self.project = self.registry.create_project(cwd=None, temporary=True)
        self.addCleanup(remove_temporary_root, self.project.root_path)
        self.lane = create_planspace(
            self.project, title="测试方向", mode="manual", store_root=store.root,
        )
        store.update_project(self.project)
        self.client = TestClient(create_app(registry=self.registry))
        self.addCleanup(self.client.close)
        self.url = f"/sessions/{self.project.id}"

    def _create(self, **fields):
        return self.client.post(f"{self.url}/virtuals", json={
            "prompt_draft": "新增检查", "planspace_id": self.lane, **fields,
        })

    def test_creation_persists_node_and_preview_without_bundled_template(self) -> None:
        response = self._create()
        self.assertEqual(response.status_code, 200, response.text)
        node = response.json()["node"]
        self.assertEqual(node["state"], "virtual")
        self.assertEqual(node["planspace_id"], self.lane)
        self.assertEqual(node["artifact_mode"], "default")
        self.assertEqual(node["artifact_spec"], "")
        self.assertFalse(node["qa_mode"])
        listed = self.client.get(f"{self.url}/nodes").json()
        self.assertIn(node["id"], [item["id"] for item in listed])
        preview = self.client.get(f"{self.url}/nodes/{node['id']}/preview")
        self.assertEqual(preview.status_code, 200)
        self.assertIn("新增检查", preview.json()["text"])

    def test_missing_dependency_is_rejected_without_partial_node(self) -> None:
        response = self._create(scheduled_deps=["missing"])
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not resolve", response.json()["detail"])
        self.assertEqual(self.registry.list_nodes(self.project.id), [])

    def test_provider_only_payloads_remain_rejected(self) -> None:
        self.assertEqual(self.client.post("/sessions", json={"provider": "claude"}).status_code, 422)
        response = self._create(provider="claude")
        self.assertEqual(response.status_code, 422)
        self.assertIn("provider", response.text)

    def test_artifact_and_qa_settings_survive_readback(self) -> None:
        response = self._create(artifact_mode="markdown", qa_mode=True)
        self.assertEqual(response.status_code, 200, response.text)
        node_id = response.json()["node"]["id"]
        node = self.client.get(f"{self.url}/nodes/{node_id}").json()
        self.assertEqual(node["artifact_mode"], "markdown")
        self.assertTrue(node["qa_mode"])

    def test_custom_artifact_requires_spec(self) -> None:
        response = self._create(artifact_mode="custom")
        self.assertEqual(response.status_code, 400)
        self.assertIn("artifact_spec", response.json()["detail"])

    def test_noncustom_artifact_discards_unused_spec(self) -> None:
        response = self._create(artifact_mode="markdown", artifact_spec="应忽略")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["node"]["artifact_spec"], "")

    def test_unsupported_artifact_mode_is_rejected(self) -> None:
        response = self._create(artifact_mode="pdf")
        self.assertEqual(response.status_code, 400)
        self.assertIn("artifact_mode", response.json()["detail"])

    def test_review_virtual_rejects_artifact_settings(self) -> None:
        response = self._create(
            category="review", subtype="agentic_review", artifact_mode="markdown",
            brief={"check_what": "检查目标", "expected": "预期", "abnormal": "异常"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("artifact_mode", response.json()["detail"])

    def test_temporary_git_review_is_rejected_before_node_creation(self) -> None:
        response = self._create(category="review", subtype="code_review")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("临时项目不支持 Git", response.json()["detail"])
        self.assertEqual(self.registry.list_nodes(self.project.id), [])
