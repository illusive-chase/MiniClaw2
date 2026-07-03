from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from miniclaw2.domain import Node, NodeKind, NodeState, Project
from miniclaw2.registry import ProjectRegistry
from miniclaw2.replay import LiveReplayBuffer, covered_by_replay
from miniclaw2.store import Store


class LiveReplayBufferTest(unittest.TestCase):
    def test_buffers_until_replay_marks_live_ready(self) -> None:
        buffer = LiveReplayBuffer()

        self.assertIsNone(
            buffer.push_live({"type": "node_started", "node_id": "n1", "seq": 1})
        )
        self.assertIsNone(buffer.push_live({"type": "text_delta", "text": "old", "seq": 2}))

        self.assertEqual(
            buffer.mark_live_ready(replay_node_id="n1", replayed_through_seq=2),
            [],
        )
        self.assertEqual(
            buffer.push_live({"type": "text_delta", "text": "new", "seq": 3}),
            {"type": "text_delta", "text": "new", "seq": 3},
        )

    def test_flushes_live_events_after_replayed_gap(self) -> None:
        buffer = LiveReplayBuffer()

        buffer.push_live({"type": "node_started", "node_id": "n1", "seq": 1})
        buffer.push_live({"type": "text_delta", "text": "later", "seq": 13})

        self.assertEqual(
            buffer.mark_live_ready(replay_node_id="n1", replayed_through_seq=12),
            [{"type": "text_delta", "text": "later", "seq": 13}],
        )

    def test_does_not_cover_other_node_events(self) -> None:
        buffer = LiveReplayBuffer()

        buffer.push_live({"type": "node_started", "node_id": "n2", "seq": 1})
        buffer.push_live({"type": "text_delta", "text": "other", "seq": 2})

        self.assertEqual(
            buffer.mark_live_ready(replay_node_id="n1", replayed_through_seq=99),
            [
                {"type": "node_started", "node_id": "n2", "seq": 1},
                {"type": "text_delta", "text": "other", "seq": 2},
            ],
        )


class CoveredByReplayTest(unittest.TestCase):
    def test_requires_matching_node_for_node_started(self) -> None:
        self.assertTrue(
            covered_by_replay(
                "n1",
                {"type": "node_started", "node_id": "n1", "seq": 1},
                "n1",
                1,
            )
        )
        self.assertFalse(
            covered_by_replay(
                "n2",
                {"type": "node_started", "node_id": "n2", "seq": 1},
                "n1",
                99,
            )
        )


class FreshNodeLaunchTest(unittest.TestCase):
    def test_start_node_does_not_inherit_previous_provider_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(root=Path(tmp))
            project = Project(root_path=tmp, provider="codex")
            store.create_project(project)

            previous = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                    provider="codex",
                    provider_session_id="thread_previous",
                    cli_session_id="thread_previous",
                )
            )

            registry = ProjectRegistry(store=store)

            class _FakeTask:
                def add_done_callback(self, callback):
                    self._callback = callback

            def fake_create_task(coro):
                coro.close()
                return _FakeTask()

            with patch("miniclaw2.registry.asyncio.create_task", side_effect=fake_create_task):
                runner = registry.start_node(project.id, "hello")

            self.assertIsNotNone(runner)

            node = store.latest_node(project.id)
            assert node is not None
            self.assertEqual(node.parent_node_id, None)
            self.assertEqual(node.provider_session_id, None)
            self.assertEqual(node.cli_session_id, None)
            self.assertNotEqual(node.id, previous.id)

    def test_start_node_can_explicitly_resume_from_source_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(root=Path(tmp))
            project = Project(root_path=tmp, provider="codex")
            store.create_project(project)

            source = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                    provider="codex",
                    provider_session_id="thread_source",
                    cli_session_id="thread_source",
                )
            )

            registry = ProjectRegistry(store=store)

            class _FakeTask:
                def add_done_callback(self, callback):
                    self._callback = callback

            def fake_create_task(coro):
                coro.close()
                return _FakeTask()

            with patch("miniclaw2.registry.asyncio.create_task", side_effect=fake_create_task):
                runner = registry.start_node(
                    project.id,
                    "continue from source",
                    resume_from_node_id=source.id,
                )

            self.assertIsNotNone(runner)

            node = store.latest_node(project.id)
            assert node is not None
            self.assertEqual(node.parent_node_id, source.id)
            self.assertEqual(node.provider, "codex")
            self.assertEqual(node.provider_session_id, "thread_source")
            self.assertEqual(node.cli_session_id, "thread_source")
            self.assertNotEqual(node.id, source.id)

    def test_start_node_rejects_resume_from_nonterminal_source_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(root=Path(tmp))
            project = Project(root_path=tmp, provider="codex")
            store.create_project(project)

            source = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.RUNNING,
                    provider="codex",
                    provider_session_id="thread_source",
                )
            )

            registry = ProjectRegistry(store=store)
            self.assertIsNone(
                registry.start_node(
                    project.id,
                    "continue from source",
                    resume_from_node_id=source.id,
                )
            )
            self.assertEqual(store.latest_node(project.id).id, source.id)


class ReplayBootstrapTest(unittest.TestCase):
    def test_replay_node_events_bootstraps_latest_node_when_id_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(root=Path(tmp))
            project = Project(root_path=tmp, provider="claude")
            store.create_project(project)
            first = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                    provider="claude",
                )
            )
            store.append_event(project.id, first.id, 1, {"type": "text_delta", "seq": 1})
            registry = ProjectRegistry(store=store)

            self.assertEqual(
                registry.replay_node_events(project.id, "", 0),
                [{"seq": 1, "event": {"type": "text_delta", "seq": 1}}],
            )


if __name__ == "__main__":
    unittest.main()
