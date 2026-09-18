"""Tests for provider-shared prompt composition."""

from __future__ import annotations

import unittest

from miniclaw2.domain import Node, Project
from miniclaw2.providers.base import AgentProviderContext, compose_system_prompt


class ComposePromptTests(unittest.TestCase):
    def test_launch_instructions_precede_context_in_system_prompt(self) -> None:
        instructions = "Framework constraints."
        context = "Project CONTEXT.md"

        composed = compose_system_prompt(instructions, context)

        self.assertTrue(composed.startswith(f"{instructions}\n\n---\n\n"))
        self.assertTrue(composed.endswith(context))

    def test_user_turn_is_only_the_node_prompt(self) -> None:
        prompt = "# Goal\n\nKeep this markdown structure.\n"
        context = AgentProviderContext(
            node=Node(project_id="p", prompt=prompt, model_preset_id="gpt-5.6"),
            project=Project(root_path="/tmp/workspace"),
            request_gate_handler=None,  # type: ignore[arg-type]
            launch_instructions="Framework constraints.",
            system_context="Project CONTEXT.md",
        )

        self.assertEqual(context.turn_text(), prompt)
        self.assertNotIn("Framework constraints.", context.turn_text())

    def test_system_prompt_can_omit_context_without_dropping_instructions(self) -> None:
        context = AgentProviderContext(
            node=Node(project_id="p", model_preset_id="gpt-5.6"),
            project=Project(root_path="/tmp/workspace"),
            request_gate_handler=None,  # type: ignore[arg-type]
            launch_instructions="Framework constraints.",
            system_context="Project CONTEXT.md",
        )

        self.assertEqual(
            context.system_prompt(include_context=False),
            "Framework constraints.",
        )


if __name__ == "__main__":
    unittest.main()
