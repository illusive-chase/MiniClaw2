"""Tests for the reap pipeline."""

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
    ReviewBrief,
    ReviewSubtype,
)
from miniclaw2.materialize import materialize_active_lane, snapshot_lane
from miniclaw2.reap import reap_lane
from miniclaw2.store import Store


def _write_preview(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _executed_payload(node_id: str, lane: str, category: str = "regular") -> dict:
    return {
        "id": node_id,
        "kind": "agent",
        "category": category,
        "state": "done",
        "ran_at": "2026-06-13T14:22:00+00:00",
        "lane": lane,
        "motivation": "m",
        "summary": "s",
        "next_implications": "ni",
    }


def _virtual_payload(slug: str, lane: str, deps: list[str] | None = None,
                     category: str = "regular") -> dict:
    payload: dict = {
        "id": slug,
        "kind": "agent",
        "category": category,
        "state": "virtual",
        "lane": lane,
        "proposed_by": "node:source",
        "motivation": "m",
        "prompt_draft": "Do thing",
    }
    if deps is not None:
        payload["scheduled_deps"] = deps
    return payload


class ReapTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self.tmp.name)
        self.store_root = tmp_path / "store"
        self.project_root = tmp_path / "project"
        self.project_root.mkdir()
        self.store = Store(root=self.store_root)
        self.project = Project(
            id="p1",
            root_path=str(self.project_root),
            model_preset_id="gpt-5.5",
        )
        self.store.create_project(self.project)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _make_running_node(
        self,
        category: Category = Category.REGULAR,
        model_preset_id: str = "gpt-5.5",
    ) -> Node:
        node = Node(
            id="n1",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=category,
            state=NodeState.DONE,
            planspace_id="lane-A",
            model_preset_id=model_preset_id,
            started_at=1.0,
            finished_at=2.0,
        )
        self.store.create_node(node)
        return node

    def _setup_lane(self, node: Node) -> tuple[Path, dict[str, str]]:
        lane_root = materialize_active_lane(self.project, node.planspace_id, self.store)
        pre = snapshot_lane(lane_root)
        return lane_root, pre


class ReapHappyPathTests(ReapTestBase):
    def test_regular_agent_writes_own_preview(self) -> None:
        node = self._make_running_node()
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A"),
        )
        result = reap_lane(self.project, node, lane_root, pre, self.store)
        self.assertTrue(result.own_preview_ok)
        self.assertFalse(result.fatal)
        self.assertEqual(result.new_virtuals, [])

    def test_planning_agent_proposes_virtual(self) -> None:
        node = self._make_running_node(
            category=Category.PLANNING,
            model_preset_id="opus-4-8",
        )
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        _write_preview(
            lane_root / "nodes" / "V_foo" / "preview.json",
            _virtual_payload("V_foo", "lane-A"),
        )
        result = reap_lane(self.project, node, lane_root, pre, self.store)
        self.assertTrue(result.ok())
        self.assertEqual(len(result.new_virtuals), 1)
        new = result.new_virtuals[0]
        self.assertEqual(new.state, NodeState.VIRTUAL)
        self.assertEqual(new.proposed_by, "node:n1")
        self.assertEqual(new.model_preset_id, "opus-4-8")
        self.assertNotEqual(new.id, "V_foo")  # canonicalized
        # And the new node is in the store
        self.assertIsNotNone(self.store.load_node("p1", new.id))

    def test_planning_agent_can_select_active_model_preset(self) -> None:
        node = self._make_running_node(
            category=Category.PLANNING,
            model_preset_id="gpt-5.6",
        )
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        virtual = _virtual_payload("V_claude", "lane-A")
        virtual["model_preset_id"] = "opus-4-8"
        _write_preview(
            lane_root / "nodes" / "V_claude" / "preview.json",
            virtual,
        )

        result = reap_lane(self.project, node, lane_root, pre, self.store)

        self.assertTrue(result.ok())
        self.assertEqual(result.new_virtuals[0].model_preset_id, "opus-4-8")
        self.assertEqual(result.new_virtuals[0].provider, "claude")


class ReapCategoryEnforcementTests(ReapTestBase):
    def test_regular_agent_proposing_virtual_is_fatal(self) -> None:
        node = self._make_running_node()
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A"),
        )
        _write_preview(
            lane_root / "nodes" / "V_x" / "preview.json",
            _virtual_payload("V_x", "lane-A"),
        )
        result = reap_lane(self.project, node, lane_root, pre, self.store)
        self.assertTrue(result.fatal)
        # No virtual persisted.
        self.assertEqual(self.store.list_nodes("p1"), [node] if False else self.store.list_nodes("p1"))
        ids = {n.id for n in self.store.list_nodes("p1")}
        self.assertEqual(ids, {"n1"})

    def test_review_agent_cannot_select_model_preset(self) -> None:
        node = Node(
            id="n1",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REVIEW,
            subtype=ReviewSubtype.AGENTIC_REVIEW,
            brief=ReviewBrief(check_what="x", expected="y", abnormal="z"),
            state=NodeState.DONE,
            planspace_id="lane-A",
            model_preset_id="gpt-5.6",
            started_at=1.0,
            finished_at=2.0,
        )
        self.store.create_node(node)
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            {
                **_executed_payload("n1", "lane-A", category="review"),
                "subtype": "agentic_review",
            },
        )
        virtual = _virtual_payload("V_x", "lane-A")
        virtual["model_preset_id"] = "opus-4-8"
        _write_preview(
            lane_root / "nodes" / "V_x" / "preview.json",
            virtual,
        )

        result = reap_lane(self.project, node, lane_root, pre, self.store)

        self.assertTrue(result.fatal)
        self.assertTrue(
            any(
                "only planning agents may set model_preset_id" in reason
                for reason in result.rejection_reasons
            )
        )
        self.assertEqual({n.id for n in self.store.list_nodes("p1")}, {"n1"})

    def test_unknown_model_preset_is_rejected(self) -> None:
        node = self._make_running_node(category=Category.PLANNING)
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        virtual = _virtual_payload("V_x", "lane-A")
        virtual["model_preset_id"] = "not-configured"
        _write_preview(lane_root / "nodes" / "V_x" / "preview.json", virtual)

        result = reap_lane(self.project, node, lane_root, pre, self.store)

        self.assertTrue(result.fatal)
        self.assertTrue(
            any("unknown model_preset_id" in reason for reason in result.rejection_reasons)
        )
        self.assertEqual({n.id for n in self.store.list_nodes("p1")}, {"n1"})

    def test_compatibility_model_preset_cannot_be_newly_selected(self) -> None:
        node = self._make_running_node(
            category=Category.PLANNING,
            model_preset_id="gpt-5.6",
        )
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        virtual = _virtual_payload("V_x", "lane-A")
        virtual["model_preset_id"] = "gpt-5.5"
        _write_preview(lane_root / "nodes" / "V_x" / "preview.json", virtual)

        result = reap_lane(self.project, node, lane_root, pre, self.store)

        self.assertTrue(result.fatal)
        self.assertTrue(
            any("compatibility-only" in reason for reason in result.rejection_reasons)
        )

    def test_explicit_null_model_preset_is_rejected(self) -> None:
        node = self._make_running_node(category=Category.PLANNING)
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        virtual = _virtual_payload("V_x", "lane-A")
        virtual["model_preset_id"] = None
        _write_preview(lane_root / "nodes" / "V_x" / "preview.json", virtual)

        result = reap_lane(self.project, node, lane_root, pre, self.store)

        self.assertTrue(result.fatal)
        self.assertTrue(
            any("is required when explicitly set" in reason for reason in result.rejection_reasons)
        )


class ReapMissingOwnPreviewTests(ReapTestBase):
    def test_missing_own_preview_signals_for_reprompt(self) -> None:
        node = self._make_running_node()
        lane_root, pre = self._setup_lane(node)
        # No write at all.
        result = reap_lane(self.project, node, lane_root, pre, self.store)
        self.assertFalse(result.own_preview_ok)
        self.assertFalse(result.fatal)
        self.assertTrue(result.rejection_reasons)


class ReapSlugCanonicalizationTests(ReapTestBase):
    def test_sibling_slug_references_resolve(self) -> None:
        node = self._make_running_node(category=Category.PLANNING)
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        _write_preview(
            lane_root / "nodes" / "V_a" / "preview.json",
            _virtual_payload("V_a", "lane-A"),
        )
        _write_preview(
            lane_root / "nodes" / "V_b" / "preview.json",
            _virtual_payload("V_b", "lane-A", deps=["V_a"]),
        )
        result = reap_lane(self.project, node, lane_root, pre, self.store)
        self.assertTrue(result.ok())
        self.assertEqual(len(result.new_virtuals), 2)
        by_initial_slug = {n.id: n for n in result.new_virtuals}
        # Find V_b's canonical (the one with non-empty deps)
        v_b = next(n for n in result.new_virtuals if n.scheduled_deps)
        v_a = next(n for n in result.new_virtuals if not n.scheduled_deps)
        self.assertEqual(v_b.scheduled_deps, [v_a.id])

    def test_unknown_dep_is_fatal(self) -> None:
        node = self._make_running_node(category=Category.PLANNING)
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        _write_preview(
            lane_root / "nodes" / "V_a" / "preview.json",
            _virtual_payload("V_a", "lane-A", deps=["does_not_exist"]),
        )
        result = reap_lane(self.project, node, lane_root, pre, self.store)
        self.assertTrue(result.fatal)

    def test_existing_cross_lane_dep_is_fatal(self) -> None:
        node = self._make_running_node(category=Category.PLANNING)
        other_lane_parent = Node(
            id="other-parent",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.DONE,
            planspace_id="lane-B",
            model_preset_id="gpt-5.5",
            started_at=1.0,
            finished_at=2.0,
        )
        self.store.create_node(other_lane_parent)
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        _write_preview(
            lane_root / "nodes" / "V_a" / "preview.json",
            _virtual_payload("V_a", "lane-A", deps=[other_lane_parent.id]),
        )

        result = reap_lane(self.project, node, lane_root, pre, self.store)

        self.assertTrue(result.fatal)
        self.assertTrue(
            any("outside this lane" in reason for reason in result.rejection_reasons)
        )

    def test_cross_lane_virtual_mutation_is_fatal(self) -> None:
        node = self._make_running_node(category=Category.PLANNING)
        other_lane_virtual = Node(
            id="other-virtual",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.VIRTUAL,
            planspace_id="lane-B",
            model_preset_id="gpt-5.5",
            prompt_draft="x",
            proposed_by="user",
            summary="m",
        )
        self.store.create_node(other_lane_virtual)
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        _write_preview(
            lane_root / "nodes" / "other-virtual" / "preview.json",
            _virtual_payload("other-virtual", "lane-A"),
        )

        result = reap_lane(self.project, node, lane_root, pre, self.store)

        self.assertTrue(result.fatal)
        self.assertTrue(
            any("outside this lane" in reason for reason in result.rejection_reasons)
        )

    def test_virtual_mutation_preserves_resume_edge(self) -> None:
        node = self._make_running_node(category=Category.PLANNING)
        resume_source = Node(
            id="build",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.DONE,
            planspace_id="lane-A",
            model_preset_id="gpt-5.5",
            started_at=1.0,
            finished_at=2.0,
        )
        existing_virtual = Node(
            id="fix",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.VIRTUAL,
            planspace_id="lane-A",
            model_preset_id="gpt-5.5",
            prompt_draft="initial fix",
            scheduled_deps=[resume_source.id],
            resume_from_node_id=resume_source.id,
            proposed_by="template:resume-fix-after-reject",
            summary="initial motivation",
        )
        self.store.create_node(resume_source)
        self.store.create_node(existing_virtual)
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        rewritten = _virtual_payload("fix", "lane-A", deps=[resume_source.id])
        rewritten["prompt_draft"] = "edited fix"
        _write_preview(
            lane_root / "nodes" / "fix" / "preview.json",
            rewritten,
        )

        result = reap_lane(self.project, node, lane_root, pre, self.store)

        self.assertTrue(result.ok())
        self.assertEqual(len(result.modified_virtuals), 1)
        self.assertEqual(result.modified_virtuals[0].resume_from_node_id, "build")
        persisted = self.store.load_node("p1", "fix")
        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertEqual(persisted.resume_from_node_id, "build")
        self.assertEqual(persisted.prompt_draft, "edited fix")

    def test_virtual_mutation_keeps_existing_model_preset(self) -> None:
        node = self._make_running_node(
            category=Category.PLANNING,
            model_preset_id="opus-4-8",
        )
        existing_virtual = Node(
            id="existing",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.VIRTUAL,
            planspace_id="lane-A",
            model_preset_id="gpt-5.6",
            prompt_draft="initial",
            proposed_by="user",
            summary="initial motivation",
        )
        self.store.create_node(existing_virtual)
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        rewritten = _virtual_payload("existing", "lane-A")
        rewritten["prompt_draft"] = "edited"
        _write_preview(
            lane_root / "nodes" / "existing" / "preview.json",
            rewritten,
        )

        result = reap_lane(self.project, node, lane_root, pre, self.store)

        self.assertTrue(result.ok())
        persisted = self.store.load_node("p1", "existing")
        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertEqual(persisted.model_preset_id, "gpt-5.6")
        self.assertEqual(persisted.prompt_draft, "edited")

    def test_virtual_mutation_can_select_active_model_preset(self) -> None:
        node = self._make_running_node(
            category=Category.PLANNING,
            model_preset_id="gpt-5.6",
        )
        existing_virtual = Node(
            id="existing",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.VIRTUAL,
            planspace_id="lane-A",
            model_preset_id="gpt-5.6",
            prompt_draft="initial",
            proposed_by="user",
            summary="initial motivation",
        )
        self.store.create_node(existing_virtual)
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        rewritten = _virtual_payload("existing", "lane-A")
        rewritten["model_preset_id"] = "opus-4-8"
        _write_preview(
            lane_root / "nodes" / "existing" / "preview.json",
            rewritten,
        )

        result = reap_lane(self.project, node, lane_root, pre, self.store)

        self.assertTrue(result.ok())
        persisted = self.store.load_node("p1", "existing")
        assert persisted is not None
        self.assertEqual(persisted.model_preset_id, "opus-4-8")
        self.assertEqual(persisted.provider, "claude")

    def test_resume_virtual_rejects_model_preset_selection(self) -> None:
        node = self._make_running_node(
            category=Category.PLANNING,
            model_preset_id="gpt-5.6",
        )
        resume_source = Node(
            id="build",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.DONE,
            planspace_id="lane-A",
            model_preset_id="gpt-5.6",
            started_at=1.0,
            finished_at=2.0,
        )
        existing_virtual = Node(
            id="fix",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.VIRTUAL,
            planspace_id="lane-A",
            model_preset_id="gpt-5.6",
            prompt_draft="fix",
            scheduled_deps=[resume_source.id],
            resume_from_node_id=resume_source.id,
            proposed_by="user",
            summary="continue build session",
        )
        self.store.create_node(resume_source)
        self.store.create_node(existing_virtual)
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        rewritten = _virtual_payload("fix", "lane-A", deps=[resume_source.id])
        rewritten["model_preset_id"] = "opus-4-8"
        _write_preview(lane_root / "nodes" / "fix" / "preview.json", rewritten)

        result = reap_lane(self.project, node, lane_root, pre, self.store)

        self.assertTrue(result.fatal)
        self.assertTrue(
            any("inherit model_preset_id" in reason for reason in result.rejection_reasons)
        )
        persisted = self.store.load_node("p1", "fix")
        assert persisted is not None
        self.assertEqual(persisted.model_preset_id, "gpt-5.6")


class ReapCycleDetectionTests(ReapTestBase):
    def test_self_loop_is_fatal(self) -> None:
        node = self._make_running_node(category=Category.PLANNING)
        # Pre-exists in the store as a virtual to give us something to reference.
        prior = Node(
            id="v-existing",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.VIRTUAL,
            planspace_id="lane-A",
            model_preset_id="gpt-5.5",
            prompt_draft="x",
            proposed_by="user",
            summary="m",
        )
        self.store.create_node(prior)
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        # Mutate the pre-existing virtual to depend on a new one that depends back on it.
        _write_preview(
            lane_root / "nodes" / "v-existing" / "preview.json",
            _virtual_payload("v-existing", "lane-A", deps=["V_new"]),
        )
        _write_preview(
            lane_root / "nodes" / "V_new" / "preview.json",
            _virtual_payload("V_new", "lane-A", deps=["v-existing"]),
        )
        result = reap_lane(self.project, node, lane_root, pre, self.store)
        self.assertTrue(result.fatal)


class ReapDeletionTests(ReapTestBase):
    def test_deleted_preview_is_fatal(self) -> None:
        node = self._make_running_node(category=Category.PLANNING)
        prior = Node(
            id="v-existing",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.VIRTUAL,
            planspace_id="lane-A",
            model_preset_id="gpt-5.5",
            prompt_draft="x",
            proposed_by="user",
            summary="m",
        )
        self.store.create_node(prior)
        lane_root, pre = self._setup_lane(node)
        # Delete v-existing's preview file.
        (lane_root / "nodes" / "v-existing" / "preview.json").unlink()
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        result = reap_lane(self.project, node, lane_root, pre, self.store)
        self.assertTrue(result.fatal)

    def test_user_deleted_virtual_is_not_resurrected_by_stale_rewrite(self) -> None:
        node = self._make_running_node(category=Category.PLANNING)
        prior = Node(
            id="v-existing",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.VIRTUAL,
            planspace_id="lane-A",
            model_preset_id="gpt-5.5",
            prompt_draft="initial",
            proposed_by="user",
            summary="initial motivation",
        )
        self.store.create_node(prior)
        lane_root, pre = self._setup_lane(node)

        self.store.delete_node("p1", prior.id)
        stale_path = lane_root / "nodes" / prior.id / "preview.json"
        stale_payload = json.loads(stale_path.read_text(encoding="utf-8"))
        stale_payload["prompt_draft"] = "stale planner rewrite"
        _write_preview(
            stale_path,
            stale_payload,
        )
        _write_preview(
            lane_root / "nodes" / node.id / "preview.json",
            _executed_payload(node.id, "lane-A", category="planning"),
        )

        result = reap_lane(self.project, node, lane_root, pre, self.store)

        self.assertTrue(result.ok())
        self.assertEqual(result.new_virtuals, [])
        self.assertEqual(result.modified_virtuals, [])
        self.assertIsNone(self.store.load_node("p1", prior.id))


class ReapAtomicityTests(ReapTestBase):
    def test_fatal_batch_persists_nothing(self) -> None:
        node = self._make_running_node(category=Category.PLANNING)
        lane_root, pre = self._setup_lane(node)
        _write_preview(
            lane_root / "nodes" / "n1" / "preview.json",
            _executed_payload("n1", "lane-A", category="planning"),
        )
        # Valid virtual + invalid dep → fatal whole batch.
        _write_preview(
            lane_root / "nodes" / "V_valid" / "preview.json",
            _virtual_payload("V_valid", "lane-A"),
        )
        _write_preview(
            lane_root / "nodes" / "V_invalid" / "preview.json",
            _virtual_payload("V_invalid", "lane-A", deps=["nonexistent"]),
        )
        ids_before = {n.id for n in self.store.list_nodes("p1")}
        result = reap_lane(self.project, node, lane_root, pre, self.store)
        ids_after = {n.id for n in self.store.list_nodes("p1")}
        self.assertTrue(result.fatal)
        self.assertEqual(ids_before, ids_after)


if __name__ == "__main__":
    unittest.main()
