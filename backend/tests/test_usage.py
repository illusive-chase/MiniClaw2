from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from miniclaw2.domain import Node, Project
from miniclaw2.events import Usage
from miniclaw2.providers.base import AgentProviderEvent
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


class NodeUsageTest(unittest.IsolatedAsyncioTestCase):
    async def test_usage_event_updates_node_snapshot_and_broadcasts_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(root=Path(tmp) / "store")
            project = Project(root_path=tmp, provider="codex")
            store.create_project(project)
            node = store.create_node(Node(project_id=project.id, provider="codex"))

            emitted: list[dict[str, object]] = []

            async def on_event(payload: dict[str, object]) -> None:
                emitted.append(payload)

            runner = NodeRunner(node, project, store, on_event)
            await runner._handle_provider_event(
                AgentProviderEvent(
                    kind="event",
                    event=Usage(
                        input_tokens=100,
                        output_tokens=30,
                        cache_read_tokens=20,
                        cache_creation_tokens=5,
                        cumulative_output_tokens=90,
                        cumulative_cache_creation_tokens=15,
                        final=True,
                    ),
                )
            )

            fresh = store.load_node(project.id, node.id)
            assert fresh is not None
            assert fresh.usage is not None
            self.assertEqual(fresh.usage.input_tokens, 100)
            self.assertEqual(fresh.usage.output_tokens, 30)
            self.assertEqual(fresh.usage.cache_read_tokens, 20)
            self.assertEqual(fresh.usage.cache_creation_tokens, 5)
            self.assertEqual(fresh.usage.cumulative_output_tokens, 90)
            self.assertEqual(fresh.usage.cumulative_cache_creation_tokens, 15)

            self.assertEqual([event["type"] for event in emitted], ["usage", "node_updated"])
            updated = emitted[1]["node"]
            assert isinstance(updated, dict)
            self.assertEqual(
                updated["usage"],
                {
                    "input_tokens": 100,
                    "output_tokens": 30,
                    "cache_read_tokens": 20,
                    "cache_creation_tokens": 5,
                    "cumulative_output_tokens": 90,
                    "cumulative_cache_creation_tokens": 15,
                },
            )


if __name__ == "__main__":
    unittest.main()
