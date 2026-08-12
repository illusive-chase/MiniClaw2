from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from miniclaw2.app import create_app
from miniclaw2.contextspace import create_planspace
from miniclaw2.domain import Category, Node, NodeKind, NodeState, Project
from miniclaw2.events import TextDelta
from miniclaw2.materialize import materialize_active_lane, runner_lane_root
from miniclaw2.registry import ProjectRegistry
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


class ProjectConcurrencyModelTests(unittest.TestCase):
    def test_default_is_one_and_legacy_record_loads(self) -> None:
        project = Project(root_path="/tmp/project")
        self.assertEqual(project.concurrency, 1)

        legacy = project.model_dump(exclude={"provider", "concurrency"})
        self.assertEqual(Project.model_validate(legacy).concurrency, 1)

    def test_concurrency_must_be_a_positive_integer(self) -> None:
        for value in (0, -1, 1.5, True):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    Project(root_path="/tmp/project", concurrency=value)


class ProjectConcurrencyApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.registry = ProjectRegistry(store=Store(root=Path(self.home.name) / "home"))
        self.client = TestClient(create_app(self.registry))

    def tearDown(self) -> None:
        self.client.close()
        self.home.cleanup()

    def test_create_update_and_persist_concurrency(self) -> None:
        created = self.client.post(
            "/sessions",
            json={"temporary": True, "concurrency": 3},
        )
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertEqual(body["concurrency"], 3)
        self.assertEqual(body["active_count"], 0)
        self.assertEqual(body["queued_count"], 0)

        updated = self.client.patch(
            f"/sessions/{body['id']}/preferences",
            json={"concurrency": 2},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["concurrency"], 2)

        reloaded = ProjectRegistry(store=Store(root=Path(self.home.name) / "home"))
        project = reloaded.get_project(body["id"])
        assert project is not None
        self.assertEqual(project.concurrency, 2)

    def test_api_rejects_non_positive_concurrency(self) -> None:
        created = self.client.post("/sessions", json={"temporary": True})
        sid = created.json()["id"]
        self.assertEqual(
            self.client.patch(
                f"/sessions/{sid}/preferences",
                json={"concurrency": 0},
            ).status_code,
            422,
        )


class _ControlledRunner:
    instances: dict[str, "_ControlledRunner"] = {}

    def __init__(self, node: Node, project: Project, store: Store, on_event, **_: object) -> None:
        self.node = node
        self.project = project
        self.store = store
        self.on_event = on_event
        self.release = asyncio.Event()
        self.interrupted = False
        self.resolved_gates: list[str] = []
        self.__class__.instances[node.id] = self

    async def run(self) -> None:
        self.node.state = NodeState.RUNNING
        self.store.update_node(self.node)
        await self.release.wait()
        self.node.state = NodeState.DONE
        self.store.update_node(self.node)

    async def interrupt(self) -> None:
        self.interrupted = True

    def resolve_gate(self, gate_id: str, **_: object) -> bool:
        self.resolved_gates.append(gate_id)
        return True


class ProjectConcurrencySchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = Store(root=root / "store")
        self.project = Project(root_path=str(root / "repo"), concurrency=2)
        Path(self.project.root_path).mkdir()
        self.store.create_project(self.project)
        self.registry = ProjectRegistry(store=self.store)
        self.lane = create_planspace(
            self.project,
            title="Work",
            mode="manual",
            store_root=self.store.root,
        )
        runtime = self.registry._runtimes[self.project.id]
        runtime.project.active_planspace_id = self.lane
        self.store.update_project(runtime.project)
        _ControlledRunner.instances.clear()
        self.runner_patch = patch("miniclaw2.registry.NodeRunner", _ControlledRunner)
        self.runner_patch.start()

    async def asyncTearDown(self) -> None:
        runtime = self.registry._runtimes.get(self.project.id)
        if runtime is not None:
            runtime.project.settings_override["auto_commit"] = False
            while runtime.runner_tasks:
                for runner in list(runtime.runners.values()):
                    runner.release.set()  # type: ignore[attr-defined]
                await asyncio.gather(
                    *list(runtime.runner_tasks.values()), return_exceptions=True
                )
                await self._settle()
        self.runner_patch.stop()
        self.temp.cleanup()

    def _virtual(self, node_id: str, deps: list[str] | None = None) -> Node:
        node = self.registry.create_virtual(
            self.project.id,
            node_id=node_id,
            prompt_draft=f"run {node_id}",
            scheduled_deps=deps or [],
            model_preset_id="gpt-5.5",
            _allow_compatibility_model_preset=True,
        )
        assert node is not None
        return node

    async def _settle(self) -> None:
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def test_limit_starts_n_and_queues_n_plus_one_then_refills(self) -> None:
        nodes = [self._virtual(f"node-{index}") for index in range(3)]
        promoted = [self.registry.promote_virtual(self.project.id, node.id) for node in nodes]
        self.assertTrue(all(node is not None for node in promoted))
        await self._settle()

        runtime = self.registry._runtimes[self.project.id]
        self.assertEqual(runtime.active_count, 2)
        self.assertEqual(set(runtime.runners), {"node-0", "node-1"})
        self.assertEqual(self.store.load_node(self.project.id, "node-2").state, NodeState.QUEUED)  # type: ignore[union-attr]

        _ControlledRunner.instances["node-0"].release.set()
        await self._settle()
        self.assertNotIn("node-0", runtime.runners)
        self.assertIn("node-2", runtime.runners)
        self.assertIn("node-1", runtime.runners)

    async def test_lowering_does_not_cancel_and_raising_refills(self) -> None:
        nodes = [self._virtual(f"limit-{index}") for index in range(3)]
        for node in nodes:
            self.registry.promote_virtual(self.project.id, node.id)
        await self._settle()

        runtime = self.registry._runtimes[self.project.id]
        self.registry.update_project_preferences(self.project.id, concurrency=1)
        self.assertEqual(runtime.active_count, 2)
        self.assertFalse(any(runner.interrupted for runner in runtime.runners.values()))  # type: ignore[attr-defined]

        self.registry.update_project_preferences(self.project.id, concurrency=3)
        await self._settle()
        self.assertEqual(runtime.active_count, 3)

    async def test_full_project_allows_virtual_create_and_manual_queue(self) -> None:
        first = self._virtual("full-first")
        second = self._virtual("full-second")
        self.registry.promote_virtual(self.project.id, first.id)
        self.registry.promote_virtual(self.project.id, second.id)
        await self._settle()

        third = self._virtual("full-third")
        promoted = self.registry.promote_virtual(self.project.id, third.id)
        self.assertIsNotNone(promoted)
        assert promoted is not None
        self.assertEqual(promoted.state, NodeState.QUEUED)
        self.assertNotIn(third.id, self.registry._runtimes[self.project.id].runners)

    async def test_busy_concierge_waits_until_project_is_idle(self) -> None:
        current = self._virtual("current-work")
        self.registry.promote_virtual(self.project.id, current.id)
        await self._settle()

        result = self.registry.create_planspace_and_launch_concierge(
            self.project.id,
            title="Background direction",
            seed="Plan the next direction",
            mode="manual",
        )
        assert result is not None
        runtime = self.registry._runtimes[self.project.id]
        self.assertFalse(result.activated)
        self.assertNotIn(result.node.id, runtime.runners)
        self.assertIn(result.node.id, runtime.deferred_until_idle_node_ids)

        _ControlledRunner.instances[current.id].release.set()
        await self._settle()

        self.assertIn(result.node.id, runtime.runners)
        self.assertNotIn(result.node.id, runtime.deferred_until_idle_node_ids)

    async def test_dequeue_restores_queued_virtual_intent(self) -> None:
        first = self._virtual("dequeue-first")
        second = self._virtual("dequeue-second")
        self.registry.promote_virtual(self.project.id, first.id)
        self.registry.promote_virtual(self.project.id, second.id)
        await self._settle()

        queued = self._virtual("dequeue-target")
        queued.pending_extra_principles = ["principles.focus"]
        queued.pending_extra_skills = [{"id": "skills.review", "suggest": True}]
        self.store.update_node(queued)
        promoted = self.registry.promote_virtual(self.project.id, queued.id)
        assert promoted is not None
        self.assertEqual(promoted.state, NodeState.QUEUED)

        dequeued = self.registry.dequeue_node(self.project.id, queued.id)
        assert dequeued is not None
        self.assertEqual(dequeued.state, NodeState.VIRTUAL)
        self.assertEqual(dequeued.prompt, "")
        self.assertEqual(dequeued.prompt_draft, "run dequeue-target")
        self.assertEqual(dequeued.pending_extra_principles, ["principles.focus"])
        self.assertEqual(dequeued.pending_extra_skills, [
            {"id": "skills.review", "suggest": True}
        ])
        self.assertNotIn("extra_principles", dequeued.settings_snapshot)
        self.assertNotIn("extra_skills", dequeued.settings_snapshot)
        preview = json.loads(
            self.store.read_node_preview(self.project.id, queued.id) or ""
        )
        self.assertEqual(preview["state"], "virtual")

    async def test_dequeue_rejects_node_already_assigned_to_runner(self) -> None:
        node = self._virtual("dequeue-running")
        self.registry.promote_virtual(self.project.id, node.id)
        self.assertIn(node.id, self.registry._runtimes[self.project.id].runner_tasks)
        self.assertIsNone(self.registry.dequeue_node(self.project.id, node.id))

    async def test_auto_mode_rejects_dequeue_without_repromoting_node(self) -> None:
        first = self._virtual("auto-dequeue-first")
        second = self._virtual("auto-dequeue-second")
        self.registry.promote_virtual(self.project.id, first.id)
        self.registry.promote_virtual(self.project.id, second.id)
        await self._settle()
        self.registry.update_planspace_mode(self.project.id, self.lane, "auto")

        queued = self.registry.create_virtual(
            self.project.id,
            node_id="auto-dequeue-target",
            prompt_draft="keep this queued",
            model_preset_id="gpt-5.5",
            _allow_compatibility_model_preset=True,
        )
        assert queued is not None
        self.assertEqual(queued.state, NodeState.QUEUED)

        self.assertIsNone(self.registry.dequeue_node(self.project.id, queued.id))
        unchanged = self.store.load_node(self.project.id, queued.id)
        assert unchanged is not None
        self.assertEqual(unchanged.state, NodeState.QUEUED)
        self.assertEqual(unchanged.prompt, "keep this queued")
        self.assertIsNone(unchanged.prompt_draft)

    async def test_auto_mode_queues_eligible_virtual_while_full(self) -> None:
        first = self._virtual("auto-first")
        second = self._virtual("auto-second")
        self.registry.promote_virtual(self.project.id, first.id)
        self.registry.promote_virtual(self.project.id, second.id)
        await self._settle()
        self.registry.update_planspace_mode(self.project.id, self.lane, "auto")

        third = self.registry.create_virtual(
            self.project.id,
            node_id="auto-third",
            prompt_draft="run automatically",
            model_preset_id="gpt-5.5",
            _allow_compatibility_model_preset=True,
        )
        assert third is not None
        self.assertEqual(third.state, NodeState.QUEUED)
        self.assertNotIn(third.id, self.registry._runtimes[self.project.id].runners)

        _ControlledRunner.instances[first.id].release.set()
        await self._settle()
        self.assertIn(third.id, self.registry._runtimes[self.project.id].runners)

    async def test_unmet_dependency_does_not_queue(self) -> None:
        parent = self._virtual("dep-parent")
        child = self._virtual("dep-child", deps=[parent.id])
        self.assertIsNone(self.registry.promote_virtual(self.project.id, child.id))
        self.assertEqual(
            self.store.load_node(self.project.id, child.id).state,  # type: ignore[union-attr]
            NodeState.VIRTUAL,
        )

    async def test_interrupt_and_gate_resolution_are_node_scoped(self) -> None:
        nodes = [self._virtual(f"route-{index}") for index in range(2)]
        for node in nodes:
            self.registry.promote_virtual(self.project.id, node.id)
        await self._settle()

        self.assertTrue(self.registry.resolve_gate(self.project.id, "gate-a", node_id="route-0", allow=True))
        self.assertTrue(self.registry.resolve_gate(self.project.id, "gate-b", node_id="route-1", allow=True))
        self.assertEqual(_ControlledRunner.instances["route-0"].resolved_gates, ["gate-a"])
        self.assertEqual(_ControlledRunner.instances["route-1"].resolved_gates, ["gate-b"])

        self.assertTrue(self.registry.interrupt(self.project.id, "route-0"))
        await self._settle()
        self.assertTrue(_ControlledRunner.instances["route-0"].interrupted)
        self.assertFalse(_ControlledRunner.instances["route-1"].interrupted)

    async def test_delete_project_cancels_all_runners(self) -> None:
        nodes = [self._virtual(f"delete-{index}") for index in range(2)]
        for node in nodes:
            self.registry.promote_virtual(self.project.id, node.id)
        await self._settle()
        tasks = list(self.registry._runtimes[self.project.id].runner_tasks.values())

        self.assertTrue(self.registry.delete_project(self.project.id))
        await self._settle()
        self.assertTrue(all(task.cancelled() or task.done() for task in tasks))

    async def test_auto_commit_op_takes_freed_slot_before_queued_work(self) -> None:
        self.registry.update_project_preferences(self.project.id, concurrency=1)
        runtime = self.registry._runtimes[self.project.id]
        runtime.project.settings_override["auto_commit"] = True
        self.store.update_project(runtime.project)
        first = self._virtual("commit-parent")
        waiting = self._virtual("after-commit")
        self.registry.promote_virtual(self.project.id, first.id)
        self.registry.promote_virtual(self.project.id, waiting.id)
        await self._settle()

        _ControlledRunner.instances[first.id].release.set()
        await self._settle()
        active_ids = set(runtime.runners)
        self.assertNotIn(waiting.id, active_ids)
        self.assertEqual(len(active_ids), 1)
        op_id = next(iter(active_ids))
        op_node = self.store.load_node(self.project.id, op_id)
        assert op_node is not None
        self.assertEqual(op_node.kind, NodeKind.OP)
        self.assertEqual(op_node.parent_node_id, first.id)

        _ControlledRunner.instances[op_id].release.set()
        await self._settle()
        self.assertIn(waiting.id, runtime.runners)

    async def test_pull_guard_stops_scheduler_after_launching_pull(self) -> None:
        runtime = self.registry._runtimes[self.project.id]
        pull = Node(
            id="queued-pull",
            project_id=self.project.id,
            kind=NodeKind.OP,
            op_kind="pull",
            state=NodeState.QUEUED,
            model_preset_id="gpt-5.5",
        )
        work = Node(
            id="queued-work",
            project_id=self.project.id,
            state=NodeState.QUEUED,
            model_preset_id="gpt-5.5",
        )
        self.store.create_node(pull)
        self.store.create_node(work)
        runtime.priority_node_ids.extend([pull.id, work.id])

        self.registry._schedule_queued(runtime)
        await self._settle()

        self.assertIn(pull.id, runtime.runners)
        self.assertNotIn(work.id, runtime.runners)

    async def test_git_status_broadcast_is_node_less(self) -> None:
        runtime = self.registry._runtimes[self.project.id]
        events: list[dict[str, object]] = []

        async def observe(event: dict[str, object]) -> None:
            events.append(event)

        token = runtime.add_observer(observe)
        try:
            self.registry._broadcast_git_status(runtime)
            await asyncio.gather(*runtime.background_tasks)
        finally:
            runtime.remove_observer(token)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "git_status")
        self.assertNotIn("node_id", events[0])

    async def test_push_rejects_queued_pull(self) -> None:
        runtime = self.registry._runtimes[self.project.id]
        pull = Node(
            id="push-blocked-pull",
            project_id=self.project.id,
            kind=NodeKind.OP,
            op_kind="pull",
            state=NodeState.QUEUED,
            model_preset_id="gpt-5.5",
        )
        self.store.create_node(pull)

        result = await self.registry.git_push(self.project.id)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result[1], "pull in progress")


class ProjectConcurrencyLegacyStoreTests(unittest.TestCase):
    def test_project_json_without_concurrency_uses_default(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw))
            project = Project(root_path="/tmp/project")
            store.create_project(project)
            path = Path(raw) / "projects" / project.id / "project.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.pop("concurrency")
            path.write_text(json.dumps(payload), encoding="utf-8")

            loaded = Store(root=Path(raw)).list_projects()
            self.assertEqual(loaded[0].concurrency, 1)


class ConcurrentProjectionTests(unittest.TestCase):
    def test_private_lane_materializations_do_not_delete_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = Store(root=root / "store")
            project = Project(root_path=str(root / "repo"))
            Path(project.root_path).mkdir()
            store.create_project(project)
            lane = "planspaces.work"
            first = store.create_node(Node(
                id="first",
                project_id=project.id,
                category=Category.REGULAR,
                state=NodeState.QUEUED,
                planspace_id=lane,
                model_preset_id="gpt-5.5",
            ))
            second = store.create_node(Node(
                id="second",
                project_id=project.id,
                category=Category.REGULAR,
                state=NodeState.QUEUED,
                planspace_id=lane,
                model_preset_id="gpt-5.5",
            ))
            first_root = runner_lane_root(project, first.id, lane)
            second_root = runner_lane_root(project, second.id, lane)
            materialize_active_lane(
                project, lane, store, current_node_id=first.id, target_root=first_root
            )
            marker = first_root / "first-marker"
            marker.write_text("keep", encoding="utf-8")
            materialize_active_lane(
                project, lane, store, current_node_id=second.id, target_root=second_root
            )

            self.assertTrue(marker.exists())
            self.assertTrue(second_root.exists())
            self.assertNotEqual(first_root, second_root)


class ConcurrentEventRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_runner_events_and_replay_keep_node_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = Store(root=root / "store")
            project = Project(root_path=str(root / "repo"), concurrency=2)
            Path(project.root_path).mkdir()
            store.create_project(project)
            broadcasts: list[dict[str, object]] = []

            async def on_event(event: dict[str, object]) -> None:
                broadcasts.append(event)

            nodes = [store.create_node(Node(
                id=f"event-{index}",
                project_id=project.id,
                category=Category.REGULAR,
                state=NodeState.RUNNING,
                model_preset_id="gpt-5.5",
            )) for index in range(2)]
            runners = [NodeRunner(node, project, store, on_event) for node in nodes]

            await asyncio.gather(
                runners[0]._emit(TextDelta(text="first")),
                runners[1]._emit(TextDelta(text="second")),
            )

            self.assertEqual({event["node_id"] for event in broadcasts}, {node.id for node in nodes})
            for node in nodes:
                replay = store.replay_events(project.id, node.id)
                self.assertEqual(len(replay), 1)
                self.assertEqual(replay[0]["event"]["node_id"], node.id)
                self.assertEqual(replay[0]["seq"], 1)
