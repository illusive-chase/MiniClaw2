from __future__ import annotations

import unittest

from miniclaw2.domain import Node, NodeState
from miniclaw2.planspace_state import (
    PlanspaceStatus,
    apply_status_update,
    derive_plan_markdown,
    parse_planspace_status,
    render_planspace_status,
    validate_planspace_status,
)


class PlanspaceStatusTest(unittest.TestCase):
    def test_parse_legacy_status_preserves_body_with_unknown_slots(self) -> None:
        status = parse_planspace_status("Current status: legacy body.\n")

        self.assertEqual(status.goal, "unknown")
        self.assertEqual(status.current_state, "unknown")
        self.assertIn("Current status: legacy body.", status.body)
        self.assertEqual(validate_planspace_status(status), [])

    def test_render_round_trips_frontmatter_slots(self) -> None:
        status = PlanspaceStatus(
            goal="Ship the redesign.",
            current_state="Backend schema is in progress.",
            open_questions=[
                {
                    "id": "Q1",
                    "summary": "How should gates merge?",
                    "raised_at": "2026-06-07",
                    "raised_by": "user",
                }
            ],
            decisions=[
                {
                    "id": "D1",
                    "summary": "PLAN is derived from STATUS.",
                    "decided_at": "2026-06-07",
                    "decided_by": "user",
                }
            ],
            out_of_scope=["Compressed query pack."],
            body="# Notes\n\nInitial note.\n",
        )

        parsed = parse_planspace_status(render_planspace_status(status))

        self.assertEqual(parsed.goal, "Ship the redesign.")
        self.assertEqual(parsed.current_state, "Backend schema is in progress.")
        self.assertEqual(parsed.open_questions[0]["id"], "Q1")
        self.assertEqual(parsed.decisions[0]["summary"], "PLAN is derived from STATUS.")
        self.assertEqual(parsed.out_of_scope, ["Compressed query pack."])
        self.assertIn("Initial note.", parsed.body)

    def test_apply_status_update_assigns_stable_ids_and_derived_plan(self) -> None:
        status = PlanspaceStatus(goal="Goal", current_state="Starting")
        node = Node(project_id="p1", id="node1", state=NodeState.DONE)

        apply_status_update(
            status,
            {
                "target": "STATUS.md",
                "operation": "add_open_question",
                "policy": "auto",
                "summary": "Which UI surface owns gates?",
            },
            node,
        )
        apply_status_update(
            status,
            {
                "target": "STATUS.md",
                "operation": "add_decision",
                "policy": "auto",
                "summary": "Use free-form user judgment.",
            },
            node,
        )
        apply_status_update(
            status,
            {
                "target": "STATUS.md",
                "operation": "add_out_of_scope",
                "policy": "auto",
                "text": "Micro-agent merge.",
            },
            node,
        )

        plan = derive_plan_markdown(status)

        self.assertEqual(status.open_questions[0]["id"], "Q1")
        self.assertEqual(status.decisions[0]["id"], "D1")
        self.assertIn("[Q1] Which UI surface owns gates?", plan)
        self.assertIn("[from D1] Use free-form user judgment.", plan)
        self.assertIn("Micro-agent merge.", plan)


if __name__ == "__main__":
    unittest.main()
