"""Host-local SSH transport for authoritative remote project worktrees."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import stat
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .domain import RemoteAccessConfig


_ROOT_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")

# ssh(1) exits 255 for its own failures; reuse it so callers that tolerate
# git's exit code 1 never mistake a dead transport for an empty result.
SSH_TRANSPORT_FAILURE = 255

# sockaddr_un.sun_path holds 104 bytes on macOS and 108 on Linux; the smaller
# limit applies everywhere. OpenSSH binds "<ControlPath>.<16 random chars>"
# and renames it into place, so the configured path must reserve that suffix.
_CONTROL_PATH_LIMIT = 103
_CONTROL_TEMP_SUFFIX = 17
_CONTROL_DIGEST_MIN = 12
_CONTROL_DIGEST_MAX = 32


def _prepare_socket_dir(directory: Path) -> bool:
    """Create a private socket directory, rejecting one we do not own."""
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
    except OSError:
        return False
    # lstat, not stat: a symlink planted in a shared /tmp must not be adopted.
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        return False
    if info.st_mode & 0o077:
        try:
            directory.chmod(0o700)
        except OSError:
            return False
    return True


def control_socket_path(digest: str) -> Path | None:
    """Choose a control path that fits sun_path, or None to skip multiplexing.

    macOS resolves TMPDIR to a ~48-character per-user folder, so the socket
    directory plus a full digest plus OpenSSH's rename suffix overflows the
    104-byte limit and every command fails before it reaches the remote host.
    Prefer TMPDIR, which is private, and fall back to shorter shared roots
    only when the budget leaves too few digest characters to stay unique.
    """
    name = f"miniclaw2-ssh-{os.getuid()}"
    for base in dict.fromkeys([tempfile.gettempdir(), "/tmp", "/var/tmp"]):
        directory = Path(base) / name
        budget = (
            _CONTROL_PATH_LIMIT - _CONTROL_TEMP_SUFFIX - len(str(directory)) - 1
        )
        if budget < _CONTROL_DIGEST_MIN:
            continue
        if not _prepare_socket_dir(directory):
            continue
        return directory / digest[: min(budget, _CONTROL_DIGEST_MAX)]
    return None


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
        self.control_path = control_socket_path(digest)

    def command(self, args: list[str]) -> list[str]:
        """Build a session command using this project's shared SSH master."""
        return [*self._ssh_prefix(), self.access.ssh_target, shlex.join(args)]

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
                returncode=SSH_TRANSPORT_FAILURE,
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
                returncode=SSH_TRANSPORT_FAILURE,
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

    def initialize_repository(self, root_path: str, mode: str, clone_url: str | None) -> None:
        if mode not in {"cloned", "init"}:
            raise ValueError("远端初始化方式无效")
        if mode == "cloned":
            if not clone_url or clone_url.startswith("-") or any(c in clone_url for c in "\x00\r\n"):
                raise ValueError("克隆源地址无效")
            parsed = urlsplit(clone_url)
            if parsed.password or parsed.query or parsed.fragment or (parsed.scheme in {"http", "https"} and parsed.username):
                raise ValueError("克隆源不能包含凭据或查询参数，请通过远端 Git/SSH 配置认证")
        elif clone_url:
            raise ValueError("git init 不接受克隆源地址")
        result = self.run([
            "python3", "-c", _INITIALIZE_REPOSITORY, root_path, mode,
            clone_url or "", uuid.uuid4().hex,
        ], timeout=120)
        if result.returncode:
            raise RemoteTransportError(self._failure(
                f"远端初始化失败；未删除目录，请检查 {root_path}", result,
            ))

    def probe_repository(self, root_path: str) -> RemoteRepositoryProbe:
        exists = self.run(["test", "-d", root_path])
        if exists.returncode == SSH_TRANSPORT_FAILURE:
            raise RemoteTransportError(
                self._failure(f"无法通过 SSH 连接到 {self.access.ssh_target}", exists)
            )
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
        if self.control_path is None or not self.control_path.exists():
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
        ]
        if self.control_path is not None:
            args.append(f"-oControlPath={self.control_path}")
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


_INITIALIZE_REPOSITORY = r'''
import fcntl, os, pathlib, subprocess, sys
root, mode, source, identity = sys.argv[1:]
path = pathlib.Path(root)
if any(p.is_symlink() for p in [path, *path.parents]):
    raise RuntimeError("初始化路径不能经过符号链接")
path.mkdir(parents=True, exist_ok=True)
fd = os.open(path, os.O_RDONLY)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
if any(path.iterdir()):
    raise RuntimeError("仅允许初始化空目录")
def git(*args):
    subprocess.run(["git", *args], cwd=root, check=True)
if mode == "cloned":
    git("clone", "--", source, ".")
else:
    git("init")
    git("-c", "user.name=MiniClaw2", "-c", "user.email=miniclaw2@localhost",
        "-c", "commit.gpgSign=false", "-c", "core.hooksPath=/dev/null",
        "commit", "--allow-empty", "-m", "MiniClaw2 project " + identity)
exclude = path / ".git/info/exclude"
with exclude.open("a") as handle:
    handle.write("\n/.miniclaw2/\n")
'''
