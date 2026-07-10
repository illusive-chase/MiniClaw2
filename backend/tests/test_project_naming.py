from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from miniclaw2.app import create_app


def _override_home(tmp: str) -> None:
    import os

    os.environ["MINICLAW_HOME"] = tmp


class ProjectNamingApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        _override_home(self._tmp.name)
        self.cwd_dir = tempfile.TemporaryDirectory()
        self.app = create_app()
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.cwd_dir.cleanup()
        self._tmp.cleanup()

    def _create(self, name: str | None = None) -> dict:
        body: dict = {"cwd": self.cwd_dir.name, "model_preset_id": "opus-4-7"}
        if name is not None:
            body["name"] = name
        res = self.client.post("/sessions", json=body)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_create_with_name_listed_back(self) -> None:
        created = self._create(name="My Project")
        self.assertEqual(created["name"], "My Project")

        listed = self.client.get("/sessions").json()
        match = next((s for s in listed if s["id"] == created["id"]), None)
        self.assertIsNotNone(match)
        self.assertEqual(match["name"], "My Project")

    def test_create_without_name_returns_empty_string(self) -> None:
        created = self._create()
        self.assertEqual(created["name"], "")

    def test_create_missing_cwd_returns_400_without_confirmation(self) -> None:
        missing = Path(self.cwd_dir.name) / "missing-project"

        res = self.client.post(
            "/sessions",
            json={"cwd": str(missing), "model_preset_id": "opus-4-7"},
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("cwd does not exist", res.json()["detail"])
        self.assertFalse(missing.exists())

    def test_create_missing_cwd_with_confirmation_creates_directory(self) -> None:
        missing = Path(self.cwd_dir.name) / "nested" / "project"

        res = self.client.post(
            "/sessions",
            json={
                "cwd": str(missing),
                "model_preset_id": "opus-4-7",
                "create_missing_cwd": True,
            },
        )

        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(missing.is_dir())

    def test_patch_renames_project(self) -> None:
        created = self._create(name="Original")
        sid = created["id"]

        res = self.client.patch(f"/sessions/{sid}", json={"name": "Renamed"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["name"], "Renamed")

        listed = self.client.get("/sessions").json()
        match = next((s for s in listed if s["id"] == sid), None)
        self.assertIsNotNone(match)
        self.assertEqual(match["name"], "Renamed")

    def test_patch_unknown_id_returns_404(self) -> None:
        res = self.client.patch("/sessions/does-not-exist", json={"name": "x"})
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
