"""Machine identity and explicit git synchronization for the metadata store."""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import yaml


MACHINE_FILENAME = "machine.json"
SCHEMA_FILENAME = "schema.json"
SCHEMA_VERSION = 8
SCHEMA_NAME = "user-template-schema-v2-v8"
DEFAULT_COMMIT_DEBOUNCE_SECONDS = 30.0


logger = logging.getLogger(__name__)


class SyncError(RuntimeError):
    """A safe, user-facing sync failure."""


class SchemaConflictError(SyncError):
    """The store schema changed independently on both sides."""


class MachineIdentityMismatchError(SyncError):
    """The local identity file belongs to a differently named host."""


@dataclass(frozen=True)
class MachineIdentity:
    id: str
    hostname: str
    label: str
    last_sync_at: float | None = None
    last_synced_commit: str | None = None
    sync_pending: bool = False

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "hostname": self.hostname,
            "label": self.label,
            "last_sync_at": self.last_sync_at,
            "last_synced_commit": self.last_synced_commit,
            "sync_pending": self.sync_pending,
        }


def current_hostname() -> str:
    return socket.gethostname() or "unknown-machine"


def machine_path(root: Path) -> Path:
    return root / MACHINE_FILENAME


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
    )


def ensure_machine_identity(root: Path) -> MachineIdentity:
    root.mkdir(parents=True, exist_ok=True)
    path = machine_path(root)
    if path.exists():
        return load_machine_identity(root)
    hostname = current_hostname()
    identity = MachineIdentity(id=str(uuid4()), hostname=hostname, label=hostname)
    _write_json(path, identity.payload())
    return identity


def machine_hostname_mismatch(identity: MachineIdentity) -> bool:
    return identity.hostname != current_hostname()


def resolve_machine_rename(root: Path, *, label: str | None = None) -> MachineIdentity:
    identity = load_machine_identity(root)
    hostname = current_hostname()
    updated = MachineIdentity(
        id=identity.id,
        hostname=hostname,
        label=(label or hostname).strip() or hostname,
        last_sync_at=identity.last_sync_at,
        last_synced_commit=identity.last_synced_commit,
        sync_pending=identity.sync_pending,
    )
    _write_json(machine_path(root), updated.payload())
    _update_owned_project_labels(root, updated)
    return updated


def resolve_machine_copy(root: Path, *, label: str | None = None) -> MachineIdentity:
    hostname = current_hostname()
    updated = MachineIdentity(
        id=str(uuid4()),
        hostname=hostname,
        label=(label or hostname).strip() or hostname,
    )
    _write_json(machine_path(root), updated.payload())
    return updated


def ensure_store_metadata(root: Path, identity: MachineIdentity) -> None:
    """Create current metadata and stamp records from pre-sync stores."""
    schema_path = root / SCHEMA_FILENAME
    existing_version = 0
    if schema_path.exists():
        try:
            payload = json.loads(schema_path.read_text(encoding="utf-8"))
            existing_version = int(payload.get("schema_version", 0))
        except (OSError, ValueError, TypeError) as exc:
            raise SyncError(f"invalid store schema {schema_path}: {exc}") from exc
    if existing_version > SCHEMA_VERSION:
        return

    context_root = _configured_contextspace_root(root)
    legacy_skill_plugs = context_root / "plugs" / "skills"
    if existing_version < 6 and (existing_version > 0 or legacy_skill_plugs.exists()):
        _migrate_principles_and_skills(root, context_root=context_root)
    if existing_version < 8:
        _migrate_user_templates_v2(root, context_root=context_root)

    project_files = sorted((root / "projects").glob("*/project.json"))
    legacy_files: list[tuple[Path, dict[str, Any]]] = []
    for project_file in project_files:
        try:
            project_payload = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SyncError(f"invalid project record {project_file}: {exc}") from exc
        if not project_payload.get("machine_id"):
            legacy_files.append((project_file, project_payload))

    if legacy_files:
        backup_root = (
            root
            / "migration-backups"
            / f"{SCHEMA_NAME}-{int(time.time())}"
        )
        for project_file, project_payload in legacy_files:
            relative = project_file.relative_to(root)
            backup_file = backup_root / relative
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(project_file, backup_file)
            project_payload["machine_id"] = identity.id
            project_payload["machine_label"] = identity.label
            _write_json(project_file, project_payload)

    _write_json(
        schema_path,
        {"schema": SCHEMA_NAME, "schema_version": SCHEMA_VERSION},
    )
    _drop_nested_git_expectation(root)
    ensure_store_gitignore(root)


def _configured_contextspace_root(root: Path) -> Path:
    override = os.environ.get("MINICLAW_CONTEXT_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (root / "contextspace").resolve()


def _migrate_user_templates_v2(root: Path, *, context_root: Path) -> None:
    """Upgrade user-authored template manifests to template schema v2."""
    from .templates.loader import SCHEMA_VERSION as template_schema_version

    templates_root = context_root / "templates"
    if not templates_root.is_dir():
        return

    backup_root = (
        root
        / "migration-backups"
        / f"{SCHEMA_NAME}-{int(time.time())}"
        / "contextspace"
        / "templates"
    )
    shutil.copytree(templates_root, backup_root, dirs_exist_ok=True)

    for template_root in sorted(templates_root.iterdir(), key=lambda path: path.name):
        manifest = template_root / "template.yaml"
        if not template_root.is_dir() or not manifest.is_file():
            continue
        try:
            payload = _read_yaml_mapping(manifest)
            if payload is None:
                raise SyncError(f"{manifest} 顶层必须是 YAML mapping")

            version = payload.get("schema_version")
            if version == template_schema_version and not isinstance(version, bool):
                continue
            if version is not None and (
                isinstance(version, bool) or not isinstance(version, int)
            ):
                raise SyncError(f"{manifest} 的 schema_version 必须是整数")
            if isinstance(version, int) and version > template_schema_version:
                raise SyncError(
                    f"{manifest} 使用了更新的模板 schema_version {version}"
                )

            placeholder_warnings = _template_placeholder_warnings(template_root)
            migrated: dict[str, Any] = {"schema_version": template_schema_version}
            migrated.update(
                (key, value)
                for key, value in payload.items()
                if key != "schema_version"
            )
            migrated.setdefault("arguments", [])
            migrated.setdefault("inputs", [])
            _write_yaml_mapping(manifest, migrated)
        except Exception as exc:  # noqa: BLE001
            logger.error("用户模板 %r 迁移失败：%s", template_root.name, exc)
            continue

        if placeholder_warnings:
            logger.warning(
                "用户模板 %r 已迁移到 schema v%d；以下占位符将被识别为参数，"
                "请确认它们符合作者意图：\n%s",
                template_root.name,
                template_schema_version,
                "\n".join(f"  - {item}" for item in placeholder_warnings),
            )


def _template_placeholder_warnings(template_root: Path) -> list[str]:
    """List prompt placeholders whose meaning changes under template schema v2."""
    from .templates.loader import PARAM_NAME_RE, _PLACEHOLDER_RE

    prompts_root = template_root / "prompts"
    if not prompts_root.is_dir():
        return []

    warnings: list[str] = []
    for prompt_path in sorted(prompts_root.rglob("*.md")):
        text = prompt_path.read_text(encoding="utf-8")
        relative = prompt_path.relative_to(template_root).as_posix()
        for match in _PLACEHOLDER_RE.finditer(text):
            name = match.group(1)
            if PARAM_NAME_RE.fullmatch(name):
                line = text.count("\n", 0, match.start()) + 1
                warnings.append(f"{relative}:{line}: {match.group(0)}")
    return warnings


def _migrate_principles_and_skills(
    root: Path, *, context_root: Path
) -> None:
    """Migrate the former injected-skill mechanism to principles."""
    backup_root = (
        root
        / "migration-backups"
        / f"principles-and-agent-skills-v6-{int(time.time())}"
    )
    for name in ("projects", "templates"):
        source = root / name
        if source.exists():
            shutil.copytree(source, backup_root / name, dirs_exist_ok=True)
    if context_root.exists():
        shutil.copytree(
            context_root,
            backup_root / "contextspace",
            dirs_exist_ok=True,
        )

    old_plugs = context_root / "plugs" / "skills"
    principle_plugs = context_root / "plugs" / "principles"
    if old_plugs.is_dir():
        principle_plugs.parent.mkdir(parents=True, exist_ok=True)
        if principle_plugs.exists():
            for child in old_plugs.iterdir():
                destination = principle_plugs / child.name
                if destination.exists():
                    raise SyncError(
                        f"cannot migrate principle {child.name!r}: destination exists"
                    )
                child.replace(destination)
            old_plugs.rmdir()
        else:
            old_plugs.replace(principle_plugs)

    for manifest in principle_plugs.glob("*/manifest.yaml"):
        payload = _read_yaml_mapping(manifest)
        if payload is None:
            continue
        changed = False
        if payload.get("kind") == "skill":
            payload["kind"] = "principle"
            changed = True
        identifier = payload.get("id")
        if isinstance(identifier, str) and identifier.startswith("skills."):
            payload["id"] = "principles." + identifier[len("skills."):]
            changed = True
        if changed:
            _write_yaml_mapping(manifest, payload)

    yaml_roots = [
        context_root / "bindings",
        context_root / "templates",
        root / "templates",
    ]
    for yaml_root in yaml_roots:
        if not yaml_root.exists():
            continue
        for path in [*yaml_root.rglob("*.yaml"), *yaml_root.rglob("*.yml")]:
            payload = _read_yaml_mapping(path)
            if payload is None:
                continue
            rewritten = _rewrite_principle_values(payload)
            if rewritten != payload:
                _write_yaml_mapping(path, rewritten)

    for node_path in (root / "projects").glob("*/nodes/*/node.json"):
        try:
            payload = json.loads(node_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SyncError(f"invalid node record {node_path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise SyncError(f"invalid node record {node_path}: expected object")
        changed = False
        settings = payload.get("settings_snapshot")
        if isinstance(settings, dict) and "extra_skills" in settings:
            settings["extra_principles"] = _rewrite_principle_values(
                settings.pop("extra_skills"), rewrite_strings=True
            )
            changed = True
        if "pending_extra_skills" in payload:
            payload["pending_extra_principles"] = _rewrite_principle_values(
                payload.pop("pending_extra_skills"), rewrite_strings=True
            )
            changed = True
        if "pending_extra_skills" not in payload:
            payload["pending_extra_skills"] = []
            changed = True
        if payload.get("agent_op_kind") == "skill_edit":
            payload["agent_op_kind"] = "principle_edit"
            changed = True
        if changed:
            _write_json(node_path, payload)


_PRINCIPLE_IDENTIFIER_KEYS = {
    "id",
    "plug_id",
    "plugs",
    "extra_skills",
    "extra_principles",
    "pending_extra_skills",
    "pending_extra_principles",
    "agent_op_kind",
}


def _rewrite_principle_values(
    value: Any, *, rewrite_strings: bool = False
) -> Any:
    if isinstance(value, str):
        if rewrite_strings and value.startswith("skills."):
            return "principles." + value[len("skills."):]
        if rewrite_strings and value == "skill_edit":
            return "principle_edit"
        return value
    if isinstance(value, list):
        return [
            _rewrite_principle_values(item, rewrite_strings=rewrite_strings)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            ("extra_principles" if key == "extra_skills" else key):
            _rewrite_principle_values(
                item,
                rewrite_strings=(rewrite_strings or key in _PRINCIPLE_IDENTIFIER_KEYS),
            )
            for key, item in value.items()
        }
    return value


def _read_yaml_mapping(path: Path) -> dict[str, Any] | None:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SyncError(f"invalid YAML {path}: {exc}") from exc
    return payload if isinstance(payload, dict) else None


def _write_yaml_mapping(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def schema_is_newer(root: Path) -> bool:
    path = root / SCHEMA_FILENAME
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return int(payload.get("schema_version", 0)) > SCHEMA_VERSION
    except (OSError, ValueError, TypeError):
        return False


def ensure_store_gitignore(root: Path) -> None:
    path = root / ".gitignore"
    required = [
        "machine.json",
        "migration-backups/",
        "*.tmp",
        "projects/*/hosts/*/local.json",
    ]
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [entry for entry in required if entry not in existing]
    if not missing:
        return
    content = "\n".join([*existing, *missing]).strip() + "\n"
    path.write_text(content, encoding="utf-8")


def _drop_nested_git_expectation(root: Path) -> None:
    path = root / "contextspace" / "contextspace.yaml"
    if not path.exists():
        return
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SyncError(f"invalid ContextSpace manifest {path}: {exc}") from exc
    if not isinstance(payload, dict) or "git" not in payload:
        return
    payload.pop("git", None)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    temporary.replace(path)


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
        identity = MachineIdentity(
            id=identity.id,
            hostname=identity.hostname,
            label=identity.label,
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
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._pending_messages: list[str] = []
        self._pre_commit_callbacks: list[Callable[[], None]] = []
        self._success_callbacks: list[Callable[[], None]] = []

    def add_pre_commit_callback(self, callback: Callable[[], None]) -> None:
        if callback not in self._pre_commit_callbacks:
            self._pre_commit_callbacks.append(callback)

    def add_success_callback(self, callback: Callable[[], None]) -> None:
        if callback not in self._success_callbacks:
            self._success_callbacks.append(callback)

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
            remote_refs = _run_raw(
                ["git", "ls-remote", "--heads", remote_url], check=False
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
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            messages = self._pending_messages
            self._pending_messages = []
            if not (self.root / ".git").exists():
                return None
            _git(self.root, "add", "-A")
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
        if configured:
            dirty = _git(self.root, "status", "--porcelain", check=False)
            head = self._head()
            changed = bool(dirty.stdout.strip()) or (
                head is not None and head != self.identity.last_synced_commit
            ) or self.identity.sync_pending
        return {
            "configured": configured,
            "remote_url": self.remote_url(),
            "status": "changed" if changed else "up-to-date",
            "changed": changed,
            "last_sync_at": self.identity.last_sync_at,
            "machine_id": self.identity.id,
            "machine_label": self.identity.label,
            "hostname_mismatch": machine_hostname_mismatch(self.identity),
        }

    def sync_now(self) -> dict[str, Any]:
        if not self.configured:
            raise SyncError("metadata sync is not configured")
        if machine_hostname_mismatch(self.identity):
            raise MachineIdentityMismatchError(
                "machine hostname changed; resolve rename versus copied store before syncing"
            )
        self._ensure_contextspace_inside_store()
        with self._lock:
            for callback in tuple(self._pre_commit_callbacks):
                try:
                    callback()
                except Exception:  # noqa: BLE001
                    logger.exception("pre-commit sync callback failed")
            self.commit_now()
            starting_head = self._head()
            branch = self._branch()
            try:
                fetched = _git(self.root, "fetch", "origin", check=False)
                if fetched.returncode != 0:
                    raise SyncError(_command_error("fetch failed", fetched))
                remote_ref = f"origin/{branch}"
                remote_exists = _git(
                    self.root, "rev-parse", "--verify", remote_ref, check=False
                ).returncode == 0
                if remote_exists:
                    self._merge_remote(remote_ref)
                pushed = _git(
                    self.root,
                    "push",
                    "--set-upstream",
                    "origin",
                    f"HEAD:{branch}",
                    check=False,
                )
                if pushed.returncode != 0:
                    raise SyncError(_command_error("push failed", pushed))
            except SyncError:
                if starting_head is not None and self._head() != starting_head:
                    _git(self.root, "reset", "--merge", starting_head, check=False)
                self._record_failure()
                raise
            self._record_success()
            return self.status()

    def _merge_remote(self, remote_ref: str) -> None:
        local_head = self._head()
        remote_head = _git(self.root, "rev-parse", remote_ref).stdout.strip()
        if local_head == remote_head:
            return
        merge = _git(self.root, "merge", "--no-edit", remote_ref, check=False)
        if merge.returncode == 0:
            return
        conflicts = _git(
            self.root, "diff", "--name-only", "--diff-filter=U", check=False
        ).stdout.splitlines()
        _git(self.root, "merge", "--abort", check=False)
        if SCHEMA_FILENAME in conflicts:
            raise SchemaConflictError(
                "schema.json changed independently on both machines; resolve it manually"
            )
        retried = _git(
            self.root,
            "merge",
            "--no-edit",
            "-X",
            "ours",
            remote_ref,
            check=False,
        )
        if retried.returncode != 0:
            _git(self.root, "merge", "--abort", check=False)
            raise SyncError(_command_error("merge failed", retried))

    def _record_success(self) -> None:
        self.identity = MachineIdentity(
            id=self.identity.id,
            hostname=self.identity.hostname,
            label=self.identity.label,
            last_sync_at=time.time(),
            last_synced_commit=self._head(),
            sync_pending=False,
        )
        _write_json(machine_path(self.root), self.identity.payload())
        for callback in tuple(self._success_callbacks):
            callback()

    def _record_failure(self) -> None:
        self.identity = MachineIdentity(
            id=self.identity.id,
            hostname=self.identity.hostname,
            label=self.identity.label,
            last_sync_at=self.identity.last_sync_at,
            last_synced_commit=self.identity.last_synced_commit,
            sync_pending=True,
        )
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
        if manager is None:
            manager = SyncManager(resolved, identity)
            _MANAGERS[resolved] = manager
        return manager


def _git(
    root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = _run_raw(["git", "-C", str(root), *args], check=False)
    if check and result.returncode != 0:
        raise SyncError(_command_error(f"git {' '.join(args)} failed", result))
    return result


def _git_init(root: Path) -> None:
    initialized = _run_raw(["git", "init", "-b", "main", str(root)], check=False)
    if initialized.returncode != 0:
        _run_raw(["git", "init", str(root)])
        _git(root, "checkout", "-b", "main")


def _run_raw(
    command: list[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=check,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError as exc:
        raise SyncError("git is not installed or not available on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise SyncError(_command_error("git command failed", exc)) from exc


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
        try:
            payload = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if payload.get("machine_id") != identity.id:
            continue
        payload["machine_label"] = identity.label
        _write_json(project_file, payload)
