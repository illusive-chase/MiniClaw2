from __future__ import annotations

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from miniclaw2.app import create_app


class VirtualCreateApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()
        os.environ.pop("MINICLAW_HOME", None)
        self._home.cleanup()

    def test_create_virtual_in_template_active_lane(self) -> None:
        launched = self.client.post(
            "/templates/hello-text/run",
            json={"provider": "claude"},
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
            json={"provider": "claude"},
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


if __name__ == "__main__":
    unittest.main()
