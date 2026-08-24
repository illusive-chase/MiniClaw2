"""Tests for the category-aware launch prompt composer."""

from __future__ import annotations

import unittest

from miniclaw2.domain import (
    ArtifactMode,
    Category,
    Node,
    NodeKind,
    NodeState,
    ReviewBrief,
    ReviewSubtype,
)
from miniclaw2.launch_prompt import (
    anti_self_poisoning_block,
    build_artifact_requirement,
    build_category_launch_block,
    build_dependency_launch_block,
    build_qa_mode_block,
    shared_host_processes_block,
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
        block = build_category_launch_block(
            node,
            outputs_path="/tmp/project/.miniclaw2/outputs/n-abc",
        )
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
        self.assertIn("/tmp/project/.miniclaw2/outputs/n-abc", block)
        self.assertIn('"artifacts": ["report.md"]', block)
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
        self.assertIn('"model_preset_id": "<active preset id>"', block)
        self.assertIn("Omit it by default", block)
        self.assertIn("inherits this planning node's preset, `gpt-5.5`", block)
        self.assertIn('provider="claude"', block)
        self.assertIn('provider="codex"', block)
        self.assertIn("when the user explicitly asks", block)


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

    def test_foreign_dependency_adds_device_and_path_disclaimer(self) -> None:
        node = _agent_node(scheduled_deps=["local", "remote"])

        block = build_dependency_launch_block(
            node,
            foreign_hosts={"remote": "workstation"},
        )

        self.assertIn('another device, "workstation"', block)
        self.assertIn("absolute paths", block)
        self.assertEqual(block.count("another device"), 1)


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
        self.assertNotIn('"model_preset_id":', block)
        self.assertIn("Model selection is framework-owned", block)
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
        self.assertNotIn('"model_preset_id":', block)
        self.assertIn("Model selection is framework-owned", block)


class ArtifactInstructionsTests(unittest.TestCase):
    def test_long_markdown_and_html_are_written_section_by_section(self) -> None:
        brief = ReviewBrief(
            check_what="the artifact is complete",
            expected="all sections are present",
            abnormal="the artifact is truncated",
        )
        nodes = [
            _agent_node(category=Category.REGULAR),
            _agent_node(category=Category.PLANNING),
            _agent_node(
                category=Category.REVIEW,
                subtype=ReviewSubtype.AGENTIC_REVIEW,
                brief=brief,
            ),
            _agent_node(
                category=Category.REVIEW,
                subtype=ReviewSubtype.HUMAN_INTERACT_REVIEW,
                brief=brief,
            ),
        ]

        for node in nodes:
            with self.subTest(category=node.category, subtype=node.subtype):
                block = build_category_launch_block(node)
                self.assertIn("`.md` or `.html` artifact is long", block)
                self.assertIn("one section at a time", block)


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
    guidance footer last."""

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


class ArtifactRequirementTests(unittest.TestCase):
    def _render(self, node: Node) -> str:
        return build_category_launch_block(
            node,
            lane_path="lane/path",
            outputs_path="/tmp/outputs",
        )

    def test_no_template_leaks_the_token(self) -> None:
        cases = [
            _agent_node(category=Category.REGULAR),
            _agent_node(category=Category.PLANNING),
            _agent_node(
                category=Category.REVIEW,
                subtype=ReviewSubtype.AGENTIC_REVIEW,
                brief=ReviewBrief(check_what="c", expected="e", abnormal="a"),
            ),
            _agent_node(
                category=Category.REVIEW,
                subtype=ReviewSubtype.HUMAN_INTERACT_REVIEW,
                brief=ReviewBrief(check_what="c", expected="e", abnormal="a"),
            ),
        ]
        for node in cases:
            with self.subTest(category=node.category, subtype=node.subtype):
                rendered = self._render(node)
                self.assertNotIn("<<artifact_requirement>>", rendered)
                self.assertIn("## Publishing artifacts", rendered)

    def test_default_mode_keeps_the_not_expected_posture(self) -> None:
        rendered = self._render(_agent_node())
        self.assertIn("Artifacts are **not** expected from this node.", rendered)

    def test_markdown_mode_requires_a_markdown_artifact(self) -> None:
        node = _agent_node()
        node.artifact_mode = ArtifactMode.MARKDOWN
        rendered = self._render(node)
        self.assertIn(
            "This node must publish at least one Markdown artifact.", rendered
        )
        self.assertNotIn("Artifacts are **not** expected", rendered)

    def test_html_mode_requires_a_self_contained_file(self) -> None:
        node = _agent_node()
        node.artifact_mode = ArtifactMode.HTML
        rendered = self._render(node)
        self.assertIn("This node must publish an HTML artifact.", rendered)
        self.assertIn("self-contained", rendered)

    def test_custom_spec_is_blockquoted_and_outranked(self) -> None:
        node = _agent_node()
        node.artifact_mode = ArtifactMode.CUSTOM
        node.artifact_spec = (
            "produce `a.md` and `b.md`\n"
            "\n"
            "then a ```fenced``` summary"
        )
        block = build_artifact_requirement(node)
        for line in node.artifact_spec.strip().splitlines():
            with self.subTest(line=line):
                if line.strip():
                    self.assertIn(f"> {line}", block)
        # Framework constraints must sit after the user's text so the ordering
        # itself reads as precedence.
        self.assertLess(
            block.find("produce `a.md`"),
            block.find("outrank the specification"),
        )
        self.assertNotIn("<<artifact_spec_quoted>>", block)


class QaModeBlockTests(unittest.TestCase):
    def test_block_is_empty_when_off(self) -> None:
        self.assertEqual(build_qa_mode_block(_agent_node()), "")

    def test_block_is_present_when_on(self) -> None:
        node = _agent_node()
        node.qa_mode = True
        block = build_qa_mode_block(node)
        self.assertIn("Q/A mode", block)
        self.assertIn("ask-user", block)

    def test_block_names_no_concrete_tool(self) -> None:
        # claude exposes AskUserQuestion, codex exposes a self-injected
        # ask_user dynamic tool; naming either one hallucinates on the other.
        node = _agent_node()
        node.qa_mode = True
        block = build_qa_mode_block(node)
        self.assertNotIn("AskUserQuestion", block)
        self.assertNotIn("ask_user", block)

    def test_composition_includes_and_omits_the_block(self) -> None:
        from miniclaw2.runner import _compose_launch_instructions

        node = _agent_node()
        node.qa_mode = True
        on = build_qa_mode_block(node)
        composed = _compose_launch_instructions("CAT", on, "LANG")
        self.assertIn(on, composed)
        node.qa_mode = False
        composed_off = _compose_launch_instructions(
            "CAT", build_qa_mode_block(node), "LANG"
        )
        self.assertEqual(composed_off, "CAT\n\n---\n\nLANG")


class SharedHostProcessesTests(unittest.TestCase):
    """A pattern kill from an agent takes the human's own :5173 down."""

    def test_block_names_the_patterns_that_broke_the_dev_server(self) -> None:
        block = shared_host_processes_block()
        # The literal command an agent ran as "Stop dev server", plus the
        # ports that belong to the human's session rather than the agent's.
        self.assertIn("pkill -f vite", block)
        self.assertIn("killall node", block)
        self.assertIn("5173", block)
        self.assertIn("8000", block)

    def test_every_agent_launch_carries_the_block(self) -> None:
        from miniclaw2.runner import _compose_launch_instructions

        block = shared_host_processes_block()
        composed = _compose_launch_instructions("CAT", block, "LANG")
        self.assertIn(block, composed)

    def test_block_is_unconditional(self) -> None:
        """Unlike qa_mode, this guidance is never node-gated."""
        self.assertEqual(shared_host_processes_block(), shared_host_processes_block())
        self.assertTrue(shared_host_processes_block().strip())


if __name__ == "__main__":
    unittest.main()
