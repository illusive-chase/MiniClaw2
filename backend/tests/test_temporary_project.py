from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


class TemporaryProjectTest(unittest.TestCase):
    def test_create_temporary_project_initialises_git_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw))
            registry = ProjectRegistry(store=store)

            project = registry.create_project(cwd=None, temporary=True)

            self.assertTrue(project.temporary)
            root = Path(project.root_path)
            self.assertTrue(root.exists())
            self.assertTrue((root / ".git").exists())

            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(len(head), 40)

            # Cleanup
            registry.delete_project(project.id)
            self.assertFalse(root.exists())

    def test_delete_temporary_project_removes_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw))
            registry = ProjectRegistry(store=store)
            project = registry.create_project(cwd=None, temporary=True)
            root = Path(project.root_path)
            self.assertTrue(root.exists())

            self.assertTrue(registry.delete_project(project.id))

            self.assertFalse(root.exists())
            self.assertFalse((Path(raw) / "projects" / project.id).exists())

    def test_create_non_temporary_requires_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw))
            registry = ProjectRegistry(store=store)

            with self.assertRaises(ValueError):
                registry.create_project(cwd=None, temporary=False)

    def test_create_non_temporary_rejects_missing_cwd_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw) / "store")
            registry = ProjectRegistry(store=store)
            missing = Path(raw) / "missing-project"

            with self.assertRaisesRegex(ValueError, "cwd does not exist"):
                registry.create_project(cwd=str(missing), temporary=False)

            self.assertFalse(missing.exists())

    def test_create_non_temporary_can_create_missing_cwd_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw) / "store")
            registry = ProjectRegistry(store=store)
            missing = Path(raw) / "nested" / "project"

            project = registry.create_project(
                cwd=str(missing),
                temporary=False,
                create_missing_cwd=True,
            )

            self.assertTrue(missing.is_dir())
            self.assertEqual(project.root_path, str(missing.resolve()))

    def test_temporary_flag_persists_across_registry_reload(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store_root = Path(raw)
            store = Store(root=store_root)
            registry = ProjectRegistry(store=store)
            project = registry.create_project(
                cwd=None, temporary=True, template_id="hello-text"
            )
            pid = project.id
            root = Path(project.root_path)

            registry2 = ProjectRegistry(store=Store(root=store_root))
            reloaded = registry2.get_project(pid)
            assert reloaded is not None
            self.assertTrue(reloaded.temporary)
            self.assertEqual(reloaded.template_id, "hello-text")

            registry2.delete_project(pid)
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
