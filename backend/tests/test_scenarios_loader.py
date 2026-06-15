from __future__ import annotations

import unittest

from miniclaw2.domain import Category, ReviewSubtype
from miniclaw2.scenarios import list_scenarios, load_scenario


class ScenariosLoaderTest(unittest.TestCase):
    def test_lists_bundled_scenarios(self) -> None:
        names = [s.name for s in list_scenarios()]
        self.assertEqual(
            names,
            [
                "hello-text",
                "bash-uname",
                "write-readme",
                "permission-approve",
                "plan-mode-approval",
                "interrupt-midstream",
                "context-md-respected",
                "resume-fix-after-reject",
                "reconnect-replay",
                "gui-calculator",
            ],
        )

    def test_each_scenario_has_required_fields(self) -> None:
        for scenario in list_scenarios():
            with self.subTest(scenario=scenario.name):
                self.assertTrue(scenario.brief)
                self.assertEqual(set(scenario.providers), {"claude", "codex"})
                self.assertGreaterEqual(len(scenario.nodes), 1)
                self.assertTrue(scenario.acceptance)
                self.assertTrue(scenario.verify_path.exists())

    def test_hello_text_metadata(self) -> None:
        scenario = load_scenario("hello-text")
        self.assertEqual(scenario.name, "hello-text")
        self.assertEqual(len(scenario.nodes), 1)
        self.assertEqual(scenario.nodes[0].kind, "agent")
        self.assertIn("[OK]", scenario.nodes[0].prompt)
        self.assertFalse(scenario.auto_commit)
        self.assertEqual(scenario.permission_mode, "bypassPermissions")

    def test_unknown_scenario_raises(self) -> None:
        from miniclaw2.scenarios import ScenarioError

        with self.assertRaises(ScenarioError):
            load_scenario("does-not-exist")

    def test_gui_calculator_uses_human_interact_review_step(self) -> None:
        scenario = load_scenario("gui-calculator")
        self.assertEqual(len(scenario.nodes), 2)
        build, review = scenario.nodes
        self.assertEqual(build.id, "build")
        self.assertEqual(build.kind, "agent")
        self.assertEqual(build.category, Category.REGULAR)
        self.assertEqual(review.id, "review")
        self.assertEqual(review.kind, "agent")
        self.assertEqual(review.category, Category.REVIEW)
        self.assertEqual(review.subtype, ReviewSubtype.HUMAN_INTERACT_REVIEW)
        self.assertEqual(review.review_source, "build")
        self.assertIsNotNone(review.brief)
        self.assertTrue(scenario.auto_commit)

    def test_resume_fix_after_reject_parses_resume_after_review(self) -> None:
        scenario = load_scenario("resume-fix-after-reject")
        self.assertEqual([n.id for n in scenario.nodes], ["build", "review", "fix"])
        fix = scenario.nodes[2]
        self.assertEqual(fix.kind, "agent")
        self.assertEqual(fix.resume_from, "build")
        self.assertEqual(fix.when_step, "")
        self.assertEqual(fix.when_outcome, "")
        self.assertTrue(scenario.auto_commit)


if __name__ == "__main__":
    unittest.main()
