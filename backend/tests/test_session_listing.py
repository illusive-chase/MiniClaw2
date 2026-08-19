from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.domain import Node, NodeState, Project
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

    def test_list_sessions_enumerates_each_projects_nodes_once(self) -> None:
        with patch.object(
            self.store,
            "list_nodes",
            wraps=self.store.list_nodes,
        ) as list_nodes:
            response = self.client.get("/sessions")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(list_nodes.call_count, len(self.projects))
        by_id = {session["id"]: session for session in response.json()}
        first = by_id[self.projects[0].id]
        self.assertEqual(first["turns"], 1)
        self.assertEqual(first["queued_count"], 1)
        self.assertEqual(first["last_activity_at"], 10.0)


if __name__ == "__main__":
    unittest.main()
