"""Tests for globally configured tool-request timeouts in the runner."""

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
from miniclaw2.global_config import load_global_config, save_global_config
from miniclaw2.providers import (
    AgentProviderContext,
    AgentProviderEvent,
    GateRequest,
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
    """Requests a gate with a tiny timeout and records the runner response."""

    name = "stub"

    def __init__(self, subtype: GateSubtype = GateSubtype.PERMISSION) -> None:
        self.interrupted = False
        self.subtype = subtype
        self.response: dict | None = None

    async def run(self, context: AgentProviderContext):
        yield AgentProviderEvent(kind="session", session_id="stub-session")
        self.response = await context.request_gate(
            GateRequest(
                subtype=self.subtype,
                tool_name=(
                    "AskUserQuestion"
                    if self.subtype is GateSubtype.ASK_USER
                    else "commandExecution"
                ),
                tool_input={"questions": []},
            )
        )
        yield AgentProviderEvent(kind="done")

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
        config = load_global_config(self.store_root)
        save_global_config(
            config.model_copy(
                update={
                    "tool_requests": config.tool_requests.model_copy(
                        update={"timeout_seconds": 1}
                    )
                }
            ),
            self.store_root,
        )
        self.project = Project(root_path=str(self.repo))
        self.store.create_project(self.project)
        self.plug_id = create_planspace(
            self.project, title="gate-lane", mode="manual"
        )
        self.project.active_planspace_id = self.plug_id
        self.store.update_project(self.project)

    async def asyncTearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    async def _run_provider(
        self,
        provider: _SupervisedGateProvider,
        *,
        delayed_response: tuple[float, bool] | None = None,
    ) -> tuple[Node, list[dict]]:
        node = Node(
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            model_preset_id="opus-4-7",
            category=Category.REGULAR,
            state=NodeState.QUEUED,
            prompt="do work",
        )
        self.store.create_node(node)
        emitted: list[dict] = []

        async def on_event(payload: dict) -> None:
            emitted.append(payload)
            if payload.get("type") == "interaction_request" and delayed_response:
                delay, allow = delayed_response
                asyncio.get_running_loop().call_later(
                    delay,
                    lambda gate_id=payload["id"], allowed=allow: runner.resolve_gate(
                        gate_id, allow=allowed
                    ),
                )

        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            await asyncio.wait_for(runner.run(), timeout=5.0)
        return node, emitted

    async def test_permission_timeout_automatically_accepts_by_default(self) -> None:
        provider = _SupervisedGateProvider()

        node, emitted = await self._run_provider(provider)

        self.assertFalse(provider.interrupted)
        self.assertEqual(provider.response["allow"], True)  # type: ignore[index]
        self.assertIn(
            "automatically accepted",
            provider.response["message"],  # type: ignore[index]
        )
        self.assertEqual(node.state, NodeState.DONE)
        self.assertIsNone(node.error)
        self.assertTrue(
            any(ev.get("type") == "interaction_request" for ev in emitted)
        )
        self.assertEqual(emitted[-1].get("type"), "turn_done")

    async def test_permission_timeout_can_automatically_reject(self) -> None:
        config = load_global_config(self.store_root)
        save_global_config(
            config.model_copy(
                update={
                    "tool_requests": config.tool_requests.model_copy(
                        update={"timeout_action": "reject"}
                    )
                }
            ),
            self.store_root,
        )
        provider = _SupervisedGateProvider()

        node, _emitted = await self._run_provider(provider)

        self.assertEqual(provider.response["allow"], False)  # type: ignore[index]
        self.assertIn(
            "automatically rejected",
            provider.response["message"],  # type: ignore[index]
        )
        self.assertEqual(node.state, NodeState.DONE)

    async def test_ask_user_waits_for_the_user_instead_of_tool_timeout(self) -> None:
        provider = _SupervisedGateProvider(GateSubtype.ASK_USER)

        node, _emitted = await self._run_provider(
            provider,
            delayed_response=(0.1, False),
        )

        self.assertEqual(provider.response["allow"], False)  # type: ignore[index]
        self.assertNotIn("timed out", provider.response.get("message", ""))  # type: ignore[union-attr]
        self.assertEqual(node.state, NodeState.DONE)


if __name__ == "__main__":
    unittest.main()
