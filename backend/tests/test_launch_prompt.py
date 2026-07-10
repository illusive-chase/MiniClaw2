"""Tests for the category-aware launch prompt composer."""

from __future__ import annotations

import unittest

from miniclaw2.domain import (
    Category,
    Node,
    NodeKind,
    NodeState,
    ReviewBrief,
    ReviewSubtype,
)
from miniclaw2.launch_prompt import (
    anti_self_poisoning_block,
    build_category_launch_block,
    build_dependency_launch_block,
)
from miniclaw2.materialize import GRAPH_DIRNAME


def _agent_node(
    *,
    node_id: str = "n1",
    lane_id: str | None = "lane-A",
    category: Category = Category.REGULAR,
    subtype: ReviewSubtype | None = None,
    brief: ReviewBrief | None = None,
    scheduled_deps: list[str] | None = None,
) -> Node:
    return Node(
        id=node_id,
        project_id="p1",
        kind=NodeKind.AGENT,
        model_preset_id="gpt-5.5",
        state=NodeState.RUNNING,
        category=category,
        subtype=subtype,
        brief=brief,
        planspace_id=lane_id,
        scheduled_deps=list(scheduled_deps or []),
    )


class RegularBlockTests(unittest.TestCase):
    def test_substitutions_applied(self) -> None:
        node = _agent_node(node_id="n-abc", lane_id="lane-X")
        block = build_category_launch_block(node)
        self.assertIn("regular execution node", block)
        # placeholder substitutions
        self.assertNotIn("<<lane_path>>", block)
        self.assertNotIn("<<node_id>>", block)
        self.assertNotIn("<<lane_id>>", block)
        self.assertIn(f"{GRAPH_DIRNAME}/lane-X", block)
        self.assertIn("n-abc", block)
        self.assertIn('"lane": "lane-X"', block)
        # JSON braces from the example preserved
        self.assertIn('"category": "regular"', block)
        # No virtual write rights for regular
        self.assertIn("Do not", block)

    def test_empty_lane_id_renders_lane_root_only(self) -> None:
        node = _agent_node(lane_id="")
        block = build_category_launch_block(node)
        # path is graph dir with trailing slash trimmed
        self.assertIn(GRAPH_DIRNAME, block)


class PlanningBlockTests(unittest.TestCase):
    def test_planning_grants_virtual_writes(self) -> None:
        node = _agent_node(category=Category.PLANNING)
        block = build_category_launch_block(node)
        self.assertIn("planning node", block)
        self.assertIn("Virtual preview shape", block)
        self.assertIn("scheduled_deps", block)
        self.assertIn(f"node:{node.id}", block)


class DependencyBlockTests(unittest.TestCase):
    def test_empty_when_no_scheduled_deps(self) -> None:
        node = _agent_node()
        self.assertEqual(build_dependency_launch_block(node), "")

    def test_lists_dependency_preview_paths(self) -> None:
        node = _agent_node(
            lane_id="lane-X",
            scheduled_deps=["dep-a", "dep-b"],
        )
        block = build_dependency_launch_block(node)

        self.assertIn("scheduled dependency index", block)
        self.assertIn(
            f"`dep-a` -> `{GRAPH_DIRNAME}/lane-X/nodes/dep-a/preview.json`",
            block,
        )
        self.assertIn(
            f"`dep-b` -> `{GRAPH_DIRNAME}/lane-X/nodes/dep-b/preview.json`",
            block,
        )


class AgenticReviewBlockTests(unittest.TestCase):
    def test_brief_inlined(self) -> None:
        brief = ReviewBrief(
            check_what="signup unit test passes",
            expected="no hardcoded urls",
            abnormal="coverage drops below 70%",
        )
        node = _agent_node(
            category=Category.REVIEW,
            subtype=ReviewSubtype.AGENTIC_REVIEW,
            brief=brief,
        )
        block = build_category_launch_block(node)
        self.assertIn("agentic review", block)
        self.assertIn("signup unit test passes", block)
        self.assertIn("no hardcoded urls", block)
        self.assertIn("coverage drops below 70%", block)
        # Does NOT reference human-review.md
        self.assertNotIn("human-review.md", block)


class HumanInteractReviewBlockTests(unittest.TestCase):
    def test_block_references_human_review_path_and_brief(self) -> None:
        brief = ReviewBrief(
            check_what="the auth design is sound",
            expected="JWT with rotating refresh",
            abnormal="session cookies",
        )
        node = _agent_node(
            node_id="rev-1",
            lane_id="lane-A",
            category=Category.REVIEW,
            subtype=ReviewSubtype.HUMAN_INTERACT_REVIEW,
            brief=brief,
        )
        block = build_category_launch_block(node)
        self.assertIn("human-interact review", block)
        self.assertIn(f"{GRAPH_DIRNAME}/lane-A/nodes/rev-1/human-review.md", block)
        self.assertIn("JWT with rotating refresh", block)
        self.assertIn("session cookies", block)


class OpAndUnknownTests(unittest.TestCase):
    def test_op_node_gets_empty_block(self) -> None:
        node = Node(
            project_id="p1",
            kind=NodeKind.OP,
            op_kind="commit",
            state=NodeState.QUEUED,
        )
        self.assertEqual(build_category_launch_block(node), "")


class AntiSelfPoisoningTests(unittest.TestCase):
    def test_footer_loaded(self) -> None:
        text = anti_self_poisoning_block()
        self.assertIn("Anti-self-poisoning", text)
        self.assertIn("transient", text.lower())


class RunnerCompositionTests(unittest.TestCase):
    """Smoke test that the runner's launch instruction composition
    places the category block first and the anti-self-poisoning
    guidance footer last (per IMPLEMENTATION_STATUS §4 contract)."""

    def test_compose_order(self) -> None:
        from miniclaw2.runner import _compose_launch_instructions

        cat = "CATEGORY-BLOCK"
        bundle = "BUNDLE-TURN-TEXT"
        lang = "LANGUAGE-HINT"
        anti = "ANTI-POISON"
        out = _compose_launch_instructions(cat, bundle, lang, anti)
        idx_cat = out.find(cat)
        idx_bundle = out.find(bundle)
        idx_lang = out.find(lang)
        idx_anti = out.find(anti)
        self.assertGreaterEqual(idx_cat, 0)
        self.assertLess(idx_cat, idx_bundle)
        self.assertLess(idx_bundle, idx_lang)
        self.assertLess(idx_lang, idx_anti)

    def test_empty_parts_are_dropped(self) -> None:
        from miniclaw2.runner import _compose_launch_instructions

        out = _compose_launch_instructions("", "X", "", "Y")
        self.assertEqual(out, "X\n\n---\n\nY")


if __name__ == "__main__":
    unittest.main()
