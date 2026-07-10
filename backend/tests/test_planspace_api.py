from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import miniclaw2.app as app_module
from miniclaw2.domain import Node, NodeState, Project


class PlanspaceApiTest(unittest.TestCase):
    def test_create_planspace_uses_seed_and_marks_user_seed_deprecated(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Project")
            node = Node(
                id="node-123",
                project_id=project.id,
                model_preset_id="gpt-5.5",
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
                    model_preset_id: str | None = None,
                ) -> object:
                    calls.append({
                        "sid": sid,
                        "title": title,
                        "seed": seed,
                        "mode": mode,
                        "model_preset_id": model_preset_id,
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
                                "seed": "Build auth",
                                "mode": "manual",
                                "model_preset_id": "gpt-5.5",
                            },
                        )
                        deprecated = client.post(
                            f"/sessions/{project.id}/planspaces",
                            json={
                                "user_seed": "Legacy auth",
                                "mode": "manual",
                                "model_preset_id": "gpt-5.5",
                            },
                        )
                    finally:
                        client.close()

            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(deprecated.status_code, 200, deprecated.text)
            self.assertEqual(deprecated.headers["deprecation"], "true")
            self.assertIn("user_seed is deprecated", deprecated.headers["warning"])
            self.assertEqual(calls, [
                {
                    "sid": project.id,
                    "title": "",
                    "seed": "Build auth",
                    "mode": "manual",
                    "model_preset_id": "gpt-5.5",
                },
                {
                    "sid": project.id,
                    "title": "",
                    "seed": "Legacy auth",
                    "mode": "manual",
                    "model_preset_id": "gpt-5.5",
                },
            ])
            body = res.json()
            self.assertEqual(body["node_id"], "node-123")
            self.assertEqual(body["planspace_id"], "planspaces.auth")
            self.assertEqual(body["binding_id"], "project.project")

    def test_create_blank_planspace_returns_seeded_virtual(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Project")
            node = Node(
                id="blank-1",
                project_id=project.id,
                model_preset_id="gpt-5.5",
                planspace_id="planspaces.blank",
                state=NodeState.VIRTUAL,
                prompt_draft="",
            )
            calls: list[dict[str, object]] = []

            class _Registry:
                store = SimpleNamespace(root=Path(raw) / "store")

                def get_project(self, sid: str) -> Project | None:
                    return project if sid == project.id else None

                def is_running(self, sid: str) -> bool:
                    return False

                def create_blank_planspace(
                    self,
                    sid: str,
                    *,
                    title: str,
                    seed: str,
                    mode: str | None = None,
                    model_preset_id: str | None = None,
                ) -> Node | None:
                    calls.append({
                        "sid": sid,
                        "title": title,
                        "seed": seed,
                        "mode": mode,
                        "model_preset_id": model_preset_id,
                    })
                    return node

            with patch.object(app_module, "ProjectRegistry", return_value=_Registry()):
                with patch.object(
                    app_module,
                    "context_refresh_status",
                    return_value={"running": False},
                ):
                    with patch.object(
                        app_module,
                        "describe_project_contextspace",
                        return_value={
                            "root": raw,
                            "exists": True,
                            "resolved_binding_id": "project.project",
                            "active_planspace_id": "planspaces.blank",
                            "bindings": [],
                        },
                    ):
                        client = TestClient(app_module.create_app())
                        try:
                            res = client.post(
                                f"/sessions/{project.id}/planspaces/blank",
                                json={
                                    "title": "Blank",
                                    "seed": "Start from scratch",
                                    "mode": "auto",
                                    "model_preset_id": "gpt-5.5",
                                },
                            )
                        finally:
                            client.close()

            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(calls, [{
                "sid": project.id,
                "title": "Blank",
                "seed": "Start from scratch",
                "mode": "auto",
                "model_preset_id": "gpt-5.5",
            }])
            body = res.json()
            self.assertEqual(body["node_id"], "blank-1")
            self.assertEqual(body["planspace_id"], "planspaces.blank")
            self.assertEqual(body["binding_id"], "project.project")

    def test_create_blank_planspace_refuses_context_refresh_running(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Project")

            class _Registry:
                store = SimpleNamespace(root=Path(raw) / "store")

                def get_project(self, sid: str) -> Project | None:
                    return project if sid == project.id else None

                def is_running(self, sid: str) -> bool:
                    return False

            with patch.object(app_module, "ProjectRegistry", return_value=_Registry()):
                with patch.object(
                    app_module,
                    "context_refresh_status",
                    return_value={"running": True},
                ):
                    client = TestClient(app_module.create_app())
                    try:
                        res = client.post(
                            f"/sessions/{project.id}/planspaces/blank",
                            json={"seed": "Blocked", "mode": "manual"},
                        )
                    finally:
                        client.close()

            self.assertEqual(res.status_code, 409, res.text)
            self.assertIn("context refresh", res.json()["detail"])

    def test_update_planspace_mode_forwards_to_registry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Project")
            calls: list[dict[str, object]] = []

            class _Registry:
                store = SimpleNamespace(root=Path(raw) / "store")

                def get_project(self, sid: str) -> Project | None:
                    return project if sid == project.id else None

                def update_planspace_mode(
                    self, sid: str, planspace_id: str, mode: str
                ) -> str | None:
                    calls.append({
                        "sid": sid,
                        "planspace_id": planspace_id,
                        "mode": mode,
                    })
                    return "auto"

            with patch.object(app_module, "ProjectRegistry", return_value=_Registry()):
                with patch.object(
                    app_module,
                    "describe_project_contextspace",
                    return_value={
                        "root": raw,
                        "exists": True,
                        "resolved_binding_id": "project.project",
                        "active_planspace_id": "planspaces.auth",
                        "bindings": [
                            {
                                "id": "project.project",
                                "path": "bindings/projects/project.project.yaml",
                                "title": "Project",
                                "local_paths": [raw],
                                "matches_project_path": True,
                                "active_planspace_id": "planspaces.auth",
                                "plugs": [
                                    {
                                        "id": "planspaces.auth",
                                        "kind": "planspace",
                                        "slug": "auth",
                                        "enabled": True,
                                        "auto_update": False,
                                        "source": "binding",
                                        "active": True,
                                        "exists": True,
                                        "title": "Auth",
                                        "mode": "auto",
                                    }
                                ],
                            }
                        ],
                    },
                ):
                    client = TestClient(app_module.create_app())
                    try:
                        res = client.patch(
                            f"/sessions/{project.id}/planspaces/planspaces.auth/mode",
                            json={"mode": "auto"},
                        )
                    finally:
                        client.close()

            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(calls, [{
                "sid": project.id,
                "planspace_id": "planspaces.auth",
                "mode": "auto",
            }])
            body = res.json()
            self.assertEqual(body["active_planspace_id"], "planspaces.auth")
            self.assertEqual(body["bindings"][0]["plugs"][0]["mode"], "auto")

    def test_promote_virtual_returns_node_payload(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Project")
            node = Node(
                id="virt-1",
                project_id=project.id,
                model_preset_id="gpt-5.5",
                state=NodeState.QUEUED,
                planspace_id="planspaces.auth",
                prompt="run this",
            )

            class _Registry:
                store = SimpleNamespace(root=Path(raw) / "store")

                def get_project(self, sid: str) -> Project | None:
                    return project if sid == project.id else None

                def is_running(self, sid: str) -> bool:
                    return False

                def promote_virtual(self, sid: str, vid: str) -> object | None:
                    if sid != project.id or vid != node.id:
                        return None
                    return SimpleNamespace(node=node)

            with patch.object(app_module, "ProjectRegistry", return_value=_Registry()):
                client = TestClient(app_module.create_app())
                try:
                    res = client.post(
                        f"/sessions/{project.id}/virtuals/{node.id}/promote"
                    )
                finally:
                    client.close()

            self.assertEqual(res.status_code, 200, res.text)
            body = res.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["node_id"], "virt-1")
            self.assertEqual(body["node"]["state"], "queued")


if __name__ == "__main__":
    unittest.main()
