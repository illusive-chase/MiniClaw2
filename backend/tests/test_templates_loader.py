from __future__ import annotations

import unittest

from miniclaw2.domain import Category, NodeKind, ReviewSubtype
from miniclaw2.templates import TemplateError, list_templates, load_template
from miniclaw2.templates.loader import _parse_allowed_model_preset_ids


class TemplatesLoaderTest(unittest.TestCase):
    def test_lists_bundled_templates(self) -> None:
        names = [s.name for s in list_templates()]
        self.assertEqual(
            names,
            [
                "hello-text",
                "bash-uname",
                "write-readme",
                "interrupt-midstream",
                "context-md-respected",
                "resume-fix-after-reject",
                "gui-calculator",
            ],
        )

    def test_each_template_has_required_fields(self) -> None:
        for template in list_templates():
            with self.subTest(template=template.name):
                self.assertTrue(template.brief)
                self.assertEqual(
                    set(template.allowed_model_preset_ids),
                    {
                        "opus-4-8",
                        "opus-4-7",
                        "gpt-5.6",
                        "gpt-5.6-x",
                        "gpt-5.6-u",
                    },
                )
                self.assertGreaterEqual(len(template.nodes), 3)
                self.assertTrue(any(n.kind is NodeKind.VERIFIER for n in template.nodes))

    def test_hello_text_metadata(self) -> None:
        template = load_template("hello-text")
        self.assertEqual(template.name, "hello-text")
        self.assertEqual([n.id for n in template.nodes], ["turn1", "verify", "accept"])
        self.assertEqual(template.nodes[0].kind, NodeKind.AGENT)
        self.assertIn("[OK]", template.nodes[0].prompt)
        self.assertFalse(template.auto_commit)
        self.assertEqual(template.permission_mode, "bypassPermissions")

    def test_unknown_template_raises(self) -> None:
        with self.assertRaises(TemplateError):
            load_template("does-not-exist")

    def test_current_loader_rejects_singular_model_preset(self) -> None:
        with self.assertRaisesRegex(TemplateError, "model_preset_id is obsolete"):
            _parse_allowed_model_preset_ids(
                "legacy",
                {"model_preset_id": "gpt-5.6"},
            )

    def test_gui_calculator_has_build_verify_accept_lane(self) -> None:
        template = load_template("gui-calculator")
        self.assertEqual([n.id for n in template.nodes], ["build", "verify", "accept"])
        build, verify, accept = template.nodes
        self.assertEqual(build.kind, NodeKind.AGENT)
        self.assertEqual(build.category, Category.REGULAR)
        self.assertEqual(verify.kind, NodeKind.VERIFIER)
        self.assertEqual(verify.subtype, ReviewSubtype.PROGRAMMATIC_REVIEW)
        self.assertEqual(verify.scheduled_deps, ["build"])
        self.assertEqual(accept.category, Category.REVIEW)
        self.assertEqual(accept.subtype, ReviewSubtype.HUMAN_INTERACT_REVIEW)
        self.assertEqual(accept.scheduled_deps, ["build", "verify"])
        self.assertTrue(template.auto_commit)

    def test_resume_fix_after_reject_parses_resume_after_review(self) -> None:
        template = load_template("resume-fix-after-reject")
        self.assertEqual(
            [n.id for n in template.nodes],
            ["build", "review", "fix", "verify", "accept"],
        )
        fix = template.nodes[2]
        self.assertEqual(fix.kind, NodeKind.AGENT)
        self.assertEqual(fix.resume_from, "build")
        self.assertEqual(fix.scheduled_deps, ["build", "review"])
        self.assertTrue(template.auto_commit)


if __name__ == "__main__":
    unittest.main()
