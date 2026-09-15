"""Machine identity and explicit git synchronization for the metadata store."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import plistlib
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


from .tags import TAGS_FILENAME
from .migrations.catalog import CURRENT_VERSION
from .migrations.errors import MigrationError
from .migrations.inventory import LEGACY_LOCAL_FILES


MACHINE_FILENAME = "machine.json"
SCHEMA_FILENAME = "schema.json"
SCHEMA_VERSION = CURRENT_VERSION
SCHEMA_NAME = "miniclaw2-store"
DEFAULT_COMMIT_DEBOUNCE_SECONDS = 30.0
REMOTE_CHECK_TIMEOUT_SECONDS = 30.0


logger = logging.getLogger(__name__)


class SyncError(RuntimeError):
    """A safe, user-facing sync failure."""


class SchemaConflictError(SyncError):
    """The store schema changed independently on both sides."""


class MachineIdentityMismatchError(SyncError):
    """The local identity cannot safely be used on this device."""


@dataclass(frozen=True)
class MachineIdentity:
    id: str
    hostname: str
    label: str
    last_sync_at: float | None = None
    last_synced_commit: str | None = None
    sync_pending: bool = False
    device_fingerprint: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "hostname": self.hostname,
            "label": self.label,
            "last_sync_at": self.last_sync_at,
            "last_synced_commit": self.last_synced_commit,
            "sync_pending": self.sync_pending,
            "device_fingerprint": self.device_fingerprint,
        }


def current_hostname() -> str:
    return socket.gethostname() or "unknown-machine"


def current_device_fingerprint() -> str | None:
    try:
        if sys.platform == "darwin":
            result = subprocess.run(
                ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice", "-a"],
                capture_output=True, check=True, timeout=5,
            )
            identifier = plistlib.loads(result.stdout)[0]["IOPlatformUUID"]
        elif sys.platform == "win32":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
            ) as key:
                identifier = winreg.QueryValueEx(key, "MachineGuid")[0]
        else:
            identifier = ""
            for path in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
                try:
                    identifier = path.read_text(encoding="utf-8").strip()
                except OSError:
                    continue
                if identifier:
                    break
        if isinstance(identifier, str) and identifier.strip():
            return hashlib.sha256(identifier.strip().encode("utf-8")).hexdigest()
    except (OSError, ValueError, TypeError, KeyError, IndexError, subprocess.SubprocessError):
        pass
    return None


def machine_path(root: Path) -> Path:
    return root / MACHINE_FILENAME


@contextmanager
def _machine_identity_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "machine.lock").open("a+b") as lock_file:
        if sys.platform == "win32":
            import msvcrt

            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_machine_identity(root: Path) -> MachineIdentity:
    path = machine_path(root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        machine_id = payload["id"]
        hostname = payload["hostname"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SyncError(f"invalid machine identity {path}: {exc}") from exc
    if not isinstance(machine_id, str) or not machine_id.strip():
        raise SyncError(f"invalid machine id in {path}")
    if not isinstance(hostname, str) or not hostname.strip():
        raise SyncError(f"invalid hostname in {path}")
    label = payload.get("label", hostname)
    if not isinstance(label, str) or not label.strip():
        label = hostname
    last_sync_at = payload.get("last_sync_at")
    if not isinstance(last_sync_at, (int, float)):
        last_sync_at = None
    last_synced_commit = payload.get("last_synced_commit")
    if not isinstance(last_synced_commit, str) or not last_synced_commit:
        last_synced_commit = None
    return MachineIdentity(
        id=machine_id,
        hostname=hostname,
        label=label,
        last_sync_at=float(last_sync_at) if last_sync_at is not None else None,
        last_synced_commit=last_synced_commit,
        sync_pending=payload.get("sync_pending") is True,
        device_fingerprint=(
            payload.get("device_fingerprint")
            if isinstance(payload.get("device_fingerprint"), str)
            else None
        ),
    )


def ensure_machine_identity(root: Path) -> MachineIdentity:
    with _machine_identity_lock(root):
        identity = _ensure_machine_identity_locked(root)
        if not schema_is_newer(root):
            _update_owned_project_labels(root, identity)
        return identity


def _ensure_machine_identity_locked(root: Path) -> MachineIdentity:
    path = machine_path(root)
    fingerprint = current_device_fingerprint()
    if path.exists():
        identity = load_machine_identity(root)
        hostname = current_hostname()
        if identity.device_fingerprint and fingerprint:
            if identity.device_fingerprint != fingerprint:
                return _new_machine_identity(root, fingerprint=fingerprint)
        elif identity.hostname != hostname:
            raise MachineIdentityMismatchError(
                "无法区分设备改名与存储副本；请先运行 "
                "`python -m miniclaw2 machine rename` 确认同一设备，或 "
                "`python -m miniclaw2 machine copy` 为新设备生成身份"
            )
        if identity.device_fingerprint and not fingerprint:
            raise MachineIdentityMismatchError("无法验证当前设备身份，请恢复系统设备标识读取后重试")
        if identity.hostname != hostname or identity.device_fingerprint != fingerprint:
            identity = replace(
                identity,
                hostname=hostname,
                label=hostname if identity.label == identity.hostname else identity.label,
                device_fingerprint=fingerprint,
            )
            _write_json(path, identity.payload())
        return identity
    return _new_machine_identity(root, fingerprint=fingerprint)


def machine_hostname_mismatch(identity: MachineIdentity) -> bool:
    return identity.hostname != current_hostname()


def resolve_machine_rename(root: Path, *, label: str | None = None) -> MachineIdentity:
    with _machine_identity_lock(root):
        identity = load_machine_identity(root)
        fingerprint = current_device_fingerprint()
        if identity.device_fingerprint and identity.device_fingerprint != fingerprint:
            raise MachineIdentityMismatchError(
                "无法确认是同一设备；请恢复系统设备标识读取，或运行 "
                "`python -m miniclaw2 machine copy` 为新设备生成身份"
            )
        hostname = current_hostname()
        default_label = hostname if identity.label == identity.hostname else identity.label
        updated = replace(
            identity,
            hostname=hostname,
            label=(label or default_label).strip() or default_label,
            device_fingerprint=fingerprint,
        )
        _write_json(machine_path(root), updated.payload())
        if not schema_is_newer(root):
            _update_owned_project_labels(root, updated)
        return updated


def resolve_machine_copy(root: Path, *, label: str | None = None) -> MachineIdentity:
    with _machine_identity_lock(root):
        return _new_machine_identity(root, fingerprint=current_device_fingerprint(), label=label)


def _new_machine_identity(
    root: Path, *, fingerprint: str | None, label: str | None = None,
) -> MachineIdentity:
    hostname = current_hostname()
    updated = MachineIdentity(
        id=str(uuid4()),
        hostname=hostname,
        label=(label or hostname).strip() or hostname,
        device_fingerprint=fingerprint,
    )
    _write_json(machine_path(root), updated.payload())
    return updated


def ensure_store_metadata(root: Path, identity: MachineIdentity) -> None:
    from .migrations.coordinator import coordinator

    coordinator(root).apply(identity.id)
    ensure_store_gitignore(root)


def _configured_contextspace_root(root: Path) -> Path:
    from .migrations.inventory import context_root

    return context_root(root)


def schema_is_newer(root: Path) -> bool:
    path = root / SCHEMA_FILENAME
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        version = payload.get("schema_version")
        if type(version) is not int:
            return True
        return version > SCHEMA_VERSION
    except (OSError, ValueError, TypeError):
        return True


def ensure_store_gitignore(root: Path) -> None:
    from .migrations.inventory import IGNORED_PATTERNS

    path = root / ".gitignore"
    required = IGNORED_PATTERNS
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [entry for entry in required if entry not in existing]
    if not missing:
        return
    content = "\n".join([*existing, *missing]).strip() + "\n"
    path.write_text(content, encoding="utf-8")


def bootstrap_store(root: Path, remote_url: str) -> MachineIdentity:
    """Bootstrap a fresh machine by cloning, or initialize an empty remote."""
    remote_url = remote_url.strip()
    if not remote_url:
        raise SyncError("git remote URL is required")
    root = root.expanduser()
    existing = list(root.iterdir()) if root.exists() else []
    if existing:
        raise SyncError(f"fresh-machine bootstrap requires an empty directory: {root}")
    if root.exists():
        root.rmdir()
    clone = _run_raw(["git", "clone", remote_url, str(root)], check=False)
    if clone.returncode != 0:
        root.mkdir(parents=True, exist_ok=True)
        probe = _run_raw(["git", "ls-remote", remote_url], check=False)
        if probe.returncode != 0:
            raise SyncError(_command_error("cannot access remote", probe))
        _git_init(root)
        _git(root, "remote", "add", "origin", remote_url)
    elif _git(root, "rev-parse", "--verify", "HEAD", check=False).returncode != 0:
        remote_main = _git(
            root, "rev-parse", "--verify", "origin/main", check=False
        )
        if remote_main.returncode == 0:
            _git(root, "checkout", "-b", "main", "origin/main")
        else:
            _git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    identity = ensure_machine_identity(root)
    ensure_store_metadata(root, identity)
    head = _git(root, "rev-parse", "--verify", "HEAD", check=False)
    if clone.returncode == 0 and head.returncode == 0:
        with _machine_identity_lock(root):
            identity = replace(
                load_machine_identity(root),
                last_sync_at=time.time(),
                last_synced_commit=head.stdout.strip(),
            )
            _write_json(machine_path(root), identity.payload())
    return identity


class SyncManager:
    """Owns local commit coalescing and user-triggered remote exchange."""

    def __init__(
        self,
        root: Path,
        identity: MachineIdentity | None = None,
        *,
        debounce_seconds: float = DEFAULT_COMMIT_DEBOUNCE_SECONDS,
    ) -> None:
        self.root = root
        self.identity = identity or ensure_machine_identity(root)
        self.debounce_seconds = debounce_seconds
        from .migrations.coordinator import open_storage

        self.coordinator = open_storage(self.root)
        self._lock = self.coordinator.mutex
        self._timer: threading.Timer | None = None
        self._pending_messages: list[str] = []
        self._pre_commit_callbacks: list[Callable[[], None]] = []
        self._success_callbacks: list[Callable[[], None]] = []
        self._publication_callbacks: list[Callable[[], None]] = []
        self.publication_generation = 0
        self._idle_callbacks: list[Callable[[], None]] = []
        self._file_commit_time_cache_head: str | None = None
        self._file_commit_time_cache: dict[Path, float | None] = {}

    def add_pre_commit_callback(self, callback: Callable[[], None]) -> None:
        if callback not in self._pre_commit_callbacks:
            self._pre_commit_callbacks.append(callback)

    def add_success_callback(self, callback: Callable[[], None]) -> None:
        if callback not in self._success_callbacks:
            self._success_callbacks.append(callback)

    def add_idle_callback(self, callback: Callable[[], None]) -> None:
        if callback not in self._idle_callbacks:
            self._idle_callbacks.append(callback)

    def add_publication_callback(self, callback: Callable[[], None]) -> None:
        if callback not in self._publication_callbacks:
            self._publication_callbacks.append(callback)

    @property
    def configured(self) -> bool:
        return (self.root / ".git").exists() and self.remote_url() is not None

    def remote_url(self) -> str | None:
        if not (self.root / ".git").exists():
            return None
        result = _git(self.root, "remote", "get-url", "origin", check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def setup_existing_store(self, remote_url: str) -> None:
        remote_url = remote_url.strip()
        if not remote_url:
            raise SyncError("git remote URL is required")
        self._ensure_contextspace_inside_store()
        with self._lock:
            self._refresh_identity()
            remote_refs = _run_raw(
                ["git", "ls-remote", "--heads", remote_url], check=False,
                timeout=REMOTE_CHECK_TIMEOUT_SECONDS,
            )
            if remote_refs.returncode != 0:
                raise SyncError(_command_error("cannot access remote", remote_refs))
            if remote_refs.stdout.strip():
                raise SyncError(
                    "both the local store and remote contain history; "
                    "v1 refuses to merge them"
                )
            ensure_store_gitignore(self.root)
            if not (self.root / ".git").exists():
                _git_init(self.root)
            current_remote = self.remote_url()
            if current_remote is None:
                _git(self.root, "remote", "add", "origin", remote_url)
            elif current_remote != remote_url:
                _git(self.root, "remote", "set-url", "origin", remote_url)
            self.commit_now("initialize metadata sync")
            branch = self._branch()
            pushed = _git(
                self.root,
                "push",
                "--set-upstream",
                "origin",
                f"HEAD:{branch}",
                check=False,
                timeout=REMOTE_CHECK_TIMEOUT_SECONDS,
            )
            if pushed.returncode != 0:
                self._record_failure()
                raise SyncError(_command_error("initial push failed", pushed))
            self._record_success()

    def schedule_commit(self, message: str) -> None:
        if not self.configured:
            return
        with self._lock:
            self._pending_messages.append(message.strip() or "update metadata")
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(
                self.debounce_seconds, self._commit_from_timer
            )
            self._timer.daemon = True
            self._timer.start()

    def commit_now(self, message: str | None = None) -> str | None:
        with self._lock:
            self.coordinator.assert_current()
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            messages = self._pending_messages
            self._pending_messages = []
            if not (self.root / ".git").exists():
                return None
            self._refresh_identity()
            ensure_store_gitignore(self.root)
            _git(self.root, "add", "-A")
            _git(self.root, "rm", "--cached", "--ignore-unmatch", "--", *sorted(LEGACY_LOCAL_FILES))
            staged = _git(self.root, "diff", "--cached", "--quiet", check=False)
            if staged.returncode == 0:
                return self._head()
            if staged.returncode != 1:
                raise SyncError(_command_error("cannot inspect staged changes", staged))
            commit_message = message or self._coalesced_message(messages)
            committed = _git(
                self.root,
                "-c",
                f"user.name=MiniClaw2 ({self.identity.label})",
                "-c",
                f"user.email=miniclaw2@{self.identity.id}.local",
                "commit",
                "-m",
                commit_message,
                check=False,
            )
            if committed.returncode != 0:
                raise SyncError(_command_error("metadata commit failed", committed))
            return self._head()

    def status(self) -> dict[str, Any]:
        configured = self.configured
        changed = False
        remote = {
            "ahead": 0,
            "behind": 0,
            "ref_at": None,
            "error": None,
        }
        if configured:
            dirty = _git(self.root, "status", "--porcelain", check=False)
            head = self._head()
            changed = bool(dirty.stdout.strip()) or (
                head is not None and head != self.identity.last_synced_commit
            ) or self.identity.sync_pending
            remote = self._remote_status(self._branch())
        return {
            "configured": configured,
            "remote_url": self.remote_url(),
            "remote": remote,
            "status": "changed" if changed else "up-to-date",
            "changed": changed,
            "last_sync_at": self.identity.last_sync_at,
            "machine_id": self.identity.id,
            "machine_label": self.identity.label,
            "hostname_mismatch": machine_hostname_mismatch(self.identity),
        }

    def check_remote(self) -> dict[str, Any]:
        if not self.configured:
            raise SyncError("metadata sync is not configured")
        with self._lock:
            branch = self._branch()
            fetched = _git(
                self.root,
                "fetch",
                "origin",
                branch,
                check=False,
                timeout=REMOTE_CHECK_TIMEOUT_SECONDS,
            )
            if fetched.returncode != 0:
                raise SyncError(_command_error("fetch failed", fetched))
            return self.status()

    def file_commit_times(self, paths: list[Path]) -> dict[Path, float]:
        """Return each path's newest committed timestamp with bounded queries."""
        if not paths or not (self.root / ".git").exists():
            return {}
        relative_paths: list[Path] = []
        for path in paths:
            try:
                relative_path = path.resolve().relative_to(self.root)
            except ValueError:
                continue
            if relative_path not in relative_paths:
                relative_paths.append(relative_path)
        if not relative_paths:
            return {}

        with self._lock:
            try:
                head = self._head()
            except SyncError:
                return {}
            if head != self._file_commit_time_cache_head:
                self._file_commit_time_cache_head = head
                self._file_commit_time_cache.clear()

            for path in relative_paths:
                if path in self._file_commit_time_cache:
                    continue
                try:
                    result = _git(
                        self.root,
                        "log",
                        "-1",
                        "--format=%ct",
                        "--",
                        path.as_posix(),
                        check=False,
                    )
                except SyncError:
                    return {}
                timestamp: float | None = None
                if result.returncode == 0 and result.stdout.strip():
                    try:
                        timestamp = float(result.stdout.strip())
                    except ValueError:
                        pass
                self._file_commit_time_cache[path] = timestamp

            return {
                path: timestamp
                for path in relative_paths
                if (timestamp := self._file_commit_time_cache[path]) is not None
            }

    def sync_now(self) -> dict[str, Any]:
        if not self.configured:
            raise SyncError("metadata sync is not configured")
        self._ensure_contextspace_inside_store()
        with self._lock:
            self.coordinator.assert_current()
            for callback in tuple(self._idle_callbacks):
                callback()
            self._refresh_identity()
            for callback in tuple(self._pre_commit_callbacks):
                try:
                    callback()
                except Exception as exc:
                    raise SyncError(f"同步前本机状态采集失败：{exc}") from exc
            self.commit_now()
            branch = self._branch()
            try:
                fetched = _git(self.root, "fetch", "origin", check=False, timeout=REMOTE_CHECK_TIMEOUT_SECONDS)
                if fetched.returncode != 0:
                    raise SyncError(_command_error("fetch failed", fetched))
                remote_ref = f"origin/{branch}"
                remote_exists = _git(
                    self.root, "rev-parse", "--verify", remote_ref, check=False
                ).returncode == 0
                if remote_exists:
                    if self._merge_remote(remote_ref):
                        self.publication_generation += 1
                        for callback in tuple(self._publication_callbacks):
                            callback()
                pushed = _git(
                    self.root,
                    "push",
                    "--set-upstream",
                    "origin",
                    f"HEAD:{branch}",
                    check=False,
                    timeout=REMOTE_CHECK_TIMEOUT_SECONDS,
                )
                if pushed.returncode != 0:
                    raise SyncError(_command_error("push failed", pushed))
            except SyncError:
                self._record_failure()
                raise
            self._record_success()
            return self.status()

    def _merge_remote(self, remote_ref: str) -> bool:
        from .migrations.sync_tree import merge_remote

        try:
            return merge_remote(self.root, remote_ref)
        except MigrationError as exc:
            raise SchemaConflictError(str(exc)) from exc

    def _remote_status(self, branch: str) -> dict[str, Any]:
        remote_ref = f"origin/{branch}"
        exists = _git(
            self.root, "rev-parse", "--verify", remote_ref, check=False
        )
        if exists.returncode != 0 or self._head() is None:
            return {"ahead": 0, "behind": 0, "ref_at": None, "error": None}

        counts = _git(
            self.root,
            "rev-list",
            "--left-right",
            "--count",
            f"HEAD...{remote_ref}",
            check=False,
        )
        if counts.returncode != 0:
            return {
                "ahead": 0,
                "behind": 0,
                "ref_at": None,
                "error": _command_error("cannot compare remote", counts),
            }
        try:
            ahead, behind = (int(value) for value in counts.stdout.split())
        except (TypeError, ValueError):
            return {
                "ahead": 0,
                "behind": 0,
                "ref_at": None,
                "error": "cannot compare remote: invalid git rev-list output",
            }

        reflog = _git(
            self.root,
            "reflog",
            "show",
            "-1",
            "--format=%ct",
            remote_ref,
            check=False,
        )
        try:
            ref_at = float(reflog.stdout.strip()) if reflog.returncode == 0 else None
        except ValueError:
            ref_at = None
        return {
            "ahead": ahead,
            "behind": behind,
            "ref_at": ref_at,
            "error": None,
        }

    def _refresh_identity(self) -> None:
        identity = load_machine_identity(self.root)
        if identity.id != self.identity.id:
            raise MachineIdentityMismatchError("设备身份已变更，请重启 MiniClaw2 后再同步")
        self.identity = identity

    def _record_success(self) -> None:
        with _machine_identity_lock(self.root):
            self._refresh_identity()
            self.identity = replace(
                self.identity,
                last_sync_at=time.time(),
                last_synced_commit=self._head(),
                sync_pending=False,
            )
            _write_json(machine_path(self.root), self.identity.payload())
        for callback in tuple(self._success_callbacks):
            callback()

    def _record_failure(self) -> None:
        with _machine_identity_lock(self.root):
            self._refresh_identity()
            self.identity = replace(self.identity, sync_pending=True)
            _write_json(machine_path(self.root), self.identity.payload())

    def _branch(self) -> str:
        upstream = _git(
            self.root,
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
            check=False,
        )
        if upstream.returncode == 0 and "/" in upstream.stdout.strip():
            return upstream.stdout.strip().split("/", 1)[1]
        branch = _git(self.root, "branch", "--show-current", check=False).stdout.strip()
        return branch or "main"

    def _ensure_contextspace_inside_store(self) -> None:
        override = os.environ.get("MINICLAW_CONTEXT_HOME")
        if override is None:
            return
        context_root = Path(override).expanduser().resolve()
        expected = (self.root / "contextspace").resolve()
        if context_root != expected:
            raise SyncError(
                "metadata sync requires ContextSpace inside MINICLAW_HOME; "
                "unset MINICLAW_CONTEXT_HOME or point it to MINICLAW_HOME/contextspace"
            )

    def _head(self) -> str | None:
        result = _git(self.root, "rev-parse", "--verify", "HEAD", check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def _commit_from_timer(self) -> None:
        try:
            self.commit_now()
        except Exception:
            # A later durable write or explicit sync retries the local commit.
            return

    @staticmethod
    def _coalesced_message(messages: list[str]) -> str:
        unique = list(dict.fromkeys(message for message in messages if message))
        if not unique:
            return "update metadata"
        if len(unique) == 1:
            return unique[0]
        return f"update metadata ({len(unique)} durable changes)"


_MANAGERS: dict[Path, SyncManager] = {}
_MANAGERS_LOCK = threading.Lock()


def get_sync_manager(root: Path, identity: MachineIdentity | None = None) -> SyncManager:
    resolved = root.expanduser().resolve()
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(resolved)
        if manager is None or (identity is not None and manager.identity.id != identity.id):
            manager = SyncManager(resolved, identity)
            _MANAGERS[resolved] = manager
        elif identity is not None:
            with manager._lock:
                manager.identity = identity
        return manager


def _git(
    root: Path,
    *args: str,
    check: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    result = _run_raw(
        ["git", "-C", str(root), *args], check=False, timeout=timeout
    )
    if check and result.returncode != 0:
        raise SyncError(_command_error(f"git {' '.join(args)} failed", result))
    return result


def _git_init(root: Path) -> None:
    initialized = _run_raw(["git", "init", "-b", "main", str(root)], check=False)
    if initialized.returncode != 0:
        _run_raw(["git", "init", str(root)])
        _git(root, "checkout", "-b", "main")


def _run_raw(
    command: list[str], *, check: bool = True, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError as exc:
        raise SyncError("git is not installed or not available on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise SyncError(_command_error("git command failed", exc)) from exc
    except subprocess.TimeoutExpired as exc:
        detail = f" after {timeout:g} seconds" if timeout is not None else ""
        raise SyncError(f"git command timed out{detail}") from exc


def _command_error(prefix: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout or "unknown git error").strip()
    return f"{prefix}: {detail}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _update_owned_project_labels(root: Path, identity: MachineIdentity) -> None:
    for project_file in (root / "projects").glob("*/project.json"):
        host_file = project_file.parent / "hosts" / identity.id / "host.json"
        if host_file.is_file():
            try:
                host_payload = json.loads(host_file.read_text(encoding="utf-8"))
                if isinstance(host_payload, dict) and host_payload.get("label") != identity.label:
                    host_payload["label"] = identity.label
                    _write_json(host_file, host_payload)
            except (OSError, ValueError):
                logger.warning("无法更新设备标签：%s", host_file)
        try:
            payload = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict) or payload.get("machine_id") != identity.id:
            continue
        if payload.get("machine_label") == identity.label:
            continue
        payload["machine_label"] = identity.label
        _write_json(project_file, payload)
