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

    ICON = {"glyph": "database", "hue": "teal"}

    def _create(self, name: str = "Project") -> dict:
        body: dict = {
            "cwd": self.cwd_dir.name,
            "model_preset_id": "opus-4-8",
            "name": name,
            "icon": self.ICON,
        }
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

    def test_create_returns_chosen_icon(self) -> None:
        created = self._create(name="Icon Project")
        self.assertEqual(created["icon"], self.ICON)
        listed = self.client.get("/sessions").json()
        self.assertEqual(listed[0]["icon"], self.ICON)

    def test_create_requires_name_and_icon(self) -> None:
        base = {"cwd": self.cwd_dir.name, "model_preset_id": "opus-4-8"}
        for body in (
            base,
            {**base, "icon": self.ICON},
            {**base, "icon": self.ICON, "name": "   "},
            {**base, "name": "Project"},
            {**base, "name": "Project", "icon": {"glyph": "not-a-glyph", "hue": "teal"}},
            {**base, "name": "Project", "icon": {"glyph": "code", "hue": "magenta"}},
        ):
            res = self.client.post("/sessions", json=body)
            self.assertIn(res.status_code, (400, 422), (body, res.text))
        self.assertEqual(self.client.get("/sessions").json(), [])

    def test_create_trims_name(self) -> None:
        self.assertEqual(self._create(name="  Spaced  ")["name"], "Spaced")

    def test_create_missing_cwd_returns_400_without_confirmation(self) -> None:
        missing = Path(self.cwd_dir.name) / "missing-project"

        res = self.client.post(
            "/sessions",
            json={
                "cwd": str(missing),
                "model_preset_id": "opus-4-8",
                "name": "Project",
                "icon": self.ICON,
            },
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
                "model_preset_id": "opus-4-8",
                "name": "Project",
                "icon": self.ICON,
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

    def test_patch_rejects_empty_name(self) -> None:
        sid = self._create(name="Keep")["id"]
        res = self.client.patch(f"/sessions/{sid}", json={"name": "  "})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.client.get(f"/sessions/{sid}").json()["name"], "Keep")

    def test_patch_icon(self) -> None:
        sid = self._create()["id"]
        res = self.client.patch(f"/sessions/{sid}/icon", json={"glyph": "rocket", "hue": "plum"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["icon"], {"glyph": "rocket", "hue": "plum"})
        bad = self.client.patch(f"/sessions/{sid}/icon", json={"glyph": "rocket", "hue": "neon"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.get(f"/sessions/{sid}").json()["icon"]["hue"], "plum")

    def test_patch_unknown_id_returns_404(self) -> None:
        res = self.client.patch("/sessions/does-not-exist", json={"name": "x"})
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
