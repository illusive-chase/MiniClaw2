from __future__ import annotations

import os
import tempfile
import unittest

from fastapi.testclient import TestClient


class VirtualCreateApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        from miniclaw2.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()
        os.environ.pop("MINICLAW_HOME", None)
        self._home.cleanup()

    def test_create_virtual_in_template_active_lane(self) -> None:
        launched = self.client.post(
            "/templates/hello-text/run",
            json={"model_preset_id": "gpt-5.6"},
        )
        self.assertEqual(launched.status_code, 200, launched.text)
        session = launched.json()
        sid = session["id"]

        nodes_res = self.client.get(f"/sessions/{sid}/nodes")
        self.assertEqual(nodes_res.status_code, 200, nodes_res.text)
        first = nodes_res.json()[0]

        created = self.client.post(
            f"/sessions/{sid}/virtuals",
            json={
                "prompt_draft": "Add an extra user-authored check.",
                "motivation": "Manual template extension",
                "category": "regular",
                "model_preset_id": "opus-4-8",
                "scheduled_deps": [first["id"]],
            },
        )

        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertTrue(body["ok"])
        node = body["node"]
        self.assertEqual(node["state"], "virtual")
        self.assertEqual(node["kind"], "agent")
        self.assertEqual(node["category"], "regular")
        self.assertEqual(node["model_preset_id"], "opus-4-8")
        self.assertEqual(node["provider"], "claude")
        self.assertEqual(node["prompt_draft"], "Add an extra user-authored check.")
        self.assertEqual(node["summary"], "Manual template extension")
        self.assertEqual(node["scheduled_deps"], [first["id"]])
        self.assertEqual(
            node["planspace_id"],
            first["planspace_id"],
        )

        listed = self.client.get(f"/sessions/{sid}/nodes")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertIn(node["id"], [item["id"] for item in listed.json()])

        preview = self.client.get(f"/sessions/{sid}/nodes/{node['id']}/preview")
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertIn("Add an extra user-authored check.", preview.json()["text"])

    def test_create_virtual_rejects_missing_dependency(self) -> None:
        launched = self.client.post(
            "/templates/hello-text/run",
            json={"model_preset_id": "gpt-5.6"},
        )
        self.assertEqual(launched.status_code, 200, launched.text)
        sid = launched.json()["id"]

        created = self.client.post(
            f"/sessions/{sid}/virtuals",
            json={
                "prompt_draft": "Invalid self dependency.",
                "scheduled_deps": ["missing"],
            },
        )

        self.assertEqual(created.status_code, 400, created.text)
        self.assertIn("does not resolve", created.json()["detail"])

    def test_provider_only_payloads_are_rejected(self) -> None:
        session_res = self.client.post("/sessions", json={"provider": "claude"})
        self.assertEqual(session_res.status_code, 422, session_res.text)
        self.assertIn("provider", session_res.text)

        launched = self.client.post(
            "/templates/hello-text/run",
            json={"model_preset_id": "gpt-5.6"},
        )
        self.assertEqual(launched.status_code, 200, launched.text)
        sid = launched.json()["id"]

        created = self.client.post(
            f"/sessions/{sid}/virtuals",
            json={
                "prompt_draft": "Old client payload.",
                "provider": "claude",
            },
        )

        self.assertEqual(created.status_code, 422, created.text)
        self.assertIn("provider", created.text)

    def _lane_session(self) -> str:
        launched = self.client.post(
            "/templates/hello-text/run",
            json={"model_preset_id": "gpt-5.6"},
        )
        self.assertEqual(launched.status_code, 200, launched.text)
        return launched.json()["id"]

    def test_create_virtual_defaults_artifact_and_qa_off(self) -> None:
        sid = self._lane_session()

        created = self.client.post(
            f"/sessions/{sid}/virtuals",
            json={"prompt_draft": "plain node"},
        )

        self.assertEqual(created.status_code, 200, created.text)
        node = created.json()["node"]
        self.assertEqual(node["artifact_mode"], "default")
        self.assertEqual(node["artifact_spec"], "")
        self.assertFalse(node["qa_mode"])

    def test_create_virtual_accepts_artifact_and_qa_mode(self) -> None:
        sid = self._lane_session()

        created = self.client.post(
            f"/sessions/{sid}/virtuals",
            json={
                "prompt_draft": "write the report",
                "artifact_mode": "markdown",
                "qa_mode": True,
            },
        )

        self.assertEqual(created.status_code, 200, created.text)
        node = created.json()["node"]
        self.assertEqual(node["artifact_mode"], "markdown")
        self.assertTrue(node["qa_mode"])

        readback = self.client.get(f"/sessions/{sid}/nodes")
        self.assertEqual(readback.status_code, 200, readback.text)
        stored = next(
            item for item in readback.json() if item["id"] == node["id"]
        )
        self.assertEqual(stored["artifact_mode"], "markdown")
        self.assertTrue(stored["qa_mode"])

    def test_create_virtual_custom_requires_spec(self) -> None:
        sid = self._lane_session()

        created = self.client.post(
            f"/sessions/{sid}/virtuals",
            json={"prompt_draft": "x", "artifact_mode": "custom"},
        )

        self.assertEqual(created.status_code, 400, created.text)
        self.assertIn("artifact_spec", created.json()["detail"])

    def test_create_virtual_drops_spec_when_mode_is_not_custom(self) -> None:
        sid = self._lane_session()

        created = self.client.post(
            f"/sessions/{sid}/virtuals",
            json={
                "prompt_draft": "x",
                "artifact_mode": "markdown",
                "artifact_spec": "ignored",
            },
        )

        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["node"]["artifact_spec"], "")

    def test_create_virtual_rejects_unknown_artifact_mode(self) -> None:
        sid = self._lane_session()

        created = self.client.post(
            f"/sessions/{sid}/virtuals",
            json={"prompt_draft": "x", "artifact_mode": "pdf"},
        )

        self.assertEqual(created.status_code, 400, created.text)
        self.assertIn("artifact_mode", created.json()["detail"])

    def test_create_review_virtual_rejects_artifact_mode(self) -> None:
        sid = self._lane_session()

        created = self.client.post(
            f"/sessions/{sid}/virtuals",
            json={
                "prompt_draft": "review it",
                "category": "review",
                "subtype": "agentic_review",
                "brief": {
                    "check_what": "c",
                    "expected": "e",
                    "abnormal": "a",
                },
                "artifact_mode": "markdown",
            },
        )

        self.assertEqual(created.status_code, 400, created.text)
        self.assertIn("artifact_mode", created.json()["detail"])


if __name__ == "__main__":
    unittest.main()
