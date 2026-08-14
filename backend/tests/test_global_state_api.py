from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.global_config import GlobalConfig, load_global_config, save_global_config
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store
from miniclaw2.templates import user_templates_root


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
        self.assertEqual(body["code_review"]["model_preset_id"], "gpt-5.6")
        self.assertEqual(
            body["tool_requests"],
            {"timeout_seconds": 120, "timeout_action": "accept"},
        )
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

    def test_legacy_user_template_matrix_does_not_block_preset_deletion(self) -> None:
        preset = {
            "id": "legacy-matrix-only",
            "label": "Legacy matrix only",
            "provider": "codex",
            "model": "legacy-model",
            "status": "active",
        }
        self.assertEqual(
            self.client.post("/global-state/model-presets", json=preset).status_code,
            201,
        )
        template_root = user_templates_root(self.root) / "legacy-matrix"
        (template_root / "prompts").mkdir(parents=True)
        (template_root / "template.yaml").write_text(
            "\n".join(
                [
                    "schema_version: 2",
                    "name: Legacy matrix",
                    "brief: Compatibility fixture.",
                    "allowed_model_preset_ids:",
                    "  - legacy-matrix-only",
                    "lane_mode: manual",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (template_root / "lane.yaml").write_text(
            "\n".join(
                [
                    "nodes:",
                    "  - id: n0",
                    "    kind: agent",
                    "    category: regular",
                    "    prompt_file: prompts/n0.md",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (template_root / "prompts" / "n0.md").write_text(
            "Run without an authored model.\n",
            encoding="utf-8",
        )

        deleted = self.client.delete(
            "/global-state/model-presets/legacy-matrix-only"
        )

        self.assertEqual(deleted.status_code, 204)

    def test_upgraded_store_missing_template_preset_remains_usable(self) -> None:
        config = load_global_config(self.root)
        local_preset = next(
            preset.model_copy(update={"id": "local-fast", "label": "Local fast"})
            for preset in config.model_presets
            if preset.id == "gpt-5.6-x"
        )
        save_global_config(
            config.model_copy(
                update={
                    "model_presets": [
                        preset
                        for preset in config.model_presets
                        if preset.id != "opus-4-7"
                    ]
                    + [local_preset]
                }
            ),
            self.root,
        )

        templates = self.client.get("/templates")
        self.assertEqual(templates.status_code, 200)
        self.assertTrue(templates.json())

        deleted = self.client.delete("/global-state/model-presets/local-fast")
        self.assertEqual(deleted.status_code, 204)

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

    def test_tool_request_settings_are_global_and_patchable(self) -> None:
        updated = self.client.patch(
            "/global-state/tool-requests",
            json={"timeout_seconds": 45, "timeout_action": "reject"},
        )

        self.assertEqual(updated.status_code, 200)
        self.assertEqual(
            updated.json()["tool_requests"],
            {"timeout_seconds": 45, "timeout_action": "reject"},
        )
        persisted = json.loads((self.root / "config.json").read_text())
        self.assertEqual(persisted["tool_requests"], updated.json()["tool_requests"])

    def test_code_review_settings_are_global_and_patchable(self) -> None:
        updated = self.client.patch(
            "/global-state/code-review",
            json={"model_preset_id": "opus-4-8"},
        )

        self.assertEqual(updated.status_code, 200)
        self.assertEqual(
            updated.json()["code_review"],
            {"model_preset_id": "opus-4-8"},
        )
        persisted = json.loads((self.root / "config.json").read_text())
        self.assertEqual(persisted["code_review"], updated.json()["code_review"])

        deleted = self.client.delete("/global-state/model-presets/opus-4-8")
        self.assertEqual(deleted.status_code, 409)
        self.assertIn("code review", deleted.json()["detail"])

    def test_legacy_config_without_tool_request_settings_gets_defaults(self) -> None:
        payload = json.loads((self.root / "config.json").read_text())
        payload.pop("tool_requests", None)
        (self.root / "config.json").write_text(json.dumps(payload), encoding="utf-8")

        response = self.client.get("/global-state")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["tool_requests"],
            {"timeout_seconds": 120, "timeout_action": "accept"},
        )

    def test_legacy_config_without_code_review_settings_gets_default(self) -> None:
        payload = json.loads((self.root / "config.json").read_text())
        payload.pop("code_review", None)
        (self.root / "config.json").write_text(json.dumps(payload), encoding="utf-8")

        response = self.client.get("/global-state")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["code_review"],
            {"model_preset_id": "gpt-5.6"},
        )

    def test_model_constructed_legacy_config_gets_code_review_default(self) -> None:
        current = load_global_config(self.root)

        migrated = GlobalConfig(
            defaults=current.defaults,
            model_presets=current.model_presets,
        )

        self.assertEqual(migrated.code_review.model_preset_id, "gpt-5.6")

    def test_legacy_code_review_default_falls_back_to_project_default(self) -> None:
        payload = json.loads((self.root / "config.json").read_text())
        payload.pop("code_review", None)
        payload["defaults"]["default_model_preset_id"] = "opus-4-8"
        payload["model_presets"] = [
            preset
            for preset in payload["model_presets"]
            if preset["id"] != "gpt-5.6"
        ]
        (self.root / "config.json").write_text(json.dumps(payload), encoding="utf-8")

        response = self.client.get("/global-state")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["code_review"],
            {"model_preset_id": "opus-4-8"},
        )

    def test_duplicate_preset_id_is_rejected(self) -> None:
        existing = self.client.get("/global-state").json()["model_presets"][0]
        response = self.client.post("/global-state/model-presets", json=existing)
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
