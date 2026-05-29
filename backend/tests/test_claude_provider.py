from __future__ import annotations

import unittest
from typing import Any

from miniclaw2.domain import Node, Project
from miniclaw2.providers.base import AgentProviderContext
from miniclaw2.providers.claude import ClaudeProvider


async def _request_gate(_gate: Any) -> dict[str, Any]:
    return {"allow": True}


class ClaudeProviderTest(unittest.TestCase):
    def test_build_options_disables_implicit_settings_sources(self) -> None:
        node = Node(project_id="project-1", prompt="Run a command")
        project = Project(
            root_path="/tmp/workspace",
            provider="claude",
            settings_override={"permission_mode": "default"},
        )
        context = AgentProviderContext(
            node=node,
            project=project,
            request_gate_handler=_request_gate,
        )

        options = ClaudeProvider()._build_options(context)

        self.assertEqual(options.permission_mode, "default")
        self.assertEqual(options.setting_sources, [])


if __name__ == "__main__":
    unittest.main()
