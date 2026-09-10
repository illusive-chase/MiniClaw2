from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from miniclaw2 import file_manager
from miniclaw2.app import MARKDOWN_READ_CAP, create_app
from miniclaw2.path_resolve import LinkBase, resolve_markdown_href


class ResolveMarkdownHrefTest(unittest.TestCase):
    """The seven-step verdict, exercised against a real tree."""

    def setUp(self) -> None:
        self._root = tempfile.TemporaryDirectory()
        self._outside = tempfile.TemporaryDirectory()
        self.root = Path(self._root.name).resolve()
        self.outside = Path(self._outside.name).resolve()
        (self.root / "docs").mkdir()
        (self.root / "docs" / "design.md").write_text("# design", encoding="utf-8")
        (self.root / "notes.markdown").write_text("notes", encoding="utf-8")
        (self.root / "app.py").write_text("print()", encoding="utf-8")
        (self.outside / "external.md").write_text("# external", encoding="utf-8")

    def tearDown(self) -> None:
        self._root.cleanup()
        self._outside.cleanup()

    def resolve(self, href: str, base: LinkBase | None = None):
        return resolve_markdown_href(self.root, base, href)

    def test_markdown_inside_root_is_readable(self) -> None:
        verdict = self.resolve("docs/design.md")
        self.assertEqual(verdict.verdict, "markdown")
        self.assertEqual(verdict.relative_path, "docs/design.md")
        self.assertEqual(verdict.path, str(self.root / "docs" / "design.md"))

    def test_markdown_suffix_variant_is_readable(self) -> None:
        self.assertEqual(self.resolve("notes.markdown").verdict, "markdown")

    def test_non_markdown_file_is_revealed(self) -> None:
        verdict = self.resolve("app.py")
        self.assertEqual(verdict.verdict, "reveal")
        self.assertIsNone(verdict.relative_path)

    def test_directory_is_revealed(self) -> None:
        self.assertEqual(self.resolve("docs").verdict, "reveal")

    def test_markdown_outside_root_is_revealed_not_read(self) -> None:
        verdict = self.resolve(str(self.outside / "external.md"))
        self.assertEqual(verdict.verdict, "reveal")
        self.assertIsNone(verdict.relative_path)

    def test_unknown_path_is_missing(self) -> None:
        verdict = self.resolve("docs/nope.md")
        self.assertEqual(verdict.verdict, "missing")
        self.assertIsNone(verdict.path)
        self.assertIsNotNone(verdict.reason)

    def test_symlink_escaping_root_is_revealed_not_read(self) -> None:
        """Why resolve() has to run before the containment test.

        A link that lives inside the root but points outside it looks
        contained to any textual prefix check, and calling it `markdown`
        would hand an arbitrary external file to the browser.
        """
        (self.root / "escape.md").symlink_to(self.outside / "external.md")
        verdict = self.resolve("escape.md")
        self.assertEqual(verdict.verdict, "reveal")
        self.assertIsNone(verdict.relative_path)

    def test_parent_traversal_out_of_root_is_revealed(self) -> None:
        rel = os.path.relpath(self.outside / "external.md", self.root / "docs")
        verdict = self.resolve(
            rel, LinkBase(kind="project-file", path="docs/design.md")
        )
        self.assertEqual(verdict.verdict, "reveal")

    def test_project_file_base_resolves_against_its_directory(self) -> None:
        (self.root / "docs" / "sibling.md").write_text("s", encoding="utf-8")
        verdict = self.resolve(
            "sibling.md", LinkBase(kind="project-file", path="docs/design.md")
        )
        self.assertEqual(verdict.verdict, "markdown")
        self.assertEqual(verdict.relative_path, "docs/sibling.md")

    def test_artifact_base_resolves_against_the_directory_itself(self) -> None:
        outputs = self.root / ".miniclaw2" / "outputs" / "abc"
        outputs.mkdir(parents=True)
        (outputs / "report.md").write_text("r", encoding="utf-8")
        verdict = self.resolve(
            "report.md", LinkBase(kind="artifact", path=".miniclaw2/outputs/abc")
        )
        self.assertEqual(verdict.verdict, "markdown")

    def test_fragment_is_stripped_before_lookup(self) -> None:
        self.assertEqual(self.resolve("docs/design.md#section").verdict, "markdown")

    def test_file_url_is_accepted(self) -> None:
        verdict = self.resolve((self.root / "docs" / "design.md").as_uri())
        self.assertEqual(verdict.verdict, "markdown")

    def test_http_url_is_not_a_local_path(self) -> None:
        self.assertEqual(self.resolve("https://example.com/a.md").verdict, "missing")


class SelectCommandTest(unittest.TestCase):
    """Files must be located, never handed to a default handler."""

    def test_macos_select_uses_dash_r(self) -> None:
        """The regression guard for the defect this split exists to fix.

        Without `-R`, `open <file>` runs the file: a .command or .app
        target would execute. Nothing about that is visible from the
        endpoint, so the assertion lives down here.
        """
        with patch.object(sys, "platform", "darwin"):
            self.assertEqual(
                file_manager.select_command("/tmp/x.command"),
                ["open", "-R", "/tmp/x.command"],
            )

    def test_windows_select_passes_one_token(self) -> None:
        with patch.object(sys, "platform", "win32"):
            self.assertEqual(
                file_manager.select_command("C:/a/b.txt"),
                ["explorer", "/select,C:/a/b.txt"],
            )

    def test_linux_select_falls_back_to_the_parent_directory(self) -> None:
        with patch.object(sys, "platform", "linux"):
            self.assertEqual(
                file_manager.select_command("/home/u/docs/a.md"),
                ["xdg-open", "/home/u/docs"],
            )

    def test_directory_reveal_is_unchanged_on_macos(self) -> None:
        with patch.object(sys, "platform", "darwin"):
            self.assertEqual(file_manager.reveal_command("/tmp/d"), ["open", "/tmp/d"])


class RevealPathTest(unittest.TestCase):
    """`reveal_path` picks its verb from what the path actually is."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.file = self.dir / "a.md"
        self.file.write_text("a", encoding="utf-8")

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_directory_is_opened(self) -> None:
        with patch.object(sys, "platform", "darwin"), patch.object(
            file_manager.subprocess, "Popen"
        ) as popen:
            file_manager.reveal_path(str(self.dir))
        self.assertEqual(popen.call_args.args[0][0], "open")
        self.assertNotIn("-R", popen.call_args.args[0])

    def test_regular_file_is_only_selected(self) -> None:
        with patch.object(sys, "platform", "darwin"), patch.object(
            file_manager.subprocess, "Popen"
        ) as popen:
            file_manager.reveal_path(str(self.file))
        self.assertEqual(popen.call_args.args[0][:2], ["open", "-R"])

    def test_missing_path_reports_rather_than_spawning(self) -> None:
        with patch.object(file_manager.subprocess, "Popen") as popen:
            with self.assertRaises(file_manager.RevealError):
                file_manager.reveal_path(str(self.dir / "nope"))
        popen.assert_not_called()

    def test_fifo_is_refused(self) -> None:
        os.mkfifo(self.dir / "pipe")
        with patch.object(file_manager.subprocess, "Popen") as popen:
            with self.assertRaises(file_manager.RevealError):
                file_manager.reveal_path(str(self.dir / "pipe"))
        popen.assert_not_called()


class MarkdownFileEndpointTest(unittest.TestCase):
    """The HTTP surface, including the boundary `read` enforces alone."""

    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self._cwd = tempfile.TemporaryDirectory()
        self._outside = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        self.root = Path(self._cwd.name).resolve()
        self.outside = Path(self._outside.name).resolve()
        (self.root / "docs").mkdir()
        (self.root / "docs" / "design.md").write_text("# design", encoding="utf-8")
        (self.root / "app.py").write_text("print()", encoding="utf-8")
        (self.outside / "external.md").write_text("# external", encoding="utf-8")
        self.client = TestClient(create_app())
        self.sid = self._create_session()

    def tearDown(self) -> None:
        self.client.close()
        self._outside.cleanup()
        self._cwd.cleanup()
        self._home.cleanup()

    def _create_session(self) -> str:
        res = self.client.post(
            "/sessions",
            json={"cwd": str(self.root), "model_preset_id": "opus-4-8"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["id"]

    def _resolve(self, href: str) -> dict:
        res = self.client.post(
            f"/sessions/{self.sid}/files/resolve", json={"href": href}
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_resolve_reports_each_verdict(self) -> None:
        self.assertEqual(self._resolve("docs/design.md")["verdict"], "markdown")
        self.assertEqual(self._resolve("app.py")["verdict"], "reveal")
        self.assertEqual(self._resolve("docs")["verdict"], "reveal")
        self.assertEqual(self._resolve("nope.md")["verdict"], "missing")
        self.assertEqual(
            self._resolve(str(self.outside / "external.md"))["verdict"], "reveal"
        )

    def test_read_returns_markdown_inside_root(self) -> None:
        res = self.client.get(
            f"/sessions/{self.sid}/files/read", params={"path": "docs/design.md"}
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["text"], "# design")
        self.assertFalse(body["truncated"])
        self.assertEqual(body["path"], "docs/design.md")

    def test_read_holds_the_boundary_without_a_prior_resolve(self) -> None:
        """`read` is the only place bytes are gated, so it must hold for a
        client that never calls `resolve` at all."""
        traversal = os.path.relpath(self.outside / "external.md", self.root)
        for path, status in (
            ("app.py", 403),
            (traversal, 403),
            (str(self.outside / "external.md"), 403),
            ("nope.md", 404),
        ):
            with self.subTest(path=path):
                res = self.client.get(
                    f"/sessions/{self.sid}/files/read", params={"path": path}
                )
                self.assertEqual(res.status_code, status, res.text)
                self.assertNotIn("external", res.text)

    def test_read_refuses_a_symlink_that_escapes_the_root(self) -> None:
        (self.root / "escape.md").symlink_to(self.outside / "external.md")
        res = self.client.get(
            f"/sessions/{self.sid}/files/read", params={"path": "escape.md"}
        )
        self.assertEqual(res.status_code, 403, res.text)
        self.assertNotIn("external", res.text)

    def test_read_truncates_past_the_cap(self) -> None:
        (self.root / "big.md").write_text(
            "x" * (MARKDOWN_READ_CAP + 10), encoding="utf-8"
        )
        body = self.client.get(
            f"/sessions/{self.sid}/files/read", params={"path": "big.md"}
        ).json()
        self.assertTrue(body["truncated"])
        self.assertEqual(len(body["text"]), MARKDOWN_READ_CAP)

    def test_reveal_selects_a_file_without_opening_it(self) -> None:
        with patch.object(sys, "platform", "darwin"), patch.object(
            file_manager.subprocess, "Popen"
        ) as popen:
            res = self.client.post(
                f"/sessions/{self.sid}/files/reveal",
                json={"path": str(self.root / "app.py")},
            )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(popen.call_args.args[0][:2], ["open", "-R"])

    def test_reveal_accepts_a_path_outside_the_project(self) -> None:
        with patch.object(file_manager.subprocess, "Popen") as popen:
            res = self.client.post(
                f"/sessions/{self.sid}/files/reveal",
                json={"path": str(self.outside / "external.md")},
            )
        self.assertEqual(res.status_code, 200, res.text)
        popen.assert_called_once()

    def test_reveal_reports_a_missing_path(self) -> None:
        with patch.object(file_manager.subprocess, "Popen") as popen:
            res = self.client.post(
                f"/sessions/{self.sid}/files/reveal",
                json={"path": str(self.root / "nope")},
            )
        self.assertEqual(res.status_code, 400, res.text)
        popen.assert_not_called()

    def test_unknown_session_is_404(self) -> None:
        res = self.client.post("/sessions/nope/files/resolve", json={"href": "a.md"})
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
