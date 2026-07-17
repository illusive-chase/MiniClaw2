"""End-to-end runner validation for librarian-authored entries."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import yaml

from miniclaw2 import runner as runner_module
from miniclaw2.contextspace import contextspace_root, create_planspace
from miniclaw2.domain import Category, Node, NodeState, Project
from miniclaw2.providers import AgentProviderContext, AgentProviderEvent
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)


def _write_skill(root: Path, slug: str, *, body: str = "Do it.\n") -> Path:
    target = root / "skills" / slug
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(
        "---\nname: Test Skill\ndescription: Use for the test workflow\n---\n\n"
        f"# Instructions\n\n{body}",
        encoding="utf-8",
    )
    return target


def _write_principle(root: Path, slug: str) -> Path:
    target = root / "plugs" / "principles" / slug
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.yaml").write_text(
        yaml.safe_dump(
            {"version": 1, "kind": "principle", "id": f"principles.{slug}"},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (target / "CONTEXT.md").write_text("# Test\n\nUse evidence.\n", encoding="utf-8")
    return target


def _write_preview(context: AgentProviderContext) -> None:
    node = context.node
    lane = node.planspace_id or ""
    path = (
        Path(context.project.root_path)
        / ".miniclaw2"
        / "graph"
        / "runs"
        / node.id
        / "lanes"
        / lane
        / "nodes"
        / node.id
        / "preview.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": node.id,
                "kind": "agent",
                "category": "regular",
                "state": "done",
                "ran_at": "2026-07-17T00:00:00+00:00",
                "lane": lane,
                "motivation": "author a library entry",
                "summary": "created library entry",
                "next_implications": "entry is available",
            }
        ),
        encoding="utf-8",
    )


class _AuthoringProvider:
    name = "stub"

    def __init__(self, writer: Callable[[AgentProviderContext], None]) -> None:
        self.writer = writer
        self.turns = 0

    async def run(self, context: AgentProviderContext):
        self.turns += 1
        self.writer(context)
        _write_preview(context)
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class LibraryEditRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.store_root = Path(self.tmp.name) / "store"
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        _init_repo(self.repo)
        self.store = Store(root=self.store_root)
        self.project = Project(root_path=str(self.repo))
        self.store.create_project(self.project)
        self.plug_id = create_planspace(
            self.project,
            title="library-lane",
            mode="manual",
        )
        self.project.active_planspace_id = self.plug_id
        self.store.update_project(self.project)
        self.context_root = contextspace_root(self.store_root)

    async def asyncTearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def _node(self) -> Node:
        node = Node(
            project_id=self.project.id,
            model_preset_id="opus-4-7",
            category=Category.REGULAR,
            state=NodeState.QUEUED,
            planspace_id=self.plug_id,
            prompt="author reusable guidance",
            agent_op_kind="library_edit",
        )
        self.store.create_node(node)
        return node

    async def _run(self, writer: Callable[[AgentProviderContext], None]) -> Node:
        node = self._node()
        provider = _AuthoringProvider(writer)

        async def on_event(_payload: dict) -> None:
            return None

        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            await asyncio.wait_for(runner.run(), timeout=5.0)
        self.assertEqual(provider.turns, 1)
        return node

    async def test_valid_authored_skill_is_done_audited_and_provenanced(self) -> None:
        node = await self._run(lambda _context: _write_skill(self.context_root, "release"))
        self.assertEqual(node.state, NodeState.DONE)
        audit = node.settings_snapshot["library_audit"]
        self.assertEqual(audit[0]["kind"], "skill")
        self.assertEqual(audit[0]["slug"], "release")
        self.assertEqual(audit[0]["action"], "created")
        self.assertEqual(audit[0]["verdict"], "valid")
        self.assertTrue(audit[0]["content_hash"])
        provenance = json.loads(
            (self.context_root / "skill-imports.json").read_text(encoding="utf-8")
        )["release"]
        self.assertEqual(provenance["import_kind"], "authored")
        self.assertEqual(provenance["import_source"], f"node:{node.id}")

    async def test_malformed_skill_errors_with_slug_and_reason(self) -> None:
        def write_bad(_context: AgentProviderContext) -> None:
            target = self.context_root / "skills" / "broken"
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("# No frontmatter\n", encoding="utf-8")

        node = await self._run(write_bad)
        self.assertEqual(node.state, NodeState.ERROR)
        self.assertIn("broken", node.error or "")
        self.assertIn("frontmatter", node.error or "")
        self.assertEqual(node.settings_snapshot["library_audit"][0]["verdict"], "error")

    async def test_nested_symlink_errors(self) -> None:
        def write_symlink(_context: AgentProviderContext) -> None:
            target = _write_skill(self.context_root, "linked")
            (target / "reference-link").symlink_to(target / "SKILL.md")

        node = await self._run(write_symlink)
        self.assertEqual(node.state, NodeState.ERROR)
        self.assertIn("linked", node.error or "")
        self.assertIn("symlink", (node.error or "").lower())

    async def test_zero_touched_entries_errors(self) -> None:
        node = await self._run(lambda _context: None)
        self.assertEqual(node.state, NodeState.ERROR)
        self.assertIn("without authoring anything", node.error or "")

    async def test_two_touched_entries_error_and_name_both(self) -> None:
        def write_two(_context: AgentProviderContext) -> None:
            _write_skill(self.context_root, "alpha")
            _write_principle(self.context_root, "beta")

        node = await self._run(write_two)
        self.assertEqual(node.state, NodeState.ERROR)
        self.assertIn("alpha", node.error or "")
        self.assertIn("beta", node.error or "")

    async def test_valid_authored_principle_is_done(self) -> None:
        node = await self._run(
            lambda _context: _write_principle(self.context_root, "evidence-first")
        )
        self.assertEqual(node.state, NodeState.DONE)
        self.assertEqual(
            node.settings_snapshot["library_audit"][0],
            {
                "kind": "principle",
                "slug": "evidence-first",
                "action": "created",
                "content_hash": node.settings_snapshot["library_audit"][0]["content_hash"],
                "verdict": "valid",
            },
        )

    async def test_refinement_preserves_existing_provenance(self) -> None:
        skill = _write_skill(self.context_root, "release", body="Old body.\n")
        provenance = {
            "release": {
                "import_source": "/tmp/community/release",
                "import_kind": "local",
                "imported_at": 123.0,
            }
        }
        (self.context_root / "skill-imports.json").write_text(
            json.dumps(provenance),
            encoding="utf-8",
        )

        def refine(_context: AgentProviderContext) -> None:
            with (skill / "SKILL.md").open("a", encoding="utf-8") as handle:
                handle.write("\nNew guidance.\n")

        node = await self._run(refine)
        self.assertEqual(node.state, NodeState.DONE)
        self.assertEqual(node.settings_snapshot["library_audit"][0]["action"], "refined")
        self.assertEqual(
            json.loads(
                (self.context_root / "skill-imports.json").read_text(encoding="utf-8")
            ),
            provenance,
        )


if __name__ == "__main__":
    unittest.main()
