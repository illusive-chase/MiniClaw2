from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.domain import Node, NodeState
from miniclaw2.registry import ProjectRegistry
from miniclaw2.self_update import (
    UpdateChecker,
    UpdateError,
    _git_env,
    exit_target_pid,
)
from miniclaw2.store import Store
from miniclaw2.sync import ensure_store_gitignore


class _PendingTask:
    def done(self) -> bool:
        return False


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str, filename: str = "file.txt") -> str:
    path = repo / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(path.read_text() + message + "\n" if path.exists() else message + "\n")
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


class SelfUpdateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.source = self.root / "source"
        self.checkout = self.root / "checkout"
        _git(self.root, "init", "--bare", str(self.remote))
        self.source.mkdir()
        _git(self.source, "init", "-b", "main")
        _git(self.source, "config", "user.name", "Test User")
        _git(self.source, "config", "user.email", "test@example.com")
        _commit(self.source, "initial")
        _git(self.source, "remote", "add", "origin", str(self.remote))
        _git(self.source, "push", "-u", "origin", "main")
        _git(self.root, "clone", "-b", "main", str(self.remote), str(self.checkout))
        _git(self.checkout, "config", "user.name", "Test User")
        _git(self.checkout, "config", "user.email", "test@example.com")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def push_update(self, message: str = "upstream") -> str:
        head = _commit(self.source, message)
        _git(self.source, "push")
        return head

    def test_check_remote_finds_and_applies_fast_forward(self) -> None:
        target = self.push_update()
        exits: list[Path] = []
        checker = UpdateChecker(self.checkout, exit_scheduler=exits.append)

        state = checker.check_remote()
        self.assertTrue(state.available)
        self.assertTrue(state.fast_forward)
        self.assertEqual(state.behind, 1)
        self.assertEqual([commit.title for commit in state.commits], ["upstream"])

        store_root = self.root / "home"
        result = checker.apply(store_root)
        self.assertEqual(result.new_head, target)
        self.assertTrue((store_root / ".update-exit-pending").is_file())
        checker.schedule_exit(store_root)
        self.assertEqual(exits, [store_root])

    def test_state_reads_local_refs_without_contacting_remote(self) -> None:
        """A new upstream commit stays invisible until the user checks."""
        self.push_update()
        checker = UpdateChecker(self.checkout, exit_scheduler=Mock())

        local = checker.state()
        self.assertTrue(local.is_repo)
        self.assertEqual(local.behind, 0)
        self.assertFalse(local.available)
        self.assertIsNone(local.error)

        # Breaking the remote must not change what the local derivation reports.
        _git(self.checkout, "remote", "set-url", "origin", str(self.root / "missing.git"))
        self.assertEqual(checker.state().behind, 0)
        self.assertIsNone(checker.state().error)

    def test_ref_at_reports_when_the_upstream_ref_last_moved(self) -> None:
        old_commit_at = 946684800
        with patch.dict(
            os.environ,
            {
                "GIT_AUTHOR_DATE": f"@{old_commit_at} +0000",
                "GIT_COMMITTER_DATE": f"@{old_commit_at} +0000",
            },
        ):
            self.push_update()
        checker = UpdateChecker(self.checkout, exit_scheduler=Mock())
        before_fetch = time.time()

        ref_at = checker.check_remote().ref_at

        self.assertIsNotNone(ref_at)
        self.assertGreaterEqual(ref_at or 0, before_fetch - 2)
        self.assertLessEqual(ref_at or 0, time.time() + 2)
        self.assertNotEqual(ref_at, old_commit_at)

    def test_apply_without_a_prior_check_refuses(self) -> None:
        """Apply derives locally too, so it cannot fast-forward to unfetched work."""
        self.push_update()
        checker = UpdateChecker(self.checkout, exit_scheduler=Mock())

        with self.assertRaisesRegex(UpdateError, "请先检查远端"):
            checker.apply(self.root / "home")

    def test_dirty_worktree_reports_update_but_blocks_apply(self) -> None:
        self.push_update()
        (self.checkout / "local.txt").write_text("dirty\n")
        checker = UpdateChecker(self.checkout, exit_scheduler=Mock())

        state = checker.check_remote()
        self.assertTrue(state.available)
        self.assertTrue(state.dirty)
        with self.assertRaisesRegex(UpdateError, "未提交改动"):
            checker.apply(self.root / "home")

    def test_local_commit_is_not_fast_forwardable_update(self) -> None:
        _commit(self.checkout, "local")
        checker = UpdateChecker(self.checkout, exit_scheduler=Mock())

        state = checker.check_remote()
        self.assertEqual(state.ahead, 1)
        self.assertFalse(state.available)
        self.assertFalse(state.fast_forward)

    def test_repository_without_upstream_is_unavailable(self) -> None:
        standalone = self.root / "standalone"
        standalone.mkdir()
        _git(standalone, "init", "-b", "main")
        _git(standalone, "config", "user.name", "Test User")
        _git(standalone, "config", "user.email", "test@example.com")
        _commit(standalone, "initial")

        state = UpdateChecker(standalone, exit_scheduler=Mock()).state()
        self.assertFalse(state.available)
        self.assertIn("upstream", state.error or "")

    def test_fetch_failure_surfaces_and_leaves_local_state_readable(self) -> None:
        self.push_update()
        checker = UpdateChecker(self.checkout, exit_scheduler=Mock())
        previous = checker.check_remote()
        _git(self.checkout, "remote", "set-url", "origin", str(self.root / "missing.git"))

        with self.assertRaisesRegex(UpdateError, "获取远端更新失败"):
            checker.check_remote()

        # The already-fetched ref still describes what this host knows.
        current = checker.state()
        self.assertEqual(current.head, previous.head)
        self.assertEqual(current.behind, previous.behind)
        self.assertTrue(current.available)

    def test_http_check_and_apply_return_update_instructions(self) -> None:
        self.push_update("frontend update")
        _commit(self.source, "package update", "frontend/package.json")
        _git(self.source, "push")
        exits: list[Path] = []
        checker = UpdateChecker(self.checkout, exit_scheduler=exits.append)
        store_root = self.root / "store"
        registry = ProjectRegistry(Store(store_root))
        client = TestClient(create_app(registry, checker))

        # Before an explicit check the source remote has not been contacted.
        self.assertEqual(client.get("/self-update").json()["behind"], 0)

        checked = client.post("/self-update/check")
        self.assertEqual(checked.status_code, 200)
        self.assertEqual(checked.json()["behind"], 2)
        self.assertEqual(checked.json()["blockers"], [])

        applied = client.post("/self-update/apply")
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(
            applied.json()["restart_commands"],
            [
                f"cd {shlex.quote(str(self.checkout))} && "
                "cd frontend && npm install && npm run build"
            ],
        )
        self.assertEqual(exits, [store_root])

    def test_http_check_reports_fetch_failure(self) -> None:
        checker = UpdateChecker(self.checkout, exit_scheduler=Mock())
        _git(self.checkout, "remote", "set-url", "origin", str(self.root / "missing.git"))
        registry = ProjectRegistry(Store(self.root / "store"))
        client = TestClient(create_app(registry, checker))

        response = client.post("/self-update/check")
        self.assertEqual(response.status_code, 409)
        self.assertIn("获取远端更新失败", response.json()["detail"])

    def test_startup_does_not_contact_the_source_remote(self) -> None:
        self.push_update()
        checker = UpdateChecker(self.checkout, exit_scheduler=Mock())
        registry = ProjectRegistry(Store(self.root / "store"))

        with patch.object(
            UpdateChecker, "check_remote", side_effect=AssertionError("fetched")
        ):
            with TestClient(create_app(registry, checker)) as client:
                self.assertEqual(client.get("/self-update").json()["behind"], 0)

    def test_terminal_runner_finalization_blocks_apply(self) -> None:
        self.push_update()
        checker = UpdateChecker(self.checkout, exit_scheduler=Mock())
        store_root = self.root / "store"
        registry = ProjectRegistry(Store(store_root))
        workspace = self.root / "workspace"
        workspace.mkdir()
        project = registry.create_project(name="Project", cwd=str(workspace))
        node = registry.store.create_node(
            Node(
                project_id=project.id,
                state=NodeState.DONE,
                model_preset_id=project.model_preset_id,
                prompt="finishing",
            )
        )
        registry._runtimes[project.id].runner_tasks[node.id] = _PendingTask()  # type: ignore[assignment]

        with TestClient(create_app(registry, checker)) as client:
            checked = client.post("/self-update/check")
            self.assertEqual(checked.status_code, 200)
            self.assertEqual(checked.json()["blockers"][0]["state"], "finalizing")

            applied = client.post("/self-update/apply")
            self.assertEqual(applied.status_code, 409)
            self.assertIn("finalizing", applied.json()["detail"])

        self.assertTrue(registry.prepare_self_update())
        registry.cancel_self_update()

    def test_update_gate_prevents_new_runner_launches(self) -> None:
        registry = ProjectRegistry(Store(self.root / "store"))
        workspace = self.root / "workspace"
        workspace.mkdir()
        project = registry.create_project(name="Project", cwd=str(workspace))

        self.assertTrue(registry.prepare_self_update())
        node = registry.start_node(project.id, "wait until restart")

        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(node.state, NodeState.QUEUED)
        self.assertEqual(registry._runtimes[project.id].runner_tasks, {})

    def test_git_environment_preserves_custom_ssh_command(self) -> None:
        with patch.dict(
            os.environ,
            {"GIT_SSH_COMMAND": "ssh -i '/tmp/key file' -o ProxyJump=host"},
        ):
            env = _git_env()

        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(
            env["GIT_SSH_COMMAND"],
            "ssh -i '/tmp/key file' -o ProxyJump=host -oBatchMode=yes",
        )

    def test_non_repository_is_silent(self) -> None:
        directory = self.root / "plain"
        directory.mkdir()
        state = UpdateChecker(directory, exit_scheduler=Mock()).state()
        self.assertFalse(state.is_repo)
        self.assertIsNone(state.error)

    def test_exit_target_uses_current_process_without_reloader(self) -> None:
        with patch("miniclaw2.self_update.multiprocessing.parent_process", return_value=None):
            self.assertEqual(exit_target_pid(), os.getpid())

    def test_exit_target_uses_reloader_parent(self) -> None:
        parent = Mock(pid=43210)
        with patch("miniclaw2.self_update.multiprocessing.parent_process", return_value=parent):
            self.assertEqual(exit_target_pid(), 43210)

    def test_update_exit_sentinel_is_ignored_by_metadata_store(self) -> None:
        store_root = self.root / "store"
        store_root.mkdir()
        ensure_store_gitignore(store_root)
        self.assertIn(
            ".update-exit-pending",
            (store_root / ".gitignore").read_text().splitlines(),
        )


if __name__ == "__main__":
    unittest.main()
