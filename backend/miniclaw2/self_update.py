"""Inspect and fast-forward the MiniClaw2 source checkout."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import shlex
import signal
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .git_state import _git, is_git_repo


UPDATE_EXIT_SENTINEL = ".update-exit-pending"


@dataclass(frozen=True)
class IncomingCommit:
    sha: str
    title: str
    author: str
    authored_at: float


@dataclass(frozen=True)
class UpdateState:
    is_repo: bool = False
    available: bool = False
    fast_forward: bool = False
    dirty: bool = False
    head: str | None = None
    branch: str | None = None
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    commits: tuple[IncomingCommit, ...] = ()
    last_checked_at: float | None = None
    checking: bool = False
    error: str | None = None


@dataclass(frozen=True)
class ApplyResult:
    old_head: str
    new_head: str
    changed_paths: tuple[str, ...] = ()
    restart_commands: tuple[str, ...] = ()


class UpdateError(RuntimeError):
    """A user-actionable reason why an update cannot be applied."""


def discover_repo_root() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    return root if is_git_repo(str(root)) else None


def exit_target_pid() -> int:
    """Target uvicorn's reloader parent when the app runs in its child."""
    parent = multiprocessing.parent_process()
    return parent.pid if parent is not None else os.getpid()


def sentinel_path(store_root: Path) -> Path:
    return store_root / UPDATE_EXIT_SENTINEL


def schedule_process_exit(store_root: Path, *, delay: float = 0.3) -> None:
    """Let the HTTP response flush, then terminate uvicorn cleanly."""

    def stop() -> None:
        sentinel_path(store_root).unlink(missing_ok=True)
        try:
            os.kill(exit_target_pid(), signal.SIGTERM)
        except ProcessLookupError:
            pass

    timer = threading.Timer(delay, stop)
    timer.daemon = True
    timer.start()


def consume_pending_exit(store_root: Path) -> bool:
    """Finish an update exit if the reloader restarted us during the merge."""
    path = sentinel_path(store_root)
    if not path.exists():
        return False
    path.unlink(missing_ok=True)
    timer = threading.Timer(0.05, _signal_exit_target)
    timer.daemon = True
    timer.start()
    return True


def _signal_exit_target() -> None:
    try:
        os.kill(exit_target_pid(), signal.SIGTERM)
    except ProcessLookupError:
        pass


def _failure(operation: str, stderr: str, stdout: str = "") -> str:
    detail = stderr.strip() or stdout.strip()
    return f"{operation}失败：{detail or 'Git 未返回详细信息'}"


def _git_env() -> dict[str, str]:
    ssh_command = os.environ.get("GIT_SSH_COMMAND", "ssh")
    return {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": f"{ssh_command} -oBatchMode=yes",
    }


def _restart_commands(repo_root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
    changed = set(paths)
    commands: list[str] = []
    checkout = shlex.quote(str(repo_root))
    if "backend/pyproject.toml" in changed:
        commands.append(f"cd {checkout} && pip install -e backend")
    frontend_changed = any(path.startswith("frontend/") for path in changed)
    if "frontend/package.json" in changed or "frontend/package-lock.json" in changed:
        commands.append(
            f"cd {checkout} && cd frontend && npm install && npm run build"
        )
    elif frontend_changed:
        commands.append(f"cd {checkout} && cd frontend && npm run build")
    return tuple(commands)


class UpdateChecker:
    """Thread-safe cached view of the source checkout's update state."""

    def __init__(
        self,
        repo_root: Path | None = None,
        *,
        exit_scheduler: Callable[[Path], None] = schedule_process_exit,
    ) -> None:
        self.repo_root = repo_root if repo_root is not None else discover_repo_root()
        self._exit_scheduler = exit_scheduler
        self._lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._checking = False
        self._state = self._inspect(last_checked_at=None)

    @property
    def enabled(self) -> bool:
        return self.repo_root is not None and self._state.is_repo

    def state(self) -> UpdateState:
        with self._lock:
            return replace(self._state, checking=self._checking)

    def _run(self, args: list[str], *, timeout: float = 10):
        if self.repo_root is None:
            raise UpdateError("当前安装不是 Git 工作区")
        return _git(
            str(self.repo_root),
            args,
            timeout=timeout,
            env=_git_env(),
        )

    def _inspect(self, *, last_checked_at: float | None) -> UpdateState:
        if self.repo_root is None or not is_git_repo(str(self.repo_root)):
            return UpdateState()

        head_result = self._run(["rev-parse", "HEAD"])
        branch_result = self._run(["branch", "--show-current"])
        upstream_result = self._run(["rev-parse", "--abbrev-ref", "@{upstream}"])
        if head_result.returncode != 0:
            return UpdateState(is_repo=True, error=_failure("读取当前版本", head_result.stderr))
        head = head_result.stdout.strip()
        branch = branch_result.stdout.strip() or None
        if not branch:
            return UpdateState(
                is_repo=True,
                head=head,
                last_checked_at=last_checked_at,
                error="当前处于 detached HEAD，无法自动更新",
            )
        if upstream_result.returncode != 0:
            return UpdateState(
                is_repo=True,
                head=head,
                branch=branch,
                last_checked_at=last_checked_at,
                error="当前分支未配置 upstream，无法检查更新",
            )
        upstream = upstream_result.stdout.strip()
        counts = self._run(["rev-list", "--left-right", "--count", "HEAD...@{upstream}"])
        if counts.returncode != 0:
            return UpdateState(
                is_repo=True,
                head=head,
                branch=branch,
                upstream=upstream,
                last_checked_at=last_checked_at,
                error=_failure("比较本地与远端版本", counts.stderr),
            )
        try:
            ahead, behind = (int(value) for value in counts.stdout.split())
        except (TypeError, ValueError):
            ahead, behind = 0, 0
        ancestor = self._run(["merge-base", "--is-ancestor", "HEAD", "@{upstream}"])
        fast_forward = ancestor.returncode == 0 and ahead == 0
        status = self._run(["status", "--porcelain", "--untracked-files=all"])
        dirty = status.returncode != 0 or bool(status.stdout.strip())
        commits = self._incoming_commits() if behind > 0 else ()
        return UpdateState(
            is_repo=True,
            available=behind > 0 and fast_forward,
            fast_forward=fast_forward,
            dirty=dirty,
            head=head,
            branch=branch,
            upstream=upstream,
            ahead=ahead,
            behind=behind,
            commits=commits,
            last_checked_at=last_checked_at,
        )

    def _incoming_commits(self) -> tuple[IncomingCommit, ...]:
        result = self._run(
            ["log", "--reverse", "--format=%H%x00%s%x00%an%x00%at%x1e", "HEAD..@{upstream}"]
        )
        if result.returncode != 0:
            return ()
        commits: list[IncomingCommit] = []
        for record in result.stdout.split("\x1e"):
            parts = record.strip("\n\x00").split("\x00")
            if len(parts) != 4:
                continue
            try:
                authored_at = float(parts[3])
            except ValueError:
                authored_at = 0
            commits.append(IncomingCommit(parts[0], parts[1], parts[2], authored_at))
        return tuple(commits)

    def refresh(self, *, fetch: bool = True) -> UpdateState:
        with self._lock:
            if self._checking:
                return replace(self._state, checking=True)
            self._checking = True
        return self._refresh_started(fetch=fetch)

    def _refresh_started(self, *, fetch: bool) -> UpdateState:
        with self._operation_lock:
            with self._lock:
                previous = self._state
            try:
                if fetch:
                    upstream = previous.upstream
                    if upstream is None:
                        local = self._inspect(last_checked_at=previous.last_checked_at)
                        upstream = local.upstream
                    if upstream is None:
                        with self._lock:
                            self._state = local
                        return local
                    remote = upstream.split("/", 1)[0]
                    fetched = self._run(["fetch", "--quiet", remote], timeout=120)
                    if fetched.returncode != 0:
                        error = _failure("获取远端更新", fetched.stderr, fetched.stdout)
                        with self._lock:
                            self._state = replace(previous, error=error)
                        return self._state
                next_state = self._inspect(last_checked_at=time.time())
                with self._lock:
                    self._state = next_state
                return next_state
            finally:
                with self._lock:
                    self._checking = False

    async def refresh_async(self) -> UpdateState:
        with self._lock:
            owner = not self._checking
            if owner:
                self._checking = True
        if not owner:
            while True:
                with self._lock:
                    if not self._checking:
                        return self._state
                await asyncio.sleep(0.05)
        return await asyncio.to_thread(self._refresh_started, fetch=True)

    def apply(self, store_root: Path) -> ApplyResult:
        with self._operation_lock, self._lock:
            current = self._inspect(last_checked_at=self._state.last_checked_at)
            self._state = current
            if current.behind <= 0:
                raise UpdateError("当前没有可快进应用的更新，请先重新检查")
            if current.ahead > 0:
                raise UpdateError("本地存在尚未推送的提交，无法自动更新")
            if not current.fast_forward:
                raise UpdateError("本地与远端历史已分叉，无法自动快进")
            if current.dirty:
                raise UpdateError("工作区有未提交改动，请先提交或贮藏")
            assert current.head is not None and current.upstream is not None
            path = sentinel_path(store_root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(current.head + "\n", encoding="utf-8")
            merged = self._run(["merge", "--ff-only", current.upstream], timeout=120)
            if merged.returncode != 0:
                path.unlink(missing_ok=True)
                raise UpdateError(_failure("快进更新", merged.stderr, merged.stdout))
            next_state = self._inspect(last_checked_at=time.time())
            self._state = next_state
            if next_state.head is None:
                path.unlink(missing_ok=True)
                raise UpdateError("更新完成后无法读取新的版本号")
            changed = self._run(
                ["diff", "--name-only", current.head, next_state.head], timeout=30
            )
            paths = tuple(
                line.strip() for line in changed.stdout.splitlines() if line.strip()
            ) if changed.returncode == 0 else ()
            return ApplyResult(
                old_head=current.head,
                new_head=next_state.head,
                changed_paths=paths,
                restart_commands=_restart_commands(self.repo_root, paths),
            )

    def schedule_exit(self, store_root: Path) -> None:
        self._exit_scheduler(store_root)
