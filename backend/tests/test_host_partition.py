from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch

import yaml
from fastapi.testclient import TestClient

import miniclaw2.sync as sync_module
from miniclaw2.app import create_app
from miniclaw2.contextspace import ensure_project_binding
from miniclaw2.domain import ArtifactRef, Node, NodeState, Project, UNBOUND_ROOT_PATH
from miniclaw2.materialize import materialize_active_lane
from miniclaw2.registry import (
    NonNativeNodeError,
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
    def test_reload_preserves_project_object_for_a_running_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(_repo(base / "repo"))))
            registry = ProjectRegistry(store)
            runtime = registry._runtimes[project.id]
            runner_project = runtime.project
            active_task = Mock()
            active_task.done.return_value = False
            runtime.runner_tasks["active"] = active_task

            registry.reload_from_store()

            self.assertIs(runtime.project, runner_project)
            remote_node = Node(
                project_id=project.id,
                model_preset_id=project.model_preset_id,
            ).bind_owner_host("remote-host")
            self.assertFalse(registry.is_native_node(runner_project, remote_node))

    def test_host_head_records_validate_sha_and_merge_into_host_list(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(_repo(base / "repo"))))
            registry = ProjectRegistry(store)
            payload = {
                "head": "a" * 40,
                "branch": "main",
                "dirty": True,
            }

            store.write_host_head(project.id, payload)

            self.assertEqual(store.read_host_heads(project.id)[store.machine.id], payload)
            local_host = next(
                host for host in store.list_hosts(project.id)
                if host["mid"] == store.machine.id
            )
            self.assertEqual(local_host["head"], "a" * 40)
            invalid = (
                store.root / "projects" / project.id / "hosts" / "invalid" / "head.json"
            )
            invalid.parent.mkdir(parents=True)
            invalid.write_text(json.dumps({"head": "not-a-sha"}), encoding="utf-8")
            self.assertNotIn("invalid", store.read_host_heads(project.id))

    def test_host_head_times_are_derived_from_each_files_latest_commit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "store"
            store = Store(root)
            subprocess.run(
                ["git", "init", "-q", "--initial-branch=main", str(root)],
                check=True,
            )
            project_id = "project"
            paths = {
                host: root / "projects" / project_id / "hosts" / host / "head.json"
                for host in ("host-a", "host-b")
            }
            for index, (host, path) in enumerate(paths.items()):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {
                            "head": str(index + 1) * 40,
                            "branch": "main",
                            "dirty": False,
                            "recorded_at": 1,
                        }
                    ),
                    encoding="utf-8",
                )
                store.sync.commit_now(f"publish {host}")

            expected = {
                host: float(
                    _git(
                        root,
                        "log",
                        "-1",
                        "--format=%ct",
                        "--",
                        str(path.relative_to(root)),
                    )
                )
                for host, path in paths.items()
            }
            paths["host-a"].write_text(
                json.dumps(
                    {"head": "f" * 40, "branch": "main", "dirty": True}
                ),
                encoding="utf-8",
            )

            heads = store.read_host_heads(project_id)

            self.assertEqual(heads["host-a"]["recorded_at"], expected["host-a"])
            self.assertEqual(heads["host-b"]["recorded_at"], expected["host-b"])
            self.assertEqual(heads["host-a"]["head"], "f" * 40)

    def test_host_head_time_query_failure_degrades_to_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Store(Path(raw) / "store")
            path = (
                store.root / "projects" / "project" / "hosts" / "host" / "head.json"
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {"head": "a" * 40, "recorded_at": 123, "dirty": False}
                ),
                encoding="utf-8",
            )

            with patch.object(
                store.sync,
                "file_commit_times",
                side_effect=sync_module.SyncError("git unavailable"),
            ):
                head = store.read_host_heads("project")["host"]

            self.assertNotIn("recorded_at", head)

    def test_host_head_time_queries_are_bounded_and_cached_per_head(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "store"
            store = Store(root)
            subprocess.run(
                ["git", "init", "-q", "--initial-branch=main", str(root)],
                check=True,
            )
            paths = []
            for host in ("host-a", "host-b"):
                path = root / "projects" / "project" / "hosts" / host / "head.json"
                path.parent.mkdir(parents=True)
                path.write_text(
                    json.dumps({"head": "a" * 40, "dirty": False}),
                    encoding="utf-8",
                )
                paths.append(path)
            store.sync.commit_now("publish host heads")

            with patch.object(sync_module, "_git", wraps=sync_module._git) as git:
                first = store.sync.file_commit_times(paths)
                second = store.sync.file_commit_times(paths)

            log_calls = [
                call.args[1:]
                for call in git.call_args_list
                if len(call.args) > 1 and call.args[1] == "log"
            ]
            self.assertEqual(first, second)
            self.assertEqual(len(log_calls), len(paths))
            self.assertTrue(
                all(args[:3] == ("log", "-1", "--format=%ct") for args in log_calls)
            )

    def test_bound_project_sync_callback_writes_host_head(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(_repo(base / "repo"))))
            registry = ProjectRegistry(store)

            registry._record_host_heads()

            host_dir = (
                store.root / "projects" / project.id / "hosts" / store.machine.id
            )
            self.assertTrue((host_dir / "host.json").is_file())
            self.assertTrue((host_dir / "head.json").exists())

    def test_partitioned_project_aggregates_nodes_by_host(self) -> None:
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

            project_dir = store.root / "projects" / project.id
            local_host = project_dir / "hosts" / store.machine.id
            self.assertFalse((project_dir / "nodes").exists())
            self.assertTrue(
                (local_host / "nodes" / local.id / "node.json").is_file()
            )
            project_payload = json.loads((project_dir / "project.json").read_text())
            self.assertNotIn("sharing", project_payload)
            self.assertNotIn("root_path", project_payload)
            self.assertNotIn("layout_hints", project_payload)
            binding_payload = yaml.safe_load(binding.path.read_text(encoding="utf-8"))
            self.assertNotIn("local_paths", binding_payload["project"])
            self.assertFalse((store.root / "migration-backups").exists())

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

    def test_locally_stored_node_stays_native_when_another_host_created_it(
        self,
    ) -> None:
        """Authority follows the partition a record lives in, not provenance.

        A project created elsewhere and then bound here keeps its creator
        machine id in synced ``project.json``. Nodes this host writes live in
        this host's partition and must stay writable — including on a cold
        owner index, which is the state after every sync.
        """
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            store = Store(base / "store")
            project = store.create_project(
                Project(root_path=str(_repo(base / "repo")))
            )
            local = store.create_node(
                Node(
                    project_id=project.id,
                    model_preset_id=project.model_preset_id,
                )
            )

            # Provenance says another device; the local binding is unchanged.
            project.machine_id = "creator-host"
            store.update_project(project)
            store.invalidate_owner_index()

            self.assertTrue(store.is_bound_here(project.id))
            self.assertEqual(
                store.load_node(project.id, local.id).owner_host_id,
                store.machine.id,
            )
            registry = ProjectRegistry(store)
            reloaded = store.load_node(project.id, local.id)
            self.assertTrue(registry.is_native_node(project, reloaded))

    def test_project_without_local_binding_remains_visible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            source = Store(base / "source-store")
            project = source.create_project(
                Project(root_path=str(_repo(base / "repo")))
            )
            destination = Store(base / "destination-store")
            shutil.copytree(
                source.root / "projects" / project.id,
                destination.root / "projects" / project.id,
            )
            loaded = destination.list_projects()

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].root_path, UNBOUND_ROOT_PATH)
            self.assertFalse(loaded[0].is_bound)

    def test_flat_project_from_legacy_peer_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            store = Store(base / "store")
            project = Project(
                root_path="/remote/checkout",
                machine_id="legacy-peer",
                machine_label="Legacy peer",
            )
            node = Node(
                id="flat-node",
                project_id=project.id,
                model_preset_id=project.model_preset_id,
                state=NodeState.RUNNING,
            )
            project_dir = store.root / "projects" / project.id
            node_file = project_dir / "nodes" / node.id / "node.json"
            node_file.parent.mkdir(parents=True)
            (project_dir / "project.json").write_text(
                json.dumps(project.model_dump(exclude={"provider"})),
                encoding="utf-8",
            )
            node_file.write_text(
                json.dumps(node.model_dump(exclude={"provider", "owner_host_id"})),
                encoding="utf-8",
            )
            (project_dir / "git_aliases.json").write_text(
                json.dumps({"old": "new"}),
                encoding="utf-8",
            )

            self.assertEqual(store.list_projects(), [])
            self.assertEqual(store.list_nodes(project.id), [])
            self.assertIsNone(store.load_node(project.id, node.id))
            self.assertEqual(store.read_git_aliases(project.id), {})
            self.assertEqual(
                store.node_dir(project.id, node.id),
                project_dir / "hosts" / store.machine.id / "nodes" / node.id,
            )

    def test_partitioned_native_project_is_unbound_on_non_owner_device(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            source = Store(base / "source-store")
            project = source.create_project(
                Project(root_path=str(_repo(base / "repo")))
            )
            destination = Store(base / "destination-store")
            shutil.copytree(
                source.root / "projects" / project.id,
                destination.root / "projects" / project.id,
            )

            loaded = destination.list_projects()

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].root_path, UNBOUND_ROOT_PATH)
            self.assertFalse(loaded[0].is_bound)

    def test_local_fingerprint_appears_after_first_commit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = base / "repo"
            repo.mkdir()
            _git(repo, "init", "-q", "--initial-branch=main")
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(repo)))
            registry = ProjectRegistry(store)
            (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
            _git(repo, "add", "seed.txt")
            _git(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "seed",
            )
            registry._record_host_heads()

            host = store.list_hosts(project.id)[0]
            self.assertTrue(host["repo"]["root_commit"])
            self.assertTrue((store.root / "projects" / project.id / "hosts" / store.machine.id / "head.json").is_file())

    def test_non_git_rebind_requires_ack_without_persisting_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            workspace = base / "workspace"
            workspace.mkdir()
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(workspace)))
            registry = ProjectRegistry(store)
            registry.unbind_project_here(project.id)

            with self.assertRaisesRegex(ValueError, "无法校验"):
                registry.bind_project_here(project.id, str(workspace))

            rebound = registry.bind_project_here(
                project.id,
                str(workspace),
                unverified_acknowledged=True,
            )

            assert rebound is not None
            self.assertTrue(store.is_bound_here(project.id))
            host = store.list_hosts(project.id)[0]
            self.assertFalse(host["is_repo"])
            self.assertNotIn("attestation", host)
            self.assertNotIn("identity", host)

    def test_empty_git_repository_cannot_be_bound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = base / "repo"
            repo.mkdir()
            _git(repo, "init", "-q", "--initial-branch=main")
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(repo)))
            registry = ProjectRegistry(store)
            registry.unbind_project_here(project.id)

            with self.assertRaisesRegex(
                ValueError,
                "repository has no root commit",
            ):
                registry.bind_project_here(project.id, str(repo))
            self.assertTrue(store.list_hosts(project.id)[0]["is_repo"])

    def test_fingerprint_refresh_records_only_observed_repository_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            workspace = base / "workspace"
            workspace.mkdir()
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(workspace)))
            _git(workspace, "init", "-q", "--initial-branch=main")
            (workspace / "seed.txt").write_text("seed\n", encoding="utf-8")
            _git(workspace, "add", "seed.txt")
            _git(
                workspace,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "seed",
            )

            self.assertTrue(store.refresh_local_fingerprint(project))
            host = store.list_hosts(project.id)[0]
            self.assertTrue(host["is_repo"])
            self.assertTrue(host["repo"]["root_commit"])
            self.assertNotIn("attestation", host)

    def test_remote_host_node_cannot_be_edited(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            store = Store(base / "store")
            project = store.create_project(
                Project(root_path=str(_repo(base / "repo")))
            )
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
            with self.assertRaises(NonNativeNodeError):
                registry.promote_virtual_result(project.id, remote.id)

    def test_remote_queue_does_not_block_local_quiescence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            store = Store(base / "store")
            project = store.create_project(
                Project(root_path=str(_repo(base / "repo")))
            )
            registry = ProjectRegistry(store)
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

    def test_unbind_rejects_active_local_work(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(_repo(base / "repo"))))
            registry = ProjectRegistry(store)
            active_task = Mock()
            active_task.done.return_value = False
            registry._runtimes[project.id].runner_tasks["active"] = active_task

            with self.assertRaisesRegex(RuntimeError, "active or queued"):
                registry.unbind_project_here(project.id)

            self.assertTrue(store.is_bound_here(project.id))

class HostPartitionApiTests(unittest.TestCase):
    def test_non_git_bind_requires_ack_and_unbind_preserves_history(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            owner_workspace = base / "owner-workspace"
            owner_workspace.mkdir()
            source = Store(base / "source-store")
            project = source.create_project(
                Project(root_path=str(owner_workspace), name="no-git")
            )
            node = source.create_node(
                Node(project_id=project.id, model_preset_id=project.model_preset_id)
            )

            destination = Store(base / "destination-store")
            shutil.copytree(
                source.root / "projects" / project.id,
                destination.root / "projects" / project.id,
            )
            peer_workspace = base / "peer-workspace"
            peer_workspace.mkdir()
            with TestClient(create_app(ProjectRegistry(destination))) as client:
                rejected = client.post(
                    f"/sessions/{project.id}/hosts",
                    json={"root_path": str(peer_workspace)},
                )
                self.assertEqual(rejected.status_code, 400, rejected.text)
                self.assertIn("无法校验", rejected.json()["detail"])
                joined = client.post(
                    f"/sessions/{project.id}/hosts",
                    json={
                        "root_path": str(peer_workspace),
                        "unverified_acknowledged": True,
                    },
                )
                self.assertEqual(joined.status_code, 200, joined.text)
                self.assertTrue(joined.json()["bound_here"])
                self.assertFalse(joined.json()["read_only"])
                self.assertTrue(
                    all("attestation" not in host for host in joined.json()["hosts"])
                )
                unbound = client.delete(
                    f"/sessions/{project.id}/hosts/{destination.machine.id}"
                )
                self.assertEqual(unbound.status_code, 200, unbound.text)
                self.assertFalse(unbound.json()["bound_here"])
                self.assertTrue(unbound.json()["read_only"])
                nodes = client.get(f"/sessions/{project.id}/nodes")
                self.assertEqual([item["id"] for item in nodes.json()], [node.id])
                rebound = client.post(
                    f"/sessions/{project.id}/hosts",
                    json={
                        "root_path": str(peer_workspace),
                        "unverified_acknowledged": True,
                    },
                )
                self.assertEqual(rebound.status_code, 200, rebound.text)
                self.assertTrue(rebound.json()["bound_here"])

    def test_remote_device_can_bind_over_a_real_remote(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            remote = base / "metadata.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
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
            repo_a = _repo(base / "repo-a")
            store_a = Store(base / "store-a")
            project = store_a.create_project(
                Project(root_path=str(repo_a), name="round-trip")
            )
            store_a.create_node(
                Node(
                    project_id=project.id,
                    model_preset_id=project.model_preset_id,
                    state=NodeState.DONE,
                )
            )
            ProjectRegistry(store_a)
            store_a.sync.setup_existing_store(str(remote))

            root_b = base / "store-b"
            bootstrap_store(root_b, str(remote))
            store_b = Store(root_b)
            registry_b = ProjectRegistry(store_b)
            repo_b = base / "repo-b"
            subprocess.run(["git", "clone", "-q", str(repo_a), str(repo_b)], check=True)

            with TestClient(create_app(registry_b)) as client_b:
                before = client_b.get(f"/sessions/{project.id}")
                self.assertEqual(before.status_code, 200, before.text)
                self.assertTrue(before.json()["can_bind_here"])

                joined = client_b.post(
                    f"/sessions/{project.id}/hosts",
                    json={"root_path": str(repo_b)},
                )
                self.assertEqual(joined.status_code, 200, joined.text)
                self.assertFalse(joined.json()["read_only"])
                self.assertTrue(joined.json()["can_delete"])

    def test_unverified_ack_does_not_bypass_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            source = Store(base / "source-store")
            project = source.create_project(
                Project(root_path=str(_repo(base / "source-repo")))
            )
            destination = Store(base / "destination-store")
            shutil.copytree(
                source.root / "projects" / project.id,
                destination.root / "projects" / project.id,
            )
            with TestClient(create_app(ProjectRegistry(destination))) as client:
                response = client.post(
                    f"/sessions/{project.id}/hosts",
                    json={
                        "root_path": str(_repo(base / "other-repo")),
                        "unverified_acknowledged": True,
                    },
                )
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("fingerprint", response.json()["detail"])

    def test_unreferenced_repository_bind_requires_ack_and_claims_no_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            workspace = base / "workspace"
            workspace.mkdir()
            source = Store(base / "source-store")
            project = source.create_project(
                Project(root_path=str(workspace), name="no-git")
            )
            peer = Store(base / "peer-store")
            shutil.copytree(
                source.root / "projects" / project.id,
                peer.root / "projects" / project.id,
            )
            peer_registry = ProjectRegistry(Store(peer.root))
            unrelated = _repo(base / "unrelated-repo")

            with self.assertRaisesRegex(ValueError, "无法校验此仓库"):
                peer_registry.bind_project_here(project.id, str(unrelated))

            bound = peer_registry.bind_project_here(
                project.id,
                str(unrelated),
                unverified_acknowledged=True,
            )
            assert bound is not None
            host = next(
                item
                for item in peer_registry.store.list_hosts(project.id)
                if item["mid"] == peer_registry.store.machine.id
            )
            self.assertTrue(host["is_repo"])
            self.assertEqual(host["repo"], {})
            self.assertIsNone(peer_registry._recorded_fingerprint(project.id))

            # The pre-sync stamping pass must not publish what the binding
            # declined to claim.
            peer_registry._record_host_heads()
            self.assertIsNone(peer_registry._recorded_fingerprint(project.id))

            # A third device holding the real non-Git tree stays bindable.
            third = Store(base / "third-store")
            for pdir in (peer_registry.store.root / "projects").iterdir():
                shutil.copytree(pdir, third.root / "projects" / pdir.name)
            third_registry = ProjectRegistry(Store(third.root))
            real_tree = base / "real-tree"
            real_tree.mkdir()
            rebound = third_registry.bind_project_here(
                project.id,
                str(real_tree),
                unverified_acknowledged=True,
            )
            self.assertIsNotNone(rebound)

    def test_nodes_response_has_no_cross_device_claim_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(_repo(base / "repo"))))
            registry = ProjectRegistry(store)
            remote = Node(
                project_id=project.id,
                id="remote-virtual",
                state=NodeState.VIRTUAL,
                prompt_draft="inspect me",
                model_preset_id=project.model_preset_id,
            )
            remote_path = (
                store.root / "projects" / project.id / "hosts" / "remote"
                / "nodes" / remote.id / "node.json"
            )
            remote_path.parent.mkdir(parents=True)
            remote_path.write_text(
                json.dumps(remote.model_dump(exclude={"provider", "owner_host_id"})),
                encoding="utf-8",
            )
            store.invalidate_owner_index()

            with TestClient(create_app(registry)) as client:
                response = client.get(f"/sessions/{project.id}/nodes")

            self.assertEqual(response.status_code, 200, response.text)
            payload = next(item for item in response.json() if item["id"] == remote.id)
            self.assertNotIn("claims", payload)

    def test_unbound_device_can_bind_matching_repository_and_delete_project(self) -> None:
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
                self.assertTrue(before.json()["can_bind_here"])

                joined = client.post(
                    f"/sessions/{project.id}/hosts",
                    json={"root_path": str(clone)},
                )

                self.assertEqual(joined.status_code, 200, joined.text)
                self.assertFalse(joined.json()["read_only"])
                self.assertFalse(joined.json()["can_bind_here"])
                self.assertTrue(joined.json()["can_delete"])
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
                self.assertEqual(deleted.status_code, 200, deleted.text)
                self.assertFalse((destination.root / "projects" / project.id).exists())

    def test_join_rejects_mismatched_repository(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            source = Store(base / "source-store")
            project = source.create_project(
                Project(root_path=str(_repo(base / "source-repo")))
            )
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
                    registry.bind_project_here(project.id, str(base / "unused"))


class HostPartitionSyncTests(unittest.TestCase):
    def test_explicit_sync_commits_current_host_head(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            remote = base / "metadata.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            repo = _repo(base / "repo")
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(repo)))
            registry = ProjectRegistry(store)
            store.sync.setup_existing_store(str(remote))

            (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            expected_head = _git(repo, "rev-parse", "HEAD")
            store.sync.sync_now()

            payload = store.read_host_heads(project.id)[store.machine.id]
            self.assertEqual(payload["head"], expected_head)
            self.assertTrue(payload["dirty"])
            self.assertNotIn(
                "recorded_at",
                json.loads(
                    (
                        store.root
                        / "projects"
                        / project.id
                        / "hosts"
                        / store.machine.id
                        / "head.json"
                    ).read_text(encoding="utf-8")
                ),
            )
            expected_recorded_at = float(
                _git(
                    store.root,
                    "log",
                    "-1",
                    "--format=%ct",
                    "--",
                    f"projects/{project.id}/hosts/{store.machine.id}/head.json",
                )
            )
            self.assertEqual(payload["recorded_at"], expected_recorded_at)
            changed = _git(
                store.root,
                "show",
                "--pretty=format:",
                "--name-only",
                "HEAD",
            ).splitlines()
            self.assertIn(
                f"projects/{project.id}/hosts/{store.machine.id}/head.json",
                changed,
            )
            commit_count = _git(store.root, "rev-list", "--count", "HEAD")

            store.sync.sync_now()

            self.assertEqual(
                _git(store.root, "rev-list", "--count", "HEAD"),
                commit_count,
            )

    def test_host_owned_state_syncs_without_conflict_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            remote = base / "metadata.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            repo_a = _repo(base / "repo-a")
            store_a = Store(base / "store-a")
            project_a = store_a.create_project(Project(root_path=str(repo_a)))
            registry_a = ProjectRegistry(store_a)
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
            registry_b.bind_project_here(project_a.id, str(repo_b))
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
                    state=NodeState.DONE,
                    planspace_id="lane-sync",
                    artifacts=[
                        ArtifactRef(
                            name="report.md",
                            bytes=13,
                            mtime=1.0,
                            sha256="a" * 64,
                            status="published",
                        )
                    ],
                )
            )
            artifact_b = store_b.node_dir(project_a.id, node_b.id) / "artifacts"
            artifact_b.mkdir()
            (artifact_b / "report.md").write_text(
                "remote report",
                encoding="utf-8",
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
            synced_project_a = registry_a.get_project(project_a.id)
            assert synced_project_a is not None
            projection = materialize_active_lane(
                synced_project_a,
                "lane-sync",
                store_a,
                target_root=base / "projection-a",
            )
            self.assertEqual(
                (
                    projection
                    / "nodes"
                    / node_b.id
                    / "artifacts"
                    / "report.md"
                ).read_text(encoding="utf-8"),
                "remote report",
            )
            self.assertEqual(
                synced_project_a.layout_hints[node_a.id],
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
