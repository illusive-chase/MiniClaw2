"""On-disk schema migrations."""

from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import fcntl

import yaml

from .model_catalog import (
    get_model_preset,
    legacy_provider_to_model_preset_id,
    legacy_settings_to_model_preset_id,
    normalize_model_preset_id,
)


CURRENT_SCHEMA_VERSION = 2
_SCHEMA_FILE = "schema.json"
_MIGRATION_NAME = "model-presets-v2"
_OLD_MODEL_SETTING_KEYS = frozenset(
    {"model", "model_provider", "service_tier", "reasoning_effort"}
)


class StoreMigrationError(RuntimeError):
    """Raised when persisted data cannot be migrated safely."""


@dataclass(frozen=True, slots=True)
class StoreMigrationReport:
    root: Path
    version_before: int
    version_after: int
    changed_files: tuple[str, ...] = ()
    backup_root: Path | None = None
    repaired: bool = False


def check_store(root: Path) -> StoreMigrationReport:
    """Validate a store without changing it."""

    root = root.expanduser()
    schema_path = root / _SCHEMA_FILE
    version = _read_schema_version(schema_path)
    if version > CURRENT_SCHEMA_VERSION:
        raise StoreMigrationError(
            f"unsupported store schema_version {version}; "
            f"this MiniClaw2 build supports {CURRENT_SCHEMA_VERSION}"
        )
    if version < CURRENT_SCHEMA_VERSION:
        raise StoreMigrationError(
            f"store schema_version {version} requires migration to "
            f"{CURRENT_SCHEMA_VERSION}; start MiniClaw2 normally or run "
            "`python -m miniclaw2 --repair-store`"
        )
    try:
        _validate_current_schema(root)
    except StoreMigrationError as exc:
        raise StoreMigrationError(
            f"{exc}\nStore is marked schema v{CURRENT_SCHEMA_VERSION} but contains "
            "inconsistent records. Stop all MiniClaw2 processes, then run "
            "`python -m miniclaw2 --repair-store`."
        ) from exc
    return StoreMigrationReport(
        root=root,
        version_before=version,
        version_after=version,
    )


def migrate_store(root: Path) -> StoreMigrationReport:
    """Migrate the store to the current schema before normal loading."""

    root = root.expanduser()
    root.mkdir(parents=True, exist_ok=True)
    with _migration_lock(root):
        return _migrate_store_locked(root, repair=False)


def repair_store(root: Path) -> StoreMigrationReport:
    """Repair legacy records even when the store is marked current."""

    root = root.expanduser()
    root.mkdir(parents=True, exist_ok=True)
    with _migration_lock(root):
        version = _read_schema_version(root / _SCHEMA_FILE)
        if version == CURRENT_SCHEMA_VERSION:
            try:
                _validate_current_schema(root)
            except StoreMigrationError:
                pass
            else:
                return StoreMigrationReport(
                    root=root,
                    version_before=version,
                    version_after=version,
                )
        return _migrate_store_locked(root, repair=True)


def _migrate_store_locked(root: Path, *, repair: bool) -> StoreMigrationReport:
    schema_path = root / _SCHEMA_FILE
    version = _read_schema_version(schema_path)
    if version > CURRENT_SCHEMA_VERSION:
        raise StoreMigrationError(
            f"unsupported store schema_version {version}; "
            f"this MiniClaw2 build supports {CURRENT_SCHEMA_VERSION}"
        )
    if version >= CURRENT_SCHEMA_VERSION and not repair:
        try:
            _validate_current_schema(root)
        except StoreMigrationError as exc:
            raise StoreMigrationError(
                f"{exc}\nStore is marked schema v{CURRENT_SCHEMA_VERSION} but contains "
                "inconsistent records. Stop all MiniClaw2 processes, then run "
                "`python -m miniclaw2 --repair-store`."
            ) from exc
        return StoreMigrationReport(
            root=root,
            version_before=version,
            version_after=version,
        )

    timestamp = _utc_timestamp()
    backup_root = root / "migration-backups" / f"{_MIGRATION_NAME}-{timestamp}"
    audit_path = root / "migrations" / f"{_MIGRATION_NAME}.jsonl"
    changed: list[str] = []
    try:
        _migrate_projects(root, backup_root, audit_path, changed=changed)
        _migrate_user_templates(root, backup_root, audit_path, changed=changed)
        _validate_current_schema(root)
        changed.extend(
            _write_json_if_changed(
                schema_path,
                {
                    "schema_version": CURRENT_SCHEMA_VERSION,
                    "migration": _MIGRATION_NAME,
                    "migrated_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                },
                root,
                backup_root,
                audit_path,
                action="write_schema",
            )
        )
        _append_audit(
            audit_path,
            {
                "action": "migration_complete",
                "schema_version": CURRENT_SCHEMA_VERSION,
                "changed_files": changed,
                "repair": repair,
                "backup_root": str(backup_root),
            },
        )
    except Exception as exc:  # noqa: BLE001
        restored = _rollback_changed_files(root, backup_root, changed)
        try:
            _append_audit(
                audit_path,
                {
                    "action": "migration_rolled_back",
                    "changed_files": changed,
                    "restored_files": restored,
                    "error": str(exc),
                },
            )
        except Exception:  # noqa: BLE001
            pass
        if isinstance(exc, StoreMigrationError):
            raise
        raise StoreMigrationError(f"store migration failed: {exc}") from exc
    return StoreMigrationReport(
        root=root,
        version_before=version,
        version_after=CURRENT_SCHEMA_VERSION,
        changed_files=tuple(changed),
        backup_root=backup_root,
        repaired=repair,
    )


@contextmanager
def _migration_lock(root: Path) -> Iterator[None]:
    lock_path = root / ".migration.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_schema_version(path: Path) -> int:
    if not path.exists():
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise StoreMigrationError(f"invalid store schema file {path}: {exc}") from exc
    raw = data.get("schema_version", 1)
    if not isinstance(raw, int):
        raise StoreMigrationError(f"invalid store schema_version in {path}")
    return raw


def _validate_current_schema(root: Path) -> None:
    """Fail fast when a store claims the current schema but still carries
    provider-only model data.
    """

    projects_dir = root / "projects"
    if projects_dir.exists():
        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            project_path = project_dir / "project.json"
            if not project_path.exists():
                continue
            project = _read_json(project_path)
            project_preset_id = _validate_project_current(project_path, project)
            nodes_dir = project_dir / "nodes"
            if not nodes_dir.exists():
                continue
            for node_dir in sorted(nodes_dir.iterdir()):
                if not node_dir.is_dir():
                    continue
                node_path = node_dir / "node.json"
                if not node_path.exists():
                    continue
                node = _read_json(node_path)
                node_preset_id = _validate_node_current(node_path, node)
                preview_path = node_dir / "preview.json"
                if preview_path.exists():
                    _validate_preview_current(
                        preview_path,
                        node_preset_id=node_preset_id or project_preset_id,
                    )
    _validate_user_templates_current(root)


def _validate_project_current(path: Path, project: dict[str, Any]) -> str:
    preset_id = _required_preset_id(
        path,
        project.get("model_preset_id"),
        owner="project",
    )
    preset = get_model_preset(preset_id)
    provider = _string(project.get("provider"))
    if provider is not None and provider != preset.provider:
        raise StoreMigrationError(
            f"{path}: provider {provider!r} does not match "
            f"model_preset_id {preset.id!r}"
        )
    settings = _dict(project.get("settings_override"))
    legacy_keys = sorted(key for key in _OLD_MODEL_SETTING_KEYS if key in settings)
    if legacy_keys:
        raise StoreMigrationError(
            f"{path}: settings_override contains obsolete model keys "
            f"{legacy_keys}; run the model preset migration"
        )
    return preset.id


def _validate_node_current(path: Path, node: dict[str, Any]) -> str | None:
    kind = _string(node.get("kind")) or "agent"
    if kind != "agent":
        existing = _string(node.get("model_preset_id"))
        if existing:
            preset_id = _required_preset_id(path, existing, owner="node")
            preset = get_model_preset(preset_id)
            provider = _string(node.get("provider"))
            if provider is not None and provider != preset.provider:
                raise StoreMigrationError(
                    f"{path}: provider {provider!r} does not match "
                    f"model_preset_id {preset.id!r}"
                )
            return preset.id
        return None

    preset_id = _required_preset_id(path, node.get("model_preset_id"), owner="node")
    preset = get_model_preset(preset_id)
    provider = _string(node.get("provider"))
    if provider is not None and provider != preset.provider:
        raise StoreMigrationError(
            f"{path}: provider {provider!r} does not match "
            f"model_preset_id {preset.id!r}"
        )
    snapshot = _dict(node.get("settings_snapshot"))
    _validate_snapshot_if_present(path, snapshot, preset)
    return preset.id


def _validate_snapshot_if_present(
    path: Path,
    snapshot: dict[str, Any],
    preset: Any,
) -> None:
    checks = {
        "model_preset_id": preset.id,
        "provider": preset.provider,
        "model": preset.model,
        "model_provider": preset.model_provider,
        "service_tier": preset.service_tier,
        "reasoning_effort": preset.reasoning_effort,
    }
    for key, expected in checks.items():
        if key not in snapshot:
            continue
        value = snapshot.get(key)
        if value != expected:
            raise StoreMigrationError(
                f"{path}: settings_snapshot {key} {value!r} does not match "
                f"model_preset_id {preset.id!r}"
            )


def _validate_preview_current(
    path: Path,
    *,
    node_preset_id: str | None,
) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise StoreMigrationError(f"{path}: invalid preview JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise StoreMigrationError(f"{path}: preview JSON root must be an object")
    if data.get("state") != "virtual":
        return
    if data.get("kind", "agent") != "agent":
        return
    preset_id = _required_preset_id(
        path,
        data.get("model_preset_id"),
        owner="virtual preview",
    )
    if node_preset_id is not None and preset_id != node_preset_id:
        raise StoreMigrationError(
            f"{path}: virtual preview model_preset_id {preset_id!r} does not "
            f"match node model_preset_id {node_preset_id!r}"
        )


def _validate_user_templates_current(root: Path) -> None:
    templates_root = _contextspace_root(root) / "templates"
    if not templates_root.exists():
        return
    for template_path in sorted(templates_root.glob("*/template.yaml")):
        try:
            data = yaml.safe_load(template_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise StoreMigrationError(f"{template_path}: invalid template YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise StoreMigrationError(f"{template_path}: template.yaml must be a mapping")
        if "providers" in data:
            raise StoreMigrationError(
                f"{template_path}: providers is obsolete; run the model preset migration"
            )
        raw = data.get("allowed_model_preset_ids")
        if not isinstance(raw, list) or not raw:
            raise StoreMigrationError(
                f"{template_path}: template must declare allowed_model_preset_ids"
            )
        _normalize_preset_list(template_path, raw)


def _required_preset_id(path: Path, raw: Any, *, owner: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise StoreMigrationError(
            f"{path}: {owner} requires model_preset_id; "
            "run the model preset migration"
        )
    try:
        return normalize_model_preset_id(raw)
    except ValueError as exc:
        raise StoreMigrationError(f"{path}: {exc}") from exc


def _migrate_projects(
    root: Path,
    backup_root: Path,
    audit_path: Path,
    *,
    changed: list[str],
) -> None:
    projects_dir = root / "projects"
    if not projects_dir.exists():
        return
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        project_path = project_dir / "project.json"
        if not project_path.exists():
            continue
        project = _read_json(project_path)
        project_legacy_provider = _string(project.get("provider"))
        project_legacy_settings = _dict(project.get("settings_override"))
        project_preset_id = _resolve_project_preset(
            project_path,
            project,
            project_legacy_provider,
            project_legacy_settings,
        )
        changed.extend(
            _write_json_if_changed(
                project_path,
                project,
                root,
                backup_root,
                audit_path,
                action="migrate_project",
            )
        )

        nodes_dir = project_dir / "nodes"
        if not nodes_dir.exists():
            continue
        for node_dir in sorted(nodes_dir.iterdir()):
            if not node_dir.is_dir():
                continue
            node_path = node_dir / "node.json"
            if not node_path.exists():
                continue
            node = _read_json(node_path)
            node_preset_id = _migrate_node(
                node_path,
                node,
                project_preset_id=project_preset_id,
                project_legacy_provider=project_legacy_provider,
                project_legacy_settings=project_legacy_settings,
            )
            changed.extend(
                _write_json_if_changed(
                    node_path,
                    node,
                    root,
                    backup_root,
                    audit_path,
                    action="migrate_node",
                )
            )
            preview_path = node_dir / "preview.json"
            if preview_path.exists():
                changed.extend(
                    _migrate_preview(
                        preview_path,
                        node_preset_id=node_preset_id,
                        root=root,
                        backup_root=backup_root,
                        audit_path=audit_path,
                    )
                )


def _resolve_project_preset(
    path: Path,
    project: dict[str, Any],
    legacy_provider: str | None,
    legacy_settings: dict[str, Any],
) -> str:
    existing = _string(project.get("model_preset_id"))
    if existing:
        try:
            preset_id = normalize_model_preset_id(existing)
        except ValueError as exc:
            raise StoreMigrationError(f"{path}: {exc}") from exc
    else:
        try:
            preset_id = legacy_settings_to_model_preset_id(
                provider=legacy_provider,
                model=_string(legacy_settings.get("model")),
                model_provider=_string(legacy_settings.get("model_provider")),
                service_tier=_string(legacy_settings.get("service_tier")),
                reasoning_effort=_string(legacy_settings.get("reasoning_effort")),
            )
        except ValueError as exc:
            raise StoreMigrationError(f"{path}: {exc}") from exc
    preset = get_model_preset(preset_id)
    cleaned_settings = {
        key: value
        for key, value in legacy_settings.items()
        if key not in _OLD_MODEL_SETTING_KEYS
    }
    project["model_preset_id"] = preset.id
    project["provider"] = preset.provider
    project["settings_override"] = cleaned_settings
    return preset.id


def _migrate_node(
    path: Path,
    node: dict[str, Any],
    *,
    project_preset_id: str,
    project_legacy_provider: str | None,
    project_legacy_settings: dict[str, Any],
) -> str | None:
    kind = _string(node.get("kind")) or "agent"
    snapshot = _dict(node.get("settings_snapshot"))
    if "cli_session_id" not in node and "sdk_session_id" in node:
        node["cli_session_id"] = node.get("sdk_session_id")
    if kind != "agent":
        existing = _string(node.get("model_preset_id"))
        if existing:
            try:
                preset = get_model_preset(existing)
            except ValueError as exc:
                raise StoreMigrationError(f"{path}: {exc}") from exc
            node["model_preset_id"] = preset.id
            node["provider"] = preset.provider
        node["settings_snapshot"] = _clean_snapshot(snapshot)
        return node.get("model_preset_id")

    existing = _string(node.get("model_preset_id"))
    if existing:
        try:
            preset_id = normalize_model_preset_id(existing)
        except ValueError as exc:
            raise StoreMigrationError(f"{path}: {exc}") from exc
    else:
        provider = _string(node.get("provider")) or project_legacy_provider
        settings_for_node = snapshot if _has_legacy_model_settings(snapshot) else {}
        if not settings_for_node and provider == project_legacy_provider:
            settings_for_node = project_legacy_settings
        try:
            preset_id = legacy_settings_to_model_preset_id(
                provider=provider,
                model=_string(settings_for_node.get("model")),
                model_provider=_string(settings_for_node.get("model_provider")),
                service_tier=_string(settings_for_node.get("service_tier")),
                reasoning_effort=_string(settings_for_node.get("reasoning_effort")),
            )
        except ValueError as exc:
            raise StoreMigrationError(f"{path}: {exc}") from exc
    preset = get_model_preset(preset_id)
    node["model_preset_id"] = preset.id
    node["provider"] = preset.provider
    cleaned_snapshot = _clean_snapshot(snapshot)
    cleaned_snapshot.update(preset.settings_snapshot())
    node["settings_snapshot"] = cleaned_snapshot
    return preset.id


def _migrate_preview(
    path: Path,
    *,
    node_preset_id: str | None,
    root: Path,
    backup_root: Path,
    audit_path: Path,
) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise StoreMigrationError(f"{path}: invalid preview JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("state") != "virtual":
        return []
    if data.get("kind", "agent") != "agent":
        return []
    existing = _string(data.get("model_preset_id"))
    if existing:
        try:
            data["model_preset_id"] = normalize_model_preset_id(existing)
        except ValueError as exc:
            raise StoreMigrationError(f"{path}: {exc}") from exc
    elif node_preset_id:
        data["model_preset_id"] = node_preset_id
    else:
        raise StoreMigrationError(
            f"{path}: cannot infer model_preset_id for virtual preview"
        )
    return _write_json_if_changed(
        path,
        data,
        root,
        backup_root,
        audit_path,
        action="migrate_virtual_preview",
    )


def _migrate_user_templates(
    root: Path,
    backup_root: Path,
    audit_path: Path,
    *,
    changed: list[str],
) -> None:
    templates_root = _contextspace_root(root) / "templates"
    if not templates_root.exists():
        return
    for template_path in sorted(templates_root.glob("*/template.yaml")):
        try:
            data = yaml.safe_load(template_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise StoreMigrationError(f"{template_path}: invalid template YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise StoreMigrationError(f"{template_path}: template.yaml must be a mapping")
        _migrate_template_yaml(template_path, data)
        changed.extend(
            _write_yaml_if_changed(
                template_path,
                data,
                root,
                backup_root,
                audit_path,
                action="migrate_user_template",
            )
        )


def _migrate_template_yaml(path: Path, data: dict[str, Any]) -> None:
    if "allowed_model_preset_ids" in data:
        raw = data.get("allowed_model_preset_ids")
        if not isinstance(raw, list) or not raw:
            raise StoreMigrationError(
                f"{path}: allowed_model_preset_ids must be a non-empty list"
            )
        data["allowed_model_preset_ids"] = _normalize_preset_list(path, raw)
        data.pop("providers", None)
        data.pop("model_preset_id", None)
        return
    if "model_preset_id" in data:
        data["allowed_model_preset_ids"] = _normalize_preset_list(
            path, [data.get("model_preset_id")]
        )
        data.pop("providers", None)
        data.pop("model_preset_id", None)
        return
    raw_providers = data.get("providers")
    if raw_providers is None:
        raise StoreMigrationError(
            f"{path}: template must declare allowed_model_preset_ids"
        )
    if not isinstance(raw_providers, list) or not raw_providers:
        raise StoreMigrationError(f"{path}: providers must be a non-empty list")
    preset_ids: list[str] = []
    for provider in raw_providers:
        if not isinstance(provider, str):
            raise StoreMigrationError(f"{path}: providers entries must be strings")
        try:
            preset_id = legacy_provider_to_model_preset_id(provider)
        except ValueError as exc:
            raise StoreMigrationError(f"{path}: {exc}") from exc
        if preset_id not in preset_ids:
            preset_ids.append(preset_id)
    data["allowed_model_preset_ids"] = preset_ids
    data.pop("providers", None)


def _normalize_preset_list(path: Path, raw: list[Any]) -> list[str]:
    preset_ids: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise StoreMigrationError(
                f"{path}: allowed_model_preset_ids entries must be strings"
            )
        try:
            preset_id = normalize_model_preset_id(item)
        except ValueError as exc:
            raise StoreMigrationError(f"{path}: {exc}") from exc
        if preset_id not in preset_ids:
            preset_ids.append(preset_id)
    return preset_ids


def _write_json_if_changed(
    path: Path,
    data: dict[str, Any],
    root: Path,
    backup_root: Path,
    audit_path: Path,
    *,
    action: str,
) -> list[str]:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return []
    _backup(path, root, backup_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    rel = _rel(path, root)
    _append_audit(audit_path, {"action": action, "path": rel})
    return [rel]


def _write_yaml_if_changed(
    path: Path,
    data: dict[str, Any],
    root: Path,
    backup_root: Path,
    audit_path: Path,
    *,
    action: str,
) -> list[str]:
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return []
    _backup(path, root, backup_root)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    rel = _rel(path, root)
    _append_audit(audit_path, {"action": action, "path": rel})
    return [rel]


def _backup(path: Path, root: Path, backup_root: Path) -> None:
    if not path.exists():
        return
    rel = _backup_relative_path(path, root)
    target = backup_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)


def _rollback_changed_files(
    root: Path,
    backup_root: Path,
    changed: list[str],
) -> list[str]:
    restored: list[str] = []
    seen: set[Path] = set()
    for raw_path in reversed(changed):
        candidate = Path(raw_path)
        path = candidate if candidate.is_absolute() else root / candidate
        if path in seen:
            continue
        seen.add(path)
        backup = backup_root / _backup_relative_path(path, root)
        if backup.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".rollback.tmp")
            shutil.copy2(backup, tmp)
            tmp.replace(path)
        else:
            path.unlink(missing_ok=True)
        restored.append(_rel(path, root))
    return restored


def _backup_relative_path(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        absolute = path.resolve(strict=False)
        parts = [
            part
            for part in absolute.parts
            if part and part != absolute.anchor
        ]
        return Path("external", *parts)


def _append_audit(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **payload,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise StoreMigrationError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise StoreMigrationError(f"{path}: JSON root must be an object")
    return data


def _clean_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in snapshot.items()
        if key not in _OLD_MODEL_SETTING_KEYS
    }


def _has_legacy_model_settings(settings: dict[str, Any]) -> bool:
    return any(
        isinstance(settings.get(key), str) and str(settings.get(key)).strip()
        for key in _OLD_MODEL_SETTING_KEYS
    )


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _contextspace_root(store_root: Path) -> Path:
    explicit = os.environ.get("MINICLAW_CONTEXT_HOME")
    if explicit:
        return Path(explicit).expanduser()
    return store_root / "contextspace"


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
