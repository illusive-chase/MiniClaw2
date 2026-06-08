from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import miniclaw2.app as app_module
from miniclaw2.context_refresh import (
    _GENERATED_END,
    _GENERATED_START,
    _render_context,
)
from miniclaw2.domain import Project


def _digest(*items: str) -> dict[str, object]:
    return {
        "top_level": list(items),
        "headers": {"README.md": "# Test Project\n\nDetails"},
    }


class ContextRefreshRenderTest(unittest.TestCase):
    def test_refresh_preserves_unmarked_existing_context(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Alpha")
            existing = (
                "# Hand Written Context\n\n"
                "## Local Rules\n\n"
                "- Keep the hand-written deployment caveat.\n"
            )

            refreshed = _render_context(
                project,
                mode="refresh",
                digest=_digest("src/", "README.md"),
                existing=existing,
            )

            self.assertIn("## Project Shape", refreshed)
            self.assertIn("- src/", refreshed)
            self.assertIn("## Existing Project Guidance", refreshed)
            self.assertIn("Keep the hand-written deployment caveat.", refreshed)
            self.assertEqual(refreshed.count(_GENERATED_START), 1)
            self.assertEqual(refreshed.count(_GENERATED_END), 1)

    def test_refresh_replaces_generated_digest_without_dropping_manual_sections(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Alpha")
            initial = _render_context(
                project,
                mode="init",
                digest=_digest("old.txt"),
                existing="",
            )
            edited = (
                initial
                + "\n## Manual Guidance\n\n"
                + "- Keep this section across refreshes.\n"
            )

            refreshed = _render_context(
                project,
                mode="refresh",
                digest=_digest("new.txt"),
                existing=edited,
            )

            self.assertIn("- new.txt", refreshed)
            self.assertNotIn("- old.txt", refreshed)
            self.assertIn("Keep this section across refreshes.", refreshed)
            self.assertEqual(refreshed.count(_GENERATED_START), 1)
            self.assertEqual(refreshed.count(_GENERATED_END), 1)


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

    def test_new_direction_rejects_running_context_task(self) -> None:
        client, project = self._client_with_project(active_turn=False)

        with patch.object(
            app_module,
            "context_refresh_status",
            return_value={"running": True},
        ):
            res = client.post(
                f"/sessions/{project.id}/planspaces",
                json={"user_seed": "Investigate startup flow"},
            )

        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["detail"], "context refresh in progress")

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
