"""Tests for runner-owned inline preview repair retries."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from miniclaw2 import runner as runner_module
from miniclaw2.contextspace import create_planspace
from miniclaw2.domain import Category, Node, NodeKind, NodeState, Project
from miniclaw2.providers import AgentProviderContext, AgentProviderEvent
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)


class _RepairProvider:
    name = "stub"

    def __init__(self, *, repair_succeeds: bool) -> None:
        self.repair_succeeds = repair_succeeds
        self.prompts: list[str] = []

    async def run(self, context: AgentProviderContext):
        self.prompts.append(context.node.prompt)
        if len(self.prompts) > 1 and self.repair_succeeds:
            _write_own_preview(context)
        yield AgentProviderEvent(kind="session", session_id="stub-session")
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class _CancellingRepairProvider:
    name = "stub"

    def __init__(self, task_getter) -> None:
        self.task_getter = task_getter
        self.prompts: list[str] = []

    async def run(self, context: AgentProviderContext):
        self.prompts.append(context.node.prompt)
        yield AgentProviderEvent(kind="session", session_id="stub-session")
        if len(self.prompts) > 1:
            self.task_getter().cancel()
            await asyncio.sleep(3600)
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class _BareProvider:
    name = "stub"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def run(self, context: AgentProviderContext):
        self.prompts.append(context.node.prompt)
        yield AgentProviderEvent(kind="session", session_id="stub-session")

    async def interrupt(self) -> None:
        return None


def _write_own_preview(context: AgentProviderContext) -> None:
    node = context.node
    lane = node.planspace_id or ""
    path = (
        Path(context.project.root_path)
        / ".miniclaw2"
        / "graph"
        / "lanes"
        / lane
        / "nodes"
        / node.id
        / "preview.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": node.id,
                "kind": "agent",
                "category": "regular",
                "state": "done",
                "ran_at": "2026-06-15T00:00:00+00:00",
                "lane": lane,
                "motivation": "m",
                "summary": "repaired",
                "next_implications": "none",
            }
        ),
        encoding="utf-8",
    )


class RunnerPreviewRepairTests(unittest.IsolatedAsyncioTestCase):
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
        self.plug_id = create_planspace(
            self.project, title="repair-lane", mode="manual"
        )
        settings = dict(self.project.settings_override)
        settings["active_planspace_id"] = self.plug_id
        self.project.settings_override = settings
        self.store.update_project(self.project)

    async def asyncTearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def _node(self) -> Node:
        node = Node(
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            model_preset_id="opus-4-7",
            category=Category.REGULAR,
            state=NodeState.QUEUED,
            provider="claude",
            prompt="do work",
        )
        self.store.create_node(node)
        return node

    async def test_missing_preview_is_reprompted_and_can_repair(self) -> None:
        node = self._node()
        emitted: list[dict] = []

        async def on_event(payload: dict) -> None:
            emitted.append(payload)

        provider = _RepairProvider(repair_succeeds=True)
        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            await asyncio.wait_for(runner.run(), timeout=5.0)

        self.assertEqual(node.state, NodeState.DONE)
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("Repair attempt 1 of 3", provider.prompts[1])
        preview = self.store.read_node_preview(self.project.id, node.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn("repaired", preview)
        repair_events = [
            ev for ev in emitted
            if ev.get("type") == "activity"
            and ev.get("kind") == "agent"
            and ev.get("status") == "progress"
            and ev.get("name") == "Preview contract repair"
        ]
        self.assertEqual(len(repair_events), 1)
        self.assertFalse(
            any(
                ev.get("type") == "error"
                and "Preview contract repair" in ev.get("message", "")
                for ev in emitted
            )
        )

    async def test_missing_preview_stubs_after_three_failed_repairs(self) -> None:
        node = self._node()

        async def on_event(_payload: dict) -> None:
            return None

        provider = _RepairProvider(repair_succeeds=False)
        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            await asyncio.wait_for(runner.run(), timeout=5.0)

        self.assertEqual(node.state, NodeState.ERROR)
        self.assertEqual(len(provider.prompts), 4)
        preview = self.store.read_node_preview(self.project.id, node.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn("preview contract abandoned", preview)

    async def test_cancellation_during_repair_finalizes_cancelled_stub(self) -> None:
        node = self._node()
        emitted: list[dict] = []

        async def on_event(payload: dict) -> None:
            emitted.append(payload)

        task: asyncio.Task[None] | None = None

        def task_getter() -> asyncio.Task[None]:
            assert task is not None
            return task

        provider = _CancellingRepairProvider(task_getter)
        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            task = asyncio.create_task(runner.run())
            await asyncio.wait_for(task, timeout=5.0)

        self.assertEqual(node.state, NodeState.CANCELLED)
        self.assertIsNotNone(node.finished_at)
        self.assertEqual(len(provider.prompts), 2)
        self.assertEqual(emitted[-1].get("type"), "turn_done")
        preview = self.store.read_node_preview(self.project.id, node.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn('"state": "cancelled"', preview)
        self.assertIn("preview repair cancelled", preview)

    async def test_provider_stream_exhaustion_without_terminal_event_errors(self) -> None:
        node = self._node()
        emitted: list[dict] = []

        async def on_event(payload: dict) -> None:
            emitted.append(payload)

        provider = _BareProvider()
        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            await asyncio.wait_for(runner.run(), timeout=5.0)

        self.assertEqual(node.state, NodeState.ERROR)
        self.assertEqual(len(provider.prompts), 1)
        self.assertIn("without a terminal event", node.error or "")
        self.assertTrue(
            any(
                ev.get("type") == "error"
                and "without a terminal event" in ev.get("message", "")
                for ev in emitted
            )
        )
        preview = self.store.read_node_preview(self.project.id, node.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn('"state": "error"', preview)

    async def test_stale_active_planspace_errors_before_provider_launch(self) -> None:
        settings = dict(self.project.settings_override)
        settings["active_planspace_id"] = "planspaces.deleted"
        self.project.settings_override = settings
        self.store.update_project(self.project)
        node = self._node()
        emitted: list[dict] = []

        async def on_event(payload: dict) -> None:
            emitted.append(payload)

        provider = _RepairProvider(repair_succeeds=True)
        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            await asyncio.wait_for(runner.run(), timeout=5.0)

        self.assertEqual(provider.prompts, [])
        self.assertEqual(node.state, NodeState.ERROR)
        self.assertIn("Stale launch settings", node.error or "")
        self.assertIn("active_planspace_id", node.error or "")
        preview = self.store.read_node_preview(self.project.id, node.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn('"state": "error"', preview)
        self.assertIn("planspaces.deleted", preview)
        self.assertTrue(
            any(
                ev.get("type") == "error"
                and "active_planspace_id" in ev.get("message", "")
                for ev in emitted
            )
        )
        self.assertEqual(emitted[-1].get("type"), "turn_done")


if __name__ == "__main__":
    unittest.main()
