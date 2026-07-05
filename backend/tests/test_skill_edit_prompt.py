"""Tests for the skill-edit launch preset (PR 2)."""

from __future__ import annotations

import unittest

from miniclaw2.launch_prompt import build_skill_init_block


class BuildSkillInitBlockTests(unittest.TestCase):
    def test_substitutes_skills_dir_token(self) -> None:
        text = build_skill_init_block("/tmp/ctx/plugs/skills")
        self.assertIn("/tmp/ctx/plugs/skills/<slug>/manifest.yaml", text)
        self.assertIn("/tmp/ctx/plugs/skills/<slug>/CONTEXT.md", text)
        self.assertNotIn("<<skills_dir>>", text)

    def test_block_is_non_empty(self) -> None:
        text = build_skill_init_block("/x")
        self.assertTrue(text.strip())
        # Must not leak the user-seed template — that lives in the user turn,
        # not the launch block.
        self.assertNotIn("<<user_seed>>", text)


if __name__ == "__main__":
    unittest.main()
