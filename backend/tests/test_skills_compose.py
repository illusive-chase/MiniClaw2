"""Tests for skill plug loading via ``settings_snapshot["extra_skills"]``.

Covers the per-node opt-in loading path added in PR 1 of the Skills
proposal — bare-slug normalization, dedupe against bindings, missing
plug reporting, and PlugRef.source provenance in bundle sources.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import yaml

from miniclaw2.contextspace import (
    compose_context_bundle,
    ensure_project_binding,
    list_skills,
)
from miniclaw2.domain import Node, Project


def _write_skill(ctx_root: Path, slug: str, *, body: str, injection: str = "system") -> None:
    plug_dir = ctx_root / "plugs" / "skills" / slug
    plug_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 1,
        "kind": "skill",
        "id": f"skills.{slug}",
        "title": slug.replace("-", " ").title(),
        "injection": injection,
    }
    (plug_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    (plug_dir / "CONTEXT.md").write_text(body, encoding="utf-8")


class ComposeExtraSkillsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx_root = Path(self.tmp.name) / "ctx"
        os.environ["MINICLAW_CONTEXT_HOME"] = str(self.ctx_root)
        self.project = Project(
            root_path=str(Path(self.tmp.name) / "repo"),
            name="proj",
        )
        Path(self.project.root_path).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def _node(self, extra_skills: list[str]) -> Node:
        return Node(
            project_id=self.project.id,
            prompt="hi",
            settings_snapshot={"extra_skills": extra_skills},
        )

    # ---- happy paths ----

    def test_bare_slug_loads_skill_with_node_opt_in_source(self) -> None:
        _write_skill(self.ctx_root, "vim", body="Use hjkl to move.\n")
        bundle = compose_context_bundle(self.project, self._node(["vim"]))
        skill_sources = [s for s in bundle.sources if s.get("kind") == "skill"]
        self.assertEqual(len(skill_sources), 1)
        self.assertEqual(skill_sources[0]["plug_id"], "skills.vim")
        self.assertEqual(skill_sources[0]["source"], "node-opt-in")
        self.assertIn("Use hjkl to move.", bundle.system_text)

    def test_full_id_loads_same_as_bare(self) -> None:
        _write_skill(self.ctx_root, "vim", body="hjkl\n")
        bundle = compose_context_bundle(self.project, self._node(["skills.vim"]))
        skill_sources = [s for s in bundle.sources if s.get("kind") == "skill"]
        self.assertEqual(len(skill_sources), 1)
        self.assertEqual(skill_sources[0]["plug_id"], "skills.vim")

    def test_injection_turn_appends_to_turn_text(self) -> None:
        _write_skill(
            self.ctx_root, "loud", body="LOUD BODY\n", injection="turn"
        )
        bundle = compose_context_bundle(self.project, self._node(["loud"]))
        self.assertIn("LOUD BODY", bundle.turn_text)
        self.assertNotIn("LOUD BODY", bundle.system_text)

    # ---- dedupe ----

    def test_dedupe_bare_and_full_form(self) -> None:
        _write_skill(self.ctx_root, "vim", body="x\n")
        bundle = compose_context_bundle(
            self.project, self._node(["vim", "skills.vim"])
        )
        skill_sources = [s for s in bundle.sources if s.get("kind") == "skill"]
        self.assertEqual(len(skill_sources), 1)

    def test_binding_wins_over_extra_skills(self) -> None:
        # Skill in the project's binding — extra_skills naming the same
        # skill must not double-load, and the binding source wins.
        _write_skill(self.ctx_root, "shared", body="body\n")
        binding = ensure_project_binding(self.project)
        raw = dict(binding.raw)
        raw["plugs"] = [{"id": "skills.shared", "enabled": True}]
        (binding.path).write_text(
            yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
        )
        bundle = compose_context_bundle(self.project, self._node(["shared"]))
        skill_sources = [s for s in bundle.sources if s.get("kind") == "skill"]
        self.assertEqual(len(skill_sources), 1)
        self.assertEqual(skill_sources[0]["source"], "binding")

    # ---- missing / bad inputs ----

    def test_missing_skill_records_missing_flag(self) -> None:
        bundle = compose_context_bundle(
            self.project, self._node(["not-a-real-skill"])
        )
        skill_sources = [s for s in bundle.sources if s.get("kind") == "skill"]
        self.assertEqual(len(skill_sources), 1)
        self.assertEqual(skill_sources[0]["plug_id"], "skills.not-a-real-skill")
        self.assertTrue(skill_sources[0].get("missing"))
        self.assertEqual(skill_sources[0]["source"], "node-opt-in")

    def test_non_skill_prefixed_ids_are_dropped(self) -> None:
        _write_skill(self.ctx_root, "vim", body="x\n")
        bundle = compose_context_bundle(
            self.project,
            self._node(["planspaces.foo", "global.bar", "", "  ", "vim"]),
        )
        skill_sources = [s for s in bundle.sources if s.get("kind") == "skill"]
        self.assertEqual(len(skill_sources), 1)
        self.assertEqual(skill_sources[0]["plug_id"], "skills.vim")

    def test_no_extra_skills_key_is_noop(self) -> None:
        node = Node(project_id=self.project.id, prompt="hi")
        # Should compose without raising and without any skill sources.
        bundle = compose_context_bundle(self.project, node)
        skill_sources = [s for s in bundle.sources if s.get("kind") == "skill"]
        self.assertEqual(skill_sources, [])


class ListSkillsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx_root = Path(self.tmp.name) / "ctx"
        os.environ["MINICLAW_CONTEXT_HOME"] = str(self.ctx_root)

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def test_empty_root_returns_empty_list(self) -> None:
        self.assertEqual(list_skills(), [])

    def test_lists_skills_with_manifest_fields(self) -> None:
        _write_skill(self.ctx_root, "vim", body="x\n")
        _write_skill(self.ctx_root, "git", body="y\n", injection="turn")
        skills = list_skills()
        by_id = {s["id"]: s for s in skills}
        self.assertEqual(set(by_id), {"skills.vim", "skills.git"})
        self.assertEqual(by_id["skills.git"]["injection"], "turn")
        self.assertEqual(by_id["skills.vim"]["kind"], "skill")
        self.assertEqual(by_id["skills.vim"]["slug"], "vim")

    def test_skips_plug_dirs_without_manifest(self) -> None:
        skills_dir = self.ctx_root / "plugs" / "skills"
        (skills_dir / "orphan").mkdir(parents=True, exist_ok=True)
        _write_skill(self.ctx_root, "vim", body="x\n")
        skills = list_skills()
        ids = {s["id"] for s in skills}
        self.assertEqual(ids, {"skills.vim"})


if __name__ == "__main__":
    unittest.main()
