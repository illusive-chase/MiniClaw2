from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from miniclaw2 import runner as runner_module
from miniclaw2.domain import Node, NodeState, Project
from miniclaw2.providers import AgentProviderContext, AgentProviderEvent
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


class _RateLimitedProvider:
    name = "codex"

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.prompts: list[str] = []

    async def run(self, context: AgentProviderContext):
        self.prompts.append(context.node.prompt)
        yield AgentProviderEvent(kind="session", session_id="thread-1")
        if len(self.prompts) <= self.failures:
            yield AgentProviderEvent(
                kind="rate_limit",
                error="quota reached",
                rate_limit_kind="usageLimitExceeded",
                rate_limit_resets_at=2_000_000_000,
                rate_limit_observed_at=1_900_000_000,
            )
        else:
            yield AgentProviderEvent(kind="done")

    async def interrupt(self) -> None:
        return None


class CodexRateLimitRetryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        workspace = root / "workspace"
        workspace.mkdir()
        self.store = Store(root / "store")
        self.project = self.store.create_project(Project(root_path=str(workspace)))
        self.node = self.store.create_node(
            Node(
                project_id=self.project.id,
                state=NodeState.RUNNING,
                model_preset_id="gpt-5.6",
                settings_snapshot={"codex_rate_limit_auto_retry": True},
            )
        )
        self.emitted: list[dict] = []

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    async def test_retries_three_times_with_configured_schedule(self) -> None:
        self.assertEqual(
            runner_module._CODEX_RATE_LIMIT_RETRY_DELAYS_SECONDS,
            (60.0, 60.0, 300.0),
        )
        provider = _RateLimitedProvider(failures=3)
        runner = NodeRunner(
            self.node,
            self.project,
            self.store,
            self._record_event,
        )

        with patch.object(
            runner_module, "_make_provider", return_value=provider
        ), patch.object(
            runner_module,
            "_CODEX_RATE_LIMIT_RETRY_DELAYS_SECONDS",
            (0.0, 0.0, 0.0),
        ):
            state, error = await runner._run_provider_turn(
                "Do the work", launch_instructions="Current contract"
            )

        self.assertEqual(state, NodeState.DONE)
        self.assertIsNone(error)
        self.assertEqual(len(provider.prompts), 4)
        self.assertEqual(provider.prompts[0], "Do the work")
        self.assertIn("第 3/3 次自动续跑", provider.prompts[-1])
        waiting_updates = [
            event
            for event in self.emitted
            if event.get("type") == "node_updated"
            and event["node"]["state"] == NodeState.WAITING.value
        ]
        self.assertEqual(len(waiting_updates), 3)

    async def test_disabled_setting_fails_without_retry(self) -> None:
        self.node.settings_snapshot["codex_rate_limit_auto_retry"] = False
        provider = _RateLimitedProvider(failures=1)
        runner = NodeRunner(
            self.node,
            self.project,
            self.store,
            self._record_event,
        )

        with patch.object(runner_module, "_make_provider", return_value=provider):
            state, error = await runner._run_provider_turn(
                "Do the work", launch_instructions="Current contract"
            )

        self.assertEqual(state, NodeState.ERROR)
        self.assertIn("自动续跑已关闭", error or "")
        self.assertEqual(provider.prompts, ["Do the work"])

    async def _record_event(self, event: dict) -> None:
        self.emitted.append(event)
