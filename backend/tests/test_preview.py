"""Tests for the preview module."""

from __future__ import annotations

import json
import unittest

from miniclaw2.domain import (
    Category,
    Node,
    NodeKind,
    NodeState,
    ReviewBrief,
    ReviewSubtype,
)
from miniclaw2.preview import (
    PreviewValidationError,
    ExecutedPreview,
    VirtualPreview,
    parse_preview,
    render_executed_preview,
    render_virtual_preview,
    validate_preview_for_node,
    virtual_preview_to_node,
)


def _executed_payload(**over) -> dict:
    base = {
        "id": "n1",
        "kind": "agent",
        "category": "regular",
        "state": "done",
        "ran_at": "2026-06-13T14:22:00+00:00",
        "lane": "auth-flow",
        "motivation": "wire signup",
        "summary": "done",
        "next_implications": "next steps...",
    }
    base.update(over)
    return base


def _virtual_payload(**over) -> dict:
    base = {
        "id": "V_reset",
        "kind": "agent",
        "category": "regular",
        "state": "virtual",
        "lane": "auth-flow",
        "proposed_by": "node:n1",
        "motivation": "needed before launch",
        "prompt_draft": "Implement /forgot-password ...",
    }
    base.update(over)
    return base


class ParsePreviewTests(unittest.TestCase):
    def test_executed_round_trip(self) -> None:
        payload = _executed_payload()
        preview = parse_preview(json.dumps(payload))
        self.assertIsInstance(preview, ExecutedPreview)
        self.assertEqual(preview.id, "n1")
        self.assertEqual(preview.summary, "done")

    def test_virtual_round_trip(self) -> None:
        payload = _virtual_payload()
        preview = parse_preview(json.dumps(payload))
        self.assertIsInstance(preview, VirtualPreview)
        self.assertEqual(preview.prompt_draft, "Implement /forgot-password ...")

    def test_unknown_field_rejected(self) -> None:
        payload = _executed_payload(rogue="field")
        with self.assertRaises(PreviewValidationError):
            parse_preview(json.dumps(payload))

    def test_invalid_json(self) -> None:
        with self.assertRaises(PreviewValidationError):
            parse_preview("not json")

    def test_review_executed_requires_subtype(self) -> None:
        payload = _executed_payload(category="review")
        with self.assertRaises(PreviewValidationError):
            parse_preview(json.dumps(payload))

    def test_review_virtual_requires_brief(self) -> None:
        payload = _virtual_payload(category="review", subtype="agentic_review")
        with self.assertRaises(PreviewValidationError):
            parse_preview(json.dumps(payload))

    def test_review_virtual_accepts_full_payload(self) -> None:
        payload = _virtual_payload(
            category="review",
            subtype="human_interact_review",
            brief={"check_what": "a", "expected": "b", "abnormal": "c"},
        )
        preview = parse_preview(json.dumps(payload))
        assert isinstance(preview, VirtualPreview)
        assert preview.brief is not None
        self.assertEqual(preview.brief.check_what, "a")

    def test_verifier_virtual_accepts_programmatic_review(self) -> None:
        payload = _virtual_payload(
            kind="verifier",
            category="review",
            subtype="programmatic_review",
            prompt_draft="",
            brief={"check_what": "a", "expected": "b", "abnormal": "c"},
        )
        preview = parse_preview(json.dumps(payload))
        self.assertIsInstance(preview, VirtualPreview)


class ValidatePreviewForNodeTests(unittest.TestCase):
    def _node(self, **over) -> Node:
        defaults = dict(
            id="n1",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.DONE,
            planspace_id="auth-flow",
            started_at=1.0,
            finished_at=2.0,
        )
        defaults.update(over)
        return Node(**defaults)

    def test_matching_preview_returns_no_issues(self) -> None:
        node = self._node()
        preview = parse_preview(json.dumps(_executed_payload()))
        self.assertEqual(validate_preview_for_node(preview, node), [])

    def test_id_mismatch_flagged(self) -> None:
        node = self._node()
        preview = parse_preview(json.dumps(_executed_payload(id="other")))
        issues = validate_preview_for_node(preview, node)
        self.assertTrue(any("preview.id" in i for i in issues))

    def test_lane_mismatch_flagged(self) -> None:
        node = self._node()
        preview = parse_preview(json.dumps(_executed_payload(lane="other-lane")))
        issues = validate_preview_for_node(preview, node)
        self.assertTrue(any("preview.lane" in i for i in issues))

    def test_category_mismatch_flagged(self) -> None:
        node = self._node(category=Category.PLANNING)
        preview = parse_preview(json.dumps(_executed_payload(category="regular")))
        issues = validate_preview_for_node(preview, node)
        self.assertTrue(any("category" in i for i in issues))


class RenderPreviewTests(unittest.TestCase):
    def test_render_executed_round_trip(self) -> None:
        node = Node(
            id="n1",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            state=NodeState.DONE,
            planspace_id="auth-flow",
            started_at=1.0,
            finished_at=2.0,
        )
        text = render_executed_preview(
            node,
            motivation="m",
            summary="s",
            next_implications="ni",
        )
        preview = parse_preview(text)
        self.assertIsInstance(preview, ExecutedPreview)
        self.assertEqual(preview.summary, "s")

    def test_render_virtual_round_trip(self) -> None:
        node = Node(
            id="v1",
            project_id="p1",
            kind=NodeKind.AGENT,
            category=Category.PLANNING,
            state=NodeState.VIRTUAL,
            planspace_id="auth-flow",
            prompt_draft="Do X",
            proposed_by="node:n0",
            summary="why we want X",
        )
        text = render_virtual_preview(node)
        preview = parse_preview(text)
        self.assertIsInstance(preview, VirtualPreview)
        self.assertEqual(preview.prompt_draft, "Do X")

    def test_render_verifier_virtual_round_trip(self) -> None:
        node = Node(
            id="v-check",
            project_id="p1",
            kind=NodeKind.VERIFIER,
            category=Category.REVIEW,
            subtype=ReviewSubtype.PROGRAMMATIC_REVIEW,
            brief=ReviewBrief(check_what="a", expected="b", abnormal="c"),
            state=NodeState.VIRTUAL,
            planspace_id="auth-flow",
            proposed_by="template:t",
            summary="verify",
            verify_script_ref="/tmp/check.sh",
        )
        text = render_virtual_preview(node)
        preview = parse_preview(text)
        self.assertIsInstance(preview, VirtualPreview)
        self.assertEqual(preview.kind, "verifier")


class VirtualPreviewToNodeTests(unittest.TestCase):
    def test_regular_virtual_promotes_to_node(self) -> None:
        preview = parse_preview(json.dumps(_virtual_payload()))
        assert isinstance(preview, VirtualPreview)
        node = virtual_preview_to_node(
            preview, project_id="p1", provider="claude", canonical_id="canon-1"
        )
        self.assertEqual(node.id, "canon-1")
        self.assertEqual(node.state, NodeState.VIRTUAL)
        self.assertEqual(node.category, Category.REGULAR)
        self.assertEqual(node.prompt_draft, "Implement /forgot-password ...")

    def test_review_virtual_promotes_with_brief(self) -> None:
        payload = _virtual_payload(
            category="review",
            subtype="agentic_review",
            brief={"check_what": "a", "expected": "b", "abnormal": "c"},
        )
        preview = parse_preview(json.dumps(payload))
        assert isinstance(preview, VirtualPreview)
        node = virtual_preview_to_node(
            preview, project_id="p1", provider="claude", canonical_id="canon-2"
        )
        self.assertEqual(node.category, Category.REVIEW)
        self.assertEqual(node.subtype, ReviewSubtype.AGENTIC_REVIEW)
        assert node.brief is not None
        self.assertEqual(node.brief.expected, "b")

    def test_verifier_virtual_promotes_with_script_ref(self) -> None:
        payload = _virtual_payload(
            kind="verifier",
            category="review",
            subtype="programmatic_review",
            prompt_draft="",
            brief={"check_what": "a", "expected": "b", "abnormal": "c"},
        )
        preview = parse_preview(json.dumps(payload))
        assert isinstance(preview, VirtualPreview)
        node = virtual_preview_to_node(
            preview,
            project_id="p1",
            provider="claude",
            canonical_id="canon-3",
            verify_script_ref="/tmp/check.sh",
        )
        self.assertEqual(node.kind, NodeKind.VERIFIER)
        self.assertEqual(node.verify_script_ref, "/tmp/check.sh")


if __name__ == "__main__":
    unittest.main()
