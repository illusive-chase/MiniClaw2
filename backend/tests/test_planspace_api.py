from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import miniclaw2.app as app_module
from miniclaw2.domain import Node, Project


class PlanspaceApiTest(unittest.TestCase):
    def test_create_planspace_accepts_user_seed_and_returns_launched_node(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Project")
            node = Node(
                id="node-123",
                project_id=project.id,
                planspace_id="planspaces.auth",
                prompt="bootstrap",
            )
            calls: list[dict[str, object]] = []

            class _Registry:
                store = SimpleNamespace(root=Path(raw) / "store")

                def get_project(self, sid: str) -> Project | None:
                    return project if sid == project.id else None

                def is_running(self, sid: str) -> bool:
                    return False

                def create_planspace_and_launch_concierge(
                    self,
                    sid: str,
                    *,
                    title: str,
                    seed: str,
                    mode: str | None = None,
                ) -> object:
                    calls.append({
                        "sid": sid,
                        "title": title,
                        "seed": seed,
                        "mode": mode,
                    })
                    return SimpleNamespace(node=node)

            with patch.object(app_module, "ProjectRegistry", return_value=_Registry()):
                with patch.object(
                    app_module,
                    "describe_project_contextspace",
                    return_value={
                        "root": raw,
                        "exists": True,
                        "resolved_binding_id": "project.project",
                        "active_planspace_id": "planspaces.auth",
                        "bindings": [],
                    },
                ):
                    client = TestClient(app_module.create_app())
                    try:
                        res = client.post(
                            f"/sessions/{project.id}/planspaces",
                            json={
                                "user_seed": "Build auth",
                                "needs_review": True,
                            },
                        )
                    finally:
                        client.close()

            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(calls, [{
                "sid": project.id,
                "title": "",
                "seed": "Build auth",
                "mode": None,
            }])
            body = res.json()
            self.assertEqual(body["node_id"], "node-123")
            self.assertEqual(body["planspace_id"], "planspaces.auth")
            self.assertEqual(body["binding_id"], "project.project")


if __name__ == "__main__":
    unittest.main()
