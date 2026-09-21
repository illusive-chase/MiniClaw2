from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import miniclaw2.app as app_module
from miniclaw2.contextspace import create_planspace, read_planspace_mode
from miniclaw2.domain import Node, NodeState, Project
from miniclaw2.registry import (
    PlanspaceCreationResult,
    ProjectRegistry,
    VirtualPromotionResult,
)
from miniclaw2.store import Store


class PlanspaceApiTest(unittest.TestCase):
    def test_contextspace_binding_cannot_be_reassigned(self) -> None:
        """``PATCH /contextspace`` was the only way to claim a foreign binding.

        A project's binding is decided by ownership, not chosen: the endpoint
        accepted any binding id on the machine, which let one project adopt
        another's memory profile and then create lanes inside it.
        """
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Project")

            class _Registry:
                store = SimpleNamespace(root=Path(raw) / "store")

                def get_project(self, sid: str) -> Project | None:
                    return project if sid == project.id else None

            with patch.object(app_module, "ProjectRegistry", return_value=_Registry()):
                client = TestClient(app_module.create_app())
                try:
                    res = client.patch(
                        f"/sessions/{project.id}/contextspace",
                        json={"project_context_binding_id": "project.demo"},
                    )
                finally:
                    client.close()

            self.assertEqual(res.status_code, 405, res.text)
            self.assertIsNone(project.project_context_binding_id)

    def test_concierge_planspace_endpoint_is_gone(self) -> None:
        """``POST /planspaces`` was the concierge path; only blank remains.

        New directions are created empty and filled in by the user, so the
        route that launched a planning agent no longer exists.
        """
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Project")

            class _Registry:
                store = SimpleNamespace(root=Path(raw) / "store")

                def get_project(self, sid: str) -> Project | None:
                    return project if sid == project.id else None

                def is_running(self, sid: str) -> bool:
                    return False

            with patch.object(app_module, "ProjectRegistry", return_value=_Registry()):
                client = TestClient(app_module.create_app())
                try:
                    res = client.post(
                        f"/sessions/{project.id}/planspaces",
                        json={"seed": "Build auth", "mode": "manual"},
                    )
                finally:
                    client.close()

            self.assertEqual(res.status_code, 404, res.text)

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
                ) -> PlanspaceCreationResult | None:
                    calls.append({
                        "sid": sid,
                        "title": title,
                        "seed": seed,
                        "mode": mode,
                        "model_preset_id": model_preset_id,
                    })
                    return PlanspaceCreationResult(node=node)

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
            # No "activated" field: creation no longer moves an execution
            # cursor, so there is nothing for the client to react to.
            self.assertNotIn("activated", body)

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
                        "bindings": [
                            {
                                "id": "project.project",
                                "path": "bindings/projects/project.project.yaml",
                                "title": "Project",
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
            self.assertEqual(body["resolved_binding_id"], "project.project")
            self.assertEqual(body["bindings"][0]["plugs"][0]["mode"], "auto")

    def test_update_planspace_mode_rejects_another_projects_lane(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            with patch.dict(
                os.environ,
                {"MINICLAW_CONTEXT_HOME": str(base / "context")},
            ):
                store = Store(base / "store")
                project_a = store.create_project(
                    Project(root_path=str(base / "a"), name="A")
                )
                project_b = store.create_project(
                    Project(root_path=str(base / "b"), name="B")
                )
                Path(project_a.root_path).mkdir()
                Path(project_b.root_path).mkdir()
                foreign_lane = create_planspace(
                    project_b,
                    title="Owned by B",
                    mode="manual",
                    store_root=store.root,
                )
                registry = ProjectRegistry(store)

                with patch("miniclaw2.app.install_hooks"), TestClient(
                    app_module.create_app(registry)
                ) as client:
                    response = client.patch(
                        f"/sessions/{project_a.id}/planspaces/{foreign_lane}/mode",
                        json={"mode": "auto"},
                    )

                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn("unknown planspace for project", response.text)
                self.assertEqual(
                    read_planspace_mode(
                        project_b,
                        foreign_lane,
                        store_root=store.root,
                    ),
                    "manual",
                )

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

                def promote_virtual_result(
                    self, sid: str, vid: str
                ) -> VirtualPromotionResult:
                    if sid != project.id or vid != node.id:
                        return VirtualPromotionResult(
                            None, "virtual_not_found", "Virtual node was not found."
                        )
                    return VirtualPromotionResult(node)

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
            self.assertFalse(body["already_promoted"])

    def test_promote_virtual_rejects_unavailable_execution_channel(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Project")

            class _Registry:
                store = SimpleNamespace(root=Path(raw) / "store")
                promotion_called = False

                def get_project(self, sid: str) -> Project | None:
                    return project if sid == project.id else None

                def require_execution_project(self, sid: str) -> None:
                    raise ValueError("远端节点执行通道尚未实现")

                def promote_virtual_result(
                    self, sid: str, vid: str
                ) -> VirtualPromotionResult:
                    self.promotion_called = True
                    raise AssertionError("promotion must not run")

            registry = _Registry()
            with patch.object(app_module, "ProjectRegistry", return_value=registry):
                client = TestClient(app_module.create_app())
                try:
                    res = client.post(
                        f"/sessions/{project.id}/virtuals/virt-1/promote"
                    )
                finally:
                    client.close()

            self.assertEqual(res.status_code, 400, res.text)
            self.assertEqual(res.json()["detail"], "远端节点执行通道尚未实现")
            self.assertFalse(registry.promotion_called)

    def test_promote_virtual_retry_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Project")
            node = Node(
                id="virt-1",
                project_id=project.id,
                model_preset_id="gpt-5.5",
                state=NodeState.RUNNING,
                planspace_id="planspaces.auth",
                prompt="run this",
                proposed_by="user",
            )

            class _Registry:
                store = SimpleNamespace(root=Path(raw) / "store")

                def get_project(self, sid: str) -> Project | None:
                    return project if sid == project.id else None

                def promote_virtual_result(
                    self, sid: str, vid: str
                ) -> VirtualPromotionResult:
                    return VirtualPromotionResult(
                        node,
                        "already_promoted",
                        "Virtual node has already been promoted.",
                    )

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
            self.assertTrue(body["already_promoted"])
            self.assertEqual(body["node"]["state"], "running")

    def test_promote_virtual_conflict_returns_code_and_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Project")

            class _Registry:
                store = SimpleNamespace(root=Path(raw) / "store")

                def get_project(self, sid: str) -> Project | None:
                    return project if sid == project.id else None

                def promote_virtual_result(
                    self, sid: str, vid: str
                ) -> VirtualPromotionResult:
                    return VirtualPromotionResult(
                        None,
                        "dependencies_not_terminal",
                        "Virtual node has dependencies that are not terminal.",
                        ("parent-1",),
                    )

            with patch.object(app_module, "ProjectRegistry", return_value=_Registry()):
                client = TestClient(app_module.create_app())
                try:
                    res = client.post(
                        f"/sessions/{project.id}/virtuals/virt-1/promote"
                    )
                finally:
                    client.close()

            self.assertEqual(res.status_code, 409, res.text)
            self.assertEqual(
                res.json()["detail"],
                {
                    "code": "dependencies_not_terminal",
                    "message": (
                        "Virtual node has dependencies that are not terminal."
                    ),
                    "blockers": ["parent-1"],
                },
            )

    def test_dequeue_queued_node_returns_virtual_payload(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Project")
            node = Node(
                id="queued-1",
                project_id=project.id,
                model_preset_id="gpt-5.5",
                state=NodeState.VIRTUAL,
                planspace_id="planspaces.auth",
                prompt_draft="run this",
            )

            class _Registry:
                store = SimpleNamespace(root=Path(raw) / "store")

                def get_project(self, sid: str) -> Project | None:
                    return project if sid == project.id else None

                def get_node(self, sid: str, nid: str) -> Node | None:
                    return node if sid == project.id and nid == node.id else None

                def dequeue_node(self, sid: str, nid: str) -> Node | None:
                    return self.get_node(sid, nid)

            with patch.object(app_module, "ProjectRegistry", return_value=_Registry()):
                client = TestClient(app_module.create_app())
                try:
                    res = client.post(
                        f"/sessions/{project.id}/nodes/{node.id}/dequeue"
                    )
                finally:
                    client.close()

            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["node"]["state"], "virtual")

    def test_delete_planspace_returns_refreshed_contextspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Project")
            calls: list[tuple[str, str]] = []

            class _Registry:
                store = SimpleNamespace(root=Path(raw) / "store")

                def get_project(self, sid: str) -> Project | None:
                    return project if sid == project.id else None

                def delete_planspace(
                    self, sid: str, planspace_id: str
                ) -> tuple[bool, list[str]]:
                    calls.append((sid, planspace_id))
                    return True, []

            with patch.object(app_module, "ProjectRegistry", return_value=_Registry()):
                with patch.object(
                    app_module,
                    "describe_project_contextspace",
                    return_value={"resolved_binding_id": "project.keep"},
                ):
                    client = TestClient(app_module.create_app())
                    try:
                        res = client.delete(
                            f"/sessions/{project.id}/planspaces/planspaces.drop"
                        )
                    finally:
                        client.close()

            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["resolved_binding_id"], "project.keep")
            self.assertEqual(calls, [(project.id, "planspaces.drop")])

    def test_delete_planspace_maps_registry_outcomes_to_status_codes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Project")

            class _Registry:
                store = SimpleNamespace(root=Path(raw) / "store")

                def get_project(self, sid: str) -> Project | None:
                    return project if sid == project.id else None

                def delete_planspace(
                    self, sid: str, planspace_id: str
                ) -> tuple[bool, list[str]]:
                    if planspace_id == "planspaces.active":
                        raise ValueError("cannot delete the active planspace")
                    if planspace_id == "planspaces.busy":
                        return False, ["node-1", "node-2"]
                    if planspace_id == "planspaces.readonly":
                        raise RuntimeError("store is read-only")
                    return False, []

            with patch.object(app_module, "ProjectRegistry", return_value=_Registry()):
                with patch.object(
                    app_module, "describe_project_contextspace", return_value={}
                ):
                    client = TestClient(app_module.create_app())
                    try:
                        base = f"/sessions/{project.id}/planspaces"
                        active = client.delete(f"{base}/planspaces.active")
                        busy = client.delete(f"{base}/planspaces.busy")
                        readonly = client.delete(f"{base}/planspaces.readonly")
                        missing = client.delete(f"{base}/planspaces.missing")
                        no_session = client.delete(
                            "/sessions/nope/planspaces/planspaces.drop"
                        )
                    finally:
                        client.close()

            self.assertEqual(active.status_code, 400, active.text)
            self.assertEqual(busy.status_code, 409, busy.text)
            self.assertEqual(busy.json()["detail"]["busy"], ["node-1", "node-2"])
            self.assertEqual(readonly.status_code, 409, readonly.text)
            self.assertEqual(missing.status_code, 404, missing.text)
            self.assertEqual(no_session.status_code, 404, no_session.text)


if __name__ == "__main__":
    unittest.main()
