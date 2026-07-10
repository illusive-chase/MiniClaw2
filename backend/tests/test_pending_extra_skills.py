"""Tests for pending_extra_skills virtual-edit + promotion (PR 4)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from miniclaw2.contextspace import (
    create_planspace,
    ensure_project_binding,
)
from miniclaw2.domain import NodeState, Project
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


def _write_skill(ctx_root: Path, slug: str, *, body: str = "body") -> None:
    plug_dir = ctx_root / "plugs" / "skills" / slug
    plug_dir.mkdir(parents=True, exist_ok=True)
    (plug_dir / "manifest.yaml").write_text(
        yaml.safe_dump({
            "version": 1,
            "kind": "skill",
            "id": f"skills.{slug}",
            "title": slug,
        }, sort_keys=False),
        encoding="utf-8",
    )
    (plug_dir / "CONTEXT.md").write_text(body, encoding="utf-8")


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "--allow-empty", "-m", "init"],
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


class UpdateVirtualPendingSkillsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.store = Store(root=Path(self.tmp.name) / "store")
        self.project = Project(
            root_path=str(Path(self.tmp.name) / "repo"),
            name="proj",
        )
        Path(self.project.root_path).mkdir(parents=True, exist_ok=True)
        self.store.create_project(self.project)
        self.registry = ProjectRegistry(store=self.store)
        ensure_project_binding(self.project, store_root=self.store.root)
        lane = create_planspace(
            self.project,
            title="Main",
            store_root=self.store.root,
            seed_text="seed",
        )
        self.project.active_planspace_id = lane
        self.store.update_project(self.project)
        self.registry = ProjectRegistry(store=self.store)
        self.lane = lane

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def test_update_virtual_accepts_pending_extra_skills(self) -> None:
        virtual = self.registry.create_virtual(
            self.project.id, prompt_draft="draft"
        )
        assert virtual is not None
        updated = self.registry.update_virtual(
            self.project.id,
            virtual.id,
            pending_extra_skills=["vim", "skills.git"],
        )
        assert updated is not None
        self.assertEqual(
            updated.pending_extra_skills, ["skills.vim", "skills.git"]
        )

    def test_update_virtual_drops_bad_skill_ids(self) -> None:
        virtual = self.registry.create_virtual(
            self.project.id, prompt_draft="draft"
        )
        assert virtual is not None
        updated = self.registry.update_virtual(
            self.project.id,
            virtual.id,
            pending_extra_skills=["vim", "", "  ", "global.foo", "planspaces.x", 42],
        )
        assert updated is not None
        self.assertEqual(updated.pending_extra_skills, ["skills.vim"])


class PromotePendingSkillsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.ctx_root = Path(os.environ["MINICLAW_CONTEXT_HOME"])
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        _init_repo(self.repo)
        self.store = Store(root=Path(self.tmp.name) / "store")
        self.project = Project(root_path=str(self.repo), name="proj")
        self.store.create_project(self.project)
        self.registry = ProjectRegistry(store=self.store)
        ensure_project_binding(self.project, store_root=self.store.root)
        lane = create_planspace(
            self.project,
            title="Main",
            store_root=self.store.root,
            seed_text="seed",
        )
        self.project.active_planspace_id = lane
        self.store.update_project(self.project)
        self.registry = ProjectRegistry(store=self.store)
        self.lane = lane

    async def asyncTearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    async def test_promotion_moves_pending_to_settings_snapshot(self) -> None:
        _write_skill(self.ctx_root, "vim")
        virtual = self.registry.create_virtual(
            self.project.id, prompt_draft="do the thing"
        )
        assert virtual is not None
        self.registry.update_virtual(
            self.project.id,
            virtual.id,
            pending_extra_skills=["vim"],
        )
        runner = self.registry.promote_virtual(self.project.id, virtual.id)
        assert runner is not None
        # Cancel the just-launched runner immediately — we only care about
        # the state mutation, not a real provider call.
        rt = self.registry._runtimes[self.project.id]
        task = rt.runner_tasks.get(virtual.id)
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, BaseException):
                pass
        promoted = self.store.load_node(self.project.id, virtual.id)
        assert promoted is not None
        self.assertEqual(promoted.pending_extra_skills, [])
        self.assertEqual(
            promoted.settings_snapshot.get("extra_skills"), ["skills.vim"]
        )
        # Promotion also flipped the state away from VIRTUAL.
        self.assertNotEqual(promoted.state, NodeState.VIRTUAL)


if __name__ == "__main__":
    unittest.main()
