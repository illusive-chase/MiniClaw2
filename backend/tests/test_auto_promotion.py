"""Tests for the auto-promotion scheduler added in step 5."""

from __future__ import annotations

import asyncio
import os
import subprocess
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
        self.tmp.cleanup()

    def _make_finished_agent(
        self, planspace_id: str, *, finished: bool = True
    ) -> Node:
        node = Node(
            project_id=self.project.id,
            kind=NodeKind.AGENT,
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
        settings = dict(rt.project.settings_override)
        settings["active_planspace_id"] = plug_id
        rt.project.settings_override = settings
        self.store.update_project(rt.project)

        finished = self._make_finished_agent(plug_id)
        virtual = self._make_virtual(plug_id)

        rt.runner = self._stub_runner(finished)  # type: ignore[assignment]
        self.registry._on_runner_done(rt)

        # No new task was spawned: virtual is still virtual.
        self.assertIsNone(rt.runner_task)
        still_virtual = self.store.load_node(self.project.id, virtual.id)
        assert still_virtual is not None
        self.assertEqual(still_virtual.state, NodeState.VIRTUAL)

    async def test_auto_mode_promotes_eligible_virtual(self) -> None:
        plug_id = create_planspace(
            self.project, title="auto", mode="auto"
        )
        rt = self.registry._runtimes[self.project.id]
        settings = dict(rt.project.settings_override)
        settings["active_planspace_id"] = plug_id
        rt.project.settings_override = settings
        self.store.update_project(rt.project)

        finished = self._make_finished_agent(plug_id)
        virtual = self._make_virtual(plug_id, prompt_draft="follow up")

        rt = self.registry._runtimes[self.project.id]
        rt.runner = self._stub_runner(finished)  # type: ignore[assignment]
        # Pre-condition: project not running.
        self.assertFalse(rt.is_running())
        self.registry._on_runner_done(rt)

        # promote_virtual should have transitioned virtual -> queued and
        # spawned a runner task. We immediately cancel to avoid touching
        # the real provider in this unit test.
        self.assertIsNotNone(rt.runner_task)
        assert rt.runner_task is not None
        rt.runner_task.cancel()
        try:
            await rt.runner_task
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

    async def test_auto_mode_skips_virtual_with_unresolved_deps(self) -> None:
        plug_id = create_planspace(
            self.project, title="auto-deps", mode="auto"
        )
        rt = self.registry._runtimes[self.project.id]
        settings = dict(rt.project.settings_override)
        settings["active_planspace_id"] = plug_id
        rt.project.settings_override = settings
        self.store.update_project(rt.project)

        finished = self._make_finished_agent(plug_id)
        gating = self._make_virtual(plug_id, nid="v-gating")
        blocked = self._make_virtual(
            plug_id, nid="v-blocked", deps=["v-gating"]
        )

        rt.runner = self._stub_runner(finished)  # type: ignore[assignment]
        self.registry._on_runner_done(rt)

        # blocked has gating as a dep; gating itself is unsatisfied (it
        # has no deps) so it should be the one promoted, not the blocked.
        await self._drain_task(rt.runner_task)
        gating_now = self.store.load_node(self.project.id, gating.id)
        blocked_now = self.store.load_node(self.project.id, blocked.id)
        assert gating_now is not None and blocked_now is not None
        self.assertNotEqual(gating_now.state, NodeState.VIRTUAL)
        self.assertEqual(blocked_now.state, NodeState.VIRTUAL)

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
