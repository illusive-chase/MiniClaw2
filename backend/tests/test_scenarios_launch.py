from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from miniclaw2.registry import ProjectRegistry
from miniclaw2.scenarios import ScenarioError, launch_scenario
from miniclaw2.store import Store


class _FakeTask:
    def add_done_callback(self, callback):
        self._callback = callback

    def done(self) -> bool:
        return True

    def cancel(self) -> bool:
        return False


def _fake_create_task(coro):
    # Don't actually run the coro — we just want to verify the node was queued.
    coro.close()
    return _FakeTask()


class LaunchScenarioTest(unittest.TestCase):
    def test_launching_hello_text_creates_temp_project_and_first_node(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw))
            registry = ProjectRegistry(store=store)

            with patch(
                "miniclaw2.registry.asyncio.create_task",
                side_effect=_fake_create_task,
            ):
                project, scenario = launch_scenario(
                    "hello-text", "claude", registry
                )

            try:
                self.assertEqual(scenario.name, "hello-text")
                self.assertTrue(project.temporary)
                self.assertEqual(project.scenario_name, "hello-text")
                self.assertEqual(project.provider, "claude")

                nodes = store.list_nodes(project.id)
                self.assertEqual(len(nodes), 1)
                first = nodes[0]
                self.assertEqual(first.kind, "agent")
                self.assertIn("[OK]", first.prompt)

                root = Path(project.root_path)
                self.assertTrue(root.exists())
                self.assertTrue((root / ".git").exists())
            finally:
                registry.delete_project(project.id)

    def test_unsupported_provider_raises(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw))
            registry = ProjectRegistry(store=store)
            with self.assertRaises(ScenarioError):
                launch_scenario("hello-text", "anthropic-foo", registry)

    def test_unknown_scenario_raises(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw))
            registry = ProjectRegistry(store=store)
            with self.assertRaises(ScenarioError):
                launch_scenario("does-not-exist", "claude", registry)


if __name__ == "__main__":
    unittest.main()
