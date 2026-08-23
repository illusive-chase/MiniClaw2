from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

from miniclaw2.domain import ArtifactMode, Category, NodeKind, ReviewSubtype
from miniclaw2.templates import TemplateError, list_templates, load_template
from miniclaw2.templates.loader import (
    SCHEMA_VERSION,
    _load_from_root,
    _parse_allowed_model_preset_ids,
)


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

    def test_bundled_templates_are_schema_v2_without_parameters(self) -> None:
        for template in list_templates():
            with self.subTest(template=template.name):
                self.assertEqual(template.arguments, [])
                self.assertEqual(template.inputs, [])
                self.assertEqual(template.warnings, [])
                self.assertEqual(
                    template.metadata()["schema_version"], SCHEMA_VERSION
                )


class TemplateSchemaV2Test(unittest.TestCase):
    """Schema v2: the ``schema_version`` gate, arguments, inputs, ``in:*`` deps."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "tpl"
        self.root.mkdir(parents=True)

    def write(
        self,
        *,
        prompts: dict[str, str],
        nodes: list[dict[str, Any]],
        template_extra: dict[str, Any] | None = None,
        schema_version: Any = SCHEMA_VERSION,
        omit_schema_version: bool = False,
    ) -> Path:
        """Materialise a minimal on-disk template and return its root."""
        template_data: dict[str, Any] = {
            "name": "fixture",
            "brief": "fixture template",
            "allowed_model_preset_ids": ["opus-4-8"],
            "lane_mode": "manual",
        }
        if not omit_schema_version:
            template_data = {"schema_version": schema_version, **template_data}
        template_data.update(template_extra or {})

        (self.root / "prompts").mkdir(parents=True, exist_ok=True)
        for rel, text in prompts.items():
            (self.root / "prompts" / rel).write_text(text, encoding="utf-8")
        (self.root / "template.yaml").write_text(
            yaml.safe_dump(template_data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        (self.root / "lane.yaml").write_text(
            yaml.safe_dump({"nodes": nodes}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return self.root

    def simple(self, prompt: str, **kwargs: Any):
        """Load a one-node template whose prompt is ``prompt``."""
        root = self.write(
            prompts={"n0.md": prompt},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                }
            ],
            **kwargs,
        )
        return _load_from_root(root, "fixture")

    # --- schema_version gate (proposal §7) ---------------------------------

    def test_missing_schema_version_is_rejected_with_migration_hint(self) -> None:
        with self.assertRaisesRegex(TemplateError, "run the template migration"):
            self.simple("plain prompt", omit_schema_version=True)

    def test_schema_version_1_is_rejected_with_migration_hint(self) -> None:
        with self.assertRaisesRegex(TemplateError, "unsupported schema_version 1"):
            self.simple("plain prompt", schema_version=1)

    def test_non_integer_schema_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(TemplateError, "schema_version must be"):
            self.simple("plain prompt", schema_version="2")

    def test_schema_version_2_loads(self) -> None:
        template = self.simple("plain prompt")
        self.assertEqual(template.name, "fixture")
        self.assertEqual(template.arguments, [])
        self.assertEqual(template.inputs, [])
        self.assertEqual(template.warnings, [])

    # --- per-node model -----------------------------------------------------

    def test_node_model_preset_id_is_parsed_and_exposed(self) -> None:
        root = self.write(
            prompts={"n0.md": "a", "n1.md": "b"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                    "model_preset_id": "opus-4-7",
                },
                {
                    "id": "n1",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n1.md",
                },
            ],
        )
        template = _load_from_root(root, "fixture")
        self.assertEqual(
            [node.model_preset_id for node in template.nodes], ["opus-4-7", None]
        )
        self.assertEqual(
            [node.metadata()["model_preset_id"] for node in template.nodes],
            ["opus-4-7", None],
        )

    def test_template_without_allowed_model_preset_ids_loads(self) -> None:
        """Per-node models made the template-level list optional.

        User templates written after that change omit it entirely; requiring it
        would make every newly saved template unloadable.
        """
        root = self.write(
            prompts={"n0.md": "a"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                }
            ],
            template_extra={"allowed_model_preset_ids": None},
        )
        template = _load_from_root(root, "fixture")
        self.assertEqual(template.allowed_model_preset_ids, [])

    def test_node_model_preset_id_must_be_a_string(self) -> None:
        self.write(
            prompts={"n0.md": "a"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                    "model_preset_id": 5,
                }
            ],
        )
        with self.assertRaisesRegex(TemplateError, "model_preset_id must be a string"):
            _load_from_root(self.root, "fixture")

    def test_verifier_node_must_not_carry_a_model(self) -> None:
        """Verifiers run a script, not an agent session, so a model is a lie."""
        (self.root / "verify.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.write(
            prompts={},
            nodes=[
                {
                    "id": "v0",
                    "kind": "verifier",
                    "script_ref": "verify.sh",
                    "brief": {
                        "check_what": "exit code",
                        "expected": "0",
                        "abnormal": "non-zero",
                    },
                    "model_preset_id": "opus-4-7",
                }
            ],
        )
        with self.assertRaisesRegex(TemplateError, "must not carry model_preset_id"):
            _load_from_root(self.root, "fixture")

    def test_template_level_model_preset_id_still_points_at_the_node_field(self) -> None:
        with self.assertRaisesRegex(TemplateError, "declare model_preset_id on each"):
            self.simple("plain prompt", template_extra={"model_preset_id": "opus-4-8"})

    # --- arguments (proposal §3) -------------------------------------------

    def test_declared_arguments_carry_description_and_default(self) -> None:
        template = self.simple(
            "topic={{topic}} style={{report_style}}",
            template_extra={
                "arguments": [
                    {"name": "topic", "description": "关注主题", "default": None},
                    {"name": "report_style", "description": "", "default": "简洁要点式"},
                ]
            },
        )
        topic, style = template.arguments
        self.assertEqual(topic.name, "topic")
        self.assertEqual(topic.description, "关注主题")
        self.assertIsNone(topic.default)
        self.assertTrue(topic.required)
        self.assertEqual(style.default, "简洁要点式")
        self.assertFalse(style.required)
        self.assertEqual(template.warnings, [])

    def test_argument_missing_default_key_is_required(self) -> None:
        template = self.simple(
            "{{topic}}",
            template_extra={"arguments": [{"name": "topic"}]},
        )
        self.assertTrue(template.arguments[0].required)
        self.assertIsNone(template.arguments[0].default)

    def test_empty_default_string_is_not_required(self) -> None:
        template = self.simple(
            "{{topic}}",
            template_extra={"arguments": [{"name": "topic", "default": ""}]},
        )
        self.assertFalse(template.arguments[0].required)
        self.assertEqual(template.arguments[0].default, "")

    def test_argument_name_must_match_naming_rule(self) -> None:
        for bad in ["Topic", "1topic", "my-topic", "topic!", ""]:
            with self.subTest(name=bad):
                with self.assertRaises(TemplateError):
                    self.simple(
                        "prompt",
                        template_extra={"arguments": [{"name": bad}]},
                    )

    def test_duplicate_argument_name_is_rejected(self) -> None:
        with self.assertRaisesRegex(TemplateError, "duplicate argument name"):
            self.simple(
                "{{topic}}",
                template_extra={
                    "arguments": [{"name": "topic"}, {"name": "topic", "default": "x"}]
                },
            )

    def test_empty_argument_list_is_legal(self) -> None:
        template = self.simple("prompt", template_extra={"arguments": []})
        self.assertEqual(template.arguments, [])

    def test_argument_default_must_be_string_or_null(self) -> None:
        with self.assertRaisesRegex(TemplateError, "default must be a string or null"):
            self.simple(
                "{{topic}}",
                template_extra={"arguments": [{"name": "topic", "default": 7}]},
            )

    # --- inputs (proposal §3) ---------------------------------------------

    def test_declared_inputs_are_parsed(self) -> None:
        template = self.simple(
            "compare {{input.alpha_branch}} against {{input.beta_branch}}",
            template_extra={
                "inputs": [
                    {"name": "alpha_branch", "description": "alpha 末端节点"},
                    {"name": "beta_branch"},
                ]
            },
        )
        self.assertEqual([i.name for i in template.inputs], ["alpha_branch", "beta_branch"])
        self.assertEqual(template.inputs[0].description, "alpha 末端节点")
        self.assertEqual(template.inputs[1].description, "")
        self.assertEqual(template.warnings, [])

    def test_input_name_must_match_naming_rule(self) -> None:
        with self.assertRaisesRegex(TemplateError, r"input name .* must match"):
            self.simple("prompt", template_extra={"inputs": [{"name": "Alpha"}]})

    def test_duplicate_input_name_is_rejected(self) -> None:
        with self.assertRaisesRegex(TemplateError, "duplicate input name"):
            self.simple(
                "{{input.alpha}}",
                template_extra={"inputs": [{"name": "alpha"}, {"name": "alpha"}]},
            )

    def test_arguments_and_inputs_are_independent_namespaces(self) -> None:
        """A ``topic`` argument and a ``topic`` input coexist — different syntax."""
        template = self.simple(
            "arg={{topic}} input={{input.topic}}",
            template_extra={
                "arguments": [{"name": "topic", "default": "d"}],
                "inputs": [{"name": "topic"}],
            },
        )
        self.assertEqual([a.name for a in template.arguments], ["topic"])
        self.assertEqual([i.name for i in template.inputs], ["topic"])
        self.assertEqual(template.warnings, [])

    # --- placeholder scan + merge (proposal §3.2) --------------------------

    def test_scanned_but_undeclared_argument_is_auto_added(self) -> None:
        template = self.simple("围绕主题「{{topic}}」展开，风格 {{report_style}}")
        self.assertEqual([a.name for a in template.arguments], ["topic", "report_style"])
        for arg in template.arguments:
            self.assertFalse(arg.declared)
            self.assertTrue(arg.required)
            self.assertEqual(arg.description, "")
        self.assertEqual(template.warnings, [])

    def test_scan_merges_with_declarations_without_duplicating(self) -> None:
        template = self.simple(
            "{{topic}} and {{extra}}",
            template_extra={
                "arguments": [{"name": "topic", "description": "d", "default": "x"}]
            },
        )
        self.assertEqual([a.name for a in template.arguments], ["topic", "extra"])
        topic, extra = template.arguments
        self.assertTrue(topic.declared)
        self.assertEqual(topic.default, "x")
        self.assertFalse(extra.declared)
        self.assertIsNone(extra.default)

    def test_scan_covers_every_node_prompt(self) -> None:
        root = self.write(
            prompts={"n0.md": "{{alpha}}", "n1.md": "{{beta}}"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                },
                {
                    "id": "n1",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n1.md",
                    "scheduled_deps": ["n0"],
                },
            ],
        )
        template = _load_from_root(root, "fixture")
        self.assertEqual([a.name for a in template.arguments], ["alpha", "beta"])

    def test_dangling_declaration_warns_but_does_not_raise(self) -> None:
        template = self.simple(
            "no placeholders here",
            template_extra={"arguments": [{"name": "topic", "default": "x"}]},
        )
        self.assertEqual([a.name for a in template.arguments], ["topic"])
        self.assertEqual(
            [(w["code"], w["name"]) for w in template.warnings],
            [("dangling_argument", "topic")],
        )
        self.assertIn("topic", template.metadata()["warnings"][0]["message"])

    def test_non_conforming_braces_are_left_literal(self) -> None:
        template = self.simple(
            "keep {{Bad-Name}} and {{ALLCAPS}} and {{9lives}} and {{a b}} intact"
        )
        self.assertEqual(template.arguments, [])
        self.assertEqual(template.warnings, [])

    def test_undeclared_input_port_placeholder_is_an_error(self) -> None:
        with self.assertRaisesRegex(TemplateError, "undeclared input port"):
            self.simple("bind {{input.alpha_branch}} here")

    def test_declared_input_port_placeholder_is_accepted(self) -> None:
        template = self.simple(
            "bind {{input.alpha_branch}} here",
            template_extra={"inputs": [{"name": "alpha_branch"}]},
        )
        self.assertEqual([i.name for i in template.inputs], ["alpha_branch"])
        self.assertEqual(template.warnings, [])

    # --- in:<name> deps (proposal §3.3) -----------------------------------

    def test_input_dep_must_reference_declared_input(self) -> None:
        root = self.write(
            prompts={"n0.md": "work"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                    "scheduled_deps": ["in:alpha_branch"],
                }
            ],
        )
        with self.assertRaisesRegex(TemplateError, "undeclared input port"):
            _load_from_root(root, "fixture")

    def test_input_dep_is_exempt_from_earlier_node_ordering(self) -> None:
        """``in:*`` is an out-of-graph source, so it never violates ordering."""
        root = self.write(
            prompts={"n0.md": "work"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                    "scheduled_deps": ["in:alpha_branch", "in:beta_branch"],
                }
            ],
            template_extra={
                "inputs": [{"name": "alpha_branch"}, {"name": "beta_branch"}]
            },
        )
        template = _load_from_root(root, "fixture")
        spec = template.nodes[0]
        self.assertEqual(spec.scheduled_deps, ["in:alpha_branch", "in:beta_branch"])
        self.assertEqual(spec.input_deps, ["alpha_branch", "beta_branch"])
        self.assertEqual(spec.internal_deps, [])
        self.assertEqual(template.warnings, [])

    def test_input_dep_does_not_participate_in_cycle_detection(self) -> None:
        """A port named after a node must not close a fake cycle."""
        root = self.write(
            prompts={"n0.md": "a", "n1.md": "b"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                    "scheduled_deps": ["in:n1"],
                },
                {
                    "id": "n1",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n1.md",
                    "scheduled_deps": ["n0"],
                },
            ],
            template_extra={"inputs": [{"name": "n1"}]},
        )
        template = _load_from_root(root, "fixture")
        self.assertEqual([n.id for n in template.nodes], ["n0", "n1"])
        self.assertEqual(template.nodes[0].input_deps, ["n1"])
        self.assertEqual(template.nodes[1].internal_deps, ["n0"])

    def test_internal_deps_still_reject_unknown_and_forward_references(self) -> None:
        forward = self.write(
            prompts={"n0.md": "a", "n1.md": "b"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                    "scheduled_deps": ["n1"],
                },
                {
                    "id": "n1",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n1.md",
                },
            ],
        )
        with self.assertRaisesRegex(TemplateError, "must reference an earlier node"):
            _load_from_root(forward, "fixture")

        unknown = self.write(
            prompts={"n0.md": "a"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                    "scheduled_deps": ["ghost"],
                }
            ],
        )
        with self.assertRaisesRegex(TemplateError, "unknown node"):
            _load_from_root(unknown, "fixture")

    # --- unreferenced input warning (proposal §3.3 rule 3) ----------------

    def test_unreferenced_input_warns_but_does_not_raise(self) -> None:
        template = self.simple(
            "no reference to the port",
            template_extra={"inputs": [{"name": "alpha_branch"}]},
        )
        self.assertEqual([i.name for i in template.inputs], ["alpha_branch"])
        self.assertEqual(
            [(w["code"], w["name"]) for w in template.warnings],
            [("unreferenced_input", "alpha_branch")],
        )

    def test_input_referenced_only_by_dep_does_not_warn(self) -> None:
        root = self.write(
            prompts={"n0.md": "no placeholder, just the edge"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                    "scheduled_deps": ["in:alpha_branch"],
                }
            ],
            template_extra={"inputs": [{"name": "alpha_branch"}]},
        )
        self.assertEqual(_load_from_root(root, "fixture").warnings, [])

    def test_input_referenced_only_by_placeholder_does_not_warn(self) -> None:
        template = self.simple(
            "see {{input.alpha_branch}}",
            template_extra={"inputs": [{"name": "alpha_branch"}]},
        )
        self.assertEqual(template.warnings, [])

    # --- metadata() surface (proposal §3, §7) -----------------------------

    def test_metadata_exposes_arguments_inputs_and_warnings(self) -> None:
        root = self.write(
            prompts={"n0.md": "{{topic}} via {{input.alpha_branch}}"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                    "scheduled_deps": ["in:alpha_branch"],
                }
            ],
            template_extra={
                "arguments": [
                    {"name": "topic", "description": "关注主题"},
                    {"name": "gone", "default": "d"},
                ],
                "inputs": [
                    {"name": "alpha_branch", "description": "alpha 末端"},
                    {"name": "orphan"},
                ],
            },
        )
        meta = _load_from_root(root, "fixture").metadata()

        self.assertEqual(meta["schema_version"], SCHEMA_VERSION)
        self.assertEqual(
            meta["arguments"],
            [
                {
                    "name": "topic",
                    "description": "关注主题",
                    "default": None,
                    "required": True,
                    "declared": True,
                },
                {
                    "name": "gone",
                    "description": "",
                    "default": "d",
                    "required": False,
                    "declared": True,
                },
            ],
        )
        self.assertEqual(
            meta["inputs"],
            [
                {"name": "alpha_branch", "description": "alpha 末端"},
                {"name": "orphan", "description": ""},
            ],
        )
        self.assertEqual(
            sorted((w["code"], w["name"]) for w in meta["warnings"]),
            [("dangling_argument", "gone"), ("unreferenced_input", "orphan")],
        )

    # --- per-node artifact intent ------------------------------------------

    def test_node_without_artifact_keys_loads_as_default(self) -> None:
        """Old lane.yaml files predate the field and must still load.

        This is why SCHEMA_VERSION is not bumped for it — a bump would make
        every existing user template report a migration error at once.
        """
        template = self.simple("plain prompt")
        node = template.nodes[0]
        self.assertIs(node.artifact_mode, ArtifactMode.DEFAULT)
        self.assertEqual(node.artifact_spec, "")
        self.assertEqual(node.metadata()["artifact_mode"], "default")

    def test_node_artifact_mode_is_parsed_and_exposed(self) -> None:
        root = self.write(
            prompts={"n0.md": "a"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                    "artifact_mode": "markdown",
                }
            ],
        )
        template = _load_from_root(root, "fixture")
        self.assertIs(template.nodes[0].artifact_mode, ArtifactMode.MARKDOWN)
        self.assertEqual(
            template.nodes[0].metadata()["artifact_mode"], "markdown"
        )

    def test_node_artifact_custom_carries_its_spec(self) -> None:
        root = self.write(
            prompts={"n0.md": "a"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                    "artifact_mode": "custom",
                    "artifact_spec": "  a risk table  ",
                }
            ],
        )
        template = _load_from_root(root, "fixture")
        self.assertIs(template.nodes[0].artifact_mode, ArtifactMode.CUSTOM)
        self.assertEqual(template.nodes[0].artifact_spec, "a risk table")

    def test_unknown_artifact_mode_is_rejected(self) -> None:
        self.write(
            prompts={"n0.md": "a"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                    "artifact_mode": "pdf",
                }
            ],
        )
        with self.assertRaisesRegex(TemplateError, "unknown artifact_mode"):
            _load_from_root(self.root, "fixture")

    def test_custom_without_spec_is_rejected(self) -> None:
        self.write(
            prompts={"n0.md": "a"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                    "artifact_mode": "custom",
                }
            ],
        )
        with self.assertRaisesRegex(TemplateError, "non-empty artifact_spec"):
            _load_from_root(self.root, "fixture")

    def test_spec_without_custom_is_rejected(self) -> None:
        self.write(
            prompts={"n0.md": "a"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "regular",
                    "prompt_file": "prompts/n0.md",
                    "artifact_mode": "markdown",
                    "artifact_spec": "stray",
                }
            ],
        )
        with self.assertRaisesRegex(TemplateError, "only valid when"):
            _load_from_root(self.root, "fixture")

    def test_review_node_must_not_carry_artifact_mode(self) -> None:
        self.write(
            prompts={"n0.md": "a"},
            nodes=[
                {
                    "id": "n0",
                    "kind": "agent",
                    "category": "review",
                    "subtype": "agentic_review",
                    "brief": {
                        "check_what": "c",
                        "expected": "e",
                        "abnormal": "a",
                    },
                    "prompt_file": "prompts/n0.md",
                    "artifact_mode": "markdown",
                }
            ],
        )
        with self.assertRaisesRegex(TemplateError, "review node"):
            _load_from_root(self.root, "fixture")


if __name__ == "__main__":
    unittest.main()
