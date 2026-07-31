from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from miniclaw2.app import create_app

from miniclaw2.contextspace import contextspace_root
from miniclaw2.domain import Node, Project
from miniclaw2.events import Activity
from miniclaw2.runner import NodeRunner
from miniclaw2.skills import (
    SkillError,
    delete_agent_skill,
    import_agent_skill,
    expand_skill_selections,
    list_agent_skills,
    materialize_agent_skills,
    normalize_skill_selections,
    skill_content_hash,
)
from miniclaw2.store import Store
from miniclaw2.sync import SCHEMA_VERSION


def _write_skill(
    root: Path,
    slug: str,
    *,
    name: str = "Test Skill",
    siblings: list[str] | None = None,
) -> Path:
    skill = root / slug
    skill.mkdir(parents=True, exist_ok=True)
    requires = ""
    if siblings is not None:
        requires = (
            "metadata:\n"
            "  requires:\n"
            f"    siblings: {json.dumps(siblings)}\n"
        )
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A useful test skill\n{requires}---\n\n"
        "# Instructions\n\nDo it.\n",
        encoding="utf-8",
    )
    (skill / "references").mkdir(exist_ok=True)
    (skill / "references" / "notes.md").write_text("notes\n", encoding="utf-8")
    return skill


class AgentSkillLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store_root = self.root / "home"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_normalizes_string_and_structured_selections(self) -> None:
        self.assertEqual(
            normalize_skill_selections([
                "alpha",
                {"id": "skills.alpha", "suggest": True},
                {"id": "beta", "suggest": False},
                "principles.nope",
            ]),
            [
                {"id": "skills.alpha", "suggest": True},
                {"id": "skills.beta", "suggest": False},
            ],
        )

    def test_import_list_hash_and_delete_local_skill(self) -> None:
        source = _write_skill(self.root / "source", "alpha", name="Alpha")
        imported = import_agent_skill(str(source), store_root=self.store_root)
        self.assertEqual(imported["id"], "skills.alpha")
        self.assertEqual(imported["name"], "Alpha")
        self.assertIn("references/notes.md", imported["files"])
        listed = list_agent_skills(self.store_root)
        self.assertEqual([item["id"] for item in listed], ["skills.alpha"])
        destination = contextspace_root(self.store_root) / "skills" / "alpha"
        before = skill_content_hash(destination)
        (destination / "references" / "notes.md").write_text("changed\n", encoding="utf-8")
        self.assertNotEqual(skill_content_hash(destination), before)
        self.assertTrue(delete_agent_skill("skills.alpha", store_root=self.store_root))
        self.assertEqual(list_agent_skills(self.store_root), [])

    def test_single_skill_import_preserves_explicit_slug_override(self) -> None:
        source = _write_skill(self.root / "source", "source-name", name="Source Name")

        imported = import_agent_skill(
            str(source),
            slug="renamed",
            store_root=self.store_root,
        )

        self.assertEqual(imported["id"], "skills.renamed")
        library = contextspace_root(self.store_root) / "skills"
        self.assertTrue((library / "renamed" / "SKILL.md").is_file())
        self.assertFalse((library / "source-name").exists())

    def test_imports_zip_and_rejects_path_traversal(self) -> None:
        archive = self.root / "skill.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr(
                "alpha/SKILL.md",
                "---\nname: Alpha\ndescription: From zip\n---\n\nBody\n",
            )
        imported = import_agent_skill(str(archive), store_root=self.store_root)
        self.assertEqual(imported["slug"], "alpha")

        malicious = self.root / "malicious.zip"
        with zipfile.ZipFile(malicious, "w") as zipped:
            zipped.writestr("../SKILL.md", "bad")
        with self.assertRaises(SkillError):
            import_agent_skill(str(malicious), store_root=self.store_root)

    def test_root_level_zip_uses_frontmatter_slug(self) -> None:
        archive = self.root / "root-skill.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr(
                "SKILL.md",
                "---\nname: Root Archive Skill\ndescription: From zip root\n---\n\nBody\n",
            )

        imported = import_agent_skill(str(archive), store_root=self.store_root)

        self.assertEqual(imported["slug"], "root-archive-skill")
        self.assertFalse(
            (contextspace_root(self.store_root) / "skills" / "archive").exists()
        )

    def test_root_level_git_uses_frontmatter_slug_without_git_metadata(self) -> None:
        repository = self.root / "source-repository"
        repository.mkdir()
        subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
        (repository / "SKILL.md").write_text(
            "---\nname: Root Git Skill\ndescription: From git root\n---\n\nBody\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "SKILL.md"], cwd=repository, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=MiniClaw Test",
                "-c",
                "user.email=test@miniclaw.invalid",
                "commit",
                "-m",
                "add skill",
            ],
            cwd=repository,
            check=True,
            capture_output=True,
        )

        imported = import_agent_skill(
            repository.as_uri(), store_root=self.store_root
        )
        imported_root = contextspace_root(self.store_root) / "skills" / "root-git-skill"

        self.assertEqual(imported["slug"], "root-git-skill")
        self.assertTrue((imported_root / "SKILL.md").is_file())
        self.assertEqual(list(imported_root.rglob(".git")), [])

    def test_materializes_claude_plugin_and_codex_extra_roots(self) -> None:
        source = _write_skill(self.root / "source", "alpha", name="Alpha")
        import_agent_skill(str(source), store_root=self.store_root)
        claude = materialize_agent_skills(
            [{"id": "alpha", "suggest": True}],
            provider="claude",
            store_root=self.store_root,
            workspace_root=self.root / "claude-run",
        )
        self.assertTrue((claude.plugin_dir / "skills" / "alpha" / "SKILL.md").is_file())
        self.assertEqual(claude.audit[0]["mechanism"], "claude-plugin-dir")
        self.assertEqual(len(claude.suggestions), 1)

        codex = materialize_agent_skills(
            ["skills.alpha", "skills.missing"],
            provider="codex",
            store_root=self.store_root,
            workspace_root=self.root / "codex-run",
        )
        self.assertEqual(len(codex.extra_roots), 1)
        self.assertTrue((Path(codex.extra_roots[0]) / "SKILL.md").is_file())
        self.assertEqual(codex.audit[0]["mechanism"], "codex-extra-roots")
        self.assertEqual(codex.env_overrides, {})
        self.assertTrue(codex.audit[1]["missing"])

    def test_recursively_expands_sibling_dependencies(self) -> None:
        source = self.root / "source"
        _write_skill(source, "shared", name="Shared")
        _write_skill(source, "child", name="Child", siblings=["shared"])
        _write_skill(source, "parent", name="Parent", siblings=["child", "missing"])
        for slug in ("shared", "child", "parent"):
            import_agent_skill(
                str(source),
                slug=slug,
                store_root=self.store_root,
            )

        expanded = expand_skill_selections(
            [{"id": "skills.parent", "suggest": True}],
            store_root=self.store_root,
        )

        self.assertEqual(
            [item["id"] for item in expanded],
            ["skills.parent", "skills.child", "skills.missing", "skills.shared"],
        )
        self.assertNotIn("auto_attached", expanded[0])
        self.assertEqual(expanded[1]["attachment_reason"], "dependency")
        self.assertEqual(expanded[1]["required_by"], "skills.parent")
        self.assertFalse(expanded[1]["suggest"])

        codex = materialize_agent_skills(
            expanded,
            provider="codex",
            store_root=self.store_root,
            workspace_root=self.root / "codex-dependencies",
        )
        self.assertEqual(len(codex.extra_roots), 3)
        self.assertTrue(next(item for item in codex.audit if item["slug"] == "missing")["missing"])

    def test_imports_and_auto_attaches_a_complete_skill_package(self) -> None:
        source = self.root / "official-pack"
        _write_skill(source, "alpha", name="Alpha")
        _write_skill(source, "beta", name="Beta", siblings=["alpha"])

        imported = import_agent_skill(str(source), store_root=self.store_root)

        self.assertEqual(imported["kind"], "skill-pack")
        self.assertEqual(imported["count"], 2)
        self.assertEqual(
            [item["id"] for item in imported["skills"]],
            ["skills.alpha", "skills.beta"],
        )
        listed = list_agent_skills(self.store_root)
        alpha = next(item for item in listed if item["slug"] == "alpha")
        self.assertTrue(alpha["auto_attach_package"])
        self.assertEqual(
            alpha["package_members"],
            ["skills.alpha", "skills.beta"],
        )

        expanded = expand_skill_selections(
            [{"id": "skills.alpha", "suggest": True}],
            store_root=self.store_root,
        )
        self.assertEqual(
            [item["id"] for item in expanded],
            ["skills.alpha", "skills.beta"],
        )
        self.assertEqual(expanded[1]["attachment_reason"], "package")
        self.assertEqual(
            expand_skill_selections(expanded[1:], store_root=self.store_root),
            [],
        )

        claude = materialize_agent_skills(
            expanded,
            provider="claude",
            store_root=self.store_root,
            workspace_root=self.root / "claude-pack",
        )
        assert claude.plugin_dir is not None
        for slug in ("alpha", "beta"):
            self.assertTrue(
                (claude.plugin_dir / "skills" / slug / "SKILL.md").is_file()
            )

        self.assertTrue(delete_agent_skill("beta", store_root=self.store_root))
        remaining = list_agent_skills(self.store_root)[0]
        self.assertEqual(remaining["package_members"], ["skills.alpha"])

    def test_package_includes_root_skill_and_nested_members(self) -> None:
        source = _write_skill(self.root, "package-root", name="Package Root")
        _write_skill(source, "nested", name="Nested")

        imported = import_agent_skill(str(source), store_root=self.store_root)

        self.assertEqual(imported["kind"], "skill-pack")
        self.assertEqual(imported["count"], 2)
        self.assertEqual(
            [item["id"] for item in imported["skills"]],
            ["skills.nested", "skills.package-root"],
        )
        listed = {item["slug"]: item for item in list_agent_skills(self.store_root)}
        self.assertEqual(set(listed), {"nested", "package-root"})
        for item in listed.values():
            self.assertEqual(
                item["package_members"],
                ["skills.nested", "skills.package-root"],
            )

    def test_single_reimport_detaches_member_from_previous_package(self) -> None:
        source = self.root / "official-pack"
        _write_skill(source, "alpha", name="Alpha")
        _write_skill(source, "beta", name="Beta")
        import_agent_skill(str(source), store_root=self.store_root)

        imported = import_agent_skill(
            str(source),
            slug="alpha",
            store_root=self.store_root,
        )

        self.assertNotIn("package_id", imported)
        listed = {item["slug"]: item for item in list_agent_skills(self.store_root)}
        self.assertNotIn("package_id", listed["alpha"])
        self.assertEqual(listed["beta"]["package_members"], ["skills.beta"])
        self.assertEqual(
            expand_skill_selections(
                [{"id": "skills.alpha", "suggest": True}],
                store_root=self.store_root,
            ),
            [{"id": "skills.alpha", "suggest": True}],
        )

    def test_package_validation_is_atomic(self) -> None:
        source = self.root / "invalid-pack"
        _write_skill(source, "alpha", name="Alpha")
        invalid = source / "beta"
        invalid.mkdir(parents=True)
        (invalid / "SKILL.md").write_text("not frontmatter\n", encoding="utf-8")

        with self.assertRaises(SkillError):
            import_agent_skill(str(source), store_root=self.store_root)

        self.assertEqual(list_agent_skills(self.store_root), [])


class AgentSkillApiTests(unittest.TestCase):
    def test_import_list_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = _write_skill(root / "source", "alpha", name="Alpha")
            previous = os.environ.get("MINICLAW_HOME")
            os.environ["MINICLAW_HOME"] = str(root / "home")
            try:
                with TestClient(create_app()) as client:
                    response = client.post("/skills/import", json={"source": str(source)})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["id"], "skills.alpha")
                    self.assertEqual(client.get("/skills").json()[0]["name"], "Alpha")
                    self.assertEqual(client.delete("/skills/alpha").status_code, 204)
                    self.assertEqual(client.get("/skills").json(), [])
            finally:
                if previous is None:
                    os.environ.pop("MINICLAW_HOME", None)
                else:
                    os.environ["MINICLAW_HOME"] = previous

    def test_imports_multi_skill_source_as_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            _write_skill(source, "alpha", name="Alpha")
            _write_skill(source, "beta", name="Beta")
            previous = os.environ.get("MINICLAW_HOME")
            os.environ["MINICLAW_HOME"] = str(root / "home")
            try:
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/skills/import",
                        json={"source": str(source)},
                    )
                    self.assertEqual(response.status_code, 200)
                    payload = response.json()
                    self.assertEqual(payload["kind"], "skill-pack")
                    self.assertEqual(payload["count"], 2)
                    self.assertEqual(
                        [item["id"] for item in client.get("/skills").json()],
                        ["skills.alpha", "skills.beta"],
                    )
            finally:
                if previous is None:
                    os.environ.pop("MINICLAW_HOME", None)
                else:
                    os.environ["MINICLAW_HOME"] = previous


class SkillAuditTests(unittest.TestCase):
    def test_activity_marks_only_confident_skill_match_used(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root=root / "home")
            project = store.create_project(Project(root_path=str(root / "repo")))
            node = store.create_node(Node(
                project_id=project.id,
                model_preset_id="gpt-5.5",
                settings_snapshot={
                    "skill_audit": [{
                        "id": "skills.alpha",
                        "slug": "alpha",
                        "name": "Alpha",
                        "materialized_path": "/tmp/run/skills/alpha",
                        "used": False,
                    }],
                },
            ))

            async def _ignore(_event: dict[str, object]) -> None:
                return None

            runner = NodeRunner(node, project, store, _ignore)
            self.assertFalse(runner._record_skill_use(Activity(
                kind="tool", status="start", id="1", name="command", summary="ls"
            )))
            self.assertTrue(runner._record_skill_use(Activity(
                kind="tool",
                status="start",
                id="2",
                name="command",
                summary="cat /tmp/run/skills/alpha/SKILL.md",
            )))
            self.assertTrue(node.settings_snapshot["skill_audit"][0]["used"])

    def test_claude_namespaced_skill_command_marks_skill_used(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root=root / "home")
            project = store.create_project(Project(root_path=str(root / "repo")))
            node = store.create_node(Node(
                project_id=project.id,
                model_preset_id="gpt-5.5",
                settings_snapshot={
                    "skill_audit": [{
                        "id": "skills.alpha",
                        "slug": "alpha",
                        "name": "Alpha",
                        "used": False,
                    }],
                },
            ))

            async def _ignore(_event: dict[str, object]) -> None:
                return None

            runner = NodeRunner(node, project, store, _ignore)
            self.assertTrue(runner._record_skill_use(Activity(
                kind="tool",
                status="start",
                id="skill-1",
                name="Skill",
                summary=json.dumps({"command": "skill-plugin:alpha"}),
            )))


class PrinciplesSkillsMigrationTests(unittest.TestCase):
    def test_schema_v5_migrates_old_skill_records_without_touching_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "schema.json").write_text(
                json.dumps({"schema": "canonical-schema-v5", "schema_version": 5}),
                encoding="utf-8",
            )
            old = root / "contextspace" / "plugs" / "skills" / "review"
            old.mkdir(parents=True)
            (old / "manifest.yaml").write_text(
                "kind: skill\nid: skills.review\ntitle: Review\n",
                encoding="utf-8",
            )
            (old / "CONTEXT.md").write_text("review\n", encoding="utf-8")
            binding = root / "contextspace" / "bindings" / "projects" / "one.yaml"
            binding.parent.mkdir(parents=True)
            binding.write_text("id: one\nplugs:\n  - id: skills.review\n", encoding="utf-8")
            template = root / "contextspace" / "templates" / "review.yaml"
            template.parent.mkdir(parents=True)
            template.write_text(
                "id: review\nsettings:\n  extra_skills:\n    - skills.review\n",
                encoding="utf-8",
            )
            node_file = root / "projects" / "p" / "nodes" / "n" / "node.json"
            node_file.parent.mkdir(parents=True)
            node_file.write_text(json.dumps({
                "settings_snapshot": {"extra_skills": ["skills.review"]},
                "pending_extra_skills": ["skills.review"],
                "agent_op_kind": "skill_edit",
            }), encoding="utf-8")
            snapshot = root / "contextspace" / "snapshots" / "old.json"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text(json.dumps({"kind": "skill", "plug_id": "skills.review"}), encoding="utf-8")

            Store(root=root)

            schema = json.loads((root / "schema.json").read_text(encoding="utf-8"))
            self.assertEqual(schema["schema_version"], SCHEMA_VERSION)
            self.assertTrue((root / "contextspace" / "plugs" / "principles" / "review").is_dir())
            migrated = json.loads(node_file.read_text(encoding="utf-8"))
            self.assertEqual(
                migrated["settings_snapshot"]["extra_principles"],
                ["principles.review"],
            )
            self.assertEqual(migrated["pending_extra_principles"], ["principles.review"])
            self.assertEqual(migrated["pending_extra_skills"], [])
            self.assertEqual(migrated["agent_op_kind"], "principle_edit")
            self.assertIn(
                "principles.review",
                template.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                json.loads(snapshot.read_text(encoding="utf-8"))["kind"],
                "skill",
            )

    def test_schema_v5_migrates_configured_external_contextspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "home"
            context_root = Path(temp) / "external-context"
            root.mkdir()
            (root / "schema.json").write_text(
                json.dumps({"schema": "canonical-schema-v5", "schema_version": 5}),
                encoding="utf-8",
            )
            old = context_root / "plugs" / "skills" / "review"
            old.mkdir(parents=True)
            (old / "manifest.yaml").write_text(
                "kind: skill\nid: skills.review\ntitle: Review\n",
                encoding="utf-8",
            )
            (old / "CONTEXT.md").write_text("review\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {"MINICLAW_CONTEXT_HOME": str(context_root)},
            ):
                Store(root=root)

            self.assertFalse(old.exists())
            migrated = context_root / "plugs" / "principles" / "review"
            self.assertTrue(migrated.is_dir())
            self.assertIn("kind: principle", (migrated / "manifest.yaml").read_text())
            backups = list(
                (root / "migration-backups").glob(
                    "*/contextspace/plugs/skills/review/CONTEXT.md"
                )
            )
            self.assertEqual(len(backups), 1)
            schema = json.loads((root / "schema.json").read_text(encoding="utf-8"))
            self.assertEqual(schema["schema_version"], SCHEMA_VERSION)

    def test_migration_preserves_unrelated_yaml_text_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "schema.json").write_text(
                json.dumps({"schema": "canonical-schema-v5", "schema_version": 5}),
                encoding="utf-8",
            )
            unchanged = root / "templates" / "unchanged.yaml"
            unchanged.parent.mkdir(parents=True)
            original = "# keep this comment\nid: ordinary\ndescription: skills.write prose\n"
            unchanged.write_text(original, encoding="utf-8")
            changed = root / "templates" / "changed.yaml"
            changed.write_text(
                "id: review\ndescription: skills.keep this prose\nsettings:\n  extra_skills:\n    - skills.review\n",
                encoding="utf-8",
            )

            Store(root=root)

            self.assertEqual(unchanged.read_text(encoding="utf-8"), original)
            migrated = changed.read_text(encoding="utf-8")
            self.assertIn("description: skills.keep this prose", migrated)
            self.assertIn("extra_principles:", migrated)
            self.assertIn("principles.review", migrated)


if __name__ == "__main__":
    unittest.main()
