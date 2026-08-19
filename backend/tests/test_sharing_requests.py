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

            record = fixture.peer.request_project_sharing(fixture.pid)

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

            first = fixture.peer.request_project_sharing(fixture.pid)
            second = fixture.peer.request_project_sharing(fixture.pid)

            assert first is not None and second is not None
            self.assertEqual(first.id, second.id)
            self.assertEqual(
                len(fixture.peer_store.sharing_requests_for_project(fixture.project)),
                1,
            )

    def test_owner_cannot_request_and_non_owner_cannot_decide(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.peer.request_project_sharing(fixture.pid)
            assert record is not None
            fixture.mirror_to_host()

            with self.assertRaisesRegex(ValueError, "native host"):
                fixture.host.request_project_sharing(fixture.pid)
            with self.assertRaises(NonNativeProjectError):
                fixture.peer.accept_project_sharing_request(fixture.pid, record.id)
            with self.assertRaises(NonNativeProjectError):
                fixture.peer.reject_project_sharing_request(fixture.pid, record.id)

    def test_host_cannot_cancel_a_request_it_did_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.peer.request_project_sharing(fixture.pid)
            assert record is not None
            fixture.mirror_to_host()

            with self.assertRaises(PermissionError):
                fixture.host.cancel_project_sharing_request(fixture.pid, record.id)

    def test_temporary_and_shared_and_unconfigured_sync_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            fixture = _Fixture(base, configure_sync=False)

            with self.assertRaisesRegex(ValueError, "metadata sync"):
                fixture.peer.request_project_sharing(fixture.pid)

            fixture.peer_store.sync.setup_existing_store(str(_bare(base / "late.git")))
            peer_project = fixture.peer.get_project(fixture.pid)
            assert peer_project is not None
            peer_project.temporary = True
            with self.assertRaisesRegex(ValueError, "temporary"):
                fixture.peer.request_project_sharing(fixture.pid)

            peer_project.temporary = False
            peer_project.sharing = "shared"
            with self.assertRaisesRegex(ValueError, "already shared"):
                fixture.peer.request_project_sharing(fixture.pid)

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
                    fixture.peer.request_project_sharing(fixture.pid)

    def test_accept_migrates_the_project_and_marks_every_request_fulfilled(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.peer.request_project_sharing(fixture.pid)
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

    def test_accept_leaves_the_request_pending_when_migration_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.peer.request_project_sharing(fixture.pid)
            assert record is not None
            fixture.mirror_to_host()

            with patch.object(ProjectRegistry, "is_running", return_value=True):
                with self.assertRaises(RuntimeError):
                    fixture.host.accept_project_sharing_request(fixture.pid, record.id)

            host_project = fixture.host.get_project(fixture.pid)
            assert host_project is not None
            self.assertEqual(host_project.sharing, "device-native")
            still = fixture.host.sharing_requests(fixture.pid)[0]
            self.assertEqual(still.status, "pending")
            self.assertIsNone(still.decision)

    def test_accept_on_a_repository_without_commits_keeps_the_request_open(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            fixture = _Fixture(base)
            record = fixture.peer.request_project_sharing(fixture.pid)
            assert record is not None
            fixture.mirror_to_host()
            bare_dir = base / "not-a-repo"
            bare_dir.mkdir()
            host_project = fixture.host.get_project(fixture.pid)
            assert host_project is not None
            host_project.root_path = str(bare_dir)

            with self.assertRaisesRegex(ValueError, "at least one commit"):
                fixture.host.accept_project_sharing_request(fixture.pid, record.id)

            self.assertEqual(
                fixture.host.sharing_requests(fixture.pid)[0].status, "pending"
            )

    def test_reject_closes_only_that_request(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.peer.request_project_sharing(fixture.pid)
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
            record = fixture.peer.request_project_sharing(fixture.pid)
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
            record = fixture.peer.request_project_sharing(fixture.pid)
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
            record = fixture.peer.request_project_sharing(fixture.pid)
            assert record is not None

            fixture.peer_store.delete_project(fixture.pid)
            fixture.peer.reload_from_store()

            orphaned = fixture.peer_store.list_sharing_requests()[0]
            self.assertEqual(orphaned.status, "orphaned")
            self.assertFalse((fixture.peer_store.root / "projects" / fixture.pid).exists())

    def test_accepted_record_without_migration_reads_as_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.peer.request_project_sharing(fixture.pid)
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
            record = fixture.peer.request_project_sharing(fixture.pid)
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
            first = fixture.peer.request_project_sharing(fixture.pid)
            assert first is not None
            fixture.mirror_to_host()

            third_store = Store(base / "third-store")
            shutil.copytree(
                fixture.host_store.root / "projects" / fixture.pid,
                third_store.root / "projects" / fixture.pid,
            )
            third_store.sync.setup_existing_store(str(_bare(base / "third.git")))
            second = ProjectRegistry(third_store).request_project_sharing(fixture.pid)
            assert second is not None
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
            record = fixture.peer.request_project_sharing(fixture.pid)
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
    def test_request_accept_and_join_round_trip_over_a_real_remote(self) -> None:
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
            registry_a = ProjectRegistry(store_a)
            store_a.sync.setup_existing_store(str(remote))

            root_b = base / "store-b"
            bootstrap_store(root_b, str(remote))
            store_b = Store(root_b)
            registry_b = ProjectRegistry(store_b)
            repo_b = base / "repo-b"
            subprocess.run(["git", "clone", "-q", str(repo_a), str(repo_b)], check=True)

            with TestClient(create_app(registry_b)) as client_b:
                created = client_b.post(f"/sessions/{project.id}/sharing-requests")
                self.assertEqual(created.status_code, 201, created.text)
                payload = created.json()["request"]
                self.assertEqual(payload["status"], "pending")
                self.assertTrue(payload["can_cancel"])
                self.assertFalse(payload["can_accept"])
                rid = payload["id"]
                self.assertEqual(
                    client_b.post("/global-state/sync").status_code, 200
                )

            with TestClient(create_app(registry_a)) as client_a:
                self.assertEqual(client_a.post("/global-state/sync").status_code, 200)
                listed = client_a.get("/sharing-requests")
                self.assertEqual(listed.status_code, 200, listed.text)
                host_view = listed.json()["requests"][0]
                self.assertEqual(host_view["id"], rid)
                self.assertTrue(host_view["can_accept"])
                self.assertFalse(host_view["can_cancel"])
                self.assertEqual(
                    host_view["requester_machine_label"], store_b.machine.label
                )

                accepted = client_a.post(
                    f"/sessions/{project.id}/sharing-requests/{rid}/accept"
                )
                self.assertEqual(accepted.status_code, 200, accepted.text)
                self.assertEqual(accepted.json()["session"]["sharing"], "shared")
                self.assertEqual(accepted.json()["request"]["status"], "fulfilled")
                self.assertEqual(client_a.post("/global-state/sync").status_code, 200)

            with TestClient(create_app(registry_b)) as client_b:
                self.assertEqual(client_b.post("/global-state/sync").status_code, 200)
                session = client_b.get(f"/sessions/{project.id}")
                self.assertEqual(session.status_code, 200, session.text)
                self.assertTrue(session.json()["can_join_here"])

                joined = client_b.post(
                    f"/sessions/{project.id}/hosts",
                    json={"root_path": str(repo_b)},
                )
                self.assertEqual(joined.status_code, 200, joined.text)
                self.assertFalse(joined.json()["read_only"])
                self.assertEqual(
                    client_b.get("/sharing-requests").json()["requests"][0]["status"],
                    "fulfilled",
                )

    def test_request_and_host_edits_merge_without_conflict_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            remote = _bare(base / "metadata.git")
            repo_a = _repo(base / "repo-a")
            store_a = Store(base / "store-a")
            project = store_a.create_project(Project(root_path=str(repo_a), name="before"))
            registry_a = ProjectRegistry(store_a)
            store_a.sync.setup_existing_store(str(remote))

            root_b = base / "store-b"
            bootstrap_store(root_b, str(remote))
            store_b = Store(root_b)
            registry_b = ProjectRegistry(store_b)

            record = registry_b.request_project_sharing(project.id)
            assert record is not None
            registry_a.rename_project(project.id, "renamed-by-host")

            store_b.sync.sync_now()
            store_a.sync.sync_now()
            registry_a.reload_from_store()

            host_project = registry_a.get_project(project.id)
            assert host_project is not None
            self.assertEqual(host_project.name, "renamed-by-host")
            self.assertEqual(
                [item.id for item in registry_a.sharing_requests(project.id)],
                [record.id],
            )

    def test_accept_conflicts_with_a_busy_project(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.peer.request_project_sharing(fixture.pid)
            assert record is not None
            fixture.mirror_to_host()

            with TestClient(create_app(fixture.host)) as client:
                with patch.object(ProjectRegistry, "is_running", return_value=True):
                    response = client.post(
                        f"/sessions/{fixture.pid}/sharing-requests/{record.id}/accept"
                    )

            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(
                fixture.host.sharing_requests(fixture.pid)[0].status, "pending"
            )

    def test_unknown_request_and_non_owner_decision_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            record = fixture.peer.request_project_sharing(fixture.pid)
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

    def test_owner_request_endpoint_is_a_client_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = _Fixture(Path(raw))
            with TestClient(create_app(fixture.host)) as client:
                response = client.post(f"/sessions/{fixture.pid}/sharing-requests")
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("native host", response.json()["detail"])


class SharingRequestSchemaTests(unittest.TestCase):
    def test_schema_gate_advances_so_older_builds_stay_read_only(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 11)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "schema.json").write_text(
                json.dumps({"schema": "node-revision-v9", "schema_version": 11}),
                encoding="utf-8",
            )
            with patch.object(sync_module, "SCHEMA_VERSION", 10):
                self.assertTrue(schema_is_newer(root))


if __name__ == "__main__":
    unittest.main()
