"""Machine identity and explicit git synchronization for the metadata store."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import plistlib
import re
import signal
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
# Each host publishes its sync checkpoints on a branch only it writes. The
# branch never merges into the metadata history, so publishing one creates
# nothing for any other host to pull.
BEACON_REF_PREFIX = "refs/heads/_hosts/"
BEACON_TRACKING_PREFIX = "refs/remotes/origin/_hosts/"
# Host-local, keyed by machine id so a copied store (``machine copy``) does
# not hand the old identity's lease to the new one. Never matched by a push
# refspec, so never published.
BEACON_LOCAL_PREFIX = "refs/miniclaw2/beacon/"
BEACON_PENDING_PREFIX = "refs/miniclaw2/beacon-pending/"
BEACON_FILENAME = "beacon.json"
BEACON_SCHEMA = 1
# Transfers are bounded by silence rather than total size: a slow link that
# keeps reporting progress may run long, a hung connection fails quickly.
REMOTE_STALL_TIMEOUT_SECONDS = 60.0
REMOTE_TRANSFER_TIMEOUT_SECONDS = 30 * 60.0
_TRANSFER_PROGRESS = re.compile(
    r"^(?:remote: )?(?:[A-Z][\w ]*:\s+\d|Total \d|Delta compression )"
)
_PROGRESS_DETAILS = {
    "idle": "未在同步",
    "preparing": "正在采集并提交本机状态",
    "fetching": "正在拉取远端元数据",
    "merging": "正在比较并合并元数据",
    "normalizing_remote": "正在读取并迁移远端快照",
    "normalizing_local": "正在读取并迁移本机快照",
    "normalizing_base": "正在读取并迁移共同祖先快照",
    "publishing": "正在校验并发布合并结果",
    "pushing": "正在推送元数据",
    "finishing": "正在记录同步结果",
}


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
        return _ensure_machine_identity_locked(root)


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
        if label and _schema_is_current(root):
            _rename_host_profile(root, updated)
        return updated


def update_machine_label(root: Path, machine_id: str, label: str) -> MachineIdentity | None:
    """Refresh the local label cache from this host's profile."""
    with _machine_identity_lock(root):
        identity = load_machine_identity(root)
        if identity.id != machine_id:
            return None
        if identity.label == label:
            return identity
        updated = replace(identity, label=label)
        _write_json(machine_path(root), updated.payload())
        return updated


def _schema_is_current(root: Path) -> bool:
    try:
        payload = json.loads((root / SCHEMA_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("schema_version") == SCHEMA_VERSION


def _rename_host_profile(root: Path, identity: MachineIdentity) -> None:
    from .host_profile import load_profile, new_profile, save_profile

    profile = load_profile(root, identity.id) or new_profile(
        identity.id, label=identity.label, hostname=identity.hostname,
    )
    save_profile(root, profile.model_copy(update={
        "label": identity.label, "hostname": identity.hostname, "updated_at": time.time(),
    }))


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
    clone = _run_transfer(
        ["git", "clone", "--progress", remote_url, str(root)], label="git clone",
    )
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
    """Owns local commit coalescing, user-triggered exchange, and read-only probes.

    Two locks, always taken in this order when both are needed:

    - ``_lock`` is the storage coordinator's mutex. Every guarded Store method
      takes it, so holding it across network IO stalls the whole store.
    - ``_remote_refs_lock`` guards only ``refs/remotes/*``. A background probe
      takes this one alone and never the storage lock; ``sync_now`` holds both
      so a probe cannot move the tracking ref under an in-flight merge.
    """

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
        self._remote_refs_lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._pending_messages: list[str] = []
        # When the oldest message in the debounce window was queued; the
        # window is in memory only and at most ``debounce_seconds`` long.
        self._pending_since: float | None = None
        self._pre_commit_callbacks: list[Callable[[], None]] = []
        self._success_callbacks: list[Callable[[], None]] = []
        self._publication_callbacks: list[Callable[[], None]] = []
        self.publication_generation = 0
        self._idle_callbacks: list[Callable[[], None]] = []
        self._file_commit_time_cache_head: str | None = None
        self._file_commit_time_cache: dict[Path, float | None] = {}
        self.progress: dict[str, Any] = {"phase": "idle", "detail": "未在同步", "started_at": None, "phase_started_at": None}
        #: Extra self-reported facts for this host's beacon (source version,
        #: daemon mode). Set by the app; the store knows nothing about them.
        self.beacon_extras: Callable[[], dict[str, Any]] | None = None
        #: Why the last beacon push failed while the metadata push succeeded.
        self.last_beacon_error: str | None = None

    def _set_progress(self, phase: str) -> None:
        now = time.time()
        self.progress = {
            "phase": phase, "detail": _PROGRESS_DETAILS[phase],
            "started_at": None if phase == "idle" else now if phase == "preparing" else self.progress["started_at"],
            "phase_started_at": None if phase == "idle" else now,
        }

    def _report_transfer(self, line: str) -> None:
        phase = self.progress["phase"]
        if phase in ("fetching", "pushing"):
            self.progress = {**self.progress, "detail": f"{_PROGRESS_DETAILS[phase]}：{line}"}

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
            pushed = _git_transfer(
                self.root, "push", "--set-upstream", "origin", f"HEAD:{branch}",
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
            if self._pending_since is None:
                self._pending_since = time.time()
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
            self._pending_since = None
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
            name, email = self.author
            committed = _git(
                self.root,
                "-c",
                f"user.name={name}",
                "-c",
                f"user.email={email}",
                "commit",
                "-m",
                commit_message,
                check=False,
            )
            if committed.returncode != 0:
                raise SyncError(_command_error("metadata commit failed", committed))
            return self._head()

    @property
    def author(self) -> tuple[str, str]:
        """This host's Git identity; the email is how commits map to hosts."""
        return f"MiniClaw2 ({self.identity.label})", host_email(self.identity.id)

    def status(self) -> dict[str, Any]:
        configured = self.configured
        changed = False
        dirty = False
        remote = {
            "ahead": 0,
            "behind": 0,
            "ref_at": None,
            "error": None,
        }
        debt: dict[str, Any] = {"unpushed_commits": 0, "unpushed_since": None}
        if configured:
            # Read-only callers (the background overview) run beside commits;
            # without this flag `status` takes index.lock to refresh stat data.
            porcelain = _git(self.root, "--no-optional-locks", "status", "--porcelain", check=False)
            dirty = bool(porcelain.stdout.strip())
            head = self._head()
            changed = dirty or (
                head is not None and head != self.identity.last_synced_commit
            ) or self.identity.sync_pending
            branch = self._branch()
            remote = self._remote_status(branch)
            debt = self._unpushed(branch)
        dirty_since = (self._pending_since or time.time()) if dirty else None
        since_candidates = [value for value in (debt["unpushed_since"], dirty_since) if value is not None]
        return {
            "configured": configured,
            "remote_url": self.remote_url(),
            "remote": remote,
            "status": "changed" if changed else "up-to-date",
            "changed": changed,
            "dirty": dirty,
            "unpushed_commits": debt["unpushed_commits"],
            "unpushed_since": debt["unpushed_since"],
            "debt_since": min(since_candidates) if since_candidates else None,
            "last_sync_failed": self.identity.sync_pending,
            "last_synced_commit": self.identity.last_synced_commit,
            "last_sync_at": self.identity.last_sync_at,
            "machine_id": self.identity.id,
            "machine_label": self.identity.label,
            "hostname_mismatch": machine_hostname_mismatch(self.identity),
            "beacon_error": self.last_beacon_error,
        }

    def _unpushed(self, branch: str) -> dict[str, Any]:
        """What this host has that the remote does not, derived from Git.

        ``origin/<branch>`` only ever moves forward to commits the remote
        holds, and other hosts' work lands on its far side, so the range is
        exact without a fetch.
        """
        tracking = f"refs/remotes/origin/{branch}"
        exists = _git(self.root, "rev-parse", "--verify", "--quiet", tracking, check=False).returncode == 0
        if self._head() is None:
            return {"unpushed_commits": 0, "unpushed_since": None}
        revision = f"{tracking}..HEAD" if exists else "HEAD"
        listed = _git(self.root, "log", "--format=%ct", revision, check=False)
        if listed.returncode != 0:
            return {"unpushed_commits": 0, "unpushed_since": None}
        stamps = [float(line) for line in listed.stdout.split() if line.strip().isdigit()]
        return {
            "unpushed_commits": len(stamps),
            "unpushed_since": min(stamps) if stamps else None,
        }

    def check_remote(self) -> dict[str, Any]:
        """Fetch the metadata branch on request; never takes the storage lock."""
        if not self.configured:
            raise SyncError("metadata sync is not configured")
        with self._remote_refs_lock:
            branch = self._branch()
            fetched = _git_transfer(
                self.root, "fetch", "origin", *_probe_fetch_refspecs(branch),
                config=_PROBE_GIT_CONFIG,
            )
            if fetched.returncode != 0:
                raise SyncError(_command_error("fetch failed", fetched))
        return self.status()

    def probe_remote(self) -> bool | None:
        """Read-only sniff of the remote; True when tracking refs moved.

        Compares a cheap ``ls-remote`` against the local tracking refs and
        fetches only on a difference. It never merges, never touches the
        worktree or local branches, and never pushes. Returns None when a
        sync or another probe already holds the remote refs.
        """
        if not self.configured:
            return False
        if not self._remote_refs_lock.acquire(blocking=False):
            return None
        try:
            branch = self._branch()
            listed = _run_raw(
                ["git", "-C", str(self.root), "ls-remote", "origin",
                 f"refs/heads/{branch}", f"{BEACON_REF_PREFIX}*"],
                check=False, timeout=REMOTE_CHECK_TIMEOUT_SECONDS, env=_probe_env(),
            )
            if listed.returncode != 0:
                raise SyncError(_command_error("ls-remote failed", listed))
            remote_refs: dict[str, str] = {}
            for line in listed.stdout.splitlines():
                sha, _, ref = line.partition("\t")
                if ref.startswith("refs/heads/"):
                    remote_refs["refs/remotes/origin/" + ref.removeprefix("refs/heads/")] = sha.strip()
            local_refs = self._tracking_refs(branch)
            if remote_refs == local_refs:
                return False
            fetched = _git_transfer(
                self.root, "fetch", "--prune", "origin", *_probe_fetch_refspecs(branch),
                config=_PROBE_GIT_CONFIG,
            )
            if fetched.returncode != 0:
                raise SyncError(_command_error("fetch failed", fetched))
            return True
        finally:
            self._remote_refs_lock.release()

    def _tracking_refs(self, branch: str) -> dict[str, str]:
        listed = _git(
            self.root, "for-each-ref", "--format=%(objectname) %(refname)",
            f"refs/remotes/origin/{branch}", BEACON_TRACKING_PREFIX, check=False,
        )
        refs: dict[str, str] = {}
        for line in listed.stdout.splitlines():
            sha, _, ref = line.partition(" ")
            if ref == f"refs/remotes/origin/{branch}" or ref.startswith(BEACON_TRACKING_PREFIX):
                refs[ref] = sha
        return refs

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
        with self._lock, self._remote_refs_lock:
            self._set_progress("preparing")
            try:
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
                    self._set_progress("fetching")
                    fetched = _git_transfer(
                        self.root, "fetch", "--prune", "origin", on_progress=self._report_transfer,
                    )
                    if fetched.returncode != 0:
                        raise SyncError(_command_error("fetch failed", fetched))
                    remote_ref = f"origin/{branch}"
                    remote_exists = _git(
                        self.root, "rev-parse", "--verify", remote_ref, check=False
                    ).returncode == 0
                    if remote_exists:
                        self._set_progress("merging")
                        if self._merge_remote(remote_ref):
                            self.publication_generation += 1
                            for callback in tuple(self._publication_callbacks):
                                callback()
                    self._set_progress("pushing")
                    self._push_with_beacon(branch, remote_ref if remote_exists else None)
                except SyncError:
                    self._record_failure()
                    raise
                self._set_progress("finishing")
                self._record_success()
                return self.status()
            finally:
                self._set_progress("idle")

    def _push_with_beacon(self, branch: str, remote_ref: str | None) -> None:
        """Push the metadata branch and this host's beacon in one transaction.

        Atomicity is what lets a beacon claim "this host had integrated X":
        the beacon never lands without the commit it names. If the atomic
        push fails, the branch is retried alone so a beacon problem (a server
        without ``--atomic``, a lease held by a cloned identity) never fails
        the sync itself; the beacon follows only once the branch is on the
        remote, and a failure there is recorded as a warning.
        """
        beacon_ref = BEACON_REF_PREFIX + self.identity.id
        previous = self._own_beacon()
        try:
            beacon = self._beacon_commit(remote_ref, previous)
        except SyncError as exc:
            logger.warning("无法生成同步信标：%s", exc)
            beacon, self.last_beacon_error = None, str(exc)
        main_refspec = f"HEAD:refs/heads/{branch}"
        if beacon is not None:
            _git(self.root, "update-ref", BEACON_PENDING_PREFIX + self.identity.id, beacon)
            lease = f"--force-with-lease={beacon_ref}:{previous or ''}"
            pushed = _git_transfer(
                self.root, "push", "--atomic", "--porcelain", "--set-upstream", lease, "origin",
                main_refspec, f"{beacon}:{beacon_ref}", on_progress=self._report_transfer,
            )
            if pushed.returncode == 0:
                self._beacon_published(beacon)
                return
        pushed = _git_transfer(
            self.root, "push", "--set-upstream", "origin", main_refspec,
            on_progress=self._report_transfer,
        )
        if pushed.returncode != 0:
            raise SyncError(_command_error("push failed", pushed))
        if beacon is None:
            return
        published = _git_transfer(
            self.root, "push", "--porcelain", f"--force-with-lease={beacon_ref}:{previous or ''}",
            "origin", f"{beacon}:{beacon_ref}",
        )
        if published.returncode == 0:
            self._beacon_published(beacon)
            return
        _git(self.root, "update-ref", "-d", BEACON_PENDING_PREFIX + self.identity.id, check=False)
        detail = (published.stdout + "\n" + published.stderr).strip()
        self.last_beacon_error = (
            "同步信标被拒绝：远端信标已被另一台使用本机身份的设备更新"
            if "stale info" in detail
            else f"同步信标推送失败：{detail or '未知错误'}"
        )
        logger.warning("%s", self.last_beacon_error)

    def _own_beacon(self) -> str | None:
        """The beacon this host last published — the lease every push holds.

        It is kept in a host-local ref because the tracking ref follows the
        remote, and a remote rewritten by a cloned identity is exactly what
        the lease exists to notice. A push whose acknowledgement was lost
        leaves its commit in the pending ref; finding that same commit on the
        remote proves it was ours. With no local record at all (first sync on
        this build) the remote's beacon is trusted once.
        """
        own = self._ref(BEACON_LOCAL_PREFIX + self.identity.id)
        pending_ref = BEACON_PENDING_PREFIX + self.identity.id
        pending = self._ref(pending_ref)
        remote = self._ref(BEACON_TRACKING_PREFIX + self.identity.id)
        if pending is not None and pending == remote:
            self._beacon_published(pending)
            return pending
        if pending is not None:
            _git(self.root, "update-ref", "-d", pending_ref, check=False)
        return own if own is not None else remote

    def _beacon_published(self, beacon: str) -> None:
        _git(self.root, "update-ref", BEACON_LOCAL_PREFIX + self.identity.id, beacon)
        _git(self.root, "update-ref", "-d", BEACON_PENDING_PREFIX + self.identity.id, check=False)
        self.last_beacon_error = None

    def _beacon_commit(self, remote_ref: str | None, previous: str | None) -> str:
        head = self._head()
        if head is None:
            raise SyncError("本机元数据尚无提交，无法生成同步信标")
        _, email = self.author
        contributed = 0
        if remote_ref is not None:
            counted = _git(
                self.root, "rev-list", "--count", "--no-merges", "--fixed-strings",
                f"--author=<{email}>", f"{remote_ref}..HEAD", check=False,
            )
            contributed = int(counted.stdout.strip() or 0) if counted.returncode == 0 else 0
        else:
            counted = _git(self.root, "rev-list", "--count", "HEAD", check=False)
            contributed = int(counted.stdout.strip() or 0) if counted.returncode == 0 else 0
        payload: dict[str, Any] = {
            "schema": BEACON_SCHEMA,
            "machine_id": self.identity.id,
            "label": self.identity.label,
            "integrated": head,
            "synced_at": time.time(),
            "contributed": contributed,
            "store_schema": SCHEMA_VERSION,
        }
        if self.beacon_extras is not None:
            try:
                payload["miniclaw"] = self.beacon_extras()
            except Exception:  # noqa: BLE001
                logger.warning("无法采集信标中的 MiniClaw2 版本信息", exc_info=True)
        blob = _git(
            self.root, "hash-object", "-w", "--stdin",
            input_text=json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        ).stdout.strip()
        tree = _git(
            self.root, "mktree", input_text=f"100644 blob {blob}\t{BEACON_FILENAME}\n",
        ).stdout.strip()
        name, _ = self.author
        parents = ["-p", previous] if previous else []
        return _git(
            self.root, "commit-tree", tree, *parents,
            input_text=f"同步信标 {self.identity.label}\n",
            env=_author_env(name, email),
        ).stdout.strip()

    def _ref(self, ref: str) -> str | None:
        result = _git(self.root, "rev-parse", "--verify", "--quiet", ref, check=False)
        return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None

    def _merge_remote(self, remote_ref: str) -> bool:
        from .migrations.sync_tree import merge_remote

        try:
            return merge_remote(
                self.root, remote_ref, on_progress=self._set_progress, author=self.author,
            )
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


def host_email(machine_id: str) -> str:
    """The commit email every MiniClaw2 commit from ``machine_id`` carries."""
    return f"miniclaw2@{machine_id}.local"


def machine_id_from_email(email: str) -> str | None:
    match = re.fullmatch(r"miniclaw2@(.+)\.local", email.strip())
    return match.group(1) if match else None


# A probe must fail fast instead of stalling on a dead link, and must never
# hang on a credential prompt.
_PROBE_GIT_CONFIG = ("-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=30")


def _probe_env() -> dict[str, str]:
    ssh_command = os.environ.get("GIT_SSH_COMMAND", "ssh")
    return {"GIT_SSH_COMMAND": f"{ssh_command} -oBatchMode=yes -oConnectTimeout=20"}


def _probe_fetch_refspecs(branch: str) -> list[str]:
    return [
        f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
        f"+{BEACON_REF_PREFIX}*:{BEACON_TRACKING_PREFIX}*",
    ]


def _author_env(name: str, email: str) -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": name, "GIT_COMMITTER_NAME": name,
        "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_EMAIL": email,
    }


def _git(
    root: Path,
    *args: str,
    check: bool = True,
    timeout: float | None = None,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = _run_raw(
        ["git", "-C", str(root), *args], check=False, timeout=timeout,
        input_text=input_text, env=env,
    )
    if check and result.returncode != 0:
        raise SyncError(_command_error(f"git {' '.join(args)} failed", result))
    return result


def _git_transfer(
    root: Path, subcommand: str, *args: str,
    on_progress: Callable[[str], None] | None = None,
    config: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return _run_transfer(
        ["git", "-C", str(root), *config, subcommand, "--progress", *args],
        label=f"git {subcommand}", on_progress=on_progress,
        env=_probe_env() if config else None,
    )


def _run_transfer(
    command: list[str],
    *,
    label: str,
    on_progress: Callable[[str], None] | None = None,
    stall_timeout: float = REMOTE_STALL_TIMEOUT_SECONDS,
    total_timeout: float = REMOTE_TRANSFER_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a network git command, failing on silence instead of total size.

    git runs in its own process group so a timeout also stops the ssh and
    pack-objects helpers; killing only git leaves them finishing the push in
    the background while the caller records a failure.
    """
    isolation: dict[str, Any] = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if sys.platform == "win32" else {"start_new_session": True}
    )
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}, **isolation,
        )
    except FileNotFoundError as exc:
        raise SyncError("git is not installed or not available on PATH") from exc
    last_activity = time.monotonic()
    last_progress = ""
    stdout: list[bytes] = []
    messages: list[str] = []

    def read_stdout() -> None:
        nonlocal last_activity
        assert process.stdout is not None
        while chunk := process.stdout.read1(65536):
            last_activity = time.monotonic()
            stdout.append(chunk)

    def read_stderr() -> None:
        # Progress lines are redrawn with "\r", so split on both terminators.
        nonlocal last_activity, last_progress
        assert process.stderr is not None
        pending = b""
        while chunk := process.stderr.read1(65536):
            last_activity = time.monotonic()
            *lines, pending = re.split(rb"[\r\n]", pending + chunk)
            for raw in lines:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if _TRANSFER_PROGRESS.match(line):
                    last_progress = line
                    if on_progress is not None:
                        on_progress(line)
                else:
                    messages.append(line)
        if tail := pending.decode("utf-8", "replace").strip():
            messages.append(tail)

    readers = [threading.Thread(target=target, daemon=True) for target in (read_stdout, read_stderr)]
    for reader in readers:
        reader.start()
    started = time.monotonic()
    failure: str | None = None
    while True:
        try:
            process.wait(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            pass
        now = time.monotonic()
        if now - started > total_timeout:
            failure = f"timed out after {total_timeout:g} seconds"
        elif now - last_activity > stall_timeout:
            failure = f"timed out: no progress for {stall_timeout:g} seconds"
        else:
            continue
        _kill_process_group(process)
        break
    for reader in readers:
        reader.join(timeout=5)
    if failure is not None:
        context = f" (last progress: {last_progress})" if last_progress else ""
        raise SyncError(f"{label} {failure}{context}")
    return subprocess.CompletedProcess(
        command, process.returncode,
        b"".join(stdout).decode("utf-8", "replace"), "\n".join(messages),
    )


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning("git 进程 %s 未能在超时后退出", process.pid)


def _git_init(root: Path) -> None:
    initialized = _run_raw(["git", "init", "-b", "main", str(root)], check=False)
    if initialized.returncode != 0:
        _run_raw(["git", "init", str(root)])
        _git(root, "checkout", "-b", "main")


def _run_raw(
    command: list[str], *, check: bool = True, timeout: float | None = None,
    input_text: str | None = None, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
            stdin=None if input_text is not None else subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})},
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

