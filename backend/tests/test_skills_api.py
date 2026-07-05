"""HTTP tests for /skills endpoints (PR 3)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from miniclaw2.app import create_app


def _write_skill(ctx_root: Path, slug: str, *, title: str, body: str = "body") -> None:
    plug_dir = ctx_root / "plugs" / "skills" / slug
    plug_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 1,
        "kind": "skill",
        "id": f"skills.{slug}",
        "title": title,
    }
    (plug_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    (plug_dir / "CONTEXT.md").write_text(body, encoding="utf-8")


class SkillsApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        self.ctx_root = Path(self._home.name) / "contextspace"
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()
        os.environ.pop("MINICLAW_HOME", None)
        self._home.cleanup()

    def test_list_empty(self) -> None:
        res = self.client.get("/skills")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), [])

    def test_list_returns_hand_created_skills(self) -> None:
        _write_skill(self.ctx_root, "vim", title="Vim motions")
        _write_skill(self.ctx_root, "git", title="Git etiquette")
        res = self.client.get("/skills")
        self.assertEqual(res.status_code, 200)
        items = {item["id"]: item for item in res.json()}
        self.assertEqual(set(items), {"skills.vim", "skills.git"})
        self.assertEqual(items["skills.vim"]["title"], "Vim motions")
        self.assertEqual(items["skills.vim"]["kind"], "skill")

    def test_delete_removes_plug_dir(self) -> None:
        _write_skill(self.ctx_root, "vim", title="Vim")
        res = self.client.delete("/skills/vim")
        self.assertEqual(res.status_code, 204)
        self.assertFalse((self.ctx_root / "plugs" / "skills" / "vim").exists())
        self.assertEqual(self.client.get("/skills").json(), [])

    def test_delete_missing_returns_404(self) -> None:
        res = self.client.delete("/skills/nope")
        self.assertEqual(res.status_code, 404)

    def test_delete_rejects_traversal(self) -> None:
        # A slug containing "/" cannot escape the skills root — the helper
        # rejects it before touching the filesystem.
        _write_skill(self.ctx_root, "vim", title="Vim")
        res = self.client.delete("/skills/..%2Fvim")
        # FastAPI normalizes %2F path segments; the safest assertion is that
        # the vim plug is still there afterwards.
        self.assertTrue((self.ctx_root / "plugs" / "skills" / "vim").exists())
        # And that path traversal-y slugs never resolve to a real skill.
        self.assertIn(res.status_code, (400, 404))


if __name__ == "__main__":
    unittest.main()
