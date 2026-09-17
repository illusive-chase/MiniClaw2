"""Host-local SSH transport for authoritative remote project worktrees."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .domain import RemoteAccessConfig


_ROOT_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")


class RemoteTransportError(RuntimeError):
    """A remote command or repository identity check failed."""


@dataclass(frozen=True, slots=True)
class RemoteRepositoryProbe:
    root_commits: tuple[str, ...]

    @property
    def root_commit(self) -> str:
        return self.root_commits[0]


class SSHProjectTransport:
    """Run commands over one lazily established, project-scoped SSH master."""

    def __init__(
        self,
        access: RemoteAccessConfig,
        *,
        project_id: str,
        store_root: Path,
    ) -> None:
        self.access = access
        digest = hashlib.sha256(
            f"{store_root.resolve(strict=False)}\0{project_id}\0{os.getpid()}".encode()
        ).hexdigest()[:32]
        socket_dir = Path(tempfile.gettempdir()) / f"miniclaw2-ssh-{os.getuid()}"
        socket_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            socket_dir.chmod(0o700)
        except OSError:
            pass
        self.control_path = socket_dir / digest

    def run(
        self,
        args: list[str],
        *,
        timeout: float = 15,
    ) -> subprocess.CompletedProcess[str]:
        command = shlex.join(args)
        try:
            return subprocess.run(
                [*self._ssh_prefix(), self.access.ssh_target, command],
                check=False,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            return subprocess.CompletedProcess(
                args=["ssh", self.access.ssh_target, command],
                returncode=1,
                stdout="",
                stderr=str(exc),
            )

    def run_bytes(
        self,
        args: list[str],
        *,
        input_data: bytes | None = None,
        timeout: float = 60,
    ) -> subprocess.CompletedProcess[bytes]:
        command = shlex.join(args)
        try:
            return subprocess.run(
                [*self._ssh_prefix(), self.access.ssh_target, command],
                check=False,
                capture_output=True,
                input=input_data,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            return subprocess.CompletedProcess(
                args=["ssh", self.access.ssh_target, command],
                returncode=1,
                stdout=b"",
                stderr=str(exc).encode("utf-8", errors="replace"),
            )

    def export_tracked_files(self, root_path: str) -> bytes:
        """Return a tar archive of the current contents of tracked files."""
        tracked = self.run_bytes(
            ["git", "-C", root_path, "ls-files", "--cached", "-z"]
        )
        if tracked.returncode != 0:
            raise RemoteTransportError(
                self._bytes_failure("无法列出远端 Git 跟踪文件", tracked)
            )
        deleted = self.run_bytes(
            ["git", "-C", root_path, "ls-files", "--deleted", "-z"]
        )
        if deleted.returncode != 0:
            raise RemoteTransportError(
                self._bytes_failure("无法识别远端已删除文件", deleted)
            )
        deleted_paths = set(deleted.stdout.split(b"\0"))
        tracked_paths = [
            path
            for path in tracked.stdout.split(b"\0")
            if path and path not in deleted_paths
        ]
        file_list = b"\0".join(tracked_paths)
        if file_list:
            file_list += b"\0"
        archive = self.run_bytes(
            [
                "tar",
                "-C",
                root_path,
                "--null",
                "--no-recursion",
                "-T",
                "-",
                "-cf",
                "-",
            ],
            input_data=file_list,
        )
        if archive.returncode != 0:
            raise RemoteTransportError(
                self._bytes_failure("无法读取远端 Git 跟踪文件", archive)
            )
        return archive.stdout

    def probe_repository(self, root_path: str) -> RemoteRepositoryProbe:
        exists = self.run(["test", "-d", root_path])
        if exists.returncode != 0:
            raise RemoteTransportError(
                self._failure(f"远端目录不存在或不可访问：{root_path}", exists)
            )

        repository = self.run(
            ["git", "-C", root_path, "rev-parse", "--is-inside-work-tree"]
        )
        if repository.returncode != 0 or repository.stdout.strip() != "true":
            raise RemoteTransportError(
                self._failure(f"远端路径不是 Git 工作树：{root_path}", repository)
            )

        roots = self.run(
            ["git", "-C", root_path, "rev-list", "--max-parents=0", "HEAD"]
        )
        if roots.returncode != 0:
            raise RemoteTransportError(
                self._failure("无法读取远端仓库根提交", roots)
            )
        raw_roots = [line.strip().lower() for line in roots.stdout.splitlines()]
        if any(not _ROOT_COMMIT_RE.fullmatch(value) for value in raw_roots):
            raise RemoteTransportError("远端仓库返回了无效的根提交")
        root_commits = tuple(sorted(set(raw_roots)))
        if not root_commits:
            raise RemoteTransportError("远端仓库没有根提交")
        return RemoteRepositoryProbe(root_commits=root_commits)

    def close(self) -> None:
        if not self.control_path.exists():
            return
        try:
            subprocess.run(
                [
                    *self._ssh_prefix(include_master=False),
                    "-O",
                    "exit",
                    self.access.ssh_target,
                ],
                check=False,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=5,
            )
        except Exception:  # noqa: BLE001
            pass

    def _ssh_prefix(self, *, include_master: bool = True) -> list[str]:
        args = [
            "ssh",
            "-T",
            "-oBatchMode=yes",
            "-oConnectTimeout=10",
            "-oForwardAgent=no",
            "-oClearAllForwardings=yes",
            f"-oControlPath={self.control_path}",
        ]
        if include_master:
            args.extend(["-oControlMaster=auto", "-oControlPersist=60"])
        if self.access.connect_via is not None:
            args.extend(["-J", self.access.connect_via])
        return args

    @staticmethod
    def _failure(summary: str, result: subprocess.CompletedProcess[str]) -> str:
        detail = (result.stderr or result.stdout).strip()
        return f"{summary}：{detail}" if detail else summary

    @staticmethod
    def _bytes_failure(
        summary: str, result: subprocess.CompletedProcess[bytes]
    ) -> str:
        detail = (result.stderr or result.stdout).decode(
            "utf-8", errors="replace"
        ).strip()
        return f"{summary}：{detail}" if detail else summary


class RemoteTransportPool:
    """Own transports so one project reuses one SSH control connection."""

    def __init__(self, store_root: Path) -> None:
        self._store_root = store_root
        self._lock = threading.Lock()
        self._transports: dict[str, SSHProjectTransport] = {}

    def get(
        self, project_id: str, access: RemoteAccessConfig
    ) -> SSHProjectTransport:
        with self._lock:
            existing = self._transports.get(project_id)
            if existing is not None and existing.access == access:
                return existing
            if existing is not None:
                existing.close()
            transport = SSHProjectTransport(
                access,
                project_id=project_id,
                store_root=self._store_root,
            )
            self._transports[project_id] = transport
            return transport

    def close(self, project_id: str) -> None:
        with self._lock:
            transport = self._transports.pop(project_id, None)
        if transport is not None:
            transport.close()

    def close_all(self) -> None:
        with self._lock:
            transports = list(self._transports.values())
            self._transports.clear()
        for transport in transports:
            transport.close()
