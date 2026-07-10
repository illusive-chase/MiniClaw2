"""Tests for supervised gate timeouts in the runner."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from miniclaw2 import runner as runner_module
from miniclaw2.contextspace import create_planspace
from miniclaw2.domain import (
    Category,
    GateSubtype,
    Node,
    NodeKind,
    NodeState,
    Project,
)
from miniclaw2.providers import (
    AgentProviderContext,
    AgentProviderEvent,
    GateRequest,
    GateTimeoutError,
)
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)


class _SupervisedGateProvider:
    """Requests a gate with a tiny supervision timeout and never answers it,
    mimicking the Claude ask-gate path when the human walks away."""

    name = "stub"

    def __init__(self) -> None:
        self.interrupted = False
        self.gate_error: Exception | None = None

    async def run(self, context: AgentProviderContext):
        yield AgentProviderEvent(kind="session", session_id="stub-session")
        try:
            await context.request_gate(
                GateRequest(
                    subtype=GateSubtype.ASK_USER,
                    tool_name="AskUserQuestion",
                    tool_input={"questions": []},
                    timeout_seconds=0.05,
                )
            )
        except GateTimeoutError as exc:
            self.gate_error = exc
        yield AgentProviderEvent(
            kind="done",
            final_state="cancelled" if self.interrupted else "done",
        )

    async def interrupt(self) -> None:
        self.interrupted = True


class RunnerGateTimeoutTests(unittest.IsolatedAsyncioTestCase):
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
            self.project, title="gate-lane", mode="manual"
        )
        settings = dict(self.project.settings_override)
        settings["active_planspace_id"] = self.plug_id
        self.project.settings_override = settings
        self.store.update_project(self.project)

    async def asyncTearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    async def test_gate_timeout_interrupts_session_with_honest_error(self) -> None:
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
        emitted: list[dict] = []

        async def on_event(payload: dict) -> None:
            emitted.append(payload)

        provider = _SupervisedGateProvider()
        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            await asyncio.wait_for(runner.run(), timeout=5.0)

        self.assertIsInstance(provider.gate_error, GateTimeoutError)
        self.assertTrue(provider.interrupted)
        self.assertEqual(node.state, NodeState.CANCELLED)
        self.assertIn("timed out", node.error or "")
        self.assertIn("interrupting the session", node.error or "")
        self.assertTrue(
            any(ev.get("type") == "interaction_request" for ev in emitted)
        )
        self.assertTrue(
            any(
                ev.get("type") == "error"
                and "timed out" in ev.get("message", "")
                for ev in emitted
            )
        )
        self.assertEqual(emitted[-1].get("type"), "turn_done")


if __name__ == "__main__":
    unittest.main()
