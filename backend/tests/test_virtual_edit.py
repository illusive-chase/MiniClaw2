from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from miniclaw2.contextspace import create_planspace
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
        self.lane = create_planspace(self.project, title="Work", mode="manual")
        runtime = self.registry._runtimes[self.project.id]
        runtime.project.active_planspace_id = self.lane
        self.store.update_project(runtime.project)

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
            model_preset_id="gpt-5.5",
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

    def test_update_virtual_can_change_model_preset(self) -> None:
        node = self._virtual("provider-node")

        updated = self.registry.update_virtual(
            self.project.id,
            node.id,
            model_preset_id="opus-4-8",
        )

        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.model_preset_id, "opus-4-8")
        self.assertEqual(updated.provider, "claude")
        reloaded = self.store.load_node(self.project.id, node.id)
        assert reloaded is not None
        self.assertEqual(reloaded.model_preset_id, "opus-4-8")
        self.assertEqual(reloaded.provider, "claude")

    def test_update_resume_virtual_rejects_model_preset_change(self) -> None:
        source = Node(
            id="resume-source",
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.DONE,
            planspace_id=self.lane,
            model_preset_id="gpt-5.5",
            provider_session_id="session-1",
            prompt="old",
        )
        self.store.create_node(source)
        created = self.registry.create_virtual(
            self.project.id,
            prompt_draft="continue",
            resume_from_node_id=source.id,
        )
        self.assertIsNotNone(created)
        assert created is not None

        unchanged = self.registry.update_virtual(
            self.project.id,
            created.id,
            motivation="still inherits source model preset",
            model_preset_id="gpt-5.5",
        )
        self.assertIsNotNone(unchanged)
        assert unchanged is not None
        self.assertEqual(unchanged.model_preset_id, "gpt-5.5")
        self.assertEqual(unchanged.provider, "codex")

        with self.assertRaisesRegex(ValueError, "inherit model_preset_id"):
            self.registry.update_virtual(
                self.project.id,
                created.id,
                model_preset_id="opus-4-8",
            )

        reloaded = self.store.load_node(self.project.id, created.id)
        assert reloaded is not None
        self.assertEqual(reloaded.model_preset_id, "gpt-5.5")
        self.assertEqual(reloaded.provider, "codex")

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
            model_preset_id="gpt-5.5",
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

    def test_create_virtual_uses_active_lane_and_writes_preview(self) -> None:
        parent = self._virtual("parent")

        created = self.registry.create_virtual(
            self.project.id,
            prompt_draft="new planned work",
            motivation="user wants this",
            scheduled_deps=[parent.id],
        )

        self.assertIsNotNone(created)
        assert created is not None
        self.assertEqual(created.state, NodeState.VIRTUAL)
        self.assertEqual(created.kind, NodeKind.AGENT)
        self.assertEqual(created.category, Category.REGULAR)
        self.assertEqual(created.planspace_id, self.lane)
        self.assertEqual(created.prompt_draft, "new planned work")
        self.assertEqual(created.summary, "user wants this")
        self.assertEqual(created.scheduled_deps, [parent.id])
        self.assertEqual(created.proposed_by, "user")

        reloaded = self.store.load_node(self.project.id, created.id)
        self.assertIsNotNone(reloaded)
        preview = self.store.read_node_preview(self.project.id, created.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn('"prompt_draft": "new planned work"', preview)

    def test_create_virtual_can_select_model_preset(self) -> None:
        created = self.registry.create_virtual(
            self.project.id,
            prompt_draft="codex planned work",
            model_preset_id="opus-4-8",
        )

        self.assertIsNotNone(created)
        assert created is not None
        self.assertEqual(created.model_preset_id, "opus-4-8")
        self.assertEqual(created.provider, "claude")

    def test_create_virtual_rejects_compatibility_model_preset(self) -> None:
        with self.assertRaisesRegex(ValueError, "compatibility-only"):
            self.registry.create_virtual(
                self.project.id,
                prompt_draft="old preset",
                model_preset_id="opus-4-7",
            )

    def test_create_virtual_rejects_missing_dependency(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not resolve"):
            self.registry.create_virtual(
                self.project.id,
                prompt_draft="new planned work",
                scheduled_deps=["missing"],
            )

        self.assertEqual(
            [n.id for n in self.store.list_nodes(self.project.id)],
            [],
        )

    def test_create_virtual_rejects_cross_lane_dependency(self) -> None:
        parent = self._virtual("other-parent")
        parent.planspace_id = "planspaces.other"
        self.store.update_node(parent)

        with self.assertRaisesRegex(ValueError, "outside this lane"):
            self.registry.create_virtual(
                self.project.id,
                prompt_draft="new planned work",
                scheduled_deps=[parent.id],
            )

        self.assertEqual(
            [n.id for n in self.store.list_nodes(self.project.id)],
            [parent.id],
        )

    def test_create_virtual_rejects_self_dependency(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not include"):
            self.registry.create_virtual(
                self.project.id,
                node_id="new-node",
                prompt_draft="new planned work",
                scheduled_deps=["new-node"],
            )

        self.assertIsNone(self.store.load_node(self.project.id, "new-node"))

    def test_create_virtual_rejects_cycle(self) -> None:
        parent = self._virtual("parent", deps=["new-node"])

        with self.assertRaisesRegex(ValueError, "cycle"):
            self.registry.create_virtual(
                self.project.id,
                node_id="new-node",
                prompt_draft="new planned work",
                scheduled_deps=[parent.id],
            )

        self.assertIsNone(self.store.load_node(self.project.id, "new-node"))

    def test_create_virtual_allows_empty_draft_but_does_not_promote_it(self) -> None:
        created = self.registry.create_virtual(
            self.project.id,
            prompt_draft="",
        )

        self.assertIsNotNone(created)
        assert created is not None
        self.assertEqual(created.prompt_draft, "")
        self.assertIsNone(self.registry.promote_virtual(self.project.id, created.id))

    def test_create_virtual_can_record_resume_source(self) -> None:
        source = Node(
            id="source",
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.ERROR,
            planspace_id=self.lane,
            model_preset_id="gpt-5.5",
            provider_session_id="session-1",
            prompt="old",
        )
        self.store.create_node(source)

        created = self.registry.create_virtual(
            self.project.id,
            prompt_draft="continue",
            resume_from_node_id=source.id,
        )

        self.assertIsNotNone(created)
        assert created is not None
        self.assertEqual(created.resume_from_node_id, source.id)
        self.assertEqual(created.model_preset_id, "gpt-5.5")
        self.assertEqual(created.provider, "codex")

    def test_create_virtual_resume_rejects_model_preset_mismatch(self) -> None:
        source = Node(
            id="source-mismatch",
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.ERROR,
            planspace_id=self.lane,
            model_preset_id="gpt-5.5",
            provider_session_id="session-1",
            prompt="old",
        )
        self.store.create_node(source)

        with self.assertRaisesRegex(ValueError, "inherit model_preset_id"):
            self.registry.create_virtual(
                self.project.id,
                prompt_draft="continue",
                resume_from_node_id=source.id,
                model_preset_id="opus-4-7",
            )

    def test_create_virtual_rejects_unresumable_resume_source(self) -> None:
        source = Node(
            id="source",
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.DONE,
            planspace_id=self.lane,
            model_preset_id="gpt-5.5",
            prompt="old",
        )
        self.store.create_node(source)

        with self.assertRaisesRegex(ValueError, "not resumable"):
            self.registry.create_virtual(
                self.project.id,
                prompt_draft="continue",
                resume_from_node_id=source.id,
            )

    def test_delete_virtual_removes_node_and_cleans_obsolete_deps(self) -> None:
        doomed = self._virtual("doomed")
        obsolete_child = self._virtual("obsolete-child", deps=[doomed.id])
        obsolete_child.obsolete_reason = "old"
        self.store.update_node(obsolete_child)

        deleted, blockers = self.registry.delete_virtual(self.project.id, doomed.id)

        self.assertTrue(deleted)
        self.assertEqual(blockers, [])
        self.assertIsNone(self.store.load_node(self.project.id, doomed.id))
        reloaded = self.store.load_node(self.project.id, obsolete_child.id)
        assert reloaded is not None
        self.assertEqual(reloaded.scheduled_deps, [])

    def test_delete_virtual_returns_false_for_missing_node(self) -> None:
        deleted, blockers = self.registry.delete_virtual(self.project.id, "missing")

        self.assertFalse(deleted)
        self.assertEqual(blockers, [])

    def test_delete_virtual_rejects_executed_node(self) -> None:
        node = Node(
            id="done",
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.DONE,
            planspace_id=self.lane,
            model_preset_id="gpt-5.5",
            started_at=1.0,
            finished_at=2.0,
        )
        self.store.create_node(node)

        with self.assertRaisesRegex(ValueError, "only virtual nodes"):
            self.registry.delete_virtual(self.project.id, node.id)

    def test_delete_virtual_reports_non_obsolete_dependency_blockers(self) -> None:
        parent = self._virtual("parent")
        child = self._virtual("child", deps=[parent.id])

        deleted, blockers = self.registry.delete_virtual(self.project.id, parent.id)

        self.assertFalse(deleted)
        self.assertEqual(blockers, [child.id])
        self.assertIsNotNone(self.store.load_node(self.project.id, parent.id))

    def test_delete_virtual_rejects_when_project_running(self) -> None:
        node = self._virtual("busy")
        runtime = self.registry._runtimes[self.project.id]
        runtime.runner_task = _PendingTask()  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(RuntimeError, "turn in progress"):
                self.registry.delete_virtual(self.project.id, node.id)
        finally:
            runtime.runner_task.cancel()


class VirtualEditApiTests(unittest.TestCase):
    def test_patch_virtual_forwards_only_supplied_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with patch.dict(os.environ, {"MINICLAW_HOME": str(Path(raw) / "home")}):
                import miniclaw2.app as app_module

                project = Project(root_path=raw, name="Project")
                node = Node(
                    id="virt-1",
                    project_id=project.id,
                    state=NodeState.VIRTUAL,
                    model_preset_id="gpt-5.5",
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

    def test_delete_virtual_returns_blockers_body(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with patch.dict(os.environ, {"MINICLAW_HOME": str(Path(raw) / "home")}):
                import miniclaw2.app as app_module

                project = Project(root_path=raw, name="Project")
                calls: list[dict[str, object]] = []

                class _Registry:
                    store = SimpleNamespace(root=Path(raw) / "store")

                    def get_project(self, sid: str) -> Project | None:
                        return project if sid == project.id else None

                    def delete_virtual(self, sid: str, vid: str) -> tuple[bool, list[str]]:
                        calls.append({"sid": sid, "vid": vid})
                        return False, ["child"]

                with patch.object(app_module, "ProjectRegistry", return_value=_Registry()):
                    with patch.object(
                        app_module,
                        "context_refresh_status",
                        return_value={"running": False},
                    ):
                        client = TestClient(app_module.create_app())
                        try:
                            res = client.delete(f"/sessions/{project.id}/virtuals/parent")
                        finally:
                            client.close()

            self.assertEqual(res.status_code, 409, res.text)
            self.assertEqual(calls, [{"sid": project.id, "vid": "parent"}])
            self.assertEqual(res.json()["detail"], {"blockers": ["child"]})

    def test_delete_virtual_success_returns_204(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with patch.dict(os.environ, {"MINICLAW_HOME": str(Path(raw) / "home")}):
                import miniclaw2.app as app_module

                project = Project(root_path=raw, name="Project")

                class _Registry:
                    store = SimpleNamespace(root=Path(raw) / "store")

                    def get_project(self, sid: str) -> Project | None:
                        return project if sid == project.id else None

                    def delete_virtual(self, sid: str, vid: str) -> tuple[bool, list[str]]:
                        return True, []

                with patch.object(app_module, "ProjectRegistry", return_value=_Registry()):
                    with patch.object(
                        app_module,
                        "context_refresh_status",
                        return_value={"running": False},
                    ):
                        client = TestClient(app_module.create_app())
                        try:
                            res = client.delete(f"/sessions/{project.id}/virtuals/virt")
                        finally:
                            client.close()

            self.assertEqual(res.status_code, 204, res.text)


class _PendingTask:
    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
