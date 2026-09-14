from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.domain import Node, NodeState, Project
from miniclaw2.node_projection import node_event_projection, node_list_projection
from miniclaw2.registry import ProjectRegistry, ProjectRuntime
from miniclaw2.store import Store


class NodeProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "store")
        self.project = self.store.create_project(Project(root_path=self.tmp.name))
        self.node = self.store.create_node(Node(
            project_id=self.project.id,
            model_preset_id=self.project.model_preset_id,
            state=NodeState.DONE,
            prompt="测试🙂" * 100 + "{{late_argument}} {{input.source}}",
            system_context_snapshot="系统上下文" * 1500,
            launch_instructions_snapshot="节点规则" * 1500,
            settings_snapshot={"skill_audit": [{"id": "skills.search"}], "active_planspace_id": "lane"},
        ))
        self.registry = ProjectRegistry(store=self.store)
        self.client = TestClient(create_app(self.registry))

    def tearDown(self) -> None:
        self.client.close()
        self.tmp.cleanup()

    def test_list_is_slim_and_detail_remains_complete(self) -> None:
        listed = self.client.get(f"/sessions/{self.project.id}/nodes").json()
        detail = self.client.get(f"/sessions/{self.project.id}/nodes/{self.node.id}").json()
        self.assertEqual(listed, [node_list_projection(detail)])
        self.assertEqual(detail, self.node.model_dump(mode="json"))
        self.assertEqual(listed[0]["prompt"], self.node.prompt[:120])
        self.assertTrue(listed[0]["prompt_truncated"])
        self.assertEqual(listed[0]["prompt_argument_names"], ["late_argument"])
        self.assertEqual(listed[0]["settings_snapshot"], self.node.settings_snapshot)
        self.assertNotIn("system_context_snapshot", listed[0])
        self.assertNotIn("launch_instructions_snapshot", listed[0])
        self.assertNotIn("prompt_truncated", detail)
        self.assertEqual(self.store.load_node(self.project.id, self.node.id), self.node)

    def test_prompt_boundaries_and_projection_idempotence(self) -> None:
        for length in (0, 119, 120, 121):
            with self.subTest(length=length):
                node = self.node.model_copy(update={"prompt": "🙂" * length})
                projected = node_list_projection(node)
                self.assertEqual(projected["prompt_truncated"], length > 120)
                self.assertEqual(len(projected["prompt"]), min(120, length))
                self.assertEqual(node_list_projection(projected), projected)

    def test_virtual_draft_and_settings_are_not_truncated(self) -> None:
        self.node.prompt_draft = "完整草稿" * 100 + "{{draft_argument}}"
        projected = node_list_projection(self.node)
        self.assertEqual(projected["prompt_draft"], self.node.prompt_draft)
        self.assertEqual(projected["prompt_argument_names"], ["draft_argument"])
        self.assertEqual(projected["settings_snapshot"], self.node.settings_snapshot)

    def test_broadcast_uses_same_projection_without_mutating_event(self) -> None:
        runtime = ProjectRuntime(self.project)
        received: list[dict] = []

        async def receive(event: dict) -> None:
            received.append(event)

        runtime.add_observer(receive)
        for event_type in ("node_started", "node_updated", "turn_done"):
            with self.subTest(event_type=event_type):
                event = {"type": event_type, "node_id": self.node.id, "node": self.node.model_dump()}
                asyncio.run(runtime.broadcast(event))
                self.assertEqual(received[-1]["node"], node_list_projection(self.node))
                self.assertEqual(event["node"], self.node.model_dump())
        text_event = {"type": "text_delta", "text": "完整文本"}
        self.assertIs(node_event_projection(text_event), text_event)

    def test_historical_replay_is_slim_without_rewriting_event_log(self) -> None:
        for seq, event_type in enumerate(("node_started", "node_updated", "turn_done"), 1):
            self.store.append_event(self.project.id, self.node.id, seq, {
                "type": event_type, "node_id": self.node.id, "node": self.node.model_dump(mode="json"),
            })
        records = self.registry.replay_node_events(self.project.id, self.node.id)
        self.assertIsNotNone(records)
        for record in records or []:
            self.assertEqual(record["event"]["node"], node_list_projection(self.node))
        persisted = self.store.replay_events(self.project.id, self.node.id)
        self.assertEqual(persisted[0]["event"]["node"], self.node.model_dump(mode="json"))

    def test_missing_project_and_node_are_still_not_found(self) -> None:
        self.assertEqual(self.client.get("/sessions/missing/nodes").status_code, 404)
        self.assertEqual(self.client.get(f"/sessions/{self.project.id}/nodes/missing").status_code, 404)

    def test_2000_node_projection_reduces_snapshot_payload(self) -> None:
        nodes = [self.node.model_copy(update={"id": f"node-{index}"}) for index in range(2000)]
        full_bytes = len(json.dumps([node.model_dump() for node in nodes]))
        projected = [node_list_projection(node) for node in nodes]
        self.assertLess(len(json.dumps(projected)), full_bytes * 0.2)
        self.assertEqual(len(projected), 2000)
