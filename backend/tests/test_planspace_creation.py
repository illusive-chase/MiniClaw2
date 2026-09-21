"""Tests for the planspace plug helpers added in step 8."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from miniclaw2.contextspace import (
    add_planspace_to_binding,
    contextspace_root,
    create_planspace,
    describe_project_contextspace,
    ensure_project_binding,
    list_project_planspace_ids,
    read_planspace_mode,
    resolve_project_binding,
    set_planspace_mode,
)
from miniclaw2.domain import NodeState, Project
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


class PlanspaceCreationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store_root = Path(self.tmp.name) / "store"
        os.environ["MINICLAW_CONTEXT_HOME"] = str(self.store_root / "ctx")
        self.project = Project(
            root_path=str(Path(self.tmp.name) / "repo"),
            name="auth-flow",
        )
        Path(self.project.root_path).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def test_create_planspace_creates_manifest_and_appends_to_binding(self) -> None:
        plug_id = create_planspace(
            self.project,
            title="Auth flow",
            mode="manual",
            seed_text="Build the auth flow",
        )
        self.assertEqual(plug_id, "planspaces.auth-flow.auth-flow")

        root = Path(os.environ["MINICLAW_CONTEXT_HOME"])
        manifest_path = (
            root
            / "plugs"
            / "planspaces"
            / "auth-flow.auth-flow"
            / "manifest.yaml"
        )
        self.assertTrue(manifest_path.exists())
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["kind"], "planspace")
        self.assertEqual(manifest["mode"], "manual")
        self.assertEqual(manifest["title"], "Auth flow")
        self.assertEqual(manifest["seed"], "Build the auth flow")
        self.assertEqual(manifest["id"], plug_id)

        binding = resolve_project_binding(self.project, root)
        assert binding is not None
        plug_ids = [ref.id for ref in binding.plugs]
        self.assertIn(plug_id, plug_ids)

    def test_create_planspace_idempotent_unique_slug(self) -> None:
        first = create_planspace(self.project, title="Direction", mode="manual")
        second = create_planspace(self.project, title="Direction", mode="manual")
        self.assertNotEqual(first, second)
        self.assertEqual(first, "planspaces.auth-flow.direction")
        self.assertEqual(second, "planspaces.auth-flow.direction-2")

    def test_same_lane_slug_is_not_numbered_across_projects(self) -> None:
        other = Project(
            root_path=str(Path(self.tmp.name) / "other-repo"),
            name="billing",
        )
        Path(other.root_path).mkdir(parents=True, exist_ok=True)

        first = create_planspace(self.project, title="Direction", mode="manual")
        other_first = create_planspace(other, title="Direction", mode="manual")

        self.assertEqual(first, "planspaces.auth-flow.direction")
        self.assertEqual(other_first, "planspaces.billing.direction")

    def test_create_planspace_rejects_unknown_mode(self) -> None:
        with self.assertRaises(ValueError):
            create_planspace(self.project, title="x", mode="weird")

    def test_add_planspace_to_binding_is_idempotent(self) -> None:
        root = Path(os.environ["MINICLAW_CONTEXT_HOME"])
        binding = ensure_project_binding(self.project)
        plug_id = "planspaces.auth-flow.foo"
        add_planspace_to_binding(binding, plug_id)
        add_planspace_to_binding(binding, plug_id)
        reloaded = resolve_project_binding(self.project, root)
        assert reloaded is not None
        plug_ids = [ref.id for ref in reloaded.plugs]
        self.assertEqual(plug_ids.count(plug_id), 1)

    def test_add_planspace_to_binding_rejects_foreign_scope(self) -> None:
        binding = ensure_project_binding(self.project)

        with self.assertRaisesRegex(ValueError, "does not belong"):
            add_planspace_to_binding(binding, "planspaces.billing.foo")

    def test_binding_owner_id_prevents_duplicate_when_project_link_is_missing(self) -> None:
        root = Path(os.environ["MINICLAW_CONTEXT_HOME"])
        original = ensure_project_binding(self.project)
        self.project.project_context_binding_id = None

        resolved = ensure_project_binding(self.project)

        self.assertEqual(resolved.id, original.id)
        self.assertEqual(self.project.project_context_binding_id, original.id)
        self.assertEqual(
            len(list((root / "bindings" / "projects").glob("*.yaml"))),
            1,
        )


class ReadPlanspaceModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.project = Project(root_path=str(Path(self.tmp.name) / "repo"))
        Path(self.project.root_path).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def test_defaults_to_manual_when_manifest_missing(self) -> None:
        self.assertEqual(
            read_planspace_mode(self.project, "planspaces.foo"),
            "manual",
        )

    def test_defaults_to_manual_when_lane_id_empty(self) -> None:
        self.assertEqual(read_planspace_mode(self.project, ""), "manual")

    def test_reads_auto_mode_from_manifest(self) -> None:
        plug_id = create_planspace(
            self.project, title="auto-lane", mode="auto"
        )
        self.assertEqual(read_planspace_mode(self.project, plug_id), "auto")

    def test_reads_manual_mode_from_manifest(self) -> None:
        plug_id = create_planspace(
            self.project, title="manual-lane", mode="manual"
        )
        self.assertEqual(read_planspace_mode(self.project, plug_id), "manual")

    def test_set_planspace_mode_updates_manifest(self) -> None:
        plug_id = create_planspace(
            self.project, title="lane", mode="manual"
        )

        written = set_planspace_mode(self.project, plug_id, "auto")

        self.assertEqual(written, "auto")
        self.assertEqual(read_planspace_mode(self.project, plug_id), "auto")

    def test_project_cannot_read_or_write_another_projects_mode(self) -> None:
        other = Project(
            root_path=str(Path(self.tmp.name) / "other-repo"),
            name="billing",
        )
        Path(other.root_path).mkdir(parents=True, exist_ok=True)
        foreign = create_planspace(other, title="Owned", mode="manual")

        self.assertEqual(read_planspace_mode(self.project, foreign), "manual")
        with self.assertRaisesRegex(ValueError, "unknown planspace for project"):
            set_planspace_mode(self.project, foreign, "auto")
        self.assertEqual(read_planspace_mode(other, foreign), "manual")

    def test_describe_project_contextspace_includes_bindings_and_mode(self) -> None:
        plug_id = create_planspace(
            self.project, title="Auto lane", mode="auto"
        )

        summary = describe_project_contextspace(self.project)

        self.assertEqual(len(summary["bindings"]), 1)
        binding = summary["bindings"][0]
        plugs = {plug["id"]: plug for plug in binding["plugs"]}
        self.assertIn(plug_id, plugs)
        self.assertEqual(plugs[plug_id]["slug"], "auto-lane")
        self.assertEqual(plugs[plug_id]["mode"], "auto")
        # No lane is privileged in the summary any more: the client decides
        # which one it is looking at.
        self.assertNotIn("active", plugs[plug_id])
        self.assertNotIn("active_planspace_id", summary)
        self.assertNotIn("active_planspace_id", binding)

    def test_describe_project_contextspace_only_returns_current_project_binding(self) -> None:
        other = Project(
            root_path=str(Path(self.tmp.name) / "other-repo"),
            name="billing",
        )
        Path(other.root_path).mkdir(parents=True, exist_ok=True)
        current_plug = create_planspace(
            self.project, title="Auth lane", mode="manual"
        )
        other_plug = create_planspace(
            other, title="Billing lane", mode="manual"
        )

        summary = describe_project_contextspace(self.project)

        self.assertEqual(len(summary["bindings"]), 1)
        binding = summary["bindings"][0]
        self.assertEqual(binding["id"], summary["resolved_binding_id"])
        plug_ids = {plug["id"] for plug in binding["plugs"]}
        self.assertIn(current_plug, plug_ids)
        self.assertNotIn(other_plug, plug_ids)
        # Another project's binding is not offered for selection: the summary
        # reports the one binding this project owns and nothing else.
        self.assertNotIn("selectable_bindings", summary)

    def test_a_binding_owned_by_another_project_is_not_honoured(self) -> None:
        """A stored id naming a foreign binding resolves to the owner's, not it.

        This is the shape a project ended up in when it could adopt another
        project's binding: the id persisted on the project record, and every
        lane created afterwards was scoped to — and stored inside — the other
        project's binding.
        """
        root = Path(os.environ["MINICLAW_CONTEXT_HOME"])
        other = Project(
            root_path=str(Path(self.tmp.name) / "other-repo"),
            name="billing",
        )
        Path(other.root_path).mkdir(parents=True, exist_ok=True)
        own = ensure_project_binding(self.project)
        foreign = ensure_project_binding(other)

        self.project.project_context_binding_id = foreign.id
        resolved = resolve_project_binding(self.project, root)

        assert resolved is not None
        self.assertEqual(resolved.id, own.id)

    def test_an_ownerless_binding_is_still_honoured(self) -> None:
        """Bindings written before the owner id existed must keep resolving."""
        root = Path(os.environ["MINICLAW_CONTEXT_HOME"])
        binding = ensure_project_binding(self.project)
        raw = dict(binding.raw)
        raw["project"] = {
            key: value
            for key, value in (raw.get("project") or {}).items()
            if key != "miniclaw_project_id"
        }
        binding.path.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

        resolved = resolve_project_binding(self.project, root)

        assert resolved is not None
        self.assertEqual(resolved.id, binding.id)


class BlankPlanspaceRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.store = Store(root=Path(self.tmp.name) / "store")
        self.project = Project(
            root_path=str(Path(self.tmp.name) / "repo"),
            name="blank-project",
        )
        Path(self.project.root_path).mkdir(parents=True, exist_ok=True)
        self.store.create_project(self.project)
        self.registry = ProjectRegistry(store=self.store)

    def tearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def test_create_blank_planspace_seeds_empty_virtual(self) -> None:
        result = self.registry.create_blank_planspace(
            self.project.id,
            title="Auth flow",
            seed="Sketch auth work",
            mode="manual",
        )

        self.assertIsNotNone(result)
        assert result is not None
        node = result.node
        self.assertEqual(node.state, NodeState.VIRTUAL)
        self.assertEqual(node.prompt_draft, "")
        self.assertEqual(node.summary, "")
        self.assertEqual(node.scheduled_deps, [])
        self.assertIsNone(node.parent_node_id)
        self.assertEqual(node.planspace_id, "planspaces.blank-project.auth-flow")

        project = self.registry.get_project(self.project.id)
        assert project is not None

        root = Path(os.environ["MINICLAW_CONTEXT_HOME"])
        manifest_path = (
            root
            / "plugs"
            / "planspaces"
            / "blank-project.auth-flow"
            / "manifest.yaml"
        )
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["mode"], "manual")
        self.assertEqual(manifest["seed"], "Sketch auth work")
        self.assertEqual(
            read_planspace_mode(project, "planspaces.blank-project.auth-flow"),
            "manual",
        )

    def test_create_blank_planspace_propagates_auto_mode(self) -> None:
        result = self.registry.create_blank_planspace(
            self.project.id,
            title="Auto lane",
            seed="Prepare auto work",
            mode="auto",
        )

        self.assertIsNotNone(result)
        assert result is not None
        node = result.node
        project = self.registry.get_project(self.project.id)
        assert project is not None
        self.assertEqual(read_planspace_mode(project, node.planspace_id or ""), "auto")
        reloaded = self.store.load_node(self.project.id, node.id)
        assert reloaded is not None
        self.assertEqual(reloaded.state, NodeState.VIRTUAL)

    def test_create_blank_planspace_uses_unique_slug(self) -> None:
        first = self.registry.create_blank_planspace(
            self.project.id,
            title="Direction",
            seed="First",
            mode="manual",
        )
        second = self.registry.create_blank_planspace(
            self.project.id,
            title="Direction",
            seed="Second",
            mode="manual",
        )

        assert first is not None
        assert second is not None
        self.assertEqual(
            first.node.planspace_id, "planspaces.blank-project.direction"
        )
        self.assertEqual(
            second.node.planspace_id, "planspaces.blank-project.direction-2"
        )
        self.assertNotEqual(first.node.planspace_id, second.node.planspace_id)

    def test_create_blank_planspace_rejects_empty_seed(self) -> None:
        with self.assertRaisesRegex(ValueError, "seed"):
            self.registry.create_blank_planspace(
                self.project.id,
                title="Blank",
                seed="  ",
                mode="manual",
            )

    def test_create_blank_planspace_while_running_creates_an_independent_lane(
        self,
    ) -> None:
        """Creating a lane works the same whether the project is busy or idle.

        Before the focus refactor, an idle project would activate the new lane
        and a busy one would defer. Creation now just makes a lane and hands
        it back; the client focuses it locally, and nothing global moves.
        """
        first = self.registry.create_blank_planspace(
            self.project.id,
            title="Current",
            seed="Current work",
            mode="manual",
        )
        assert first is not None
        old_lane = first.node.planspace_id
        runtime = self.registry._runtimes[self.project.id]
        runtime.runner_tasks["busy"] = _PendingTask()  # type: ignore[assignment]
        try:
            result = self.registry.create_blank_planspace(
                self.project.id,
                title="Busy",
                seed="Busy work",
                mode="manual",
            )
        finally:
            runtime.runner_tasks["busy"].cancel()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.node.state, NodeState.VIRTUAL)
        self.assertNotEqual(result.node.planspace_id, old_lane)
        # The earlier lane is untouched: both exist, and neither is privileged.
        lane_ids = list_project_planspace_ids(
            self.project, contextspace_root(self.store.root)
        )
        self.assertIn(old_lane, lane_ids)
        self.assertIn(result.node.planspace_id, lane_ids)

    def test_creating_a_lane_persists_the_binding(self) -> None:
        result = self.registry.create_blank_planspace(
            self.project.id,
            title="Queued first lane",
            seed="Prepare queued work",
            mode="auto",
        )

        assert result is not None
        project = self.registry.get_project(self.project.id)
        assert project is not None
        persisted = {
            item.id: item for item in Store(root=self.store.root).list_projects()
        }[self.project.id]
        self.assertEqual(
            persisted.project_context_binding_id,
            project.project_context_binding_id,
        )
        self.assertIsNotNone(persisted.project_context_binding_id)


class _PendingTask:
    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
