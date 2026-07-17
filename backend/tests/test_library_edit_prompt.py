"""Tests for the unified librarian launch preset."""

from __future__ import annotations

import unittest
from pathlib import Path

from miniclaw2.domain import Category, Node
from miniclaw2.launch_prompt import build_library_init_block
from miniclaw2.runner import _authoring_init_block, _compose_launch_instructions


class BuildLibraryInitBlockTests(unittest.TestCase):
    def test_substitutes_both_library_directories(self) -> None:
        text = build_library_init_block(
            "/tmp/ctx/plugs/principles",
            "/tmp/ctx/skills",
        )
        self.assertIn("/tmp/ctx/plugs/principles/<slug>/manifest.yaml", text)
        self.assertIn("/tmp/ctx/skills/<slug>/SKILL.md", text)
        self.assertNotIn("<<principles_dir>>", text)
        self.assertNotIn("<<skills_dir>>", text)

    def test_dispatches_only_for_library_edit_and_keeps_first_slot(self) -> None:
        library_node = Node(
            project_id="p1",
            model_preset_id="gpt-5.5",
            category=Category.REGULAR,
            agent_op_kind="library_edit",
        )
        ordinary_node = library_node.model_copy(update={"agent_op_kind": None})
        block = _authoring_init_block(library_node, Path("/tmp/home"))
        self.assertIn("librarian", block.lower())
        self.assertEqual(_authoring_init_block(ordinary_node, Path("/tmp/home")), "")
        composed = _compose_launch_instructions(block, "CATEGORY", "BUNDLE")
        self.assertTrue(composed.startswith(block))


if __name__ == "__main__":
    unittest.main()
