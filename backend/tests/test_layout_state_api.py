from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from miniclaw2.app import create_app


class LayoutStateApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self._cwd = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()
        self._cwd.cleanup()
        self._home.cleanup()

    def _create_session(self) -> str:
        res = self.client.post(
            "/sessions",
            json={"cwd": self._cwd.name, "model_preset_id": "opus-4-8"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["id"]

    def test_layout_positions_and_viewport_round_trip(self) -> None:
        sid = self._create_session()

        patch = self.client.patch(
            f"/sessions/{sid}/layout-hints",
            json={
                "updates": {
                    "root": {"x": 128.5, "y": -16},
                    "node-1": {"x": 320, "y": 240},
                },
                "layout_viewport": {"x": -42, "y": 18.25, "zoom": 1.2},
            },
        )

        self.assertEqual(patch.status_code, 200, patch.text)
        body = patch.json()
        self.assertEqual(body["layout_hints"]["root"], {"x": 128.5, "y": -16.0})
        self.assertEqual(body["layout_hints"]["node-1"], {"x": 320.0, "y": 240.0})
        self.assertEqual(
            body["layout_viewport"],
            {"x": -42.0, "y": 18.25, "zoom": 1.2},
        )

        restarted = TestClient(create_app())
        try:
            fetched = restarted.get(f"/sessions/{sid}")
            self.assertEqual(fetched.status_code, 200, fetched.text)
            self.assertEqual(fetched.json()["layout_hints"], body["layout_hints"])
            self.assertEqual(fetched.json()["layout_viewport"], body["layout_viewport"])

            listed = restarted.get("/sessions")
            self.assertEqual(listed.status_code, 200, listed.text)
            match = next(item for item in listed.json() if item["id"] == sid)
            self.assertEqual(match["layout_hints"], body["layout_hints"])
            self.assertEqual(match["layout_viewport"], body["layout_viewport"])

            project_json = (
                Path(self._home.name) / "projects" / sid / "project.json"
            )
            self.assertTrue(project_json.exists())
        finally:
            restarted.close()
