"""Tests for the materialize module."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from miniclaw2.domain import (
    Category,
    Node,
    NodeKind,
    NodeState,
    Project,
)
from miniclaw2.materialize import (
    diff_lane,
    lane_root,
    materialize_active_lane,
    snapshot_lane,
)
from miniclaw2.store import Store


class MaterializeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.store_root = self.tmp_path / "store"
        self.project_root = self.tmp_path / "project"
        self.project_root.mkdir()
        self.store = Store(root=self.store_root)
        self.project = Project(
            id="p1",
            root_path=str(self.project_root),
            provider="claude",
        )
        self.store.create_project(self.project)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_empty_lane_creates_root(self) -> None:
        root = materialize_active_lane(self.project, "lane-A", self.store)
        self.assertTrue(root.exists())
        self.assertEqual(list(root.iterdir()), [])

    def test_materializes_executed_and_virtual_nodes(self) -> None:
        executed = Node(
            id="ex1",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.DONE,
            planspace_id="lane-A",
            started_at=1.0,
            finished_at=2.0,
        )
        virtual = Node(
            id="v1",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.PLANNING,
            state=NodeState.VIRTUAL,
            planspace_id="lane-A",
            prompt_draft="do thing",
            proposed_by="node:ex1",
            summary="motivation",
        )
        other_lane = Node(
            id="o1",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.DONE,
            planspace_id="lane-B",
            started_at=1.0,
            finished_at=2.0,
        )
        for n in (executed, virtual, other_lane):
            self.store.create_node(n)
        root = materialize_active_lane(self.project, "lane-A", self.store)
        self.assertTrue((root / "nodes" / "ex1" / "preview.json").exists())
        self.assertTrue((root / "nodes" / "v1" / "preview.json").exists())
        self.assertFalse((root / "nodes" / "o1").exists())
        ex_preview = json.loads((root / "nodes" / "ex1" / "preview.json").read_text())
        self.assertEqual(ex_preview["state"], "done")
        v_preview = json.loads((root / "nodes" / "v1" / "preview.json").read_text())
        self.assertEqual(v_preview["state"], "virtual")
        self.assertEqual(v_preview["prompt_draft"], "do thing")

    def test_durable_preview_overrides_stub(self) -> None:
        node = Node(
            id="ex1",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.DONE,
            planspace_id="lane-A",
            started_at=1.0,
            finished_at=2.0,
        )
        self.store.create_node(node)
        durable_text = json.dumps({
            "id": "ex1",
            "kind": "agent",
            "category": "regular",
            "state": "done",
            "ran_at": "2026-06-13T00:00:00+00:00",
            "lane": "lane-A",
            "motivation": "real",
            "summary": "real",
            "next_implications": "real",
        }, indent=2)
        self.store.write_node_preview("p1", "ex1", durable_text)
        root = materialize_active_lane(self.project, "lane-A", self.store)
        materialized = (root / "nodes" / "ex1" / "preview.json").read_text()
        self.assertEqual(materialized, durable_text)

    def test_skips_inflight_nodes_by_default(self) -> None:
        node = Node(
            id="q1",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.QUEUED,
            planspace_id="lane-A",
        )
        self.store.create_node(node)
        self.store.write_node_preview(
            "p1",
            node.id,
            json.dumps(
                {
                    "id": "q1",
                    "kind": "agent",
                    "category": "regular",
                    "state": "virtual",
                    "lane": "lane-A",
                    "proposed_by": "node:parent",
                    "motivation": "review",
                    "prompt_draft": "review things",
                    "scheduled_deps": ["parent"],
                }
            ),
        )

        root = materialize_active_lane(self.project, "lane-A", self.store)

        self.assertFalse((root / "nodes" / "q1").exists())

    def test_materializes_current_inflight_durable_preview(self) -> None:
        node = Node(
            id="q1",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.QUEUED,
            planspace_id="lane-A",
        )
        self.store.create_node(node)
        self.store.write_node_preview(
            "p1",
            node.id,
            json.dumps(
                {
                    "id": "q1",
                    "kind": "agent",
                    "category": "regular",
                    "state": "virtual",
                    "lane": "lane-A",
                    "proposed_by": "node:parent",
                    "motivation": "review",
                    "prompt_draft": "review things",
                    "scheduled_deps": ["parent"],
                }
            ),
        )

        root = materialize_active_lane(
            self.project,
            "lane-A",
            self.store,
            current_node_id="q1",
        )

        preview = json.loads((root / "nodes" / "q1" / "preview.json").read_text())
        self.assertEqual(preview["state"], "virtual")
        self.assertEqual(preview["scheduled_deps"], ["parent"])


class SnapshotDiffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_snapshot_empty(self) -> None:
        snap = snapshot_lane(self.root)
        self.assertEqual(snap, {})

    def test_diff_detects_created(self) -> None:
        snap = snapshot_lane(self.root)
        (self.root / "f.txt").write_text("hi")
        diff = diff_lane(snap, self.root)
        self.assertEqual(diff.created, ["f.txt"])
        self.assertEqual(diff.modified, [])
        self.assertEqual(diff.deleted, [])

    def test_diff_detects_modified(self) -> None:
        (self.root / "f.txt").write_text("hi")
        snap = snapshot_lane(self.root)
        (self.root / "f.txt").write_text("bye")
        diff = diff_lane(snap, self.root)
        self.assertEqual(diff.modified, ["f.txt"])
        self.assertEqual(diff.created, [])

    def test_diff_detects_deleted(self) -> None:
        (self.root / "f.txt").write_text("hi")
        snap = snapshot_lane(self.root)
        (self.root / "f.txt").unlink()
        diff = diff_lane(snap, self.root)
        self.assertEqual(diff.deleted, ["f.txt"])

    def test_no_change_empty_diff(self) -> None:
        (self.root / "f.txt").write_text("hi")
        snap = snapshot_lane(self.root)
        diff = diff_lane(snap, self.root)
        self.assertTrue(diff.is_empty())


if __name__ == "__main__":
    unittest.main()
