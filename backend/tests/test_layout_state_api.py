from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.domain import Node
from miniclaw2.store import Store
from miniclaw2.migrations.transaction import atomic_json


class LayoutStateApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self._cwd = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()
        self._cwd.cleanup()
        self._home.cleanup()

    def _create_session(self) -> str:
        res = self.client.post(
            "/sessions",
            json={"cwd": self._cwd.name, "model_preset_id": "opus-4-8"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["id"]

    def _node(self, sid: str) -> Node:
        return Store(Path(self._home.name)).create_node(Node(model_preset_id="opus-4-8", project_id=sid, state="done"))

    def test_node_positions_round_trip_without_viewport(self) -> None:
        sid = self._create_session()
        node = self._node(sid)
        position = {"x": 128.5, "y": -16, "space": "canvas"}
        patch = self.client.patch(f"/sessions/{sid}/node-layout", json={"updates": {node.id: position}})
        self.assertEqual(patch.status_code, 200, patch.text)
        self.assertEqual(patch.json()["node_positions"], {node.id: position})
        self.assertNotIn("layout_hints", patch.json())
        self.assertNotIn("layout_viewport", patch.json())
        with TestClient(create_app()) as restarted:
            fetched = restarted.get(f"/sessions/{sid}")
            self.assertEqual(fetched.json()["node_positions"], {node.id: position})
            listed = restarted.get("/sessions").json()
            self.assertEqual(next(item for item in listed if item["id"] == sid)["node_positions"], {node.id: position})
        removed = self.client.patch(f"/sessions/{sid}/node-layout", json={"remove": [node.id]})
        self.assertEqual(removed.json()["node_positions"], {})
        project = Path(self._home.name) / "projects" / sid
        self.assertNotIn("node_positions", json.loads((project / "project.json").read_text()))
        self.assertEqual(list(project.glob("hosts/*/layout.json")), [])

    def test_git_positions_survive_restart_and_stay_separate(self) -> None:
        sid = self._create_session()
        node_id = "commit:" + "a" * 40
        position = {"x": 812.5, "y": -256, "space": "canvas"}
        response = self.client.patch(f"/sessions/{sid}/git-layout", json={"updates": {node_id: position, "commit:ghost": position}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["git_positions"][node_id], position)
        self.assertEqual(response.json()["node_positions"], {})
        with TestClient(create_app()) as restarted:
            self.assertEqual(restarted.get(f"/sessions/{sid}").json()["git_positions"][node_id], position)
            sessions = restarted.get("/sessions").json()
            self.assertEqual(next(item for item in sessions if item["id"] == sid)["git_positions"][node_id], position)
        project = Path(self._home.name) / "projects" / sid
        before = (project / "git-layout.json").read_bytes()
        for invalid_id, invalid_position in [
            ("agent-id", position), ("commit:../unsafe", position),
            (node_id, {**position, "space": "planspace:lane"}),
            (node_id, {**position, "x": "100"}),
        ]:
            response = self.client.patch(f"/sessions/{sid}/git-layout", json={"updates": {invalid_id: invalid_position}})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual((project / "git-layout.json").read_bytes(), before)
        response = self.client.patch(f"/sessions/{sid}/git-layout", json={"remove": ["commit:ghost"]})
        self.assertEqual(response.json()["git_positions"], {node_id: position})
        self.assertNotIn("git_positions", json.loads((project / "project.json").read_text()))
        for binding in project.glob("hosts/*/local.json"):
            binding.unlink()
        self.assertEqual(self.client.get(f"/sessions/{sid}").json()["git_positions"], {node_id: position})
        self.assertEqual(self.client.patch(f"/sessions/{sid}/git-layout", json={"updates": {node_id: position}}).status_code, 403)

    def test_lane_positions_persist_without_rewriting_children_or_git(self) -> None:
        sid = self._create_session()
        node = self._node(sid)
        child_position = {"x": 40, "y": 160, "space": "canvas"}
        self.client.patch(f"/sessions/{sid}/node-layout", json={"updates": {node.id: child_position}})
        git_position = {"x": -100, "y": 20, "space": "canvas"}
        self.client.patch(f"/sessions/{sid}/git-layout", json={"updates": {"commit:ghost": git_position}})
        project = Path(self._home.name) / "projects" / sid
        originals = {path: path.read_bytes() for path in project.rglob("*.json")}
        position = {"x": -1704, "y": 3480, "space": "canvas"}
        response = self.client.patch(f"/sessions/{sid}/lane-layout", json={"updates": {"planspace:lane": position}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["lane_positions"], {"planspace:lane": position})
        self.assertEqual(response.json()["node_positions"], {node.id: child_position})
        self.assertEqual(response.json()["git_positions"], {"commit:ghost": git_position})
        for path, before in originals.items():
            self.assertEqual(path.read_bytes(), before)
        with TestClient(create_app()) as restarted:
            self.assertEqual(restarted.get(f"/sessions/{sid}").json()["lane_positions"], {"planspace:lane": position})
            sessions = restarted.get("/sessions").json()
            self.assertEqual(next(item for item in sessions if item["id"] == sid)["lane_positions"], {"planspace:lane": position})
        for invalid_id, invalid_position in [
            (node.id, position), ("commit:ghost", position), ("planspace:", position),
            ("planspace:lane", {**position, "space": "planspace:lane"}),
            ("planspace:lane", {**position, "x": True}),
        ]:
            response = self.client.patch(f"/sessions/{sid}/lane-layout", json={"updates": {"planspace:valid": position, invalid_id: invalid_position}})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get(f"/sessions/{sid}").json()["lane_positions"], {"planspace:lane": position})
        removed = self.client.patch(f"/sessions/{sid}/lane-layout", json={"remove": ["planspace:lane"]})
        self.assertEqual(removed.json()["lane_positions"], {})
        for binding in project.glob("hosts/*/local.json"):
            binding.unlink()
        self.assertEqual(self.client.patch(f"/sessions/{sid}/lane-layout", json={"updates": {"planspace:lane": position}}).status_code, 403)

    def test_rejects_foreign_synthetic_and_stale_space_atomically(self) -> None:
        sid = self._create_session()
        native = self._node(sid)
        foreign = Node(model_preset_id="opus-4-8", project_id=sid)
        peer = Path(self._home.name) / "projects" / sid / "hosts" / "peer"
        atomic_json(peer / "nodes" / foreign.id / "node.json", foreign.model_dump(exclude={"provider", "owner_host_id"}))
        position = {"x": 1, "y": 2, "space": "canvas"}
        for invalid in [foreign.id, "commit:abc", "planspace:lane", "missing"]:
            with self.subTest(invalid=invalid):
                response = self.client.patch(f"/sessions/{sid}/node-layout", json={"updates": {native.id: position, invalid: position}})
                self.assertEqual(response.status_code, 409, response.text)
                response = self.client.patch(f"/sessions/{sid}/node-layout", json={"remove": [invalid]})
                self.assertEqual(response.status_code, 409)
        response = self.client.patch(f"/sessions/{sid}/node-layout", json={"updates": {native.id: {**position, "space": "planspace:stale"}}})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get(f"/sessions/{sid}").json()["node_positions"], {})
        for invalid in [{**position, "x": True}, {**position, "x": "NaN"}, {"x": 1, "y": 2}]:
            response = self.client.patch(f"/sessions/{sid}/node-layout", json={"updates": {native.id: invalid}})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.patch(f"/sessions/{sid}/node-layout", json={"layout_viewport": {}}).status_code, 422)
        self.assertEqual(self.client.patch(f"/sessions/{sid}/layout-hints", json={}).status_code, 404)
