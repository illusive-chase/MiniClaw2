from __future__ import annotations

import tempfile
import unittest
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.artifacts import publish_artifacts, workspace_artifacts_dir
from miniclaw2.domain import Category, Node, NodeState, ReviewSubtype
from miniclaw2.git_state import is_git_repo
from miniclaw2.registry import NonNativeNodeError, ProjectRegistry
from miniclaw2.store import Store
from miniclaw2.workspace import create_temporary_root, remove_temporary_root


class TemporaryProjectTest(unittest.TestCase):
    def test_nested_tmpdir_does_not_inherit_parent_repository(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            subprocess.run(["git", "init", "-q", raw], check=True)
            with patch("miniclaw2.workspace.tempfile.gettempdir", return_value=raw):
                root = create_temporary_root()
            try:
                self.assertFalse(is_git_repo(root))
                self.assertNotEqual(Path(root).parent, Path(raw))
            finally:
                remove_temporary_root(root)

    def test_foreign_temporary_project_is_writable_without_manual_binding(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            source_store = Store(Path(raw) / "source")
            source = ProjectRegistry(source_store)
            project = source.create_project(cwd=None, temporary=True)
            original_root = Path(project.root_path)
            self.addCleanup(remove_temporary_root, str(original_root))
            node = Node(
                project_id=project.id, state=NodeState.DONE,
                model_preset_id=project.model_preset_id,
            )
            source_store.create_node(node)
            outputs = workspace_artifacts_dir(project, node.id)
            outputs.mkdir(parents=True)
            (outputs / "result.md").write_text("持久产物", encoding="utf-8")
            publish_artifacts(project, node, ["result.md"], source_store)
            source_store.update_node(node)
            target_store = Store(Path(raw) / "target")
            target = ProjectRegistry(target_store)
            shutil.copytree(
                source_store.root / "projects", target_store.root / "projects",
                dirs_exist_ok=True,
            )

            target.reload_from_store()
            imported = target.require_native(project.id)
            self.addCleanup(remove_temporary_root, imported.root_path)
            self.assertNotEqual(imported.root_path, str(original_root))
            self.assertTrue(Path(imported.root_path).is_dir())
            remote_node = target_store.load_node(project.id, node.id)
            assert remote_node is not None
            with self.assertRaises(NonNativeNodeError):
                target.require_native_node(imported, remote_node)
            with patch.object(target, "_schedule_queued"):
                new_node = target.start_node(project.id, "本机继续执行")
            assert new_node is not None
            self.assertTrue(target.is_native_node(imported, new_node))
            with TestClient(create_app(registry=target)) as client:
                info = client.get(f"/sessions/{project.id}").json()
                self.assertTrue(info["bound_here"])
                self.assertFalse(info["read_only"])
                self.assertFalse(info["can_bind_here"])
                self.assertEqual(info["capabilities"], {"workspace": False, "git_review": False})
                response = client.get(
                    f"/sessions/{project.id}/nodes/{node.id}/artifacts/result.md?raw=1"
                )
                self.assertEqual(response.text, "持久产物")
                self.assertEqual(client.delete(f"/sessions/{project.id}").status_code, 200)
            self.assertTrue(original_root.exists())
            self.assertFalse(Path(imported.root_path).exists())

    def test_missing_cache_is_recreated_on_restart_and_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ProjectRegistry(Store(Path(raw)))
            project = registry.create_project(cwd=None, temporary=True)
            first_root = project.root_path
            remove_temporary_root(first_root)
            reloaded = ProjectRegistry(Store(Path(raw)))
            restored = reloaded.get_project(project.id)
            assert restored is not None
            self.assertNotEqual(restored.root_path, first_root)
            remove_temporary_root(restored.root_path)
            reloaded.require_native(project.id)
            self.assertTrue(Path(restored.root_path).is_dir())
            reloaded.delete_project(project.id)

    def test_git_and_workspace_api_reject_without_creating_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ProjectRegistry(Store(Path(raw)))
            project = registry.create_project(cwd=None, temporary=True)
            self.addCleanup(remove_temporary_root, project.root_path)
            with TestClient(create_app(registry=registry)) as client:
                for method, suffix, body in [
                    ("GET", "/git", None),
                    ("POST", "/git/commit", {"message": "禁止提交"}),
                    ("POST", "/git/review", {}),
                    ("POST", "/git/pull", None),
                    ("POST", "/git/push", None),
                    ("POST", "/reveal", None),
                    ("GET", "/nodes/unknown/diff", None),
                ]:
                    with self.subTest(suffix=suffix):
                        response = client.request(method, f"/sessions/{project.id}{suffix}", json=body)
                        self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(registry.list_nodes(project.id), [])
            for create in (registry.start_node, registry.create_virtual):
                with self.subTest(create=create.__name__):
                    arguments = {"prompt": "审阅"} if create == registry.start_node else {"prompt_draft": "审阅"}
                    with self.assertRaisesRegex(ValueError, "临时项目不支持 Git"):
                        create(project.id, category=Category.REVIEW, subtype=ReviewSubtype.CODE_REVIEW, **arguments)

    def test_auto_commit_is_skipped_without_blocking_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ProjectRegistry(Store(Path(raw)))
            project = registry.create_project(cwd=None, temporary=True)
            project.settings_override["auto_commit"] = True
            self.addCleanup(remove_temporary_root, project.root_path)
            node = Node(
                project_id=project.id, state=NodeState.DONE,
                model_preset_id=project.model_preset_id,
            )
            runtime = registry._runtimes[project.id]
            runtime.runners[node.id] = Mock(node=node)
            with (
                patch.object(registry, "_spawn_op_commit") as commit,
                patch.object(registry, "_auto_promote_eligible_virtuals") as promote,
                patch.object(registry, "_schedule_queued"),
            ):
                registry._on_runner_done(runtime, node.id)
            commit.assert_not_called()
            promote.assert_called_once_with(runtime)

    def test_continuation_uses_fresh_provider_session(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(Path(raw))
            registry = ProjectRegistry(store)
            project = registry.create_project(cwd=None, temporary=True)
            self.addCleanup(remove_temporary_root, project.root_path)
            source = Node(
                project_id=project.id, state=NodeState.DONE,
                model_preset_id=project.model_preset_id,
                provider_session_id="device-local-session",
            )
            store.create_node(source)
            with patch.object(registry, "_schedule_queued"):
                node = registry.start_node(project.id, "继续", resume_from_node_id=source.id)
            assert node is not None
            self.assertIsNone(node.provider_session_id)
            self.assertEqual(node.parent_node_id, source.id)

    def test_create_temporary_project_uses_non_git_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(root=Path(raw))
            registry = ProjectRegistry(store=store)

            project = registry.create_project(cwd=None, temporary=True)

            self.assertTrue(project.temporary)
            root = Path(project.root_path)
            self.assertTrue(root.exists())
            self.assertFalse((root / ".git").exists())

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
