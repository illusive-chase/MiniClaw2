from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.domain import GitPosition, LanePosition, Node, NodePosition, NodeState, Project
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


class SessionListingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "store")
        self.projects: list[Project] = []
        for index in range(3):
            project = self.store.create_project(
                Project(root_path=f"/tmp/session-listing-{index}", created_at=float(index + 1))
            )
            self.projects.append(project)
            self.store.create_node(
                Node(
                    project_id=project.id,
                    model_preset_id=project.model_preset_id,
                    created_at=float(index + 10),
                    state=NodeState.QUEUED if index == 0 else NodeState.DONE,
                )
            )
        self.registry = ProjectRegistry(store=self.store)
        self.client = TestClient(create_app(self.registry))

    def tearDown(self) -> None:
        self.client.close()
        self.tmp.cleanup()

    def test_list_sessions_does_not_load_full_nodes(self) -> None:
        with patch.object(
            self.store,
            "list_nodes",
            wraps=self.store.list_nodes,
        ) as list_nodes:
            response = self.client.get("/sessions")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(list_nodes.call_count, 0)
        by_id = {session["id"]: session for session in response.json()}
        first = by_id[self.projects[0].id]
        self.assertEqual(first["turns"], 1)
        self.assertEqual(first["queued_count"], 1)
        self.assertEqual(first["last_activity_at"], 10.0)

    def test_list_omits_positions_but_detail_preserves_them(self) -> None:
        project = self.projects[0]
        node = self.store.list_nodes(project.id)[0]
        self.store.update_node_positions(
            project.id, {node.id: NodePosition(x=10, y=20, space="canvas")}, []
        )
        self.store.update_git_positions(
            project.id, {"commit:" + "a" * 40: GitPosition(x=30, y=40, space="canvas")}, []
        )
        self.store.update_lane_positions(
            project.id, {"planspace:lane": LanePosition(x=50, y=60, space="canvas")}, []
        )
        self.store.update_context_positions(
            project.id,
            {
                "ctx:project-root::context::CONTEXT.md": NodePosition(
                    x=70, y=80, space="canvas"
                )
            },
            [],
        )
        response = self.client.get("/sessions")
        self.assertEqual(response.status_code, 200)
        listed = next(info for info in response.json() if info["id"] == project.id)
        response = self.client.get(f"/sessions/{project.id}")
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        expected = {
            "node_positions": {node.id: {"x": 10, "y": 20, "space": "canvas"}},
            "git_positions": {"commit:" + "a" * 40: {"x": 30, "y": 40, "space": "canvas"}},
            "lane_positions": {"planspace:lane": {"x": 50, "y": 60, "space": "canvas"}},
            "context_positions": {
                "ctx:project-root::context::CONTEXT.md": {
                    "x": 70,
                    "y": 80,
                    "space": "canvas",
                }
            },
        }
        for field, positions in expected.items():
            self.assertNotIn(field, listed)
            self.assertEqual(detail.pop(field), positions)
        self.assertEqual(detail, listed)

    def test_gzip_round_trip_preserves_large_json_and_cors(self) -> None:
        project = self.projects[0]
        self.store.create_node(
            Node(
                project_id=project.id,
                model_preset_id=project.model_preset_id,
                prompt="重复的上下文内容" * 3000,
            )
        )
        url = f"/sessions/{project.id}/nodes"
        plain = self.client.get(url, headers={"Accept-Encoding": "identity"})
        compressed = self.client.get(
            url,
            headers={"Accept-Encoding": "gzip", "Origin": "http://localhost:5931"},
        )
        self.assertEqual(plain.status_code, 200)
        self.assertEqual(compressed.status_code, 200)
        self.assertNotIn("content-encoding", plain.headers)
        self.assertEqual(compressed.headers["content-encoding"], "gzip")
        self.assertIn("Accept-Encoding", compressed.headers["vary"])
        self.assertEqual(compressed.headers["access-control-allow-origin"], "*")
        self.assertEqual(compressed.json(), plain.json())
        self.assertLess(int(compressed.headers["content-length"]), len(plain.content))

    def test_gzip_skips_small_responses(self) -> None:
        response = self.client.get("/health", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.status_code, 200)
        self.assertLess(len(response.content), 1024)
        self.assertNotIn("content-encoding", response.headers)

    def test_health_echoes_dev_supervisor_instance(self) -> None:
        with patch.dict(os.environ, {"MINICLAW_DEV_INSTANCE_TOKEN": "instance-1"}):
            response = self.client.get("/health")

        self.assertEqual(response.headers["X-MiniClaw-Dev-Instance"], "instance-1")


if __name__ == "__main__":
    unittest.main()
