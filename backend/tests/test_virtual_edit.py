from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from miniclaw2.contextspace import create_planspace, set_planspace_mode
from miniclaw2.domain import (
    ArtifactMode,
    Category,
    Node,
    NodeKind,
    NodeState,
    Project,
    ReviewBrief,
    ReviewSubtype,
)
from miniclaw2.registry import PlanspaceModePreconditionError, ProjectRegistry
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

    def test_update_virtual_preserves_owner_host_so_edits_stay_repeatable(
        self,
    ) -> None:
        """The updated node is revalidated from a dump, which drops private
        attributes. The owner host must be rebound, or the edited node reports
        belonging to no host and every ownership check reads that as foreign —
        locking the node the user just edited."""
        child = self._virtual("child")

        updated = self.registry.update_virtual(
            self.project.id, child.id, prompt_draft="new draft"
        )

        assert updated is not None
        self.assertEqual(updated.owner_host_id, self.store.machine.id)
        self.assertTrue(self.registry.is_native_node(self.project, updated))

        # The node the panel now holds must still accept a second edit.
        again = self.registry.update_virtual(
            self.project.id, child.id, prompt_draft="newer draft"
        )
        assert again is not None
        self.assertEqual(again.owner_host_id, self.store.machine.id)

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

    def test_update_virtual_mode_precondition_prevents_auto_lane_write(self) -> None:
        node = self._virtual("conditional")

        set_planspace_mode(
            self.project,
            self.lane,
            "auto",
            store_root=self.store.root,
        )

        with self.assertRaises(PlanspaceModePreconditionError):
            self.registry.update_virtual(
                self.project.id,
                node.id,
                expected_planspace_mode="manual",
                prompt_draft="must not be persisted",
            )

        unchanged = self.store.load_node(self.project.id, node.id)
        assert unchanged is not None
        self.assertEqual(unchanged.prompt_draft, node.prompt_draft)
        self.assertNotIn(node.id, self.registry._runtimes[self.project.id].runner_tasks)

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

    def test_update_virtual_can_toggle_library_op_kind(self) -> None:
        node = self._virtual("classify-node")

        promoted = self.registry.update_virtual(
            self.project.id,
            node.id,
            agent_op_kind="library_edit",
        )
        assert promoted is not None
        self.assertEqual(promoted.agent_op_kind, "library_edit")
        self.assertEqual(promoted.category, Category.REGULAR)
        reloaded = self.store.load_node(self.project.id, node.id)
        assert reloaded is not None
        self.assertEqual(reloaded.agent_op_kind, "library_edit")

        cleared = self.registry.update_virtual(
            self.project.id,
            node.id,
            agent_op_kind=None,
        )
        assert cleared is not None
        self.assertIsNone(cleared.agent_op_kind)

    def test_update_virtual_rejects_unknown_agent_op_kind(self) -> None:
        node = self._virtual("bad-op-kind")

        with self.assertRaises(ValueError):
            self.registry.update_virtual(
                self.project.id,
                node.id,
                agent_op_kind="skill_edit",
            )

    def test_update_virtual_rejects_library_op_kind_with_review_category(self) -> None:
        node = self._virtual("librarian-node")
        self.registry.update_virtual(
            self.project.id,
            node.id,
            agent_op_kind="library_edit",
        )

        with self.assertRaises(ValueError):
            self.registry.update_virtual(
                self.project.id,
                node.id,
                category="review",
                subtype="agentic_review",
                brief={"check_what": "c", "expected": "e", "abnormal": "a"},
            )

        unchanged = self.store.load_node(self.project.id, node.id)
        assert unchanged is not None
        self.assertEqual(unchanged.agent_op_kind, "library_edit")
        self.assertEqual(unchanged.category, Category.REGULAR)

    def test_switching_librarian_to_review_clears_op_kind_in_one_call(self) -> None:
        node = self._virtual("switch-node")
        self.registry.update_virtual(
            self.project.id,
            node.id,
            agent_op_kind="library_edit",
        )

        switched = self.registry.update_virtual(
            self.project.id,
            node.id,
            agent_op_kind=None,
            category="review",
            subtype="agentic_review",
            brief={"check_what": "c", "expected": "e", "abnormal": "a"},
        )
        assert switched is not None
        self.assertIsNone(switched.agent_op_kind)
        self.assertEqual(switched.category, Category.REVIEW)

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

    def test_metadata_only_edit_allows_blank_regular_virtual(self) -> None:
        node = self.registry.create_virtual(
            self.project.id,
            prompt_draft="",
            planspace_id=self.lane,
        )
        assert node is not None

        updated = self.registry.update_virtual(
            self.project.id,
            node.id,
            obsolete_reason="not needed",
        )

        assert updated is not None
        self.assertEqual(updated.obsolete_reason, "not needed")

    def test_code_review_to_regular_rejects_empty_prompt_immediately(self) -> None:
        node = self.registry.create_virtual(
            self.project.id,
            prompt_draft="",
            category=Category.REVIEW,
            subtype=ReviewSubtype.CODE_REVIEW,
            planspace_id=self.lane,
        )
        assert node is not None

        with self.assertRaisesRegex(ValueError, "prompt_draft"):
            self.registry.update_virtual(
                self.project.id,
                node.id,
                category=Category.REGULAR,
            )

    def test_switching_away_from_code_review_discards_default_target(self) -> None:
        node = self.registry.create_virtual(
            self.project.id,
            prompt_draft="review this",
            category=Category.REVIEW,
            subtype=ReviewSubtype.CODE_REVIEW,
            planspace_id=self.lane,
        )
        assert node is not None and node.review_target is not None

        updated = self.registry.update_virtual(
            self.project.id,
            node.id,
            subtype=ReviewSubtype.AGENTIC_REVIEW,
            brief=ReviewBrief(
                check_what="behavior",
                expected="works",
                abnormal="fails",
            ),
        )

        assert updated is not None
        self.assertEqual(updated.subtype, ReviewSubtype.AGENTIC_REVIEW)
        self.assertIsNone(updated.review_target)

    def test_non_code_review_rejects_explicit_non_null_target(self) -> None:
        node = self.registry.create_virtual(
            self.project.id,
            prompt_draft="review this",
            category=Category.REVIEW,
            subtype=ReviewSubtype.CODE_REVIEW,
            planspace_id=self.lane,
        )
        assert node is not None

        with self.assertRaisesRegex(ValueError, "review_target"):
            self.registry.update_virtual(
                self.project.id,
                node.id,
                category=Category.REGULAR,
                review_target={"type": "uncommitted"},
            )

    def test_update_virtual_toggles_qa_mode_both_ways(self) -> None:
        node = self._virtual("qa-node")

        on = self.registry.update_virtual(self.project.id, node.id, qa_mode=True)
        assert on is not None
        self.assertTrue(on.qa_mode)

        # False is falsy, so only a real _UNSET sentinel can distinguish
        # "not sent" from "explicitly turned off".
        off = self.registry.update_virtual(
            self.project.id, node.id, qa_mode=False
        )
        assert off is not None
        self.assertFalse(off.qa_mode)

    def test_update_virtual_leaves_qa_mode_alone_when_not_sent(self) -> None:
        node = self._virtual("qa-keep")
        self.registry.update_virtual(self.project.id, node.id, qa_mode=True)

        updated = self.registry.update_virtual(
            self.project.id, node.id, prompt_draft="unrelated edit"
        )

        assert updated is not None
        self.assertTrue(updated.qa_mode)

    def test_update_virtual_sets_and_clears_artifact_mode(self) -> None:
        node = self._virtual("artifact-node")

        markdown = self.registry.update_virtual(
            self.project.id, node.id, artifact_mode="markdown"
        )
        assert markdown is not None
        self.assertIs(markdown.artifact_mode, ArtifactMode.MARKDOWN)

        back = self.registry.update_virtual(
            self.project.id, node.id, artifact_mode="default"
        )
        assert back is not None
        self.assertIs(back.artifact_mode, ArtifactMode.DEFAULT)

    def test_clearing_mode_without_sending_spec_drops_the_stale_spec(
        self,
    ) -> None:
        """Both artifact fields resolve in one branch.

        Resolving them separately would leave a custom spec behind when only the
        mode moves back to default, and the paired Node invariant then rejects a
        save the user never asked for.
        """
        node = self._virtual("artifact-custom")
        self.registry.update_virtual(
            self.project.id,
            node.id,
            artifact_mode="custom",
            artifact_spec="one table",
        )

        cleared = self.registry.update_virtual(
            self.project.id, node.id, artifact_mode="default"
        )

        assert cleared is not None
        self.assertIs(cleared.artifact_mode, ArtifactMode.DEFAULT)
        self.assertEqual(cleared.artifact_spec, "")

    def test_update_virtual_custom_requires_spec(self) -> None:
        node = self._virtual("artifact-bad")

        with self.assertRaises(ValueError):
            self.registry.update_virtual(
                self.project.id, node.id, artifact_mode="custom"
            )

    def test_update_virtual_rejects_unknown_artifact_mode(self) -> None:
        node = self._virtual("artifact-unknown")

        with self.assertRaises(ValueError):
            self.registry.update_virtual(
                self.project.id, node.id, artifact_mode="pdf"
            )

    def test_switching_to_review_with_zeroed_artifact_fields_succeeds(
        self,
    ) -> None:
        """The panel zeroes both fields when the classification leaves work.

        Without that, this same call carries a review category plus a live
        artifact intent and the user gets a 400 they cannot connect to the
        classification switch they just made.
        """
        node = self._virtual("artifact-switch")
        self.registry.update_virtual(
            self.project.id, node.id, artifact_mode="markdown"
        )

        updated = self.registry.update_virtual(
            self.project.id,
            node.id,
            category=Category.REVIEW,
            subtype=ReviewSubtype.AGENTIC_REVIEW,
            brief=ReviewBrief(check_what="c", expected="e", abnormal="a"),
            artifact_mode="default",
            artifact_spec="",
        )

        assert updated is not None
        self.assertIs(updated.category, Category.REVIEW)
        self.assertIs(updated.artifact_mode, ArtifactMode.DEFAULT)

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
            prompt_draft="claude planned work",
            model_preset_id="opus-4-7",
        )

        self.assertIsNotNone(created)
        assert created is not None
        self.assertEqual(created.model_preset_id, "opus-4-7")
        self.assertEqual(created.provider, "claude")

    def test_create_virtual_rejects_compatibility_model_preset(self) -> None:
        with self.assertRaisesRegex(ValueError, "compatibility-only"):
            self.registry.create_virtual(
                self.project.id,
                prompt_draft="old preset",
                model_preset_id="gpt-5.5",
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

    def test_delete_unrelated_virtual_while_project_running(self) -> None:
        running = Node(
            id="running",
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.RUNNING,
            planspace_id=self.lane,
            model_preset_id="gpt-5.5",
            prompt="work",
        )
        self.store.create_node(running)
        node = self._virtual("unrelated")
        runtime = self.registry._runtimes[self.project.id]
        runtime.runner_tasks[running.id] = _PendingTask()  # type: ignore[assignment]
        try:
            deleted, blockers = self.registry.delete_virtual(self.project.id, node.id)
        finally:
            runtime.runner_tasks[running.id].cancel()

        self.assertTrue(deleted)
        self.assertEqual(blockers, [])
        self.assertIsNone(self.store.load_node(self.project.id, node.id))
        self.assertIsNotNone(self.store.load_node(self.project.id, running.id))


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
                        if kwargs.get("expected_planspace_mode") == "auto":
                            raise PlanspaceModePreconditionError(
                                "planspace mode changed before the virtual node was saved"
                            )
                        calls.append({"sid": sid, "vid": vid, **kwargs})
                        return node

                with patch.object(app_module, "ProjectRegistry", return_value=_Registry()):
                    client = TestClient(app_module.create_app())
                    try:
                        res = client.patch(
                            f"/sessions/{project.id}/virtuals/{node.id}",
                            json={
                                "expected_planspace_mode": "manual",
                                "prompt_draft": "updated",
                                "motivation": "new motivation",
                                "obsolete_reason": None,
                            },
                        )
                        conflict = client.patch(
                            f"/sessions/{project.id}/virtuals/{node.id}",
                            json={
                                "expected_planspace_mode": "auto",
                                "prompt_draft": "must not be saved",
                            },
                        )
                    finally:
                        client.close()

            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(conflict.status_code, 409, conflict.text)
            self.assertEqual(calls, [{
                "sid": project.id,
                "vid": node.id,
                "expected_planspace_mode": "manual",
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
