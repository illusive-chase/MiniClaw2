"""Tests for deleting a planspace lane and everything it owns."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

import yaml

from miniclaw2.artifacts import workspace_artifacts_dir
from miniclaw2.contextspace import (
    contextspace_root,
    create_planspace,
    delete_planspace,
    describe_project_contextspace,
    ensure_project_binding,
    resolve_project_binding,
)
from miniclaw2.domain import Node, NodeState, Project
from miniclaw2.materialize import ARTIFACTS_DIRNAME, lane_root
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


class DeletePlanspacePlugTests(unittest.TestCase):
    """Plug-level delete: manifest directory + binding ref."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.project = Project(
            root_path=str(Path(self.tmp.name) / "repo"),
            name="auth-flow",
        )
        Path(self.project.root_path).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def test_delete_removes_manifest_dir_and_binding_ref(self) -> None:
        keep = create_planspace(self.project, title="Keep", mode="manual")
        drop = create_planspace(self.project, title="Drop", mode="manual")
        root = contextspace_root()
        drop_dir = root / "plugs" / "planspaces" / "auth-flow.drop"
        self.assertTrue(drop_dir.exists())

        self.assertTrue(delete_planspace(self.project, drop))

        self.assertFalse(drop_dir.exists())
        self.assertTrue((root / "plugs" / "planspaces" / "auth-flow.keep").exists())
        binding = resolve_project_binding(self.project, root)
        assert binding is not None
        plug_ids = [ref.id for ref in binding.plugs]
        self.assertEqual(plug_ids, [keep])
        raw = yaml.safe_load(binding.path.read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in raw["plugs"]], [keep])

    def test_delete_unknown_planspace_is_false(self) -> None:
        create_planspace(self.project, title="Keep", mode="manual")
        self.assertFalse(
            delete_planspace(self.project, "planspaces.auth-flow.missing")
        )

    def test_delete_rejects_non_planspace_plug(self) -> None:
        ensure_project_binding(self.project)
        with self.assertRaises(ValueError):
            delete_planspace(self.project, "principles.house-style")

    def test_shared_planspace_dir_is_retained_for_other_bindings(self) -> None:
        other = Project(
            root_path=str(Path(self.tmp.name) / "other-repo"),
            name="billing",
        )
        Path(other.root_path).mkdir(parents=True, exist_ok=True)
        shared = create_planspace(self.project, title="Shared", mode="manual")
        other_binding = ensure_project_binding(other)
        from miniclaw2.contextspace import add_planspace_to_binding

        add_planspace_to_binding(other_binding, shared)

        self.assertTrue(delete_planspace(self.project, shared))

        root = contextspace_root()
        self.assertTrue((root / "plugs" / "planspaces" / "auth-flow.shared").exists())
        binding = resolve_project_binding(self.project, root)
        assert binding is not None
        self.assertEqual([ref.id for ref in binding.plugs], [])


class DeletePlanspaceRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.store = Store(root=Path(self.tmp.name) / "store")
        self.project = Project(
            root_path=str(Path(self.tmp.name) / "repo"),
            name="delete-project",
        )
        Path(self.project.root_path).mkdir(parents=True, exist_ok=True)
        self.store.create_project(self.project)
        self.registry = ProjectRegistry(store=self.store)

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def _make_lane(self, title: str) -> str:
        result = self.registry.create_blank_planspace(
            self.project.id, title=title, seed=title, mode="manual"
        )
        assert result is not None
        return result.node.planspace_id or ""

    def _runtime_project(self) -> Project:
        return self.registry.get_project(self.project.id) or self.project

    def _add_node(self, lane_id: str, state: NodeState, **kwargs: object) -> Node:
        node = Node(
            project_id=self.project.id,
            state=state,
            planspace_id=lane_id,
            model_preset_id=self.project.model_preset_id,
            **kwargs,
        )
        self.store.create_node(node)
        return node

    def test_delete_removes_lane_nodes_and_plug(self) -> None:
        keep_lane = self._make_lane("Keep")
        drop_lane = self._make_lane("Drop")
        # Creating a second lane while idle activates it; move back to the first.
        self.registry.update_project_context(
            self.project.id, active_planspace_id=keep_lane
        )
        executed = self._add_node(drop_lane, NodeState.DONE, prompt="ran already")

        deleted, busy = self.registry.delete_planspace(self.project.id, drop_lane)

        self.assertTrue(deleted)
        self.assertEqual(busy, [])
        remaining = self.store.list_nodes(self.project.id)
        self.assertTrue(all((n.planspace_id or "") != drop_lane for n in remaining))
        self.assertTrue(any((n.planspace_id or "") == keep_lane for n in remaining))
        summary = describe_project_contextspace(
            self._runtime_project(), store_root=self.store.root
        )
        plug_ids = {
            plug["id"]
            for binding in summary["bindings"]
            for plug in binding["plugs"]
        }
        self.assertNotIn(drop_lane, plug_ids)
        self.assertIn(keep_lane, plug_ids)

    def test_delete_active_planspace_is_rejected(self) -> None:
        self._make_lane("Only")
        active = self._runtime_project().active_planspace_id or ""
        self.assertTrue(active)

        with self.assertRaises(ValueError):
            self.registry.delete_planspace(self.project.id, active)

        self.assertTrue(self.store.list_nodes(self.project.id))

    def test_delete_reports_busy_nodes_without_mutating(self) -> None:
        keep_lane = self._make_lane("Keep")
        drop_lane = self._make_lane("Drop")
        self.registry.update_project_context(
            self.project.id, active_planspace_id=keep_lane
        )
        running = self._add_node(drop_lane, NodeState.RUNNING, prompt="in flight")

        deleted, busy = self.registry.delete_planspace(self.project.id, drop_lane)

        self.assertFalse(deleted)
        self.assertEqual(busy, [running.id])
        self.assertIsNotNone(self.store.load_node(self.project.id, running.id))
        summary = describe_project_contextspace(
            self._runtime_project(), store_root=self.store.root
        )
        plug_ids = {
            plug["id"]
            for binding in summary["bindings"]
            for plug in binding["plugs"]
        }
        self.assertIn(drop_lane, plug_ids)

    def test_delete_allowed_while_another_lane_runs(self) -> None:
        keep_lane = self._make_lane("Keep")
        drop_lane = self._make_lane("Drop")
        self.registry.update_project_context(
            self.project.id, active_planspace_id=keep_lane
        )
        running_elsewhere = self._add_node(
            keep_lane, NodeState.RUNNING, prompt="busy in the other lane"
        )

        deleted, busy = self.registry.delete_planspace(self.project.id, drop_lane)

        self.assertTrue(deleted)
        self.assertEqual(busy, [])
        self.assertIsNotNone(
            self.store.load_node(self.project.id, running_elsewhere.id)
        )

    def test_delete_strips_dangling_scheduled_deps_in_other_lanes(self) -> None:
        keep_lane = self._make_lane("Keep")
        drop_lane = self._make_lane("Drop")
        self.registry.update_project_context(
            self.project.id, active_planspace_id=keep_lane
        )
        doomed = self._add_node(
            drop_lane, NodeState.VIRTUAL, prompt_draft="will be deleted"
        )
        dependent = self._add_node(
            keep_lane,
            NodeState.VIRTUAL,
            prompt_draft="depends on the doomed node",
            scheduled_deps=[doomed.id],
        )

        deleted, busy = self.registry.delete_planspace(self.project.id, drop_lane)

        self.assertTrue(deleted)
        self.assertEqual(busy, [])
        survivor = self.store.load_node(self.project.id, dependent.id)
        assert survivor is not None
        self.assertEqual(survivor.scheduled_deps, [])

    def test_delete_clears_view_prefs_and_layout_hints(self) -> None:
        keep_lane = self._make_lane("Keep")
        drop_lane = self._make_lane("Drop")
        self.registry.update_project_context(
            self.project.id, active_planspace_id=keep_lane
        )
        node = self._add_node(
            drop_lane, NodeState.VIRTUAL, prompt_draft="placed by the user"
        )
        self.registry.update_planspace_view(
            self.project.id, {drop_lane: {"hidden": True}}
        )
        self.registry.update_layout_hints(
            self.project.id,
            {f"planspace:{drop_lane}": {"x": 10, "y": 20}, node.id: {"x": 1, "y": 2}},
        )

        self.registry.delete_planspace(self.project.id, drop_lane)

        project = self._runtime_project()
        self.assertNotIn(drop_lane, project.planspace_view)
        self.assertNotIn(f"planspace:{drop_lane}", project.layout_hints)
        self.assertNotIn(node.id, project.layout_hints)

    def test_delete_removes_materialized_lane_directory(self) -> None:
        keep_lane = self._make_lane("Keep")
        drop_lane = self._make_lane("Drop")
        self.registry.update_project_context(
            self.project.id, active_planspace_id=keep_lane
        )
        project = self._runtime_project()
        projection = lane_root(project, drop_lane)
        (projection / "nodes" / "abc").mkdir(parents=True, exist_ok=True)
        (projection / "nodes" / "abc" / "preview.json").write_text("{}", "utf-8")
        keep_projection = lane_root(project, keep_lane)
        keep_projection.mkdir(parents=True, exist_ok=True)

        self.registry.delete_planspace(self.project.id, drop_lane)

        self.assertFalse(projection.exists())
        self.assertTrue(keep_projection.exists())

    def test_delete_unknown_planspace_returns_not_found(self) -> None:
        self._make_lane("Only")

        deleted, busy = self.registry.delete_planspace(
            self.project.id, "planspaces.delete-project.missing"
        )

        self.assertFalse(deleted)
        self.assertEqual(busy, [])

    def test_delete_requires_a_planspace_id(self) -> None:
        with self.assertRaises(ValueError):
            self.registry.delete_planspace(self.project.id, "   ")

    def test_delete_removes_workspace_artifacts_of_lane_nodes(self) -> None:
        keep_lane = self._make_lane("Keep")
        drop_lane = self._make_lane("Drop")
        self.registry.update_project_context(
            self.project.id, active_planspace_id=keep_lane
        )
        doomed = self._add_node(drop_lane, NodeState.DONE, prompt="published a report")
        survivor = self._add_node(keep_lane, NodeState.DONE, prompt="still here")
        project = self._runtime_project()
        doomed_outputs = workspace_artifacts_dir(project, doomed.id)
        doomed_outputs.mkdir(parents=True, exist_ok=True)
        (doomed_outputs / "report.md").write_text("declared", encoding="utf-8")
        (doomed_outputs / "scratch.txt").write_text("never declared", encoding="utf-8")
        survivor_outputs = workspace_artifacts_dir(project, survivor.id)
        survivor_outputs.mkdir(parents=True, exist_ok=True)
        (survivor_outputs / "keep.md").write_text("mine", encoding="utf-8")

        deleted, busy = self.registry.delete_planspace(self.project.id, drop_lane)

        self.assertTrue(deleted)
        self.assertEqual(busy, [])
        self.assertFalse(doomed_outputs.exists())
        self.assertTrue((survivor_outputs / "keep.md").is_file())

    def test_delete_tolerates_nodes_without_workspace_artifacts(self) -> None:
        keep_lane = self._make_lane("Keep")
        drop_lane = self._make_lane("Drop")
        self.registry.update_project_context(
            self.project.id, active_planspace_id=keep_lane
        )
        node = self._add_node(drop_lane, NodeState.DONE, prompt="published nothing")
        self.assertFalse(
            workspace_artifacts_dir(self._runtime_project(), node.id).exists()
        )

        deleted, busy = self.registry.delete_planspace(self.project.id, drop_lane)

        self.assertTrue(deleted)
        self.assertEqual(busy, [])

    def test_workspace_artifact_cleanup_stays_inside_outputs_dir(self) -> None:
        project = self._runtime_project()
        outputs_root = Path(project.root_path) / ARTIFACTS_DIRNAME
        outputs_root.mkdir(parents=True, exist_ok=True)
        outsider = Path(project.root_path) / "src"
        outsider.mkdir(parents=True, exist_ok=True)
        (outsider / "main.py").write_text("keep me", encoding="utf-8")
        sibling = Path(self.tmp.name) / "elsewhere"
        sibling.mkdir(parents=True, exist_ok=True)
        (sibling / "secret.txt").write_text("keep me too", encoding="utf-8")

        for malformed in ("../src", "..", ".", "a/b", "..\\src", "", "nested/../../src"):
            self.registry._remove_workspace_artifacts(project, malformed)

        self.assertTrue((outsider / "main.py").is_file())
        self.assertTrue((sibling / "secret.txt").is_file())
        self.assertTrue(outputs_root.is_dir())


class DeletePlanspaceFinalizationTests(unittest.IsolatedAsyncioTestCase):
    """A node whose runner is still finalizing must block the lane delete."""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.store = Store(root=Path(self.tmp.name) / "store")
        self.project = Project(
            root_path=str(Path(self.tmp.name) / "repo"),
            name="finalizing-project",
        )
        Path(self.project.root_path).mkdir(parents=True, exist_ok=True)
        self.store.create_project(self.project)
        self.registry = ProjectRegistry(store=self.store)

    async def asyncTearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        rt = self.registry._runtimes.get(self.project.id)
        if rt is not None:
            rt.closed = True
            await asyncio.gather(
                *list(rt.background_tasks), return_exceptions=True
            )
        self.tmp.cleanup()

    def _make_lane(self, title: str) -> str:
        result = self.registry.create_blank_planspace(
            self.project.id, title=title, seed=title, mode="manual"
        )
        assert result is not None
        return result.node.planspace_id or ""

    def _stub_runner(self, node: Node) -> object:
        class _Stub:
            def __init__(self, n: Node) -> None:
                self.node = n

        return _Stub(node)

    async def test_terminal_node_still_in_runner_tasks_reads_as_busy(self) -> None:
        keep_lane = self._make_lane("Keep")
        drop_lane = self._make_lane("Drop")
        self.registry.update_project_context(
            self.project.id, active_planspace_id=keep_lane
        )
        # A runner persists its terminal state before its final broadcasts, so
        # the store says DONE while rt.runner_tasks still owns the node.
        finishing = Node(
            project_id=self.project.id,
            state=NodeState.DONE,
            planspace_id=drop_lane,
            model_preset_id=self.project.model_preset_id,
            prompt="terminal in the store, runner still winding down",
            started_at=1.0,
            finished_at=2.0,
        )
        self.store.create_node(finishing)
        rt = self.registry._runtimes[self.project.id]
        finalizing = asyncio.get_running_loop().create_future()
        task = asyncio.get_running_loop().create_task(asyncio.wait_for(finalizing, 5))
        rt.runner_tasks[finishing.id] = task
        rt.runners[finishing.id] = self._stub_runner(finishing)  # type: ignore[assignment]

        deleted, busy = self.registry.delete_planspace(self.project.id, drop_lane)

        self.assertFalse(deleted)
        self.assertEqual(busy, [finishing.id])
        self.assertIsNotNone(self.store.load_node(self.project.id, finishing.id))
        summary = describe_project_contextspace(
            self.registry.get_project(self.project.id) or self.project,
            store_root=self.store.root,
        )
        plug_ids = {
            plug["id"]
            for binding in summary["bindings"]
            for plug in binding["plugs"]
        }
        self.assertIn(drop_lane, plug_ids)

        # Once the runner callback drops the task, the same request succeeds.
        finalizing.set_result(None)
        await task
        self.registry._on_runner_done(rt, finishing.id, task)
        self.assertNotIn(finishing.id, rt.runner_tasks)

        deleted, busy = self.registry.delete_planspace(self.project.id, drop_lane)

        self.assertTrue(deleted)
        self.assertEqual(busy, [])
        self.assertIsNone(self.store.load_node(self.project.id, finishing.id))


if __name__ == "__main__":
    unittest.main()
