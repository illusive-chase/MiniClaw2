from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.domain import Node, NodeState, Project
from miniclaw2.registry import NonNativeProjectError, ProjectRegistry
from miniclaw2.sharing_requests import (
    DECISION_FILENAME,
    REQUEST_FILENAME,
    SharingDecision,
    request_dir,
    write_record,
)
from miniclaw2.store import Store, StoreReadOnlyError
from miniclaw2.sync import SCHEMA_VERSION, bootstrap_store, schema_is_newer
import miniclaw2.sync as sync_module


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


def _bare(path: Path) -> Path:
    subprocess.run(["git", "init", "--bare", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "--git-dir", str(path), "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
    )
    return path


class _Fixture:
    """A host store plus a second store that only mirrors the project record.

    Copying `projects/<pid>/` is how the existing host-partition tests model
    "the other device has synced this project but owns nothing in it".
    """

    def __init__(self, base: Path, *, configure_sync: bool = True) -> None:
        self.base = base
        self.repo = _repo(base / "repo")
        self.host_store = Store(base / "host-store")
        self.project = self.host_store.create_project(
            Project(root_path=str(self.repo), name="shared-candidate")
        )
        self.host = ProjectRegistry(self.host_store)
        self.peer_store = Store(base / "peer-store")
        shutil.copytree(
            self.host_store.root / "projects" / self.project.id,
            self.peer_store.root / "projects" / self.project.id,
        )
        if configure_sync:
            self.peer_store.sync.setup_existing_store(str(_bare(base / "peer.git")))
        self.peer = ProjectRegistry(self.peer_store)

    @property
    def pid(self) -> str:
        return self.project.id

    def create_historical_request(self):
        """Seed a v11 request record now that the creation API is retired."""
        project = self.peer.get_project(self.pid)
        assert project is not None
        return self.peer_store.create_sharing_request(project)

    def mirror_to_host(self) -> None:
        """Copy the peer's request tree over, standing in for a metadata sync."""
        source = self.peer_store.root / "sharing-requests"
        if source.is_dir():
            shutil.copytree(
                source,
                self.host_store.root / "sharing-requests",
                dirs_exist_ok=True,
            )

    def mirror_to_peer(self) -> None:
        shutil.rmtree(
            self.peer_store.root / "projects" / self.project.id,
            ignore_errors=True,
        )
        shutil.copytree(
            self.host_store.root / "projects" / self.project.id,
            self.peer_store.root / "projects" / self.project.id,
        )
        source = self.host_store.root / "sharing-requests"
        if source.is_dir():
            shutil.copytree(
                source,
                self.peer_store.root / "sharing-requests",
                dirs_exist_ok=True,
            )
        self.peer_store.invalidate_owner_index()
        self.peer.reload_from_store()


class SharingRequestStoreTests(unittest.TestCase):
    def test_request_lands_outside_the_project_tree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))

            record = fixture.create_historical_request()

            assert record is not None
            path = request_dir(fixture.peer_store.root, fixture.pid, record.id)
            self.assertTrue((path / REQUEST_FILENAME).is_file())
            self.assertEqual(record.status, "pending")
            # The device-native project tree stays single-writer: the request
            # is not filed anywhere under it.
            project_tree = fixture.peer_store.root / "projects" / fixture.pid
            self.assertEqual(list(project_tree.rglob(REQUEST_FILENAME)), [])
            self.assertNotIn(
                project_tree,
                path.parents,
            )

    def test_repeated_request_from_one_device_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))

            first = fixture.create_historical_request()
            second = fixture.create_historical_request()

            assert first is not None and second is not None
            self.assertEqual(first.id, second.id)
            self.assertEqual(
                len(fixture.peer_store.sharing_requests_for_project(fixture.project)),
                1,
            )

    def test_owner_cannot_request_and_non_owner_cannot_decide(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.create_historical_request()
            assert record is not None
            fixture.mirror_to_host()

            with self.assertRaises(NonNativeProjectError):
                fixture.peer.accept_project_sharing_request(fixture.pid, record.id)
            with self.assertRaises(NonNativeProjectError):
                fixture.peer.reject_project_sharing_request(fixture.pid, record.id)

    def test_host_cannot_cancel_a_request_it_did_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.create_historical_request()
            assert record is not None
            fixture.mirror_to_host()

            with self.assertRaises(PermissionError):
                fixture.host.cancel_project_sharing_request(fixture.pid, record.id)

    def test_read_only_store_refuses_to_record_a_request(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            with patch.object(
                type(fixture.peer_store),
                "read_only_reason",
                new_callable=PropertyMock,
                return_value="newer schema",
            ):
                with self.assertRaises(StoreReadOnlyError):
                    fixture.create_historical_request()

    def test_accept_migrates_the_project_and_marks_every_request_fulfilled(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.create_historical_request()
            assert record is not None
            fixture.mirror_to_host()

            accepted = fixture.host.accept_project_sharing_request(
                fixture.pid, record.id
            )

            assert accepted is not None
            project, updated = accepted
            self.assertEqual(project.sharing, "shared")
            self.assertEqual(updated.status, "fulfilled")
            host_dir = (
                fixture.host_store.root
                / "projects"
                / fixture.pid
                / "hosts"
                / fixture.host_store.machine.id
            )
            self.assertTrue((host_dir / "host.json").is_file())
            fingerprint = json.loads((host_dir / "host.json").read_text(encoding="utf-8"))
            self.assertEqual(
                fingerprint["repo"]["root_commit"],
                _git(fixture.repo, "rev-list", "--max-parents=0", "HEAD"),
            )

    def test_accept_is_not_blocked_by_a_running_project(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.create_historical_request()
            assert record is not None
            fixture.mirror_to_host()

            with patch.object(ProjectRegistry, "is_running", return_value=True):
                accepted = fixture.host.accept_project_sharing_request(
                    fixture.pid, record.id
                )

            assert accepted is not None
            host_project = fixture.host.get_project(fixture.pid)
            assert host_project is not None
            self.assertEqual(host_project.sharing, "shared")
            still = fixture.host.sharing_requests(fixture.pid)[0]
            self.assertEqual(still.status, "fulfilled")

    def test_accept_on_a_repository_without_commits_keeps_the_request_open(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            fixture = _Fixture(base)
            record = fixture.create_historical_request()
            assert record is not None
            fixture.mirror_to_host()
            host_file = (
                fixture.host_store.root / "projects" / fixture.pid / "hosts"
                / fixture.host_store.machine.id / "host.json"
            )
            payload = json.loads(host_file.read_text(encoding="utf-8"))
            payload["repo"] = {}
            host_file.write_text(json.dumps(payload), encoding="utf-8")
            empty_repo = base / "empty-repo"
            empty_repo.mkdir()
            _git(empty_repo, "init", "-q", "--initial-branch=main")
            host_project = fixture.host.get_project(fixture.pid)
            assert host_project is not None
            host_project.root_path = str(empty_repo)

            with self.assertRaisesRegex(ValueError, "at least one commit"):
                fixture.host.accept_project_sharing_request(fixture.pid, record.id)

            self.assertEqual(
                fixture.host.sharing_requests(fixture.pid)[0].status, "pending"
            )

    def test_reject_closes_only_that_request(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.create_historical_request()
            assert record is not None
            fixture.mirror_to_host()

            rejected = fixture.host.reject_project_sharing_request(
                fixture.pid, record.id
            )

            assert rejected is not None
            self.assertEqual(rejected.status, "rejected")
            host_project = fixture.host.get_project(fixture.pid)
            assert host_project is not None
            self.assertEqual(host_project.sharing, "device-native")
            with self.assertRaisesRegex(ValueError, "already rejected"):
                fixture.host.reject_project_sharing_request(fixture.pid, record.id)

    def test_rejected_request_becomes_fulfilled_when_the_host_shares_later(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.create_historical_request()
            assert record is not None
            fixture.mirror_to_host()
            fixture.host.reject_project_sharing_request(fixture.pid, record.id)

            fixture.host.enable_sharing(fixture.pid)

            self.assertEqual(
                fixture.host.sharing_requests(fixture.pid)[0].status, "fulfilled"
            )

    def test_cancelled_request_is_closed_for_the_host_too(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.create_historical_request()
            assert record is not None

            cancelled = fixture.peer.cancel_project_sharing_request(
                fixture.pid, record.id
            )

            assert cancelled is not None
            self.assertEqual(cancelled.status, "cancelled")
            fixture.mirror_to_host()
            with self.assertRaisesRegex(ValueError, "already cancelled"):
                fixture.host.accept_project_sharing_request(fixture.pid, record.id)

    def test_deleted_project_leaves_the_request_orphaned(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.create_historical_request()
            assert record is not None

            fixture.peer_store.delete_project(fixture.pid)
            fixture.peer.reload_from_store()

            orphaned = fixture.peer_store.list_sharing_requests()[0]
            self.assertEqual(orphaned.status, "orphaned")
            self.assertFalse((fixture.peer_store.root / "projects" / fixture.pid).exists())

    def test_accepted_record_without_migration_reads_as_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.create_historical_request()
            assert record is not None
            fixture.mirror_to_host()
            write_record(
                request_dir(fixture.host_store.root, fixture.pid, record.id)
                / DECISION_FILENAME,
                SharingDecision(
                    request_id=record.id,
                    decision="accepted",
                    decided_by_machine_id=fixture.host_store.machine.id,
                    decided_at=1.0,
                ),
            )

            stale = fixture.host.sharing_requests(fixture.pid)[0]

            self.assertEqual(stale.status, "invalid")
            # An invalid record stays actionable so the host can retry.
            self.assertTrue(stale.is_open)

    def test_forged_decision_from_another_machine_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.create_historical_request()
            assert record is not None
            write_record(
                request_dir(fixture.peer_store.root, fixture.pid, record.id)
                / DECISION_FILENAME,
                SharingDecision(
                    request_id=record.id,
                    decision="rejected",
                    decided_by_machine_id="not-the-owner",
                    decided_at=1.0,
                ),
            )

            observed = fixture.peer.sharing_requests(fixture.pid)[0]

            self.assertEqual(observed.status, "pending")
            self.assertIsNone(observed.decision)

    def test_two_devices_requesting_the_same_project_do_not_collide(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            fixture = _Fixture(base)
            first = fixture.create_historical_request()
            assert first is not None
            fixture.mirror_to_host()

            third_store = Store(base / "third-store")
            shutil.copytree(
                fixture.host_store.root / "projects" / fixture.pid,
                third_store.root / "projects" / fixture.pid,
            )
            third_store.sync.setup_existing_store(str(_bare(base / "third.git")))
            third_registry = ProjectRegistry(third_store)
            third_project = third_registry.get_project(fixture.pid)
            assert third_project is not None
            second = third_store.create_sharing_request(third_project)
            shutil.copytree(
                third_store.root / "sharing-requests",
                fixture.host_store.root / "sharing-requests",
                dirs_exist_ok=True,
            )

            self.assertNotEqual(first.id, second.id)
            self.assertEqual(len(fixture.host.sharing_requests(fixture.pid)), 2)

            fixture.host.accept_project_sharing_request(fixture.pid, first.id)

            self.assertEqual(
                {record.status for record in fixture.host.sharing_requests(fixture.pid)},
                {"fulfilled"},
            )

    def test_visible_requests_exclude_other_devices_projects(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            fixture = _Fixture(base)
            record = fixture.create_historical_request()
            assert record is not None
            foreign = fixture.peer_store.create_project(
                Project(root_path=str(_repo(base / "foreign-repo")), machine_id="elsewhere")
            )
            write_record(
                request_dir(fixture.peer_store.root, foreign.id, "abcdef")
                / REQUEST_FILENAME,
                fixture.peer_store.sharing_requests_for_project(
                    fixture.project
                )[0].request.model_copy(
                    update={
                        "id": "abcdef",
                        "project_id": foreign.id,
                        "requester_machine_id": "somebody-else",
                        "observed_owner_machine_id": "elsewhere",
                    }
                ),
            )
            fixture.peer.reload_from_store()

            visible = fixture.peer.visible_sharing_requests()

            self.assertEqual([item.id for item in visible], [record.id])
            self.assertEqual(len(fixture.peer_store.list_sharing_requests()), 2)


class SharingRequestApiTests(unittest.TestCase):
    def test_remote_device_can_enable_and_join_over_a_real_remote(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            remote = _bare(base / "metadata.git")
            repo_a = _repo(base / "repo-a")
            store_a = Store(base / "store-a")
            project = store_a.create_project(Project(root_path=str(repo_a), name="round-trip"))
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
                self.assertTrue(before.json()["can_enable_sharing"])
                enabled = client_b.post(
                    f"/sessions/{project.id}/sharing",
                    json={"sharing": "shared"},
                )
                self.assertEqual(enabled.status_code, 200, enabled.text)
                self.assertEqual(enabled.json()["sharing"], "shared")
                self.assertTrue(enabled.json()["can_join_here"])

                joined = client_b.post(
                    f"/sessions/{project.id}/hosts",
                    json={"root_path": str(repo_b)},
                )
                self.assertEqual(joined.status_code, 200, joined.text)
                self.assertFalse(joined.json()["read_only"])

    def test_historical_accept_is_not_blocked_by_a_busy_project(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.create_historical_request()
            assert record is not None
            fixture.mirror_to_host()

            with TestClient(create_app(fixture.host)) as client:
                with patch.object(ProjectRegistry, "is_running", return_value=True):
                    response = client.post(
                        f"/sessions/{fixture.pid}/sharing-requests/{record.id}/accept"
                    )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(
                fixture.host.sharing_requests(fixture.pid)[0].status, "fulfilled"
            )

    def test_unknown_request_and_non_owner_decision_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.create_historical_request()
            assert record is not None
            fixture.mirror_to_host()

            with TestClient(create_app(fixture.host)) as host_client:
                missing = host_client.post(
                    f"/sessions/{fixture.pid}/sharing-requests/nope/accept"
                )
                self.assertEqual(missing.status_code, 404, missing.text)

            with TestClient(create_app(fixture.peer)) as peer_client:
                forbidden = peer_client.post(
                    f"/sessions/{fixture.pid}/sharing-requests/{record.id}/accept"
                )
                self.assertEqual(forbidden.status_code, 403, forbidden.text)

    def test_request_creation_endpoint_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            with TestClient(create_app(fixture.host)) as client:
                response = client.post(f"/sessions/{fixture.pid}/sharing-requests")
            self.assertEqual(response.status_code, 404, response.text)


class SharingRequestSchemaTests(unittest.TestCase):
    def test_schema_gate_advances_so_older_builds_stay_read_only(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 12)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "schema.json").write_text(
                json.dumps({"schema": "node-revision-v9", "schema_version": 12}),
                encoding="utf-8",
            )
            with patch.object(sync_module, "SCHEMA_VERSION", 11):
                self.assertTrue(schema_is_newer(root))


if __name__ == "__main__":
    unittest.main()
