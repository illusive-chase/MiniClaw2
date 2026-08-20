from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
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

    def test_refresh_finds_and_applies_fast_forward(self) -> None:
        target = self.push_update()
        exits: list[Path] = []
        checker = UpdateChecker(self.checkout, exit_scheduler=exits.append)

        state = checker.refresh()
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

    def test_dirty_worktree_reports_update_but_blocks_apply(self) -> None:
        self.push_update()
        (self.checkout / "local.txt").write_text("dirty\n")
        checker = UpdateChecker(self.checkout, exit_scheduler=Mock())

        state = checker.refresh()
        self.assertTrue(state.available)
        self.assertTrue(state.dirty)
        with self.assertRaisesRegex(UpdateError, "未提交改动"):
            checker.apply(self.root / "home")

    def test_local_commit_is_not_fast_forwardable_update(self) -> None:
        _commit(self.checkout, "local")
        checker = UpdateChecker(self.checkout, exit_scheduler=Mock())

        state = checker.refresh()
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

    def test_fetch_failure_keeps_previous_update_result(self) -> None:
        self.push_update()
        checker = UpdateChecker(self.checkout, exit_scheduler=Mock())
        previous = checker.refresh()
        _git(self.checkout, "remote", "set-url", "origin", str(self.root / "missing.git"))

        failed = checker.refresh()
        self.assertEqual(failed.head, previous.head)
        self.assertEqual(failed.behind, previous.behind)
        self.assertTrue(failed.available)
        self.assertIn("获取远端更新失败", failed.error or "")

    def test_http_check_and_apply_return_update_instructions(self) -> None:
        self.push_update("frontend update")
        _commit(self.source, "package update", "frontend/package.json")
        _git(self.source, "push")
        exits: list[Path] = []
        checker = UpdateChecker(self.checkout, exit_scheduler=exits.append)
        store_root = self.root / "store"
        registry = ProjectRegistry(Store(store_root))
        client = TestClient(create_app(registry, checker))

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
