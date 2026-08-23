"""Tests for the new Node ontology invariants."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from miniclaw2.domain import (
    ArtifactMode,
    Category,
    Node,
    NodeKind,
    NodeState,
    ReviewBrief,
    ReviewSubtype,
    ReviewTarget,
)


class NodeInvariantTests(unittest.TestCase):
    def test_agent_node_defaults_to_regular_category(self) -> None:
        node = Node(project_id="p1", kind=NodeKind.AGENT, model_preset_id="gpt-5.5")
        self.assertIs(node.category, Category.REGULAR)

    def test_op_node_rejects_category(self) -> None:
        with self.assertRaises(ValidationError):
            Node(
                project_id="p1",
                kind=NodeKind.OP,
                op_kind="commit",
                category=Category.REGULAR,
            )

    def test_op_node_accepts_none_category(self) -> None:
        node = Node(project_id="p1", kind=NodeKind.OP, op_kind="commit")
        self.assertIsNone(node.category)

    def test_review_agent_requires_subtype_and_brief(self) -> None:
        with self.assertRaises(ValidationError):
            Node(
                project_id="p1",
                kind=NodeKind.AGENT,
                model_preset_id="gpt-5.5",
                category=Category.REVIEW,
            )
        with self.assertRaises(ValidationError):
            Node(
                project_id="p1",
                kind=NodeKind.AGENT,
                model_preset_id="gpt-5.5",
                category=Category.REVIEW,
                subtype=ReviewSubtype.AGENTIC_REVIEW,
            )

    def test_review_agent_accepts_full_review_fields(self) -> None:
        brief = ReviewBrief(check_what="a", expected="b", abnormal="c")
        node = Node(
            project_id="p1",
            kind=NodeKind.AGENT,
            model_preset_id="gpt-5.5",
            category=Category.REVIEW,
            subtype=ReviewSubtype.AGENTIC_REVIEW,
            brief=brief,
        )
        self.assertIs(node.subtype, ReviewSubtype.AGENTIC_REVIEW)
        self.assertEqual(node.brief.check_what, "a")

    def test_code_review_defaults_target_and_allows_empty_brief(self) -> None:
        node = Node(
            project_id="p1",
            model_preset_id="gpt-5.5",
            category=Category.REVIEW,
            subtype=ReviewSubtype.CODE_REVIEW,
        )
        self.assertEqual(node.review_target, ReviewTarget(type="uncommitted"))

    def test_review_target_is_forbidden_elsewhere(self) -> None:
        with self.assertRaises(ValidationError):
            Node(
                project_id="p1",
                model_preset_id="gpt-5.5",
                review_target=ReviewTarget(),
            )

    def test_verifier_requires_programmatic_review_fields(self) -> None:
        brief = ReviewBrief(check_what="a", expected="b", abnormal="c")
        node = Node(
            project_id="p1",
            kind=NodeKind.VERIFIER,
            category=Category.REVIEW,
            subtype=ReviewSubtype.PROGRAMMATIC_REVIEW,
            brief=brief,
            verify_script_ref="/tmp/check.sh",
        )
        self.assertIs(node.subtype, ReviewSubtype.PROGRAMMATIC_REVIEW)
        with self.assertRaises(ValidationError):
            Node(
                project_id="p1",
                kind=NodeKind.AGENT,
                model_preset_id="gpt-5.5",
                category=Category.REVIEW,
                subtype=ReviewSubtype.PROGRAMMATIC_REVIEW,
                brief=brief,
            )

    def test_regular_agent_rejects_subtype(self) -> None:
        with self.assertRaises(ValidationError):
            Node(
                project_id="p1",
                kind=NodeKind.AGENT,
                model_preset_id="gpt-5.5",
                category=Category.REGULAR,
                subtype=ReviewSubtype.AGENTIC_REVIEW,
            )

    def test_library_edit_is_a_known_agent_operation(self) -> None:
        node = Node(
            project_id="p1",
            kind=NodeKind.AGENT,
            model_preset_id="gpt-5.5",
            agent_op_kind="library_edit",
        )
        self.assertEqual(node.agent_op_kind, "library_edit")

    def test_unknown_agent_operation_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Node(
                project_id="p1",
                kind=NodeKind.AGENT,
                model_preset_id="gpt-5.5",
                agent_op_kind="not-real",
            )

    def test_library_edit_is_agent_only(self) -> None:
        with self.assertRaises(ValidationError):
            Node(
                project_id="p1",
                kind=NodeKind.OP,
                op_kind="commit",
                agent_op_kind="library_edit",
            )

    def test_authoring_op_kinds_require_regular_category(self) -> None:
        for op_kind in ("library_edit", "principle_edit"):
            with self.subTest(op_kind=op_kind):
                with self.assertRaises(ValidationError):
                    Node(
                        project_id="p1",
                        kind=NodeKind.AGENT,
                        model_preset_id="gpt-5.5",
                        agent_op_kind=op_kind,
                        category=Category.REVIEW,
                        subtype=ReviewSubtype.AGENTIC_REVIEW,
                        brief=ReviewBrief(
                            check_what="c", expected="e", abnormal="a"
                        ),
                    )
                with self.assertRaises(ValidationError):
                    Node(
                        project_id="p1",
                        kind=NodeKind.AGENT,
                        model_preset_id="gpt-5.5",
                        agent_op_kind=op_kind,
                        category=Category.PLANNING,
                    )
                node = Node(
                    project_id="p1",
                    kind=NodeKind.AGENT,
                    model_preset_id="gpt-5.5",
                    agent_op_kind=op_kind,
                    category=Category.REGULAR,
                )
                self.assertEqual(node.category, Category.REGULAR)

    def test_virtual_state_rejects_timestamps(self) -> None:
        with self.assertRaises(ValidationError):
            Node(
                project_id="p1",
                kind=NodeKind.AGENT,
                model_preset_id="gpt-5.5",
                category=Category.PLANNING,
                state=NodeState.VIRTUAL,
                started_at=123.0,
            )


class ArtifactModeInvariantTests(unittest.TestCase):
    def _agent(self, **kwargs: object) -> Node:
        return Node(
            project_id="p1",
            kind=NodeKind.AGENT,
            model_preset_id="gpt-5.5",
            **kwargs,
        )

    def test_defaults_to_default_mode(self) -> None:
        node = self._agent()
        self.assertIs(node.artifact_mode, ArtifactMode.DEFAULT)
        self.assertEqual(node.artifact_spec, "")

    def test_work_and_planning_accept_non_default_modes(self) -> None:
        for category in (Category.REGULAR, Category.PLANNING):
            for mode in (ArtifactMode.MARKDOWN, ArtifactMode.HTML):
                with self.subTest(category=category, mode=mode):
                    node = self._agent(category=category, artifact_mode=mode)
                    self.assertIs(node.artifact_mode, mode)

    def test_op_node_rejects_artifact_fields(self) -> None:
        with self.assertRaises(ValidationError):
            Node(
                project_id="p1",
                kind=NodeKind.OP,
                op_kind="commit",
                artifact_mode=ArtifactMode.MARKDOWN,
            )
        with self.assertRaises(ValidationError):
            Node(
                project_id="p1",
                kind=NodeKind.OP,
                op_kind="commit",
                artifact_spec="something",
            )

    def test_review_node_rejects_artifact_mode(self) -> None:
        with self.assertRaises(ValidationError):
            self._agent(
                category=Category.REVIEW,
                subtype=ReviewSubtype.AGENTIC_REVIEW,
                brief=ReviewBrief(check_what="c", expected="e", abnormal="a"),
                artifact_mode=ArtifactMode.MARKDOWN,
            )

    def test_library_op_kinds_reject_artifact_mode(self) -> None:
        # A library node's category is forced to REGULAR, so a category-only
        # gate would let it through; the op-kind gate is what stops it.
        for op_kind in ("library_edit", "principle_edit"):
            with self.subTest(op_kind=op_kind):
                with self.assertRaises(ValidationError):
                    self._agent(
                        agent_op_kind=op_kind,
                        category=Category.REGULAR,
                        artifact_mode=ArtifactMode.MARKDOWN,
                    )

    def test_custom_requires_spec(self) -> None:
        with self.assertRaises(ValidationError):
            self._agent(artifact_mode=ArtifactMode.CUSTOM)
        with self.assertRaises(ValidationError):
            self._agent(artifact_mode=ArtifactMode.CUSTOM, artifact_spec="   ")
        node = self._agent(
            artifact_mode=ArtifactMode.CUSTOM, artifact_spec="one table"
        )
        self.assertEqual(node.artifact_spec, "one table")

    def test_spec_without_custom_is_rejected(self) -> None:
        for mode in (
            ArtifactMode.DEFAULT,
            ArtifactMode.MARKDOWN,
            ArtifactMode.HTML,
        ):
            with self.subTest(mode=mode):
                with self.assertRaises(ValidationError):
                    self._agent(artifact_mode=mode, artifact_spec="stray")


class QaModeInvariantTests(unittest.TestCase):
    def test_defaults_off(self) -> None:
        node = Node(
            project_id="p1", kind=NodeKind.AGENT, model_preset_id="gpt-5.5"
        )
        self.assertFalse(node.qa_mode)

    def test_work_and_planning_accept_qa_mode(self) -> None:
        for category in (Category.REGULAR, Category.PLANNING):
            with self.subTest(category=category):
                node = Node(
                    project_id="p1",
                    kind=NodeKind.AGENT,
                    model_preset_id="gpt-5.5",
                    category=category,
                    qa_mode=True,
                )
                self.assertTrue(node.qa_mode)

    def test_op_node_rejects_qa_mode(self) -> None:
        with self.assertRaises(ValidationError):
            Node(
                project_id="p1",
                kind=NodeKind.OP,
                op_kind="commit",
                qa_mode=True,
            )

    def test_review_node_rejects_qa_mode(self) -> None:
        with self.assertRaises(ValidationError):
            Node(
                project_id="p1",
                kind=NodeKind.AGENT,
                model_preset_id="gpt-5.5",
                category=Category.REVIEW,
                subtype=ReviewSubtype.AGENTIC_REVIEW,
                brief=ReviewBrief(check_what="c", expected="e", abnormal="a"),
                qa_mode=True,
            )


if __name__ == "__main__":
    unittest.main()
