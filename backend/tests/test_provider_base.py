"""Tests for provider-shared prompt composition."""

from __future__ import annotations

import unittest

from miniclaw2.providers.base import compose_turn_text


class ComposeTurnTextTests(unittest.TestCase):
    def test_prompt_is_unchanged_without_launch_instructions(self) -> None:
        prompt = "Implement the requested workflow."

        self.assertEqual(compose_turn_text(prompt), prompt)

    def test_launch_instructions_are_distinguished_from_node_task(self) -> None:
        instructions = "Framework constraints."
        prompt = "Implement the requested workflow."

        composed = compose_turn_text(prompt, instructions)

        self.assertTrue(composed.startswith(f"{instructions}\n\n---\n\n"))
        self.assertIn("# MiniClaw2 — task to execute", composed)
        self.assertIn("node instructions end above", composed)
        self.assertIn("not another framework instruction", composed)
        self.assertIn("primary objective", composed)
        self.assertTrue(composed.endswith(prompt))

    def test_user_task_is_preserved_verbatim(self) -> None:
        prompt = "# Goal\n\nKeep this markdown structure.\n"

        composed = compose_turn_text(prompt, "Framework constraints.")

        self.assertTrue(composed.endswith(prompt))


if __name__ == "__main__":
    unittest.main()
