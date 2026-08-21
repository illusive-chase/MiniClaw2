from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import miniclaw2.app as app_module
import miniclaw2.context_refresh as context_refresh
from miniclaw2.context_refresh import _run_agent_context_task, ContextTask
from miniclaw2.domain import Project
from miniclaw2.providers.base import AgentProviderContext, AgentProviderEvent


class FakeProvider:
    """Test double that records the context it was given and emits a done event."""

    name = "claude"

    def __init__(self, *, write_context: bool = True, content: str = "# Context\n") -> None:
        self.write_context = write_context
        self.content = content
        self.captured: AgentProviderContext | None = None
        self.interrupted = False

    async def run(self, context: AgentProviderContext):
        self.captured = context
        if self.write_context:
            (Path(context.project.root_path) / "CONTEXT.md").write_text(
                self.content, encoding="utf-8"
            )
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        self.interrupted = True


class AgentDrivenContextTaskTest(unittest.TestCase):
    def setUp(self) -> None:
        # Reset module-level registry between tests.
        context_refresh._TASKS.clear()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_init_runs_provider_in_minimal_mode_with_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Alpha")
            provider = FakeProvider()
            record = ContextTask(
                project_id=project.id,
                mode="init",
                started_at=1.0,
            )

            with patch("miniclaw2.runner._make_provider", return_value=provider):
                self._run(_run_agent_context_task(project, record))

            self.assertIsNotNone(provider.captured)
            ctx = provider.captured
            assert ctx is not None  # for type narrower
            self.assertTrue(ctx.minimal_mode)
            self.assertEqual(ctx.tool_allowlist, ["Read", "Glob", "Grep", "Write"])
            self.assertEqual(ctx.system_context, "")
            self.assertIn("first version of `CONTEXT.md`", ctx.node.prompt)
            self.assertTrue((Path(raw) / "CONTEXT.md").exists())
            meta = (Path(raw) / ".miniclaw2" / "context.meta.json").read_text(encoding="utf-8")
            self.assertIn('"source": "init"', meta)
            self.assertIn('"rewritten": true', meta)

    def test_init_raises_when_agent_skips_writing_context(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Alpha")
            provider = FakeProvider(write_context=False)
            record = ContextTask(
                project_id=project.id,
                mode="init",
                started_at=1.0,
            )

            with patch("miniclaw2.runner._make_provider", return_value=provider):
                with self.assertRaises(RuntimeError):
                    self._run(_run_agent_context_task(project, record))

    def test_refresh_uses_refresh_preset_and_tolerates_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Alpha")
            (Path(raw) / "CONTEXT.md").write_text("hand-written\n", encoding="utf-8")
            provider = FakeProvider(write_context=False)
            record = ContextTask(
                project_id=project.id,
                mode="refresh",
                started_at=1.0,
            )

            with patch("miniclaw2.runner._make_provider", return_value=provider):
                self._run(_run_agent_context_task(project, record))

            ctx = provider.captured
            assert ctx is not None
            self.assertIn("light-touch", ctx.node.prompt)
            self.assertEqual(
                (Path(raw) / "CONTEXT.md").read_text(encoding="utf-8"),
                "hand-written\n",
            )
            meta = (Path(raw) / ".miniclaw2" / "context.meta.json").read_text(encoding="utf-8")
            self.assertIn('"rewritten": false', meta)

    def test_task_broadcasts_running_and_terminal_status(self) -> None:
        async def scenario(project: Project, provider: FakeProvider) -> None:
            statuses: list[dict[str, object]] = []

            async def on_status(status: dict[str, object]) -> None:
                statuses.append(status)

            with patch("miniclaw2.runner._make_provider", return_value=provider):
                start_context_task = context_refresh.start_context_task
                start_context_task(project, mode="init", on_status=on_status)
                task = context_refresh._TASKS[project.id].task
                assert task is not None
                await task

            self.assertEqual(statuses[0]["running"], True)
            self.assertEqual(statuses[0]["mode"], "init")
            self.assertEqual(statuses[-1], {"running": False})
            self.assertEqual(context_refresh.context_refresh_status(project.id), {"running": False})

        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Alpha")
            self._run(scenario(project, FakeProvider()))


class ContextRefreshApiTest(unittest.TestCase):
    def _client_with_project(
        self,
        *,
        active_turn: bool,
    ) -> tuple[TestClient, Project]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "repo"
        root.mkdir()
        project = Project(root_path=str(root), name="Running Project")
        observers: dict[str, object] = {}

        class _Registry:
            store = SimpleNamespace(root=Path(tmp.name) / "store")

            def get_project(self, sid: str) -> Project | None:
                return project if sid == project.id else None

            def is_running(self, sid: str) -> bool:
                return active_turn and sid == project.id

            def attach_observer(self, sid: str, on_event: object) -> str | None:
                if sid != project.id:
                    return None
                observers["obs"] = on_event
                return "obs"

            def detach_observer(self, sid: str, token: str | None) -> None:
                if sid == project.id and token is not None:
                    observers.pop(token, None)

            def start_node(self, *args: object, **kwargs: object) -> object:
                raise AssertionError("start_node should not be called")

        patcher = patch.object(app_module, "ProjectRegistry", return_value=_Registry())
        patcher.start()
        self.addCleanup(patcher.stop)
        client = TestClient(app_module.create_app())
        self.addCleanup(client.close)
        return client, project

    def test_context_init_rejects_active_turn_without_starting_task(self) -> None:
        client, project = self._client_with_project(active_turn=True)

        with patch.object(app_module, "start_context_task") as start:
            res = client.post(f"/sessions/{project.id}/context/init")

        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["detail"], "turn in progress")
        start.assert_not_called()

    def test_context_refresh_rejects_active_turn_without_starting_task(self) -> None:
        client, project = self._client_with_project(active_turn=True)
        (Path(project.root_path) / "CONTEXT.md").write_text("manual\n", encoding="utf-8")

        with patch.object(app_module, "start_context_task") as start:
            res = client.post(f"/sessions/{project.id}/context/refresh")

        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["detail"], "turn in progress")
        start.assert_not_called()

    def test_context_cancel_is_idempotent_when_nothing_runs(self) -> None:
        client, project = self._client_with_project(active_turn=False)
        res = client.post(f"/sessions/{project.id}/context/cancel")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertFalse(res.json()["context_refresh"]["running"])

    def test_websocket_user_message_rejects_running_context_task(self) -> None:
        client, project = self._client_with_project(active_turn=False)

        with patch.object(
            app_module,
            "context_refresh_status",
            return_value={"running": True},
        ):
            with client.websocket_connect(f"/ws/{project.id}") as websocket:
                websocket.send_json({"type": "user_message", "text": "hello"})
                event = websocket.receive_json()

        self.assertEqual(event["type"], "error")
        self.assertEqual(event["message"], "context refresh in progress")


if __name__ == "__main__":
    unittest.main()
