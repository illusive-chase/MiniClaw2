from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from miniclaw2.code_files import (
    TREE_LIMIT,
    CodeFileError,
    list_tree,
    read_code_file,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class GitVisibleSetTest(unittest.TestCase):
    def setUp(self) -> None:
        self._root = tempfile.TemporaryDirectory()
        self._outside = tempfile.TemporaryDirectory()
        self.root = Path(self._root.name).resolve()
        self.outside = Path(self._outside.name).resolve()
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "t")
        (self.root / ".gitignore").write_text("ignored.py\nbuild/\n", encoding="utf-8")
        (self.root / "tracked.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "gone.py").write_text("y = 1\n", encoding="utf-8")
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-q", "-m", "init")
        (self.root / "gone.py").unlink()
        (self.root / "untracked.py").write_text("z = 1\n", encoding="utf-8")
        (self.root / "ignored.py").write_text("secret = 1\n", encoding="utf-8")
        (self.root / "build").mkdir()
        (self.root / "build" / "out.py").write_text("", encoding="utf-8")
        (self.root / ".miniclaw2").mkdir()
        (self.root / ".miniclaw2" / "state.json").write_text("{}", encoding="utf-8")
        (self.outside / "external.py").write_text("leak = 1\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._root.cleanup()
        self._outside.cleanup()

    def paths(self) -> list[str]:
        entries, is_git, truncated = list_tree(self.root)
        self.assertTrue(is_git)
        self.assertFalse(truncated)
        return [entry.path for entry in entries]

    def test_tree_is_the_git_visible_set(self) -> None:
        paths = self.paths()
        self.assertIn("tracked.py", paths)
        self.assertIn("untracked.py", paths)
        self.assertIn(".gitignore", paths)
        self.assertNotIn("ignored.py", paths)
        self.assertNotIn("build/out.py", paths)
        # Deleted from the worktree but still in the index.
        self.assertNotIn("gone.py", paths)
        self.assertFalse(any(p.startswith(".miniclaw2") for p in paths))

    def test_miniclaw_dir_is_hidden_even_when_tracked(self) -> None:
        _git(self.root, "add", "-f", ".miniclaw2/state.json")
        self.assertFalse(any(p.startswith(".miniclaw2") for p in self.paths()))
        with self.assertRaises(CodeFileError) as caught:
            read_code_file(self.root, ".miniclaw2/state.json")
        self.assertEqual(caught.exception.status, 403)

    def test_submodule_shows_as_directory(self) -> None:
        nested = self.root / "vendor"
        nested.mkdir()
        _git(nested, "init", "-q")
        (nested / "lib.py").write_text("", encoding="utf-8")
        entries, _, _ = list_tree(self.root)
        by_path = {entry.path: entry for entry in entries}
        self.assertIn("vendor", by_path)
        self.assertTrue(by_path["vendor"].directory)
        self.assertNotIn("vendor/lib.py", by_path)

    def test_read_returns_text_and_hash(self) -> None:
        body = read_code_file(self.root, "tracked.py")
        self.assertEqual(body["text"], "x = 1\n")
        self.assertEqual(body["language"], "python")
        self.assertEqual(len(str(body["sha256"])), 64)
        self.assertEqual(read_code_file(self.root, "untracked.py")["text"], "z = 1\n")

    def test_read_refuses_everything_outside_the_visible_set(self) -> None:
        traversal = os.path.relpath(self.outside / "external.py", self.root)
        (self.root / "escape.py").symlink_to(self.outside / "external.py")
        for path, status in (
            ("ignored.py", 403),
            ("build/out.py", 403),
            (str(self.outside / "external.py"), 403),
            (traversal, 403),
            ("escape.py", 403),
            ("nope.py", 404),
        ):
            with self.subTest(path=path):
                with self.assertRaises(CodeFileError) as caught:
                    read_code_file(self.root, path)
                self.assertEqual(caught.exception.status, status)

    def test_symlink_inside_the_project_reads_its_target(self) -> None:
        (self.root / "alias.py").symlink_to(self.root / "tracked.py")
        body = read_code_file(self.root, "alias.py")
        self.assertEqual(body["path"], "tracked.py")

    def test_binary_large_encoding_and_newlines(self) -> None:
        (self.root / "blob.bin").write_bytes(b"\x89PNG\0\0data")
        (self.root / "big.txt").write_bytes(b"a" * (2 * 1024 * 1024 + 1))
        (self.root / "latin.py").write_bytes("# -*- coding: latin-1 -*-\ns = 'é'\n".encode("latin-1"))
        (self.root / "crlf.py").write_bytes(b"a = 1\r\nb = 2\rc = 3\r\n")
        self.assertTrue(read_code_file(self.root, "blob.bin")["binary"])
        big = read_code_file(self.root, "big.txt")
        self.assertTrue(big["too_large"])
        self.assertEqual(big["text"], "")
        self.assertIn("s = 'é'", str(read_code_file(self.root, "latin.py")["text"]))
        self.assertEqual(read_code_file(self.root, "crlf.py")["text"], "a = 1\nb = 2\nc = 3\n")


class NonGitTreeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._root = tempfile.TemporaryDirectory()
        self.root = Path(self._root.name).resolve()
        for path in ("src/app.py", "node_modules/x/index.js", ".venv/lib.py",
                     ".env", ".env.local", ".npmrc", ".pypirc", ".netrc", "src/.npmrc",
                     "dist/out.js", ".hidden/a.py", "README.md"):
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        self._root.cleanup()

    def test_exclusion_table_applies_without_git(self) -> None:
        entries, is_git, _ = list_tree(self.root)
        self.assertFalse(is_git)
        self.assertEqual(sorted(e.path for e in entries), ["README.md", "src/app.py"])
        for path in (".env", ".npmrc", ".pypirc", ".netrc", "src/.npmrc",
                     "node_modules/x/index.js", ".hidden/a.py"):
            with self.subTest(path=path), self.assertRaises(CodeFileError) as caught:
                read_code_file(self.root, path)
            self.assertEqual(caught.exception.status, 403)

    def test_tree_is_truncated_at_the_limit(self) -> None:
        bulk = self.root / "bulk"
        bulk.mkdir()
        for index in range(TREE_LIMIT + 5):
            (bulk / f"f{index}.txt").touch()
        entries, _, truncated = list_tree(self.root)
        self.assertTrue(truncated)
        self.assertEqual(len(entries), TREE_LIMIT)


if __name__ == "__main__":
    unittest.main()
