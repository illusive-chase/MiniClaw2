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
    def test_reload_applies_shared_policy_to_a_running_runtime(self) -> None:
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
            store.update_project(runtime.project.model_copy(update={"sharing": "shared"}))

            registry.reload_from_store()

            self.assertIs(runtime.project, runner_project)
            self.assertEqual(runner_project.sharing, "shared")
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
            registry.enable_sharing(project.id)
            payload = {
                "head": "a" * 40,
                "branch": "main",
                "recorded_at": 123.0,
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

    def test_device_native_sync_callback_does_not_write_shared_head(self) -> None:
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
            self.assertFalse((host_dir / "head.json").exists())

    def test_enable_sharing_flips_policy_and_aggregates_nodes_by_host(self) -> None:
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
            self.assertEqual(loaded[0].sharing, "device-native")
            self.assertEqual(loaded[0].root_path, UNBOUND_ROOT_PATH)
            self.assertFalse(loaded[0].is_bound)

    def test_owner_fingerprint_becomes_ready_after_first_commit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = base / "repo"
            repo.mkdir()
            _git(repo, "init", "-q", "--initial-branch=main")
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(repo)))
            registry = ProjectRegistry(store)
            self.assertEqual(
                store.sharing_readiness(project), "waiting-for-owner-commit"
            )

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

            self.assertEqual(store.sharing_readiness(project), "ready")
            self.assertFalse(
                (
                    store.root / "projects" / project.id / "hosts"
                    / store.machine.id / "head.json"
                ).exists()
            )

    def test_non_git_project_is_ready_unverified_but_requires_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            workspace = base / "workspace"
            workspace.mkdir()
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(workspace)))
            registry = ProjectRegistry(store)

            self.assertEqual(store.sharing_readiness(project), "ready-unverified")
            with self.assertRaisesRegex(ValueError, "无法发现设备间文件分歧"):
                registry.enable_sharing(project.id)

            shared = registry.enable_sharing(
                project.id,
                unverified_identity_acknowledged=True,
                topology="shared-filesystem",
            )

            assert shared is not None
            self.assertEqual(shared.identity, "environment-attested")
            host = store.list_hosts(project.id)[0]
            self.assertFalse(host["is_repo"])
            self.assertEqual(host["identity"], "environment-attested")
            self.assertEqual(
                Path(host["attestation"]["root_path_declared"]).resolve(),
                workspace.resolve(),
            )
            self.assertEqual(host["attestation"]["topology"], "shared-filesystem")

    def test_non_owner_can_enable_non_git_project_from_owner_observation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            workspace = base / "workspace"
            workspace.mkdir()
            source = Store(base / "source-store")
            project = source.create_project(Project(root_path=str(workspace)))
            destination = Store(base / "destination-store")
            shutil.copytree(
                source.root / "projects" / project.id,
                destination.root / "projects" / project.id,
            )
            registry = ProjectRegistry(destination)
            remote = registry.get_project(project.id)
            assert remote is not None

            self.assertEqual(
                destination.sharing_readiness(remote), "ready-unverified"
            )
            shared = registry.enable_sharing(
                project.id,
                unverified_identity_acknowledged=True,
            )

            assert shared is not None
            self.assertEqual(shared.identity, "environment-attested")
            self.assertEqual(shared.sharing, "shared")
            self.assertFalse(
                destination.has_host_binding(project.id, destination.machine.id)
            )

    def test_empty_git_repository_is_not_ready_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = base / "repo"
            repo.mkdir()
            _git(repo, "init", "-q", "--initial-branch=main")
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(repo)))

            self.assertEqual(
                store.sharing_readiness(project), "waiting-for-owner-commit"
            )
            self.assertTrue(store.list_hosts(project.id)[0]["is_repo"])

    def test_fingerprint_refresh_preserves_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            workspace = base / "workspace"
            workspace.mkdir()
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(workspace)))
            registry = ProjectRegistry(store)
            shared = registry.enable_sharing(
                project.id,
                unverified_identity_acknowledged=True,
            )
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

            self.assertTrue(store.update_owner_fingerprint(project))
            host = store.list_hosts(project.id)[0]
            self.assertEqual(host["identity"], "environment-attested")
            self.assertEqual(
                Path(host["attestation"]["root_path_declared"]).resolve(),
                workspace.resolve(),
            )
            assert shared is not None
            self.assertEqual(store.sharing_readiness(shared), "ready-unverified")

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

    def test_claim_foreign_virtual_copies_intent_without_mutating_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(_repo(base / "repo"))))
            registry = ProjectRegistry(store)
            registry.enable_sharing(project.id)
            source = Node(
                project_id=project.id,
                id="foreign-virtual",
                state=NodeState.VIRTUAL,
                prompt_draft="implement the task",
                scheduled_deps=["upstream"],
                pending_extra_principles=["principles.careful"],
                pending_extra_skills=[
                    {"id": "skills.review", "suggest": True}
                ],
                settings_snapshot={"cwd": "/remote/repo"},
                provider_session_id="remote-session",
                origin_machine_id="remote-host",
                commit_before="a" * 40,
                commit_after="b" * 40,
                model_preset_id=project.model_preset_id,
            )
            source_path = (
                store.root
                / "projects"
                / project.id
                / "hosts"
                / "remote-host"
                / "nodes"
                / source.id
                / "node.json"
            )
            source_path.parent.mkdir(parents=True)
            source_path.write_text(
                json.dumps(source.model_dump(exclude={"provider", "owner_host_id"})),
                encoding="utf-8",
            )
            before = source_path.read_bytes()
            store.invalidate_owner_index()

            with patch.object(registry, "_schedule_queued"):
                claimed = registry.claim_foreign_virtual(project.id, source.id)

            self.assertEqual(source_path.read_bytes(), before)
            self.assertEqual(claimed.state, NodeState.QUEUED)
            self.assertEqual(claimed.promoted_from, source.id)
            self.assertEqual(claimed.prompt, source.prompt_draft)
            self.assertEqual(claimed.scheduled_deps, source.scheduled_deps)
            self.assertEqual(claimed.pending_extra_principles, [])
            self.assertEqual(claimed.pending_extra_skills, [])
            self.assertEqual(
                claimed.settings_snapshot,
                {
                    "extra_principles": ["principles.careful"],
                    "extra_skills": [
                        {"id": "skills.review", "suggest": True}
                    ],
                },
            )
            self.assertNotEqual(claimed.origin_machine_id, source.origin_machine_id)
            self.assertIsNone(claimed.provider_session_id)
            self.assertIsNone(claimed.commit_before)
            self.assertIsNone(claimed.commit_after)
            claims = store.list_claims(project.id)[source.id]
            self.assertEqual(claims[0]["claimed_by"], store.machine.id)
            self.assertEqual(claims[0]["as_node"], claimed.id)

            with patch.object(registry, "_schedule_queued") as schedule:
                retried = registry.claim_foreign_virtual(project.id, source.id)

            self.assertEqual(retried.id, claimed.id)
            schedule.assert_not_called()
            local_claimed = [
                node
                for node in store.list_nodes(project.id)
                if node.promoted_from == source.id
                and node.owner_host_id == store.machine.id
            ]
            self.assertEqual([node.id for node in local_claimed], [claimed.id])

    def test_list_claims_reports_independent_host_claims(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(_repo(base / "repo"))))
            ProjectRegistry(store).enable_sharing(project.id)
            for mid, node_id in (("host-a", "node-a"), ("host-b", "node-b")):
                path = (
                    store.root
                    / "projects"
                    / project.id
                    / "hosts"
                    / mid
                    / "claims"
                    / "virtual-1.json"
                )
                store._write_json(
                    path,
                    {"claimed_by": mid, "as_node": node_id, "claimed_at": 1.0},
                )

            claims = store.list_claims(project.id)

            self.assertEqual(
                [claim["as_node"] for claim in claims["virtual-1"]],
                ["node-a", "node-b"],
            )


class HostPartitionApiTests(unittest.TestCase):

    def test_non_git_enable_and_join_require_ack_and_record_each_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            owner_workspace = base / "owner-workspace"
            owner_workspace.mkdir()
            source = Store(base / "source-store")
            project = source.create_project(
                Project(root_path=str(owner_workspace), name="no-git")
            )
            with TestClient(create_app(ProjectRegistry(source))) as client:
                before = client.get(f"/sessions/{project.id}")
                self.assertEqual(before.status_code, 200, before.text)
                self.assertTrue(before.json()["can_enable_sharing"])
                self.assertEqual(
                    before.json()["sharing_readiness"], "ready-unverified"
                )
                rejected = client.post(
                    f"/sessions/{project.id}/sharing",
                    json={"sharing": "shared"},
                )
                self.assertEqual(rejected.status_code, 400, rejected.text)
                self.assertIn("无法发现设备间文件分歧", rejected.json()["detail"])
                enabled = client.post(
                    f"/sessions/{project.id}/sharing",
                    json={
                        "sharing": "shared",
                        "unverified_identity_acknowledged": True,
                        "topology": "replicated",
                    },
                )
                self.assertEqual(enabled.status_code, 200, enabled.text)
                self.assertEqual(
                    enabled.json()["identity"], "environment-attested"
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
                joined = client.post(
                    f"/sessions/{project.id}/hosts",
                    json={
                        "root_path": str(peer_workspace),
                        "unverified_identity_acknowledged": True,
                    },
                )
                self.assertEqual(joined.status_code, 200, joined.text)
                self.assertFalse(joined.json()["read_only"])
                paths = {
                    host["attestation"]["root_path_declared"]
                    for host in joined.json()["hosts"]
                }
                self.assertEqual(len(paths), 2)
                self.assertEqual(
                    {Path(path).resolve() for path in paths},
                    {owner_workspace.resolve(), peer_workspace.resolve()},
                )

    def test_git_identity_ack_does_not_bypass_fingerprint(self) -> None:
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
                    json={
                        "root_path": str(_repo(base / "other-repo")),
                        "unverified_identity_acknowledged": True,
                    },
                )
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("fingerprint", response.json()["detail"])
    def test_nodes_response_includes_claims_for_foreign_virtual(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(_repo(base / "repo"))))
            registry = ProjectRegistry(store)
            registry.enable_sharing(project.id)
            remote = Node(
                project_id=project.id,
                id="remote-virtual",
                state=NodeState.VIRTUAL,
                prompt_draft="claim me",
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
            store._write_json(
                store.root / "projects" / project.id / "hosts" / "other"
                / "claims" / f"{remote.id}.json",
                {"claimed_by": "other", "as_node": "claimed-node", "claimed_at": 1.0},
            )
            store.invalidate_owner_index()

            with TestClient(create_app(registry)) as client:
                response = client.get(f"/sessions/{project.id}/nodes")

            self.assertEqual(response.status_code, 200, response.text)
            payload = next(item for item in response.json() if item["id"] == remote.id)
            self.assertEqual(payload["claims"][0]["as_node"], "claimed-node")

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
    def test_explicit_sync_commits_current_host_head(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            remote = base / "metadata.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            repo = _repo(base / "repo")
            store = Store(base / "store")
            project = store.create_project(Project(root_path=str(repo)))
            registry = ProjectRegistry(store)
            registry.enable_sharing(project.id)
            store.sync.setup_existing_store(str(remote))

            (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            expected_head = _git(repo, "rev-parse", "HEAD")
            store.sync.sync_now()

            payload = store.read_host_heads(project.id)[store.machine.id]
            self.assertEqual(payload["head"], expected_head)
            self.assertTrue(payload["dirty"])
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
