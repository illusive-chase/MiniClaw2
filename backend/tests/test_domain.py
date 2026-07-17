"""Tests for the new Node ontology invariants."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from miniclaw2.domain import (
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


if __name__ == "__main__":
    unittest.main()
