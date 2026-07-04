from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from miniclaw2.contextspace import create_planspace
from miniclaw2.domain import (
    Category,
    Node,
    NodeKind,
    NodeState,
    Project,
)
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


class StaleNodeRepairTests(unittest.TestCase):
    """Registry init sweeps nodes stuck in non-terminal states."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.store = Store(root=Path(self.tmp.name) / "store")
        self.project = Project(root_path=str(Path(self.tmp.name) / "repo"))
        self.store.create_project(self.project)

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def _make_stale(self, nid: str, state: NodeState) -> Node:
        node = Node(
            id=nid,
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=state,
            provider="claude",
            prompt="do the thing",
            started_at=1.0,
        )
        self.store.create_node(node)
        return node

    def test_running_node_is_cancelled_on_registry_init(self) -> None:
        stale = self._make_stale("stale-running", NodeState.RUNNING)

        ProjectRegistry(store=self.store)

        loaded = self.store.load_node(self.project.id, stale.id)
        assert loaded is not None
        self.assertEqual(loaded.state, NodeState.CANCELLED)
        self.assertIn("interrupted by backend restart", loaded.error or "")
        self.assertIsNotNone(loaded.finished_at)

    def test_waiting_and_queued_nodes_also_swept(self) -> None:
        w = self._make_stale("stale-waiting", NodeState.WAITING)
        q = self._make_stale("stale-queued", NodeState.QUEUED)
        ah = self._make_stale("stale-ahi", NodeState.AWAITING_HUMAN_INPUT)

        ProjectRegistry(store=self.store)

        for nid in (w.id, q.id, ah.id):
            loaded = self.store.load_node(self.project.id, nid)
            assert loaded is not None
            self.assertEqual(loaded.state, NodeState.CANCELLED)
            self.assertIn("interrupted by backend restart", loaded.error or "")

    def test_terminal_and_virtual_nodes_left_alone(self) -> None:
        done = Node(
            id="done",
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.DONE,
            provider="claude",
            prompt="done",
            started_at=1.0,
            finished_at=2.0,
        )
        virtual = Node(
            id="virt",
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.VIRTUAL,
            provider="claude",
            prompt_draft="pending",
            proposed_by="user",
        )
        self.store.create_node(done)
        self.store.create_node(virtual)

        ProjectRegistry(store=self.store)

        done_after = self.store.load_node(self.project.id, done.id)
        virt_after = self.store.load_node(self.project.id, virtual.id)
        assert done_after is not None and virt_after is not None
        self.assertEqual(done_after.state, NodeState.DONE)
        self.assertEqual(virt_after.state, NodeState.VIRTUAL)

    def test_stale_node_gets_a_preview_written(self) -> None:
        stale = self._make_stale("stale-preview", NodeState.RUNNING)

        ProjectRegistry(store=self.store)

        preview = self.store.read_node_preview(self.project.id, stale.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn("cancelled", preview)
        self.assertIn("interrupted by backend restart", preview)


class RerunNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.store = Store(root=Path(self.tmp.name) / "store")
        self.project = Project(root_path=str(Path(self.tmp.name) / "repo"))
        self.store.create_project(self.project)
        self.registry = ProjectRegistry(store=self.store)
        self.lane = create_planspace(self.project, title="Work", mode="manual")
        runtime = self.registry._runtimes[self.project.id]
        settings = dict(runtime.project.settings_override)
        settings["active_planspace_id"] = self.lane
        runtime.project.settings_override = settings
        self.store.update_project(runtime.project)

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def _failed(self, nid: str, state: NodeState, prompt: str = "do it") -> Node:
        node = Node(
            id=nid,
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=state,
            planspace_id=self.lane,
            provider="claude",
            prompt=prompt,
            started_at=1.0,
            finished_at=2.0,
            error="something broke",
        )
        self.store.create_node(node)
        return node

    def test_rerun_creates_fresh_virtual_with_same_prompt(self) -> None:
        failed = self._failed("failed-1", NodeState.ERROR, prompt="ship it")

        result = self.registry.rerun_node(self.project.id, failed.id)

        assert result is not None
        self.assertEqual(result.state, NodeState.VIRTUAL)
        self.assertEqual(result.prompt_draft, "ship it")
        self.assertEqual(result.planspace_id, self.lane)
        self.assertTrue((result.proposed_by or "").startswith("rerun:"))
        self.assertIn(failed.id, result.proposed_by or "")
        self.assertNotEqual(result.id, failed.id)

    def test_rerun_works_for_cancelled_nodes(self) -> None:
        failed = self._failed("cx-1", NodeState.CANCELLED)
        result = self.registry.rerun_node(self.project.id, failed.id)
        assert result is not None
        self.assertEqual(result.state, NodeState.VIRTUAL)

    def test_rerun_rejects_terminal_done_nodes(self) -> None:
        done = self._failed("done-1", NodeState.DONE)
        with self.assertRaises(ValueError):
            self.registry.rerun_node(self.project.id, done.id)

    def test_rerun_rejects_nonagent_kinds(self) -> None:
        op = Node(
            id="op-1",
            project_id=self.project.id,
            kind=NodeKind.OP,
            op_kind="commit",
            state=NodeState.ERROR,
            planspace_id=self.lane,
            started_at=1.0,
            finished_at=2.0,
        )
        self.store.create_node(op)
        with self.assertRaises(ValueError):
            self.registry.rerun_node(self.project.id, op.id)

    def test_rerun_returns_none_for_missing_node(self) -> None:
        self.assertIsNone(self.registry.rerun_node(self.project.id, "no-such"))

    def test_rerun_preserves_continuation_context(self) -> None:
        source = Node(
            id="src-1",
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.DONE,
            planspace_id=self.lane,
            provider="claude",
            provider_session_id="prov-sess-1",
            prompt="original prompt",
            started_at=1.0,
            finished_at=2.0,
        )
        self.store.create_node(source)
        failed = Node(
            id="cont-1",
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.ERROR,
            planspace_id=self.lane,
            provider="claude",
            prompt="follow up",
            parent_node_id=source.id,
            resume_from_node_id=source.id,
            started_at=3.0,
            finished_at=4.0,
            error="boom",
        )
        self.store.create_node(failed)

        result = self.registry.rerun_node(self.project.id, failed.id)

        assert result is not None
        self.assertEqual(result.resume_from_node_id, source.id)
        self.assertEqual(result.parent_node_id, source.id)
