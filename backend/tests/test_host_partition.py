from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

import yaml
from fastapi.testclient import TestClient

import miniclaw2.sync as sync_module
from miniclaw2.app import create_app
from miniclaw2.contextspace import ensure_project_binding
from miniclaw2.domain import Node, NodeState, Project, UNBOUND_ROOT_PATH
from miniclaw2.registry import (
    NonNativeNodeError,
    NonNativeProjectError,
    ProjectRegistry,
)
from miniclaw2.store import Store, StoreReadOnlyError
from miniclaw2.sync import bootstrap_store


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "--initial-branch=main")
    (path / "identity.txt").write_text(path.name, encoding="utf-8")
    _git(path, "add", "identity.txt")
    _git(
        path,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "-m",
        "root",
    )
    return path


class HostPartitionStoreTests(unittest.TestCase):
    def test_enable_sharing_migrates_and_aggregates_nodes_by_host(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            store = Store(base / "store")
            project = store.create_project(
                Project(root_path=str(_repo(base / "repo")))
            )
            binding = ensure_project_binding(project, store_root=store.root)
            local = store.create_node(
                Node(
                    project_id=project.id,
                    model_preset_id=project.model_preset_id,
                )
            )
            registry = ProjectRegistry(store)

            migrated = registry.enable_sharing(project.id)

            self.assertIsNotNone(migrated)
            project_dir = store.root / "projects" / project.id
            local_host = project_dir / "hosts" / store.machine.id
            self.assertFalse((project_dir / "nodes").exists())
            self.assertTrue(
                (local_host / "nodes" / local.id / "node.json").is_file()
            )
            shared_payload = json.loads((project_dir / "project.json").read_text())
            self.assertEqual(shared_payload["sharing"], "shared")
            self.assertNotIn("root_path", shared_payload)
            self.assertNotIn("layout_hints", shared_payload)
            binding_payload = yaml.safe_load(binding.path.read_text(encoding="utf-8"))
            self.assertEqual(binding_payload["project"]["local_paths"], [])
            self.assertTrue(
                list(
                    (store.root / "migration-backups").glob(
                        "host-partition-v7-*/projects/*/project.json"
                    )
                )
            )

            remote_id = "remote-host"
            remote = Node(
                project_id=project.id,
                id="remote-node",
                state=NodeState.RUNNING,
                model_preset_id=project.model_preset_id,
            )
            remote_file = (
                project_dir
                / "hosts"
                / remote_id
                / "nodes"
                / remote.id
                / "node.json"
            )
            remote_file.parent.mkdir(parents=True)
            remote_file.write_text(
                json.dumps(
                    remote.model_dump(exclude={"provider", "owner_host_id"})
                ),
                encoding="utf-8",
            )
            store.invalidate_owner_index()

            nodes = {node.id: node for node in store.list_nodes(project.id)}
            self.assertEqual(nodes[local.id].owner_host_id, store.machine.id)
            self.assertEqual(nodes[remote.id].owner_host_id, remote_id)
            store.invalidate_owner_index()
            self.assertEqual(
                store.load_node(project.id, remote.id).owner_host_id, remote_id
            )
            ProjectRegistry(store)
            self.assertEqual(
                store.load_node(project.id, remote.id).state,
                NodeState.RUNNING,
            )

    def test_shared_project_without_local_binding_remains_visible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            source = Store(base / "source-store")
            project = source.create_project(
                Project(root_path=str(_repo(base / "repo")))
            )
            ProjectRegistry(source).enable_sharing(project.id)

            destination = Store(base / "destination-store")
            shutil.copytree(
                source.root / "projects" / project.id,
                destination.root / "projects" / project.id,
            )
            loaded = destination.list_projects()

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].sharing, "shared")
            self.assertEqual(loaded[0].root_path, UNBOUND_ROOT_PATH)
            self.assertFalse(loaded[0].is_bound)

    def test_remote_host_node_cannot_be_edited(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            store = Store(base / "store")
            project = store.create_project(
                Project(root_path=str(_repo(base / "repo")))
            )
            ProjectRegistry(store).enable_sharing(project.id)
            project_dir = store.root / "projects" / project.id
            remote = Node(
                project_id=project.id,
                id="remote-virtual",
                state=NodeState.VIRTUAL,
                prompt_draft="remote",
                model_preset_id=project.model_preset_id,
            )
            path = (
                project_dir
                / "hosts"
                / "remote"
                / "nodes"
                / remote.id
                / "node.json"
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    remote.model_dump(exclude={"provider", "owner_host_id"})
                ),
                encoding="utf-8",
            )
            registry = ProjectRegistry(store)

            with self.assertRaises(NonNativeNodeError):
                registry.update_virtual(project.id, remote.id, prompt_draft="changed")

    def test_remote_queue_does_not_block_local_quiescence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            store = Store(base / "store")
            project = store.create_project(
                Project(root_path=str(_repo(base / "repo")))
            )
            registry = ProjectRegistry(store)
            registry.enable_sharing(project.id)
            remote = Node(
                project_id=project.id,
                id="remote-queued",
                state=NodeState.QUEUED,
                model_preset_id=project.model_preset_id,
            )
            path = (
                store.root
                / "projects"
                / project.id
                / "hosts"
                / "remote"
                / "nodes"
                / remote.id
                / "node.json"
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    remote.model_dump(exclude={"provider", "owner_host_id"})
                ),
                encoding="utf-8",
            )
            store.invalidate_owner_index()

            self.assertEqual(registry.queued_count(project.id), 0)
            self.assertTrue(registry.quiescent(project.id))


class HostPartitionApiTests(unittest.TestCase):
    def test_unbound_device_can_join_matching_repository(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            source_repo = _repo(base / "source-repo")
            source = Store(base / "source-store")
            project = source.create_project(Project(root_path=str(source_repo)))
            source.create_node(
                Node(
                    project_id=project.id,
                    model_preset_id=project.model_preset_id,
                )
            )
            ProjectRegistry(source).enable_sharing(project.id)

            clone = base / "clone"
            subprocess.run(
                ["git", "clone", "-q", str(source_repo), str(clone)], check=True
            )
            destination = Store(base / "destination-store")
            shutil.copytree(
                source.root / "projects" / project.id,
                destination.root / "projects" / project.id,
            )
            registry = ProjectRegistry(destination)
            with TestClient(create_app(registry)) as client:
                before = client.get(f"/sessions/{project.id}")
                self.assertEqual(before.status_code, 200, before.text)
                self.assertTrue(before.json()["can_join_here"])

                joined = client.post(
                    f"/sessions/{project.id}/hosts",
                    json={"root_path": str(clone)},
                )

                self.assertEqual(joined.status_code, 200, joined.text)
                self.assertFalse(joined.json()["read_only"])
                self.assertFalse(joined.json()["can_join_here"])
                self.assertFalse(joined.json()["can_delete"])
                nodes = client.get(f"/sessions/{project.id}/nodes")
                self.assertEqual(nodes.status_code, 200, nodes.text)
                self.assertEqual(
                    nodes.json()[0]["owner_host_id"], source.machine.id
                )
                self.assertTrue(
                    (
                        destination.root
                        / "projects"
                        / project.id
                        / "hosts"
                        / destination.machine.id
                        / "host.json"
                    ).is_file()
                )
                exclude = _git(clone, "rev-parse", "--git-path", "info/exclude")
                exclude_path = Path(exclude)
                if not exclude_path.is_absolute():
                    exclude_path = clone / exclude_path
                self.assertIn(
                    ".miniclaw2/",
                    exclude_path.read_text(encoding="utf-8").splitlines(),
                )

                deleted = client.delete(f"/sessions/{project.id}")
                self.assertEqual(deleted.status_code, 403, deleted.text)
                self.assertTrue(
                    (destination.root / "projects" / project.id).is_dir()
                )

            with self.assertRaises(NonNativeProjectError):
                registry.delete_project(project.id)

    def test_join_rejects_mismatched_repository(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            source = Store(base / "source-store")
            project = source.create_project(
                Project(root_path=str(_repo(base / "source-repo")))
            )
            ProjectRegistry(source).enable_sharing(project.id)
            destination = Store(base / "destination-store")
            shutil.copytree(
                source.root / "projects" / project.id,
                destination.root / "projects" / project.id,
            )
            with TestClient(create_app(ProjectRegistry(destination))) as client:
                response = client.post(
                    f"/sessions/{project.id}/hosts",
                    json={"root_path": str(_repo(base / "other-repo"))},
                )
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("fingerprint", response.json()["detail"])

    def test_join_respects_store_read_only_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            source = Store(base / "source-store")
            project = source.create_project(
                Project(root_path=str(_repo(base / "source-repo")))
            )
            ProjectRegistry(source).enable_sharing(project.id)
            destination = Store(base / "destination-store")
            shutil.copytree(
                source.root / "projects" / project.id,
                destination.root / "projects" / project.id,
            )
            registry = ProjectRegistry(destination)
            with patch.object(
                type(destination),
                "read_only_reason",
                new_callable=PropertyMock,
                return_value="newer schema",
            ):
                with self.assertRaises(StoreReadOnlyError):
                    registry.join_shared_project(project.id, str(base / "unused"))


class HostPartitionSyncTests(unittest.TestCase):
    def test_host_owned_state_syncs_without_conflict_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            remote = base / "metadata.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            repo_a = _repo(base / "repo-a")
            store_a = Store(base / "store-a")
            project_a = store_a.create_project(Project(root_path=str(repo_a)))
            registry_a = ProjectRegistry(store_a)
            registry_a.enable_sharing(project_a.id)
            store_a.sync.setup_existing_store(str(remote))
            subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(remote),
                    "symbolic-ref",
                    "HEAD",
                    "refs/heads/main",
                ],
                check=True,
            )

            root_b = base / "store-b"
            bootstrap_store(root_b, str(remote))
            store_b = Store(root_b)
            repo_b = base / "repo-b"
            subprocess.run(
                ["git", "clone", "-q", str(repo_a), str(repo_b)],
                check=True,
            )
            registry_b = ProjectRegistry(store_b)
            registry_b.join_shared_project(project_a.id, str(repo_b))
            store_b.sync.sync_now()
            store_a.sync.sync_now()
            registry_a.reload_from_store()

            node_a = store_a.create_node(
                Node(
                    project_id=project_a.id,
                    model_preset_id=project_a.model_preset_id,
                )
            )
            project_b = registry_b.get_project(project_a.id)
            assert project_b is not None
            node_b = store_b.create_node(
                Node(
                    project_id=project_a.id,
                    model_preset_id=project_b.model_preset_id,
                )
            )
            registry_a.update_layout_hints(
                project_a.id, {node_a.id: {"x": 10, "y": 20}}
            )
            registry_b.update_layout_hints(
                project_a.id, {node_b.id: {"x": 30, "y": 40}}
            )
            store_a.write_git_aliases(project_a.id, {"old-a": "new-a"})
            store_b.write_git_aliases(project_a.id, {"old-b": "new-b"})

            store_a.sync.sync_now()
            original_git = sync_module._git
            with patch.object(sync_module, "_git", wraps=original_git) as git_call:
                store_b.sync.sync_now()
            self.assertFalse(
                any("-X" in call.args[1:] for call in git_call.call_args_list)
            )
            store_a.sync.sync_now()
            registry_a.reload_from_store()
            registry_b.reload_from_store()

            nodes_a = {node.id: node for node in store_a.list_nodes(project_a.id)}
            nodes_b = {node.id: node for node in store_b.list_nodes(project_a.id)}
            self.assertEqual(set(nodes_a), {node_a.id, node_b.id})
            self.assertEqual(set(nodes_b), {node_a.id, node_b.id})
            self.assertEqual(nodes_a[node_a.id].owner_host_id, store_a.machine.id)
            self.assertEqual(nodes_a[node_b.id].owner_host_id, store_b.machine.id)
            self.assertEqual(
                registry_a.get_project(project_a.id).layout_hints[node_a.id],
                {"x": 10.0, "y": 20.0},
            )
            self.assertEqual(
                registry_b.get_project(project_a.id).layout_hints[node_b.id],
                {"x": 30.0, "y": 40.0},
            )
            self.assertEqual(
                store_a.read_git_aliases(project_a.id), {"old-a": "new-a"}
            )
            self.assertEqual(
                store_b.read_git_aliases(project_a.id), {"old-b": "new-b"}
            )
