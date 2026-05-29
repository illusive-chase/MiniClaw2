from __future__ import annotations

import unittest

from miniclaw2.scenarios import list_scenarios, load_scenario


class ScenariosLoaderTest(unittest.TestCase):
    def test_lists_bundled_scenarios(self) -> None:
        names = {s.name for s in list_scenarios()}
        self.assertEqual(
            names,
            {
                "hello-text",
                "bash-uname",
                "write-readme",
                "permission-approve",
                "plan-mode-approval",
                "interrupt-midstream",
            },
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


if __name__ == "__main__":
    unittest.main()
