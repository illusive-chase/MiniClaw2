"""GET / PATCH /sessions/{sid}/planspaces/{pid}/status."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import miniclaw2.app as app_module
from miniclaw2.contextspace import bootstrap_project_contextspace
from miniclaw2.domain import Project
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


class PlanspaceStatusApiTest(unittest.TestCase):
    def _bootstrap(
        self,
    ) -> tuple[tempfile.TemporaryDirectory, TestClient, str, str]:
        tmp = tempfile.TemporaryDirectory()
        repo = Path(tmp.name) / "repo"
        repo.mkdir()
        store = Store(root=Path(tmp.name) / "store")
        project = Project(root_path=str(repo))
        store.create_project(project)
        boot = bootstrap_project_contextspace(project, store_root=store.root)
        store.update_project(
            Project(
                **{
                    **project.model_dump(),
                    "project_context_binding_id": boot["binding_id"],
                    "settings_override": {
                        **project.settings_override,
                        "active_planspace_id": boot["planspace_id"],
                    },
                }
            )
        )

        registry = ProjectRegistry(store=store)
        # Swap registry in App; the FastAPI app constructed in create_app
        # is module-level so we build a new one against our store.
        app_module.ProjectRegistry = lambda *a, **k: registry  # type: ignore[misc]
        client = TestClient(app_module.create_app())
        # The freshly-constructed ProjectRegistry inside create_app picks
        # up the same store via Store's default; force consistency by
        # mounting the project explicitly.
        client_registry = registry
        # Ensure the project is loaded by our intercepted registry.
        return tmp, client, project.id, boot["planspace_id"]

    def test_get_planspace_status_returns_slots_and_color(self) -> None:
        tmp, client, sid, pid = self._bootstrap()
        try:
            res = client.get(f"/sessions/{sid}/planspaces/{pid}/status")
            self.assertEqual(res.status_code, 200)
            body = res.json()
            self.assertEqual(body["planspace_id"], pid)
            self.assertEqual(body["color"], "indigo")
            self.assertIn("goal", body["status"])
            self.assertIn("decisions", body["status"])
        finally:
            tmp.cleanup()

    def test_patch_planspace_status_applies_add_and_remove(self) -> None:
        tmp, client, sid, pid = self._bootstrap()
        try:
            add_res = client.patch(
                f"/sessions/{sid}/planspaces/{pid}/status",
                json={
                    "operations": [
                        {
                            "operation": "add_open_question",
                            "summary": "Should the gate accept partial reviews?",
                        }
                    ]
                },
            )
            self.assertEqual(add_res.status_code, 200, add_res.text)
            body = add_res.json()
            self.assertEqual(len(body["status"]["open_questions"]), 1)
            q_id = body["status"]["open_questions"][0]["id"]

            remove_res = client.patch(
                f"/sessions/{sid}/planspaces/{pid}/status",
                json={
                    "operations": [
                        {"operation": "remove_open_question", "id": q_id}
                    ]
                },
            )
            self.assertEqual(remove_res.status_code, 200, remove_res.text)
            self.assertEqual(remove_res.json()["status"]["open_questions"], [])
        finally:
            tmp.cleanup()

    def test_patch_unknown_planspace_returns_404(self) -> None:
        tmp, client, sid, _ = self._bootstrap()
        try:
            res = client.patch(
                f"/sessions/{sid}/planspaces/planspaces.missing/status",
                json={"operations": []},
            )
            self.assertEqual(res.status_code, 404)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
