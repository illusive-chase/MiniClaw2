from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


class GlobalStateApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.workspace = tempfile.TemporaryDirectory()
        self.root = Path(self.home.name)
        self.registry = ProjectRegistry(Store(self.root))
        self.client = TestClient(create_app(self.registry))

    def tearDown(self) -> None:
        self.workspace.cleanup()
        self.home.cleanup()

    def test_store_bootstraps_editable_global_config(self) -> None:
        response = self.client.get("/global-state")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["config_path"], str(self.root / "config.json"))
        self.assertEqual(body["defaults"]["default_model_preset_id"], "gpt-5.6")
        self.assertTrue((self.root / "config.json").is_file())
        persisted = json.loads((self.root / "config.json").read_text())
        self.assertEqual(
            [item["id"] for item in persisted["model_presets"]],
            [item["id"] for item in body["model_presets"]],
        )

    def test_presets_are_created_replaced_and_deleted_from_config(self) -> None:
        preset = {
            "id": "local-fast",
            "label": "Local fast",
            "provider": "codex",
            "model": "local-model",
            "reasoning_effort": "low",
            "status": "active",
        }
        created = self.client.post("/global-state/model-presets", json=preset)
        self.assertEqual(created.status_code, 201)
        self.assertIn(
            "local-fast",
            {item["id"] for item in created.json()["model_presets"]},
        )

        preset["label"] = "Local fast v2"
        replaced = self.client.put(
            "/global-state/model-presets/local-fast",
            json=preset,
        )
        self.assertEqual(replaced.status_code, 200)
        current = next(
            item
            for item in replaced.json()["model_presets"]
            if item["id"] == "local-fast"
        )
        self.assertEqual(current["label"], "Local fast v2")

        deleted = self.client.delete("/global-state/model-presets/local-fast")
        self.assertEqual(deleted.status_code, 204)
        ids = {
            item["id"]
            for item in self.client.get("/model-presets").json()
        }
        self.assertNotIn("local-fast", ids)

    def test_default_preset_cannot_be_deleted(self) -> None:
        response = self.client.delete("/global-state/model-presets/gpt-5.6")

        self.assertEqual(response.status_code, 409)
        self.assertIn("default", response.json()["detail"])

    def test_template_referenced_preset_cannot_be_deleted(self) -> None:
        preset = next(
            item
            for item in self.client.get("/global-state").json()["model_presets"]
            if item["id"] == "opus-4-7"
        )
        preset["label"] = "Configured Opus"

        replaced = self.client.put(
            "/global-state/model-presets/opus-4-7",
            json=preset,
        )
        self.assertEqual(replaced.status_code, 200)
        self.assertEqual(
            next(
                item["label"]
                for item in replaced.json()["model_presets"]
                if item["id"] == "opus-4-7"
            ),
            "Configured Opus",
        )

        deleted = self.client.delete("/global-state/model-presets/opus-4-7")
        self.assertEqual(deleted.status_code, 409)
        self.assertIn("template", deleted.json()["detail"])

        templates = self.client.get("/templates")
        self.assertEqual(templates.status_code, 200)

    def test_store_specific_preset_can_create_session(self) -> None:
        preset = {
            "id": "store-custom",
            "label": "Store custom",
            "provider": "codex",
            "model": "store-model",
            "status": "active",
        }
        created_preset = self.client.post(
            "/global-state/model-presets",
            json=preset,
        )
        self.assertEqual(created_preset.status_code, 201)

        created_session = self.client.post(
            "/sessions",
            json={
                "cwd": self.workspace.name,
                "model_preset_id": "store-custom",
            },
        )
        self.assertEqual(created_session.status_code, 200)
        self.assertEqual(created_session.json()["model_preset_id"], "store-custom")
        self.assertEqual(created_session.json()["provider"], "codex")

        node = self.registry.start_node(
            created_session.json()["id"],
            "Use the custom preset",
            model_preset_id="store-custom",
        )
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(node.model_preset_id, "store-custom")
        self.assertEqual(node.provider, "codex")

    def test_global_defaults_apply_when_create_fields_are_omitted(self) -> None:
        updated = self.client.patch(
            "/global-state/defaults",
            json={
                "default_model_preset_id": "opus-4-8",
                "auto_commit": True,
                "preferred_language": "zh-CN",
                "concurrency": 3,
            },
        )
        self.assertEqual(updated.status_code, 200)

        created = self.client.post(
            "/sessions",
            json={"cwd": self.workspace.name},
        )
        self.assertEqual(created.status_code, 200)
        body = created.json()
        self.assertEqual(body["model_preset_id"], "opus-4-8")
        self.assertEqual(body["preferred_language"], "Simplified Chinese")
        self.assertEqual(body["concurrency"], 3)
        project = self.registry.get_project(body["id"])
        self.assertIsNotNone(project)
        assert project is not None
        self.assertTrue(project.settings_override["auto_commit"])

    def test_duplicate_preset_id_is_rejected(self) -> None:
        existing = self.client.get("/global-state").json()["model_presets"][0]
        response = self.client.post("/global-state/model-presets", json=existing)
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
