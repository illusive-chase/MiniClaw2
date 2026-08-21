from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from miniclaw2.domain import Node, NodeKind, NodeState, Project
from miniclaw2.registry import ProjectRegistry, ProjectRuntime
from miniclaw2.replay import (
    EVENT_SCHEMA_VERSION,
    LiveReplayBuffer,
    covered_by_replay,
    upgrade_event_record,
    upgrade_legacy_interaction_response,
)
from miniclaw2.store import Store


class LiveReplayBufferTest(unittest.TestCase):
    def test_buffers_until_replay_marks_live_ready(self) -> None:
        buffer = LiveReplayBuffer()

        self.assertIsNone(
            buffer.push_live({"type": "node_started", "node_id": "n1", "seq": 1})
        )
        self.assertIsNone(buffer.push_live({
            "type": "text_delta", "text": "old", "node_id": "n1", "seq": 2
        }))

        self.assertEqual(
            buffer.mark_live_ready(replay_node_id="n1", replayed_through_seq=2),
            [],
        )
        self.assertEqual(
            buffer.push_live({
                "type": "text_delta", "text": "new", "node_id": "n1", "seq": 3
            }),
            {"type": "text_delta", "text": "new", "node_id": "n1", "seq": 3},
        )

    def test_flushes_live_events_after_replayed_gap(self) -> None:
        buffer = LiveReplayBuffer()

        buffer.push_live({"type": "node_started", "node_id": "n1", "seq": 1})
        buffer.push_live({
            "type": "text_delta", "text": "later", "node_id": "n1", "seq": 13
        })

        self.assertEqual(
            buffer.mark_live_ready(replay_node_id="n1", replayed_through_seq=12),
            [{"type": "text_delta", "text": "later", "node_id": "n1", "seq": 13}],
        )

    def test_keeps_other_node_events_buffered_until_their_replay(self) -> None:
        buffer = LiveReplayBuffer()

        buffer.push_live({"type": "node_started", "node_id": "n2", "seq": 1})
        buffer.push_live({
            "type": "text_delta", "text": "other", "node_id": "n2", "seq": 2
        })

        self.assertEqual(
            buffer.mark_live_ready(replay_node_id="n1", replayed_through_seq=99),
            [],
        )
        self.assertIsNone(
            buffer.push_live({
                "type": "text_delta", "text": "new", "node_id": "n2", "seq": 3
            })
        )
        self.assertEqual(
            buffer.mark_live_ready(replay_node_id="n2", replayed_through_seq=2),
            [
                {"type": "text_delta", "text": "new", "node_id": "n2", "seq": 3},
            ],
        )
        self.assertEqual(
            buffer.push_live({"type": "node_updated", "node_id": "n2", "seq": 0}),
            {"type": "node_updated", "node_id": "n2", "seq": 0},
        )

    def test_node_less_events_are_not_covered_by_node_replay(self) -> None:
        buffer = LiveReplayBuffer()
        removed = {"type": "node_removed", "removed_node_id": "n2", "seq": 0}

        self.assertIsNone(buffer.push_live(removed))
        self.assertEqual(
            buffer.mark_live_ready(replay_node_id="n1", replayed_through_seq=99),
            [removed],
        )
        later = {"type": "node_removed", "removed_node_id": "n3", "seq": 0}
        self.assertEqual(buffer.push_live(later), later)


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


    def test_legacy_checkpoint_review_is_upgraded_before_replay(self) -> None:
        upgraded = upgrade_event_record(
            {
                "seq": 3,
                "event": {
                    "type": "interaction_request",
                    "interaction_type": "checkpoint_review",
                    "tool_name": "checkpoint_review",
                },
            }
        )

        self.assertEqual(upgraded["schema_version"], EVENT_SCHEMA_VERSION)
        self.assertEqual(
            upgraded["event"]["interaction_type"],
            "human_review_prose",
        )
        self.assertEqual(upgraded["event"]["tool_name"], "human_review_prose")

    def test_legacy_ask_response_is_upgraded_to_canonical_answers(self) -> None:
        upgraded = upgrade_legacy_interaction_response(
            {
                "type": "interaction_response",
                "id": "gate-1",
                "updated_input": {
                    "answers": {
                        "framework": "React",
                        "checks": ["types", "tests"],
                    }
                },
            }
        )

        self.assertEqual(
            upgraded["response"],
            {
                "answers": {
                    "framework": {"answers": ["React"]},
                    "checks": {"answers": ["types", "tests"]},
                }
            },
        )
        self.assertNotIn("updated_input", upgraded)


class RegistryReplayCompatibilityTest(unittest.TestCase):
    def test_legacy_lifecycle_events_receive_current_node_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw) / "store")
            registry = ProjectRegistry(store=store)
            project_root = Path(raw) / "repo"
            project_root.mkdir()
            project = store.create_project(
                Project(root_path=str(project_root), machine_id=store.machine.id)
            )
            registry._runtimes[project.id] = ProjectRuntime(project)
            node = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    model_preset_id=project.model_preset_id,
                    state=NodeState.DONE,
                )
            )
            store.append_event(
                project.id,
                node.id,
                1,
                {"type": "node_started", "node_id": node.id, "seq": 1},
            )
            store.append_event(
                project.id,
                node.id,
                2,
                {"type": "turn_done", "node_id": node.id, "seq": 2},
            )

            records = registry.replay_node_events(project.id, node.id)

            assert records is not None
            self.assertEqual(records[0]["event"]["node"]["id"], node.id)
            self.assertEqual(records[1]["event"]["node"]["state"], "done")


class FreshNodeLaunchTest(unittest.TestCase):
    def test_start_node_does_not_inherit_previous_provider_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(root=Path(tmp))
            project = Project(root_path=tmp, model_preset_id="gpt-5.5")
            store.create_project(project)

            previous = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                    model_preset_id="gpt-5.6",
                    provider_session_id="thread_previous",
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
            self.assertNotEqual(node.id, previous.id)

    def test_start_node_uses_explicit_model_preset_for_new_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(root=Path(tmp))
            project = Project(root_path=tmp, model_preset_id="opus-4-7")
            store.create_project(project)

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
                    "hello",
                    model_preset_id="gpt-5.6",
                )

            self.assertIsNotNone(runner)

            node = store.latest_node(project.id)
            assert node is not None
            self.assertEqual(node.model_preset_id, "gpt-5.6")
            self.assertEqual(node.provider, "codex")
            self.assertEqual(node.provider_session_id, None)

    def test_start_node_can_explicitly_resume_from_source_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(root=Path(tmp))
            project = Project(root_path=tmp, model_preset_id="gpt-5.5")
            store.create_project(project)

            source = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                    model_preset_id="gpt-5.5",
                    provider_session_id="thread_source",
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
            self.assertEqual(node.model_preset_id, "gpt-5.5")
            self.assertEqual(node.provider, "codex")
            self.assertEqual(node.provider_session_id, "thread_source")
            self.assertNotEqual(node.id, source.id)

    def test_start_node_resume_rejects_model_preset_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(root=Path(tmp))
            project = Project(root_path=tmp, model_preset_id="opus-4-7")
            store.create_project(project)

            source = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                    model_preset_id="opus-4-7",
                    provider_session_id="thread_source",
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
                with self.assertRaisesRegex(ValueError, "inherit model_preset_id"):
                    registry.start_node(
                        project.id,
                        "continue from source",
                        resume_from_node_id=source.id,
                        model_preset_id="gpt-5.5",
                    )

                runner = registry.start_node(
                    project.id,
                    "continue from source",
                    resume_from_node_id=source.id,
                    model_preset_id="opus-4-7",
                )

            self.assertIsNotNone(runner)

            node = store.latest_node(project.id)
            assert node is not None
            self.assertEqual(node.parent_node_id, source.id)
            self.assertEqual(node.model_preset_id, "opus-4-7")
            self.assertEqual(node.provider, "claude")
            self.assertEqual(node.provider_session_id, "thread_source")

    def test_start_node_rejects_resume_from_nonterminal_source_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(root=Path(tmp))
            project = Project(root_path=tmp, model_preset_id="gpt-5.5")
            store.create_project(project)

            # Registry init sweeps stale non-terminal nodes to CANCELLED; add
            # the RUNNING source AFTER init so start_node sees a non-terminal
            # resume source and exercises its own safety check.
            registry = ProjectRegistry(store=store)
            source = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.RUNNING,
                    model_preset_id="gpt-5.5",
                    provider_session_id="thread_source",
                )
            )

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
            project = Project(root_path=tmp, model_preset_id="opus-4-7")
            store.create_project(project)
            first = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                    model_preset_id="opus-4-7",
                )
            )
            store.append_event(project.id, first.id, 1, {"type": "text_delta", "seq": 1})
            registry = ProjectRegistry(store=store)

            self.assertEqual(
                registry.replay_node_events(project.id, "", 0),
                [
                    {
                        "schema_version": EVENT_SCHEMA_VERSION,
                        "seq": 1,
                        "event": {
                            "type": "text_delta",
                            "seq": 1,
                            "node_id": first.id,
                        },
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
