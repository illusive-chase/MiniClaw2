from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import miniclaw2.app as app_module
from miniclaw2.domain import (
    Category,
    Node,
    NodeKind,
    NodeState,
    Project,
    ReviewSubtype,
)
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


class VirtualEditRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.store = Store(root=Path(self.tmp.name) / "store")
        self.project = Project(root_path=str(Path(self.tmp.name) / "repo"))
        self.store.create_project(self.project)
        self.registry = ProjectRegistry(store=self.store)
        self.lane = "planspaces.work"

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def _virtual(
        self,
        nid: str,
        *,
        deps: list[str] | None = None,
        category: Category = Category.REGULAR,
    ) -> Node:
        node = Node(
            id=nid,
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            category=category,
            state=NodeState.VIRTUAL,
            planspace_id=self.lane,
            prompt_draft=f"draft {nid}",
            proposed_by="user",
            scheduled_deps=deps or [],
            summary=f"motivation {nid}",
        )
        self.store.create_node(node)
        return node

    def test_update_virtual_edits_prompt_motivation_deps_and_obsolete_reason(self) -> None:
        parent = self._virtual("parent")
        child = self._virtual("child")

        updated = self.registry.update_virtual(
            self.project.id,
            child.id,
            prompt_draft="new draft",
            motivation="new motivation",
            scheduled_deps=[parent.id],
            obsolete_reason="superseded",
        )

        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.prompt_draft, "new draft")
        self.assertEqual(updated.summary, "new motivation")
        self.assertEqual(updated.scheduled_deps, [parent.id])
        self.assertEqual(updated.obsolete_reason, "superseded")

        preview = self.store.read_node_preview(self.project.id, child.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn('"prompt_draft": "new draft"', preview)
        self.assertIn('"obsolete_reason": "superseded"', preview)

    def test_update_virtual_rejects_cycle(self) -> None:
        parent = self._virtual("parent")
        child = self._virtual("child", deps=[parent.id])

        with self.assertRaisesRegex(ValueError, "cycle"):
            self.registry.update_virtual(
                self.project.id,
                parent.id,
                scheduled_deps=[child.id],
            )

        reloaded = self.store.load_node(self.project.id, parent.id)
        assert reloaded is not None
        self.assertEqual(reloaded.scheduled_deps, [])

    def test_update_virtual_rejects_cross_lane_dependency(self) -> None:
        parent = self._virtual("other-parent")
        parent.planspace_id = "planspaces.other"
        self.store.update_node(parent)
        child = self._virtual("child")

        with self.assertRaisesRegex(ValueError, "outside this lane"):
            self.registry.update_virtual(
                self.project.id,
                child.id,
                scheduled_deps=[parent.id],
            )

        reloaded = self.store.load_node(self.project.id, child.id)
        assert reloaded is not None
        self.assertEqual(reloaded.scheduled_deps, [])

    def test_update_virtual_review_requires_brief(self) -> None:
        node = self._virtual("review-me")

        with self.assertRaisesRegex(ValueError, "brief"):
            self.registry.update_virtual(
                self.project.id,
                node.id,
                category="review",
                subtype=ReviewSubtype.AGENTIC_REVIEW.value,
            )

    def test_update_virtual_can_make_review_virtual(self) -> None:
        node = self._virtual("review-me")

        updated = self.registry.update_virtual(
            self.project.id,
            node.id,
            category="review",
            subtype="human_interact_review",
            brief={
                "check_what": "Check behavior",
                "expected": "It works",
                "abnormal": "It regresses",
            },
        )

        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.category, Category.REVIEW)
        self.assertEqual(updated.subtype, ReviewSubtype.HUMAN_INTERACT_REVIEW)
        self.assertIsNotNone(updated.brief)

    def test_update_virtual_returns_none_for_executed_node(self) -> None:
        node = Node(
            id="done",
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.DONE,
            planspace_id=self.lane,
            started_at=1.0,
            finished_at=2.0,
        )
        self.store.create_node(node)

        updated = self.registry.update_virtual(
            self.project.id,
            node.id,
            prompt_draft="cannot",
        )

        self.assertIsNone(updated)


class VirtualEditApiTests(unittest.TestCase):
    def test_patch_virtual_forwards_only_supplied_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Project(root_path=raw, name="Project")
            node = Node(
                id="virt-1",
                project_id=project.id,
                state=NodeState.VIRTUAL,
                prompt_draft="updated",
                summary="new motivation",
            )
            calls: list[dict[str, object]] = []

            class _Registry:
                store = SimpleNamespace(root=Path(raw) / "store")

                def get_project(self, sid: str) -> Project | None:
                    return project if sid == project.id else None

                def is_running(self, sid: str) -> bool:
                    return False

                def get_node(self, sid: str, nid: str) -> Node | None:
                    return node if sid == project.id and nid == node.id else None

                def update_virtual(self, sid: str, vid: str, **kwargs: object) -> Node | None:
                    calls.append({"sid": sid, "vid": vid, **kwargs})
                    return node

            with patch.object(app_module, "ProjectRegistry", return_value=_Registry()):
                client = TestClient(app_module.create_app())
                try:
                    res = client.patch(
                        f"/sessions/{project.id}/virtuals/{node.id}",
                        json={
                            "prompt_draft": "updated",
                            "motivation": "new motivation",
                            "obsolete_reason": None,
                        },
                    )
                finally:
                    client.close()

            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(calls, [{
                "sid": project.id,
                "vid": node.id,
                "prompt_draft": "updated",
                "motivation": "new motivation",
                "obsolete_reason": None,
            }])
            body = res.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["node"]["id"], node.id)


if __name__ == "__main__":
    unittest.main()
