"""Sync visibility: Git-derived debt, host beacons, and read-only probes."""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import miniclaw2.sync as sync_module
from miniclaw2.app import create_app
from miniclaw2.domain import Project
from miniclaw2.git_state import worktree_debt
from miniclaw2.host_beacons import list_hosts
from miniclaw2.host_profile import load_profile
from miniclaw2.migrations.catalog import marker
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store
from miniclaw2.sync import BEACON_TRACKING_PREFIX, SchemaConflictError, bootstrap_store
from miniclaw2.sync_local import sync_local
from miniclaw2.sync_overview import classify_outbound


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


class ClassifyOutboundTests(unittest.TestCase):
    def test_categories(self) -> None:
        common = {"threshold": 100.0, "now": 1000.0}
        self.assertEqual(classify_outbound(configured=False, failed=False, since=1.0, **common), "unconfigured")
        self.assertEqual(classify_outbound(configured=True, failed=True, since=None, **common), "failed")
        self.assertEqual(classify_outbound(configured=True, failed=False, since=None, **common), "clean")
        self.assertEqual(classify_outbound(configured=True, failed=False, since=950.0, **common), "pending")
        self.assertEqual(classify_outbound(configured=True, failed=False, since=900.0, **common), "overdue")


class ThreeHostSyncTests(unittest.TestCase):
    """Hosts A and B sharing one bare remote, as in the design's §5.1."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.remote = self.base / "remote.git"
        _git("init", "--bare", str(self.remote))
        self.root_a = self.base / "machine-a"
        self.store_a = Store(self.root_a)
        self.store_a.sync.setup_existing_store(str(self.remote))
        _git("--git-dir", str(self.remote), "symbolic-ref", "HEAD", "refs/heads/main")
        self.root_b = self.base / "machine-b"
        bootstrap_store(self.root_b, str(self.remote))
        self.store_b = Store(self.root_b)
        # Give B's first profile commit a path to the remote.
        self.store_b.sync.sync_now()
        self.store_a.sync.sync_now()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def hosts(self, store: Store) -> dict[str, dict]:
        payload = list_hosts(store.root, store.sync, sync_local(store.root))
        return {entry["machine_id"]: entry for entry in payload["hosts"]}

    def test_unpushed_debt_is_derived_from_git_without_network(self) -> None:
        status = self.store_a.sync.status()
        self.assertEqual(status["unpushed_commits"], 0)
        self.assertIsNone(status["debt_since"])
        before = time.time()
        self.store_a.create_project(Project(root_path="/machine-a/new", name="New"))
        self.store_a.sync.commit_now()
        with patch.object(sync_module, "_run_transfer", side_effect=AssertionError("network")):
            status = self.store_a.sync.status()
        self.assertEqual(status["unpushed_commits"], 1)
        self.assertGreaterEqual(status["unpushed_since"], int(before))
        self.assertEqual(status["debt_since"], status["unpushed_since"])
        self.store_a.sync.sync_now()
        self.assertEqual(self.store_a.sync.status()["unpushed_commits"], 0)

    def test_dirty_debounce_window_counts_as_debt(self) -> None:
        manager = self.store_a.sync
        manager.debounce_seconds = 3600
        self.store_a.create_project(Project(root_path="/machine-a/dirty", name="Dirty"))
        status = manager.status()
        self.assertTrue(status["dirty"])
        self.assertIsNotNone(status["debt_since"])
        manager.commit_now()

    def test_merge_commits_carry_the_merging_host_identity(self) -> None:
        self.store_a.create_project(Project(root_path="/machine-a/one", name="A1"))
        self.store_a.sync.sync_now()
        self.store_b.create_project(Project(root_path="/machine-b/one", name="B1"))
        self.store_b.sync.sync_now()
        log = _git("log", "--merges", "-1", "--format=%an <%ae>", cwd=self.root_b)
        self.assertEqual(log, f"MiniClaw2 ({self.store_b.machine.label}) <miniclaw2@{self.store_b.machine.id}.local>")

    def test_beacons_tell_b_whether_a_has_pulled(self) -> None:
        a, b = self.store_a.machine.id, self.store_b.machine.id
        # t1: A pushes a1.
        self.store_a.create_project(Project(root_path="/machine-a/a1", name="a1"))
        self.store_a.sync.sync_now()
        # t2: B merges a1 with b1 and pushes.
        self.store_b.create_project(Project(root_path="/machine-b/b1", name="b1"))
        self.store_b.sync.sync_now()
        seen_by_b = self.hosts(self.store_b)[a]
        self.assertEqual(seen_by_b["status"], "behind")
        self.assertFalse(seen_by_b["includes_my_push"])
        self.assertTrue(any(item["machine_id"] == b for item in seen_by_b["missing_by"]))

        # t3: A has nothing new; its sync only fast-forwards, yet the
        # beacon still publishes, and B's read-only probe picks it up.
        main_before = _git("ls-remote", str(self.remote), "refs/heads/main")
        self.store_a.sync.sync_now()
        self.assertEqual(_git("ls-remote", str(self.remote), "refs/heads/main"), main_before)
        self.assertTrue(self.store_b.sync.probe_remote())
        seen_by_b = self.hosts(self.store_b)[a]
        self.assertEqual(seen_by_b["status"], "current")
        self.assertTrue(seen_by_b["includes_my_push"])
        self.assertEqual(seen_by_b["behind"], 0)
        self.assertGreaterEqual(len(seen_by_b["history"]), 2)
        # Nothing changed remotely since: the probe does not fetch again.
        with patch.object(sync_module, "_git_transfer", side_effect=AssertionError("fetched")):
            self.assertFalse(self.store_b.sync.probe_remote())

    def test_probe_never_touches_local_branch_or_worktree(self) -> None:
        self.store_b.create_project(Project(root_path="/machine-b/new", name="New"))
        self.store_b.sync.sync_now()
        head = _git("rev-parse", "HEAD", cwd=self.root_a)
        machine = (self.root_a / "machine.json").read_bytes()
        self.assertTrue(self.store_a.sync.probe_remote())
        self.assertEqual(_git("rev-parse", "HEAD", cwd=self.root_a), head)
        self.assertEqual((self.root_a / "machine.json").read_bytes(), machine)
        self.assertGreater(self.store_a.sync.status()["remote"]["behind"], 0)

    def test_probe_does_not_hold_the_storage_lock(self) -> None:
        manager = self.store_a.sync
        entered, release = threading.Event(), threading.Event()
        real = sync_module._run_raw

        def slow(command, **kwargs):
            if "ls-remote" in command:
                entered.set()
                release.wait(5)
            return real(command, **kwargs)

        with patch.object(sync_module, "_run_raw", side_effect=slow):
            worker = threading.Thread(target=manager.probe_remote)
            worker.start()
            self.assertTrue(entered.wait(5))
            # A store write needs the coordinator mutex; it must not wait for
            # the probe's network round trip.
            acquired = manager._lock.acquire(timeout=1)
            self.assertTrue(acquired)
            manager._lock.release()
            # A second probe while one is in flight just skips.
            self.assertIsNone(manager.probe_remote())
            release.set()
            worker.join(5)

    def test_cloned_identity_lease_conflict_is_a_warning_not_a_failure(self) -> None:
        a = self.store_a.machine.id
        # Another device publishes under A's id.
        _git("push", str(self.remote), f"{BEACON_TRACKING_PREFIX}{self.store_b.machine.id}:refs/heads/_hosts/{a}", "--force", cwd=self.root_b)
        self.store_a.create_project(Project(root_path="/machine-a/next", name="next"))
        status = self.store_a.sync.sync_now()
        self.assertEqual(status["unpushed_commits"], 0)
        self.assertIn("另一台", status["beacon_error"])

    def test_newer_remote_schema_is_refused_before_extraction(self) -> None:
        schema = json.loads((self.root_b / "schema.json").read_text())
        schema["schema_version"] += 1
        (self.root_b / "schema.json").write_text(json.dumps(schema) + "\n")
        # A future top-level directory sorts before schema.json in the archive.
        (self.root_b / "hosts" / "future").mkdir(parents=True, exist_ok=True)
        (self.root_b / "aaa-future").mkdir()
        (self.root_b / "aaa-future" / "x.json").write_text("{}\n")
        _git("add", "-A", "--force", cwd=self.root_b)
        _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "future", cwd=self.root_b)
        _git("push", "origin", "HEAD:main", cwd=self.root_b)
        self.store_a.create_project(Project(root_path="/machine-a/diverge", name="diverge"))
        with self.assertRaisesRegex(SchemaConflictError, "新于程序"):
            self.store_a.sync.sync_now()
        self.assertEqual(json.loads((self.root_a / "schema.json").read_text()), marker())

    def test_host_profile_rename_reaches_the_peer(self) -> None:
        self.store_a.update_own_profile(label="办公室 Mac", icon="laptop", color="azure")
        self.store_a.sync.sync_now()
        self.store_b.sync.sync_now()
        profile = load_profile(self.root_b, self.store_a.machine.id)
        assert profile is not None
        self.assertEqual((profile.label, profile.icon, profile.color), ("办公室 Mac", "laptop", "azure"))
        self.assertEqual(self.hosts(self.store_b)[self.store_a.machine.id]["label"], "办公室 Mac")
        self.assertEqual(self.store_a.machine.label, "办公室 Mac")

    def test_hosts_api_and_profile_patch(self) -> None:
        registry = ProjectRegistry(self.store_a)
        with TestClient(create_app(registry)) as client:
            listed = client.get("/hosts").json()
            self.assertEqual(listed["self_id"], self.store_a.machine.id)
            self.assertEqual({entry["machine_id"] for entry in listed["hosts"]},
                             {self.store_a.machine.id, self.store_b.machine.id})
            rejected = client.patch("/hosts/self", json={"icon": "spaceship"})
            self.assertEqual(rejected.status_code, 422)
            renamed = client.patch("/hosts/self", json={"label": "  实验室  ", "color": "teal"})
            self.assertEqual(renamed.status_code, 200, renamed.text)
            self.assertEqual(renamed.json()["label"], "实验室")
            overview = client.get("/sync-overview").json()
            self.assertIn(overview["metadata"]["state"], {"pending", "clean"})
            health = client.get("/health").json()
            self.assertIn("boot_id", health)


class ProjectDebtTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.remote = self.base / "remote.git"
        _git("init", "--bare", "-b", "main", str(self.remote))
        self.repo = self.base / "repo"
        _git("clone", str(self.remote), str(self.repo))
        _git("config", "user.name", "t", cwd=self.repo)
        _git("config", "user.email", "t@t", cwd=self.repo)
        (self.repo / "a.txt").write_text("a\n")
        _git("add", "a.txt", cwd=self.repo)
        _git("commit", "-m", "a", cwd=self.repo)
        _git("push", "-u", "origin", "HEAD:main", cwd=self.repo)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_clean_dirty_ahead_and_no_upstream(self) -> None:
        clean = worktree_debt(str(self.repo))
        self.assertTrue(clean.is_repo)
        self.assertEqual((clean.dirty_count, clean.ahead), (0, 0))
        (self.repo / "b.txt").write_text("b\n")
        (self.repo / ".miniclaw2").mkdir()
        (self.repo / ".miniclaw2" / "x").write_text("generated\n")
        dirty = worktree_debt(str(self.repo))
        self.assertEqual(dirty.dirty_count, 1)
        self.assertIsNotNone(dirty.dirty_mtime)
        _git("add", "b.txt", cwd=self.repo)
        _git("commit", "-m", "b", cwd=self.repo)
        ahead = worktree_debt(str(self.repo))
        self.assertEqual(ahead.ahead, 1)
        self.assertIsNotNone(ahead.ahead_since)
        _git("checkout", "-b", "local-only", cwd=self.repo)
        orphan = worktree_debt(str(self.repo))
        self.assertIsNone(orphan.upstream)
        self.assertEqual(worktree_debt(str(self.base)).is_repo, False)

    def test_overview_marks_overdue_project_and_clears_ledger(self) -> None:
        store = Store(self.base / "store")
        registry = ProjectRegistry(store)
        project = registry.create_project(name="Repo", cwd=str(self.repo))
        (self.repo / "c.txt").write_text("c\n")
        with TestClient(create_app(registry)) as client:
            overview = client.post("/sync-overview/refresh").json()
            entry = next(item for item in overview["projects"] if item["id"] == project.id)
            self.assertEqual(entry["state"], "pending")
            self.assertEqual(entry["reasons"], ["dirty"])
            ledger = sync_local(store.root).section("projects")
            sync_local(store.root).update_section("projects", {
                project.id: {"first_seen_dirty_at": time.time() - 2 * 86400},
            })
            overview = client.get("/sync-overview").json()
            entry = next(item for item in overview["projects"] if item["id"] == project.id)
            self.assertEqual(entry["state"], "overdue")
            self.assertEqual(overview["projects_overdue"], 1)
            (self.repo / "c.txt").unlink()
            client.post("/sync-overview/refresh")
            self.assertNotIn(project.id, sync_local(store.root).section("projects"))
            self.assertIn(project.id, ledger)
            settings = client.patch("/global-state/sync-local", json={"project_overdue_hours": 0})
            self.assertEqual(settings.status_code, 422)


if __name__ == "__main__":
    unittest.main()
