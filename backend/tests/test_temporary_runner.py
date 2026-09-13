from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from miniclaw2.domain import COLD_START_AGENT_OP_KIND, Node, NodeKind, NodeState, Project
from miniclaw2.providers import AgentProviderContext, AgentProviderEvent
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


class _FreshSessionProvider:
    name = "stub"

    def __init__(self) -> None:
        self.session_ids: list[str | None] = []

    async def run(self, context: AgentProviderContext):
        self.session_ids.append(context.node.provider_session_id)
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class TemporaryRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_does_not_probe_git_or_resume_a_cached_session(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(Path(raw) / "store")
            project = Project(root_path=raw, temporary=True)
            store.create_project(project)
            node = Node(
                project_id=project.id,
                model_preset_id=project.model_preset_id,
                state=NodeState.QUEUED,
                agent_op_kind=COLD_START_AGENT_OP_KIND,
                provider_session_id="stale-session",
                prompt="执行临时任务",
            )
            store.create_node(node)
            provider = _FreshSessionProvider()

            async def on_event(payload: dict) -> None:
                return None

            runner = NodeRunner(node, project, store, on_event)
            with (
                patch("miniclaw2.runner.git_head", side_effect=AssertionError("不应读取 Git")),
                patch("miniclaw2.runner._make_provider", return_value=provider),
            ):
                await runner.run()
            self.assertEqual(node.state, NodeState.DONE, node.error)
            self.assertIsNone(node.commit_before)
            self.assertIsNone(node.commit_after)
            self.assertEqual(provider.session_ids, [None])

    async def test_legacy_queued_git_op_cannot_mutate_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(Path(raw) / "store")
            project = Project(root_path=raw, temporary=True)
            store.create_project(project)

            async def on_event(payload: dict) -> None:
                return None

            for op_kind in ("commit", "pull"):
                with self.subTest(op_kind=op_kind):
                    node = Node(
                        project_id=project.id,
                        model_preset_id=project.model_preset_id,
                        kind=NodeKind.OP,
                        op_kind=op_kind,
                        state=NodeState.QUEUED,
                    )
                    store.create_node(node)
                    runner = NodeRunner(node, project, store, on_event)
                    with (
                        patch("miniclaw2.runner.git_head") as head,
                        patch("miniclaw2.runner.commit_all") as commit,
                        patch("miniclaw2.runner.git_pull_rebase") as pull,
                    ):
                        await runner.run()
                    self.assertEqual(node.state, NodeState.ERROR)
                    self.assertIn("临时项目不支持 Git", node.error or "")
                    head.assert_not_called()
                    commit.assert_not_called()
                    pull.assert_not_called()
