"""Anti-self-poisoning filter contract appended at launch time.

The filter is the final block in ``_compose_launch_instructions`` so the
agent sees it after the planspace update contract and the optional
review-guidance contract — last-instructions-win bias.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from miniclaw2.domain import Node, NodeState, Project
from miniclaw2.providers.base import AgentProviderEvent
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store

from test_contextspace import _write_contextspace


_FILTER_HEADING = "# Planspace update filter"


class _CaptureProvider:
    name = "capture"

    def __init__(self) -> None:
        self.contexts: list[Any] = []

    async def run(self, context: Any):
        self.contexts.append(context)
        yield AgentProviderEvent(kind="session", session_id="stub-session")
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class AntiSelfPoisoningTest(unittest.IsolatedAsyncioTestCase):
    async def test_filter_block_appears_when_planspace_auto_updates(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = Store(root=tmp / "store")
            project_root = tmp / "repo"
            project_root.mkdir()
            _write_contextspace(store.root, project_root)

            project = Project(
                root_path=str(project_root),
                project_context_binding_id="project.test",
            )
            store.create_project(project)
            node = store.create_node(Node(project_id=project.id, prompt="Do the work."))

            async def on_event(payload: dict[str, object]) -> None:
                return None

            provider = _CaptureProvider()
            with patch("miniclaw2.runner._make_provider", return_value=provider):
                runner = NodeRunner(node, project, store, on_event)
                await runner.run()

            self.assertEqual(node.state, NodeState.DONE)
            launch = provider.contexts[0].launch_instructions
            self.assertIn(_FILTER_HEADING, launch)
            # Each block is separated by the same "---" delimiter
            # _compose_launch_instructions uses; the filter must be last.
            blocks = [block.strip() for block in launch.split("\n\n---\n\n")]
            self.assertTrue(blocks[-1].startswith(_FILTER_HEADING))

    async def test_filter_block_absent_when_no_planspace_bound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = Store(root=tmp / "store")
            project_root = tmp / "repo"
            project_root.mkdir()
            # Project intentionally has no contextspace binding.

            project = Project(root_path=str(project_root))
            store.create_project(project)
            node = store.create_node(Node(project_id=project.id, prompt="Do."))

            async def on_event(payload: dict[str, object]) -> None:
                return None

            provider = _CaptureProvider()
            with patch("miniclaw2.runner._make_provider", return_value=provider):
                runner = NodeRunner(node, project, store, on_event)
                await runner.run()

            launch = provider.contexts[0].launch_instructions
            self.assertNotIn(_FILTER_HEADING, launch)

    async def test_filter_block_absent_when_auto_update_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = Store(root=tmp / "store")
            project_root = tmp / "repo"
            project_root.mkdir()
            ctx = _write_contextspace(store.root, project_root)
            binding_path = ctx / "bindings" / "projects" / "project.test.yaml"
            binding_path.write_text(
                binding_path.read_text(encoding="utf-8").replace(
                    "    auto_update: true",
                    "    auto_update: false",
                    1,
                ),
                encoding="utf-8",
            )

            project = Project(
                root_path=str(project_root),
                project_context_binding_id="project.test",
            )
            store.create_project(project)
            node = store.create_node(Node(project_id=project.id, prompt="Do."))

            async def on_event(payload: dict[str, object]) -> None:
                return None

            provider = _CaptureProvider()
            with patch("miniclaw2.runner._make_provider", return_value=provider):
                runner = NodeRunner(node, project, store, on_event)
                await runner.run()

            launch = provider.contexts[0].launch_instructions
            self.assertNotIn(_FILTER_HEADING, launch)


if __name__ == "__main__":
    unittest.main()
