from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from miniclaw2.context import load_project_context


class LoadProjectContextTest(unittest.TestCase):
    def test_returns_file_contents_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "CONTEXT.md").write_text(
                "# Project context\n\nUse pytest.\n",
                encoding="utf-8",
            )
            self.assertEqual(
                load_project_context(tmp),
                "# Project context\n\nUse pytest.\n",
            )

    def test_returns_empty_string_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_project_context(tmp), "")

    def test_returns_empty_string_for_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "CONTEXT.md").write_text("", encoding="utf-8")
            self.assertEqual(load_project_context(tmp), "")

    def test_returns_empty_string_when_path_does_not_exist(self) -> None:
        self.assertEqual(
            load_project_context("/this/path/should/not/exist/xyz"),
            "",
        )

    def test_returns_empty_string_when_context_path_is_a_directory(self) -> None:
        # If a directory called CONTEXT.md exists (degenerate case), reading
        # it raises OSError; loader should swallow.
        with tempfile.TemporaryDirectory() as tmp:
            os.mkdir(Path(tmp) / "CONTEXT.md")
            self.assertEqual(load_project_context(tmp), "")


if __name__ == "__main__":
    unittest.main()
