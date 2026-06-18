from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from miniclaw2.registry import ProjectRegistry
from miniclaw2.templates import TemplateError, launch_template
from miniclaw2.store import Store


class LaunchTemplateTest(unittest.TestCase):
    def test_launching_hello_text_creates_temp_project_and_virtual_lane(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw))
            registry = ProjectRegistry(store=store)

            project, template = launch_template("hello-text", "claude", registry)

            try:
                self.assertEqual(template.name, "hello-text")
                self.assertTrue(project.temporary)
                self.assertEqual(project.template_id, "hello-text")
                self.assertEqual(project.provider, "claude")
                self.assertEqual(
                    project.settings_override.get("permission_mode"),
                    "bypassPermissions",
                )
                self.assertTrue(project.settings_override.get("active_planspace_id"))

                nodes = store.list_nodes(project.id)
                self.assertEqual(len(nodes), 3)
                first = nodes[0]
                self.assertEqual(first.kind, "agent")
                self.assertEqual(first.state, "virtual")
                self.assertEqual(
                    first.planspace_id,
                    project.settings_override.get("active_planspace_id"),
                )
                self.assertIn("[OK]", first.prompt_draft or "")
                self.assertIsNotNone(store.read_node_preview(project.id, first.id))
                self.assertEqual(nodes[1].kind, "verifier")
                self.assertEqual(nodes[1].subtype, "programmatic_review")
                self.assertEqual(nodes[1].scheduled_deps, [first.id])
                self.assertEqual(nodes[2].category, "review")
                self.assertEqual(set(nodes[2].scheduled_deps), {first.id, nodes[1].id})

                root = Path(project.root_path)
                self.assertTrue(root.exists())
                self.assertTrue((root / ".git").exists())
            finally:
                registry.delete_project(project.id)

    def test_launching_bash_uname_codex_sets_noninteractive_workspace_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw))
            registry = ProjectRegistry(store=store)

            project, template = launch_template("bash-uname", "codex", registry)

            try:
                self.assertEqual(template.name, "bash-uname")
                self.assertEqual(
                    project.settings_override.get("approval_policy"),
                    "never",
                )
                self.assertEqual(
                    project.settings_override.get("sandbox"),
                    "workspace-write",
                )
                self.assertEqual(
                    project.settings_override.get("permission_mode"),
                    "bypassPermissions",
                )
            finally:
                registry.delete_project(project.id)

    def test_launching_permission_approve_codex_sets_interactive_policy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw))
            registry = ProjectRegistry(store=store)

            project, template = launch_template(
                "permission-approve", "codex", registry
            )

            try:
                self.assertEqual(template.name, "permission-approve")
                self.assertEqual(
                    project.settings_override.get("approval_policy"),
                    "untrusted",
                )
                self.assertEqual(
                    project.settings_override.get("sandbox"),
                    "read-only",
                )
                self.assertEqual(
                    project.settings_override.get("permission_mode"),
                    "default",
                )
            finally:
                registry.delete_project(project.id)

    def test_unsupported_provider_raises(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw))
            registry = ProjectRegistry(store=store)
            with self.assertRaises(TemplateError):
                launch_template("hello-text", "anthropic-foo", registry)

    def test_unknown_template_raises(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw))
            registry = ProjectRegistry(store=store)
            with self.assertRaises(TemplateError):
                launch_template("does-not-exist", "claude", registry)


if __name__ == "__main__":
    unittest.main()
