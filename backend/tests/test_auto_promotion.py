"""Tests for the auto-promotion scheduler added in step 5."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from miniclaw2.contextspace import create_planspace
from miniclaw2.domain import (
    Category,
    Node,
    NodeKind,
    NodeState,
    Project,
    ReviewBrief,
    ReviewSubtype,
)
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)


class PromotionCandidateTests(unittest.TestCase):
    """Pure-store tests for _next_promotion_candidate selection logic."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.store_root = Path(self.tmp.name) / "store"
        repo = Path(self.tmp.name) / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        self.store = Store(root=self.store_root)
        self.project = Project(root_path=str(repo))
        self.store.create_project(self.project)
        self.registry = ProjectRegistry(store=self.store)
        self.lane = "planspaces.work"

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def _virtual(
        self,
        *,
        nid: str,
        created_at: float,
        deps: list[str] | None = None,
        obsolete: str | None = None,
    ) -> Node:
        node = Node(
            id=nid,
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            model_preset_id="gpt-5.5",
            category=Category.REGULAR,
            state=NodeState.VIRTUAL,
            planspace_id=self.lane,
            prompt_draft="do thing",
            proposed_by="user",
            scheduled_deps=deps or [],
            obsolete_reason=obsolete,
            created_at=created_at,
            summary="m",
        )
        self.store.create_node(node)
        return node

    def _executed(self, *, nid: str, state: NodeState, created_at: float) -> Node:
        node = Node(
            id=nid,
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            model_preset_id="gpt-5.5",
            category=Category.REGULAR,
            state=state,
            planspace_id=self.lane,
            started_at=created_at,
            finished_at=created_at + 1.0,
            created_at=created_at,
        )
        self.store.create_node(node)
        return node

    def test_returns_none_when_no_virtuals(self) -> None:
        self._executed(nid="ex1", state=NodeState.DONE, created_at=1.0)
        candidate = self.registry._next_promotion_candidate(
            self.project.id, self.lane
        )
        self.assertIsNone(candidate)

    def test_picks_earliest_created_when_all_eligible(self) -> None:
        late = self._virtual(nid="v-late", created_at=5.0)
        early = self._virtual(nid="v-early", created_at=2.0)
        candidate = self.registry._next_promotion_candidate(
            self.project.id, self.lane
        )
        assert candidate is not None
        self.assertEqual(candidate.id, early.id)
        self.assertNotEqual(candidate.id, late.id)

    def test_promptless_code_review_is_eligible(self) -> None:
        review = Node(
            id="v-review",
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            model_preset_id="gpt-5.5",
            category=Category.REVIEW,
            subtype=ReviewSubtype.CODE_REVIEW,
            state=NodeState.VIRTUAL,
            planspace_id=self.lane,
            prompt_draft="",
            proposed_by="planner",
            created_at=1.0,
        )
        self.store.create_node(review)

        candidate = self.registry._next_promotion_candidate(
            self.project.id, self.lane
        )

        assert candidate is not None
        self.assertEqual(candidate.id, review.id)

    def test_skips_obsoleted_virtuals(self) -> None:
        self._virtual(nid="v-obsolete", created_at=1.0, obsolete="not needed")
        candidate = self.registry._next_promotion_candidate(
            self.project.id, self.lane
        )
        self.assertIsNone(candidate)

    def test_skips_virtuals_with_unresolved_deps(self) -> None:
        running_dep = self._executed(
            nid="ex-running", state=NodeState.RUNNING, created_at=1.0
        )
        self._virtual(nid="v-child", created_at=2.0, deps=[running_dep.id])
        candidate = self.registry._next_promotion_candidate(
            self.project.id, self.lane
        )
        self.assertIsNone(candidate)

    def test_eligible_when_deps_terminal(self) -> None:
        done_dep = self._executed(
            nid="ex-done", state=NodeState.DONE, created_at=1.0
        )
        child = self._virtual(nid="v-child", created_at=2.0, deps=[done_dep.id])
        candidate = self.registry._next_promotion_candidate(
            self.project.id, self.lane
        )
        assert candidate is not None
        self.assertEqual(candidate.id, child.id)

    def test_auto_candidate_waits_when_dep_failed_or_cancelled(self) -> None:
        for state in (NodeState.ERROR, NodeState.CANCELLED):
            with self.subTest(state=state):
                tmp = tempfile.TemporaryDirectory()
                try:
                    store = Store(root=Path(tmp.name) / "store")
                    project = Project(root_path=str(Path(tmp.name) / "repo"))
                    store.create_project(project)
                    registry = ProjectRegistry(store=store)
                    failed_dep = Node(
                        id=f"dep-{state.value}",
                        project_id=project.id,
                        kind=NodeKind.AGENT,
                        model_preset_id="gpt-5.5",
                        category=Category.REVIEW,
                        subtype=ReviewSubtype.AGENTIC_REVIEW,
                        brief=ReviewBrief(
                            check_what="check",
                            expected="pass",
                            abnormal="fail",
                        ),
                        state=state,
                        planspace_id=self.lane,
                        started_at=1.0,
                        finished_at=2.0,
                        created_at=1.0,
                    )
                    store.create_node(failed_dep)
                    child = Node(
                        id=f"child-{state.value}",
                        project_id=project.id,
                        kind=NodeKind.AGENT,
                        model_preset_id="gpt-5.5",
                        category=Category.REGULAR,
                        state=NodeState.VIRTUAL,
                        planspace_id=self.lane,
                        prompt_draft="follow up",
                        proposed_by="user",
                        scheduled_deps=[failed_dep.id],
                        created_at=2.0,
                        summary="m",
                    )
                    store.create_node(child)

                    candidate = registry._next_promotion_candidate(
                        project.id, self.lane
                    )
                    self.assertIsNone(candidate)
                finally:
                    tmp.cleanup()

    def test_obsoleted_dep_counts_as_terminal(self) -> None:
        self._virtual(nid="v-parent", created_at=1.0, obsolete="abandoned")
        child = self._virtual(
            nid="v-child", created_at=2.0, deps=["v-parent"]
        )
        candidate = self.registry._next_promotion_candidate(
            self.project.id, self.lane
        )
        assert candidate is not None
        self.assertEqual(candidate.id, child.id)


class AutoPromoteOnRunnerDoneTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end: auto mode promotes when a node finishes; manual does not."""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.store_root = Path(self.tmp.name) / "store"
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        _init_repo(self.repo)
        self.store = Store(root=self.store_root)
        self.project = Project(root_path=str(self.repo))
        self.store.create_project(self.project)
        self.registry = ProjectRegistry(store=self.store)

    async def asyncTearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        runtime = self.registry._runtimes.get(self.project.id)
        if runtime is not None:
            runtime.closed = True
            runner_tasks = list(runtime.runner_tasks.values())
            for task in runner_tasks:
                task.cancel()
            await asyncio.gather(*runner_tasks, return_exceptions=True)
            await asyncio.gather(
                *list(runtime.background_tasks), return_exceptions=True
            )
        self.tmp.cleanup()

    def _make_finished_agent(
        self, planspace_id: str, *, finished: bool = True
    ) -> Node:
        node = Node(
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            model_preset_id="gpt-5.5",
            category=Category.REGULAR,
            state=NodeState.DONE if finished else NodeState.RUNNING,
            planspace_id=planspace_id,
            started_at=1.0,
            finished_at=2.0 if finished else None,
        )
        self.store.create_node(node)
        return node

    def _make_virtual(
        self,
        planspace_id: str,
        *,
        nid: str = "",
        deps: list[str] | None = None,
        prompt_draft: str = "go",
    ) -> Node:
        kwargs: dict[str, object] = dict(
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            model_preset_id="gpt-5.5",
            category=Category.REGULAR,
            state=NodeState.VIRTUAL,
            planspace_id=planspace_id,
            prompt_draft=prompt_draft,
            proposed_by="user",
            scheduled_deps=deps or [],
            summary="m",
        )
        if nid:
            kwargs["id"] = nid
        node = Node(**kwargs)  # type: ignore[arg-type]
        self.store.create_node(node)
        return node

    def _stub_runner(self, node: Node) -> object:
        class _Stub:
            def __init__(self, n: Node) -> None:
                self.node = n
        return _Stub(node)

    async def test_manual_mode_does_not_auto_promote(self) -> None:
        plug_id = create_planspace(
            self.project, title="manual", mode="manual"
        )
        rt = self.registry._runtimes[self.project.id]
        self.store.update_project(rt.project)

        finished = self._make_finished_agent(plug_id)
        virtual = self._make_virtual(plug_id)

        rt.runners[finished.id] = self._stub_runner(finished)  # type: ignore[assignment]
        self.registry._on_runner_done(rt, finished.id)

        # No new task was spawned: virtual is still virtual.
        self.assertEqual(rt.runner_tasks, {})
        still_virtual = self.store.load_node(self.project.id, virtual.id)
        assert still_virtual is not None
        self.assertEqual(still_virtual.state, NodeState.VIRTUAL)

    async def test_auto_mode_promotes_eligible_virtual(self) -> None:
        plug_id = create_planspace(
            self.project, title="auto", mode="auto"
        )
        rt = self.registry._runtimes[self.project.id]
        self.store.update_project(rt.project)

        finished = self._make_finished_agent(plug_id)
        virtual = self._make_virtual(plug_id, prompt_draft="follow up")

        rt = self.registry._runtimes[self.project.id]
        rt.runners[finished.id] = self._stub_runner(finished)  # type: ignore[assignment]
        # Pre-condition: project not running.
        self.assertFalse(rt.is_running())
        self.registry._on_runner_done(rt, finished.id)

        # promote_virtual should have transitioned virtual -> queued and
        # spawned a runner task. We immediately cancel to avoid touching
        # the real provider in this unit test.
        task = rt.runner_tasks.get(virtual.id)
        self.assertIsNotNone(task)
        assert task is not None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except BaseException:
            # The runner may raise from inside the cancellation path; we
            # only care that *something* was launched.
            pass

        reloaded = self.store.load_node(self.project.id, virtual.id)
        assert reloaded is not None
        self.assertNotEqual(reloaded.state, NodeState.VIRTUAL)
        self.assertEqual(reloaded.prompt, "follow up")

    async def test_parallel_promotion_across_lanes_is_not_serialized(self) -> None:
        """Two manual lanes each promote; neither blocks the other.

        The Phase 2 safety net for removing the Promote gate: lanes are
        limited by project concurrency, not by taking turns being "active".
        """
        lane_a = create_planspace(self.project, title="a", mode="manual")
        lane_b = create_planspace(self.project, title="b", mode="manual")
        rt = self.registry._runtimes[self.project.id]
        # Enough capacity for both so a queue limit cannot mask serialization.
        rt.project.concurrency = 2
        self.store.update_project(rt.project)
        virtual_a = self._make_virtual(lane_a, prompt_draft="lane a work")
        virtual_b = self._make_virtual(lane_b, prompt_draft="lane b work")

        with patch.object(self.registry, "_launch_node", return_value=None):
            result_a = self.registry.promote_virtual(self.project.id, virtual_a.id)
            result_b = self.registry.promote_virtual(self.project.id, virtual_b.id)

        self.assertIsNotNone(result_a)
        self.assertIsNotNone(result_b)
        for virtual, lane in ((virtual_a, lane_a), (virtual_b, lane_b)):
            reloaded = self.store.load_node(self.project.id, virtual.id)
            assert reloaded is not None
            self.assertEqual(reloaded.state, NodeState.QUEUED)
            self.assertEqual(reloaded.planspace_id, lane)

    async def test_each_lane_launches_with_its_own_context_snapshot(self) -> None:
        """Removing the gate must not remove per-lane context isolation.

        Each launched node's snapshot must name its own lane. If these ever
        collide, two directions are sharing one context — the exact failure
        the Promote gate used to mask.
        """
        lane_a = create_planspace(self.project, title="ctx-a", mode="manual")
        lane_b = create_planspace(self.project, title="ctx-b", mode="manual")
        rt = self.registry._runtimes[self.project.id]
        rt.project.concurrency = 2
        self.store.update_project(rt.project)
        virtual_a = self._make_virtual(lane_a, prompt_draft="a")
        virtual_b = self._make_virtual(lane_b, prompt_draft="b")

        snapshots: dict[str, str | None] = {}

        def _capture(runtime: object, node: Node, *args: object, **kwargs: object):
            # Mirrors runner.py's own rule: the snapshot records the node's
            # own lane, under the frozen `active_planspace_id` key.
            snapshots[node.id] = node.planspace_id
            # Register the "launch" so the scheduler moves on to the next
            # queued node instead of retrying this one.
            done = asyncio.get_running_loop().create_future()
            done.set_result(None)
            runtime.runner_tasks[node.id] = done  # type: ignore[attr-defined]
            return self._stub_runner(node)

        with patch.object(self.registry, "_launch_node", side_effect=_capture):
            self.registry.promote_virtual(self.project.id, virtual_a.id)
            self.registry.promote_virtual(self.project.id, virtual_b.id)

        self.assertEqual(snapshots.get(virtual_a.id), lane_a)
        self.assertEqual(snapshots.get(virtual_b.id), lane_b)
        self.assertNotEqual(snapshots[virtual_a.id], snapshots[virtual_b.id])

    async def test_auto_lanes_advance_without_being_focused(self) -> None:
        """Every auto lane is autonomous, including ones nobody is viewing."""
        create_planspace(self.project, title="other-manual", mode="manual")
        lane_auto = create_planspace(self.project, title="elsewhere", mode="auto")
        rt = self.registry._runtimes[self.project.id]
        self.store.update_project(rt.project)
        virtual = self._make_virtual(lane_auto, prompt_draft="auto work")

        with patch.object(self.registry, "_launch_node", return_value=None):
            self.registry._auto_promote_eligible_virtuals(rt)

        reloaded = self.store.load_node(self.project.id, virtual.id)
        assert reloaded is not None
        self.assertEqual(reloaded.state, NodeState.QUEUED)

    async def test_enabling_auto_mode_promotes_existing_eligible_virtual(self) -> None:
        plug_id = create_planspace(
            self.project, title="manual-first", mode="manual"
        )
        rt = self.registry._runtimes[self.project.id]
        self.store.update_project(rt.project)
        virtual = self._make_virtual(plug_id, prompt_draft="already ready")

        mode = self.registry.update_planspace_mode(
            self.project.id,
            plug_id,
            "auto",
        )

        self.assertEqual(mode, "auto")
        self.assertIn(virtual.id, rt.runner_tasks)
        await self._drain_task(rt.runner_tasks.get(virtual.id))
        reloaded = self.store.load_node(self.project.id, virtual.id)
        assert reloaded is not None
        self.assertNotEqual(reloaded.state, NodeState.VIRTUAL)
        self.assertEqual(reloaded.prompt, "already ready")

    async def test_promote_retry_after_manual_to_auto_switch_is_idempotent(self) -> None:
        plug_id = create_planspace(
            self.project, title="manual-first", mode="manual"
        )
        rt = self.registry._runtimes[self.project.id]
        self.store.update_project(rt.project)
        finished = self._make_finished_agent(plug_id)
        virtual = self._make_virtual(
            plug_id,
            deps=[finished.id],
            prompt_draft="already eligible",
        )

        self.registry.update_planspace_mode(self.project.id, plug_id, "auto")
        retry = self.registry.promote_virtual_result(self.project.id, virtual.id)

        self.assertIsNotNone(retry.node)
        self.assertEqual(retry.code, "already_promoted")
        assert retry.node is not None
        self.assertNotEqual(retry.node.state, NodeState.VIRTUAL)
        await self._drain_task(rt.runner_tasks.get(virtual.id))

    async def test_promotion_result_identifies_non_terminal_dependencies(self) -> None:
        plug_id = create_planspace(
            self.project, title="manual", mode="manual"
        )
        rt = self.registry._runtimes[self.project.id]
        self.store.update_project(rt.project)
        running = self._make_finished_agent(plug_id, finished=False)
        virtual = self._make_virtual(plug_id, deps=[running.id])

        result = self.registry.promote_virtual_result(self.project.id, virtual.id)

        self.assertIsNone(result.node)
        self.assertEqual(result.code, "dependencies_not_terminal")
        self.assertEqual(result.blockers, (running.id,))

    async def test_promote_virtual_succeeds_in_any_manual_lane(self) -> None:
        """Promotion is decided by the node's own lane, and nothing else.

        Semantics reversed by the focus refactor: there is no longer a
        global cursor a lane could fail to be, so a virtual in any manual
        lane is promotable, including one in a lane nobody is viewing.
        """
        create_planspace(self.project, title="elsewhere", mode="manual")
        other_lane = create_planspace(
            self.project, title="other", mode="manual"
        )
        rt = self.registry._runtimes[self.project.id]
        self.store.update_project(rt.project)
        virtual = self._make_virtual(other_lane, prompt_draft="other lane")

        runner = self.registry.promote_virtual(self.project.id, virtual.id)

        self.assertIsNotNone(runner)
        reloaded = self.store.load_node(self.project.id, virtual.id)
        assert reloaded is not None
        self.assertEqual(reloaded.state, NodeState.QUEUED)
        self.assertEqual(reloaded.planspace_id, other_lane)

    async def test_start_node_rejects_a_lane_outside_the_project(self) -> None:
        """``start_node`` takes the lane from its caller, so it must check it.

        With the cursor fallback gone, an unlaned node is a legitimate
        outcome, but a named lane the project's binding cannot reach is
        not: the node would run against a lane no projection or reap step
        can resolve.
        """
        own_lane = create_planspace(self.project, title="mine", mode="manual")
        rt = self.registry._runtimes[self.project.id]
        self.store.update_project(rt.project)

        with patch.object(self.registry, "_schedule_queued"):
            before = len(self.store.list_nodes(self.project.id))
            with self.assertRaisesRegex(ValueError, "unknown planspace"):
                self.registry.start_node(
                    self.project.id,
                    "work",
                    planspace_id="planspaces.other-project.lane",
                )
            self.assertEqual(
                len(self.store.list_nodes(self.project.id)), before
            )

            # The project's own lane still passes through untouched, and
            # naming no lane at all stays legitimate.
            laned = self.registry.start_node(
                self.project.id, "work", planspace_id=own_lane
            )
            assert laned is not None
            self.assertEqual(laned.planspace_id, own_lane)
            unlaned = self.registry.start_node(self.project.id, "work")
            assert unlaned is not None
            self.assertIsNone(unlaned.planspace_id)

    async def test_promote_virtual_preserves_virtual_preview(self) -> None:
        plug_id = create_planspace(
            self.project, title="active", mode="manual"
        )
        rt = self.registry._runtimes[self.project.id]
        self.store.update_project(rt.project)
        parent = self._make_finished_agent(plug_id)
        virtual = self._make_virtual(
            plug_id,
            deps=[parent.id],
            prompt_draft="review parent",
        )

        runner = self.registry.promote_virtual(self.project.id, virtual.id)

        self.assertIsNotNone(runner)
        await self._drain_task(rt.runner_tasks.get(virtual.id))
        preview_text = self.store.read_node_preview(self.project.id, virtual.id)
        self.assertIsNotNone(preview_text)
        assert preview_text is not None
        self.assertIn(parent.id, preview_text)
        reloaded = self.store.load_node(self.project.id, virtual.id)
        assert reloaded is not None
        self.assertEqual(reloaded.prompt, "review parent")
        self.assertIsNone(reloaded.prompt_draft)

    async def test_auto_mode_skips_virtual_with_unresolved_deps(self) -> None:
        plug_id = create_planspace(
            self.project, title="auto-deps", mode="auto"
        )
        rt = self.registry._runtimes[self.project.id]
        self.store.update_project(rt.project)

        finished = self._make_finished_agent(plug_id)
        gating = self._make_virtual(plug_id, nid="v-gating")
        blocked = self._make_virtual(
            plug_id, nid="v-blocked", deps=["v-gating"]
        )

        rt.runners[finished.id] = self._stub_runner(finished)  # type: ignore[assignment]
        self.registry._on_runner_done(rt, finished.id)

        # blocked has gating as a dep; gating itself is unsatisfied (it
        # has no deps) so it should be the one promoted, not the blocked.
        await self._drain_task(rt.runner_tasks.get(gating.id))
        gating_now = self.store.load_node(self.project.id, gating.id)
        blocked_now = self.store.load_node(self.project.id, blocked.id)
        assert gating_now is not None and blocked_now is not None
        self.assertNotEqual(gating_now.state, NodeState.VIRTUAL)
        self.assertEqual(blocked_now.state, NodeState.VIRTUAL)

    async def test_editing_virtual_triggers_auto_promotion_when_eligible(self) -> None:
        plug_id = create_planspace(
            self.project, title="auto-edit", mode="auto"
        )
        rt = self.registry._runtimes[self.project.id]
        self.store.update_project(rt.project)

        blocking = self._make_finished_agent(plug_id, finished=False)
        virtual = self._make_virtual(
            plug_id,
            nid="v-edited",
            deps=[blocking.id],
            prompt_draft="run after edit",
        )

        updated = self.registry.update_virtual(
            self.project.id,
            virtual.id,
            scheduled_deps=[],
        )

        self.assertIsNotNone(updated)
        self.assertIn(virtual.id, rt.runner_tasks)
        await self._drain_task(rt.runner_tasks.get(virtual.id))
        reloaded = self.store.load_node(self.project.id, virtual.id)
        assert reloaded is not None
        self.assertNotEqual(reloaded.state, NodeState.VIRTUAL)
        self.assertEqual(reloaded.prompt, "run after edit")

    async def _drain_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None:
            return
        task.cancel()
        try:
            await task
        except BaseException:
            return


if __name__ == "__main__":
    unittest.main()
