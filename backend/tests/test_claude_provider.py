from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
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

    def test_minimal_write_is_scoped_to_root_context_md(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            node = Node(project_id="project-1", prompt="Refresh context")
            project = Project(
                root_path=raw,
                provider="claude",
            )
            context = AgentProviderContext(
                node=node,
                project=project,
                request_gate_handler=_request_gate,
                minimal_mode=True,
                tool_allowlist=["Read", "Write"],
            )
            callback = ClaudeProvider()._make_can_use_tool(context)

            relative = asyncio.run(callback("Write", {"file_path": "CONTEXT.md"}, None))
            absolute = asyncio.run(
                callback("Write", {"file_path": str(Path(raw) / "CONTEXT.md")}, None)
            )
            nested = asyncio.run(
                callback("Write", {"file_path": "docs/CONTEXT.md"}, None)
            )
            other_file = asyncio.run(
                callback("Write", {"file_path": "README.md"}, None)
            )
            missing_path = asyncio.run(callback("Write", {}, None))
            read = asyncio.run(callback("Read", {"file_path": "README.md"}, None))

            self.assertEqual(relative.behavior, "allow")
            self.assertEqual(absolute.behavior, "allow")
            self.assertEqual(read.behavior, "allow")
            self.assertEqual(nested.behavior, "deny")
            self.assertEqual(other_file.behavior, "deny")
            self.assertEqual(missing_path.behavior, "deny")
            self.assertIn("CONTEXT.md", other_file.message)


if __name__ == "__main__":
    unittest.main()
