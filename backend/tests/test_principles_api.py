"""HTTP tests for /principles endpoints (PR 3)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from miniclaw2.app import create_app


def _write_principle(ctx_root: Path, slug: str, *, title: str, body: str = "body") -> None:
    plug_dir = ctx_root / "plugs" / "principles" / slug
    plug_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 1,
        "kind": "principle",
        "id": f"principles.{slug}",
        "title": title,
    }
    (plug_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    (plug_dir / "CONTEXT.md").write_text(body, encoding="utf-8")


class PrinciplesApiTest(unittest.TestCase):
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
        res = self.client.get("/principles")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), [])

    def test_list_returns_hand_created_principles(self) -> None:
        _write_principle(self.ctx_root, "vim", title="Vim motions")
        _write_principle(self.ctx_root, "git", title="Git etiquette")
        res = self.client.get("/principles")
        self.assertEqual(res.status_code, 200)
        items = {item["id"]: item for item in res.json()}
        self.assertEqual(set(items), {"principles.vim", "principles.git"})
        self.assertEqual(items["principles.vim"]["title"], "Vim motions")
        self.assertEqual(items["principles.vim"]["kind"], "principle")

    def test_delete_removes_plug_dir(self) -> None:
        _write_principle(self.ctx_root, "vim", title="Vim")
        res = self.client.delete("/principles/vim")
        self.assertEqual(res.status_code, 204)
        self.assertFalse((self.ctx_root / "plugs" / "principles" / "vim").exists())
        self.assertEqual(self.client.get("/principles").json(), [])

    def test_delete_missing_returns_404(self) -> None:
        res = self.client.delete("/principles/nope")
        self.assertEqual(res.status_code, 404)

    def test_delete_rejects_traversal(self) -> None:
        # A slug containing "/" cannot escape the principles root — the helper
        # rejects it before touching the filesystem.
        _write_principle(self.ctx_root, "vim", title="Vim")
        res = self.client.delete("/principles/..%2Fvim")
        # FastAPI normalizes %2F path segments; the safest assertion is that
        # the vim plug is still there afterwards.
        self.assertTrue((self.ctx_root / "plugs" / "principles" / "vim").exists())
        # And that path traversal-y slugs never resolve to a real principle.
        self.assertIn(res.status_code, (400, 404))

    def test_detail_carries_context_md_body(self) -> None:
        # The list response deliberately omits the body; the preview modal is
        # the only caller that needs the full text.
        _write_principle(
            self.ctx_root, "vim", title="Vim motions", body="# Vim\n\nUse hjkl.\n"
        )
        listed = self.client.get("/principles").json()
        self.assertNotIn("body", listed[0])

        res = self.client.get("/principles/vim")
        self.assertEqual(res.status_code, 200)
        detail = res.json()
        self.assertEqual(detail["id"], "principles.vim")
        self.assertEqual(detail["title"], "Vim motions")
        self.assertEqual(detail["body"], "# Vim\n\nUse hjkl.\n")
        self.assertTrue(detail["body_path"].endswith("CONTEXT.md"))

    def test_detail_body_is_null_without_context_md(self) -> None:
        # A plug with a manifest but no CONTEXT.md is a real authoring state,
        # not an error — the body comes back null and the path still resolves.
        plug_dir = self.ctx_root / "plugs" / "principles" / "bare"
        plug_dir.mkdir(parents=True)
        (plug_dir / "manifest.yaml").write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "kind": "principle",
                    "id": "principles.bare",
                    "title": "Bare",
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        res = self.client.get("/principles/bare")
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.json()["body"])

    def test_detail_accepts_prefixed_id(self) -> None:
        _write_principle(self.ctx_root, "vim", title="Vim")
        res = self.client.get("/principles/principles.vim")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["id"], "principles.vim")

    def test_detail_missing_returns_404(self) -> None:
        res = self.client.get("/principles/nope")
        self.assertEqual(res.status_code, 404)

    def test_detail_without_manifest_returns_404(self) -> None:
        # `list_principles` skips manifest-less directories; the detail endpoint
        # must agree rather than inventing a summary for one.
        (self.ctx_root / "plugs" / "principles" / "orphan").mkdir(parents=True)
        res = self.client.get("/principles/orphan")
        self.assertEqual(res.status_code, 404)

    def test_detail_rejects_traversal(self) -> None:
        _write_principle(self.ctx_root, "vim", title="Vim")
        res = self.client.get("/principles/..%2Fvim")
        self.assertIn(res.status_code, (400, 404))
        # A non-principle plug id is rejected outright rather than read.
        self.assertEqual(self.client.get("/principles/global.default").status_code, 400)
        # A prefixed id must not let its extracted slug resolve to the parent
        # plugs directory (``_plug_slug('principles...') == '..'``).
        self.assertEqual(self.client.get("/principles/principles...").status_code, 400)


if __name__ == "__main__":
    unittest.main()
