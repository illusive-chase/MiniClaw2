"""Tests for the concierge bootstrap launcher added in step 8."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from miniclaw2 import runner as runner_module
from miniclaw2.domain import Category, NodeKind, NodeState, Project
from miniclaw2.providers import AgentProviderContext, AgentProviderEvent
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)


class _StubProvider:
    name = "stub"

    def __init__(self) -> None:
        self.last_context: AgentProviderContext | None = None

    async def run(self, context: AgentProviderContext):
        self.last_context = context
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class ConciergeBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.store_root = Path(self.tmp.name) / "store"
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        _init_repo(self.repo)
        self.store = Store(root=self.store_root)
        project = Project(root_path=str(self.repo), name="auth-flow")
        self.store.create_project(project)
        self.registry = ProjectRegistry(store=self.store)
        self.project = self.registry.get_project(project.id)
        assert self.project is not None
        self.pid = self.project.id

    async def asyncTearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    async def test_creates_planspace_activates_and_launches_planning_agent(self) -> None:
        stub = _StubProvider()
        with patch.object(runner_module, "_make_provider", return_value=stub):
            runner = self.registry.create_planspace_and_launch_concierge(
                self.pid,
                title="Auth flow",
                seed="Build a signup screen wired to Stripe",
                mode="manual",
            )
            self.assertIsNotNone(runner)
            assert runner is not None
            rt = self.registry._runtimes[self.pid]
            await asyncio.wait_for(rt.runner_task, timeout=5.0)

        # Planspace plug exists.
        ctx_root = Path(os.environ["MINICLAW_CONTEXT_HOME"])
        manifest = ctx_root / "plugs" / "planspaces" / "auth-flow" / "manifest.yaml"
        self.assertTrue(manifest.exists())

        # Project's active planspace updated.
        refreshed = self.registry.get_project(self.pid)
        assert refreshed is not None
        self.assertEqual(
            refreshed.settings_override.get("active_planspace_id"),
            "planspaces.auth-flow",
        )

        # The launched node is a planning agent with the right lane.
        node = runner.node
        self.assertEqual(node.kind, NodeKind.AGENT)
        self.assertEqual(node.category, Category.PLANNING)
        self.assertEqual(node.planspace_id, "planspaces.auth-flow")
        # Prompt contains the seed and the bootstrap header.
        self.assertIn(
            "Build a signup screen wired to Stripe", node.prompt
        )
        self.assertIn("Direction concierge bootstrap", node.prompt)
        # Provider was actually invoked.
        self.assertIsNotNone(stub.last_context)
        # Planning category came through to the launch instructions.
        ctx = stub.last_context
        assert ctx is not None
        self.assertIn("planning", ctx.launch_instructions.lower())

    async def test_empty_seed_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.registry.create_planspace_and_launch_concierge(
                self.pid,
                title="",
                seed="   ",
                mode="manual",
            )

    async def test_unknown_mode_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.registry.create_planspace_and_launch_concierge(
                self.pid,
                title="title",
                seed="seed",
                mode="weird",
            )

    async def test_returns_none_when_project_busy(self) -> None:
        rt = self.registry._runtimes[self.pid]

        async def _hold() -> None:
            await asyncio.sleep(0.5)

        rt.runner_task = asyncio.create_task(_hold())
        try:
            runner = self.registry.create_planspace_and_launch_concierge(
                self.pid,
                title="t",
                seed="s",
                mode="manual",
            )
            self.assertIsNone(runner)
        finally:
            rt.runner_task.cancel()
            try:
                await rt.runner_task
            except BaseException:
                pass


if __name__ == "__main__":
    unittest.main()
