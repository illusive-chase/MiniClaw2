"""On-disk schema migrations."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import fcntl

import yaml

from .language import normalize_preferred_language
from .model_catalog import (
    get_model_preset,
    legacy_provider_to_model_preset_id,
    legacy_settings_to_model_preset_id,
    normalize_model_preset_id,
)


CURRENT_SCHEMA_VERSION = 3
_SCHEMA_FILE = "schema.json"
_MIGRATION_NAME = "canonical-schema-v3"
_OLD_MODEL_SETTING_KEYS = frozenset(
    {"model", "model_provider", "service_tier", "reasoning_effort"}
)
_OLD_PROJECT_SETTING_KEYS = frozenset({
    "project_context_binding_id",
    "context_binding_id",
    "active_planspace_id",
    "preferred_language",
    "language",
})
_OLD_PROJECT_FIELDS = frozenset({
    "provider",
    "head_commit",
    "parent_project_id",
    "parent_commit",
})
_OLD_NODE_FIELDS = frozenset({
    "provider",
    "sdk_session_id",
    "cli_session_id",
    "context_sources",
})
_OLD_SNAPSHOT_KEYS = _OLD_MODEL_SETTING_KEYS | frozenset({
    "model_preset_id",
    "provider",
})


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
    dry_run: bool = False
    audit_path: Path | None = None


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


def dry_run_store_migration(root: Path) -> StoreMigrationReport:
    """Run the migration against a temporary copy and report planned writes."""

    source_root = root.expanduser()
    source_context_root = _contextspace_root(source_root)
    with tempfile.TemporaryDirectory(prefix="miniclaw2-migration-dry-run-") as raw:
        scratch = Path(raw)
        scratch_root = scratch / "store"
        if source_root.exists():
            shutil.copytree(source_root, scratch_root)
        else:
            scratch_root.mkdir(parents=True)

        context_override: Path | None = None
        if source_context_root != source_root / "contextspace":
            context_override = scratch / "contextspace"
            if source_context_root.exists():
                shutil.copytree(source_context_root, context_override)
            else:
                context_override.mkdir(parents=True)

        previous_context_home = os.environ.get("MINICLAW_CONTEXT_HOME")
        try:
            if context_override is not None:
                os.environ["MINICLAW_CONTEXT_HOME"] = str(context_override)
            report = migrate_store(scratch_root)
        finally:
            if previous_context_home is None:
                os.environ.pop("MINICLAW_CONTEXT_HOME", None)
            else:
                os.environ["MINICLAW_CONTEXT_HOME"] = previous_context_home

    return StoreMigrationReport(
        root=source_root,
        version_before=report.version_before,
        version_after=report.version_after,
        changed_files=report.changed_files,
        backup_root=source_root / "migration-backups",
        dry_run=True,
        audit_path=source_root / "migrations" / f"{_MIGRATION_NAME}.jsonl",
    )


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
        return StoreMigrationReport(
            root=root,
            version_before=version,
            version_after=version,
        )

    timestamp = _utc_timestamp()
    backup_root = root / "migration-backups" / f"{_MIGRATION_NAME}-{timestamp}"
    audit_path = root / "migrations" / f"{_MIGRATION_NAME}.jsonl"
    legacy_shape_counts: dict[str, int] = {}
    changed: list[str] = []
    try:
        legacy_shape_counts = _count_legacy_shapes(root)
        _migrate_projects(root, backup_root, audit_path, changed=changed)
        _migrate_context_bindings(
            root,
            backup_root,
            audit_path,
            changed=changed,
        )
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
                "legacy_shape_counts": legacy_shape_counts,
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
        audit_path=audit_path,
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


def _count_legacy_shapes(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}

    def increment(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    projects_root = root / "projects"
    if projects_root.exists():
        for project_path in projects_root.glob("*/project.json"):
            project = _read_json(project_path)
            for field in _OLD_PROJECT_FIELDS:
                if field in project:
                    increment(f"project.{field}")
            settings = _dict(project.get("settings_override"))
            for field in _OLD_MODEL_SETTING_KEYS | _OLD_PROJECT_SETTING_KEYS:
                if field in settings:
                    increment(f"project.settings_override.{field}")

        for node_path in projects_root.glob("*/nodes/*/node.json"):
            node = _read_json(node_path)
            for field in _OLD_NODE_FIELDS:
                if field in node:
                    increment(f"node.{field}")
            snapshot = _dict(node.get("settings_snapshot"))
            for field in _OLD_SNAPSHOT_KEYS:
                if field in snapshot:
                    increment(f"node.settings_snapshot.{field}")

    for _binding_path, binding in _binding_records(root):
        if "active_planspace_id" in binding:
            increment("binding.active_planspace_id")

    templates_root = _contextspace_root(root) / "templates"
    if templates_root.exists():
        for template_path in templates_root.glob("*/template.yaml"):
            try:
                template = yaml.safe_load(
                    template_path.read_text(encoding="utf-8")
                ) or {}
            except yaml.YAMLError:
                continue
            if not isinstance(template, dict):
                continue
            for field in ("providers", "model_preset_id"):
                if field in template:
                    increment(f"template.{field}")
    return dict(sorted(counts.items()))


def _validate_current_schema(root: Path) -> None:
    """Fail fast when a store claims the current canonical schema."""

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
    _validate_context_bindings_current(root)
    _validate_user_templates_current(root)


def _validate_project_current(path: Path, project: dict[str, Any]) -> str:
    preset_id = _required_preset_id(
        path,
        project.get("model_preset_id"),
        owner="project",
    )
    obsolete_fields = sorted(key for key in _OLD_PROJECT_FIELDS if key in project)
    if obsolete_fields:
        raise StoreMigrationError(
            f"{path}: project contains obsolete fields {obsolete_fields}; "
            "run the canonical schema migration"
        )
    settings = _dict(project.get("settings_override"))
    legacy_keys = sorted(
        key
        for key in (_OLD_MODEL_SETTING_KEYS | _OLD_PROJECT_SETTING_KEYS)
        if key in settings
    )
    if legacy_keys:
        raise StoreMigrationError(
            f"{path}: settings_override contains obsolete keys "
            f"{legacy_keys}; run the canonical schema migration"
        )
    for key in ("project_context_binding_id", "active_planspace_id"):
        value = project.get(key)
        if value is not None and not _string(value):
            raise StoreMigrationError(f"{path}: {key} must be a string or null")
    preferred_language = project.get("preferred_language")
    try:
        normalized_language = normalize_preferred_language(preferred_language)
    except ValueError as exc:
        raise StoreMigrationError(f"{path}: {exc}") from exc
    if preferred_language != normalized_language:
        raise StoreMigrationError(
            f"{path}: preferred_language is not canonical; run the migration"
        )
    return preset_id


def _validate_node_current(path: Path, node: dict[str, Any]) -> str | None:
    obsolete_fields = sorted(key for key in _OLD_NODE_FIELDS if key in node)
    if obsolete_fields:
        raise StoreMigrationError(
            f"{path}: node contains obsolete fields {obsolete_fields}; "
            "run the canonical schema migration"
        )
    kind = _string(node.get("kind")) or "agent"
    snapshot = _dict(node.get("settings_snapshot"))
    _validate_snapshot_if_present(path, snapshot)
    if kind != "agent":
        existing = _string(node.get("model_preset_id"))
        if existing:
            preset_id = _required_preset_id(path, existing, owner="node")
            return preset_id
        return None

    preset_id = _required_preset_id(path, node.get("model_preset_id"), owner="node")
    return preset_id


def _validate_snapshot_if_present(
    path: Path,
    snapshot: dict[str, Any],
) -> None:
    obsolete = sorted(key for key in _OLD_SNAPSHOT_KEYS if key in snapshot)
    if obsolete:
        raise StoreMigrationError(
            f"{path}: settings_snapshot contains derived model fields {obsolete}; "
            "run the canonical schema migration"
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
                f"{template_path}: providers is obsolete; run the schema migration"
            )
        if "model_preset_id" in data:
            raise StoreMigrationError(
                f"{template_path}: model_preset_id is obsolete; "
                "use allowed_model_preset_ids"
            )
        raw = data.get("allowed_model_preset_ids")
        if not isinstance(raw, list) or not raw:
            raise StoreMigrationError(
                f"{template_path}: template must declare allowed_model_preset_ids"
            )
        _normalize_preset_list(template_path, raw)


def _validate_context_bindings_current(root: Path) -> None:
    for binding_path, raw in _binding_records(root):
        if "active_planspace_id" in raw:
            raise StoreMigrationError(
                f"{binding_path}: active_planspace_id is project state and must "
                "not be stored in a binding manifest"
            )


def _required_preset_id(path: Path, raw: Any, *, owner: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise StoreMigrationError(
            f"{path}: {owner} requires model_preset_id; "
            "run the schema migration"
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
        _migrate_project_fields(project_path, project, root=root)
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
    project["settings_override"] = cleaned_settings
    return preset.id


def _migrate_project_fields(
    path: Path,
    project: dict[str, Any],
    *,
    root: Path,
) -> None:
    settings = _dict(project.get("settings_override"))
    binding_id = _strict_selection(
        path,
        "project_context_binding_id",
        (
            (
                "project.project_context_binding_id",
                project.get("project_context_binding_id"),
            ),
            (
                "settings_override.project_context_binding_id",
                settings.get("project_context_binding_id"),
            ),
            (
                "settings_override.context_binding_id",
                settings.get("context_binding_id"),
            ),
        ),
    )
    if binding_id is None:
        binding_id = _discover_project_binding_id(root, project, path=path)

    binding = _load_binding_by_id(root, binding_id) if binding_id else None
    binding_active = binding[1].get("active_planspace_id") if binding else None
    active_planspace_id = _strict_selection(
        path,
        "active_planspace_id",
        (
            ("project.active_planspace_id", project.get("active_planspace_id")),
            (
                "settings_override.active_planspace_id",
                settings.get("active_planspace_id"),
            ),
            ("binding.active_planspace_id", binding_active),
        ),
    )
    if active_planspace_id is None and binding is not None:
        planspace_ids = _binding_planspace_ids(binding[1])
        if len(planspace_ids) == 1:
            active_planspace_id = planspace_ids[0]

    language_raw = project.get("preferred_language")
    if language_raw is None:
        if "preferred_language" in settings:
            language_raw = settings.get("preferred_language")
        elif "language" in settings:
            language_raw = settings.get("language")
    try:
        preferred_language = normalize_preferred_language(language_raw)
    except ValueError as exc:
        raise StoreMigrationError(f"{path}: {exc}") from exc

    for key in _OLD_PROJECT_FIELDS:
        project.pop(key, None)
    for key in _OLD_PROJECT_SETTING_KEYS:
        settings.pop(key, None)
    project["project_context_binding_id"] = binding_id
    project["active_planspace_id"] = active_planspace_id
    project["preferred_language"] = preferred_language
    project["settings_override"] = settings


def _strict_selection(
    path: Path,
    field: str,
    candidates: tuple[tuple[str, Any], ...],
) -> str | None:
    resolved: list[tuple[str, str]] = []
    for source, raw in candidates:
        value = _string(raw)
        if value is not None:
            resolved.append((source, value.strip()))
    unique = {value for _, value in resolved}
    if len(unique) > 1:
        details = ", ".join(f"{source}={value!r}" for source, value in resolved)
        raise StoreMigrationError(
            f"{path}: conflicting {field} values: {details}"
        )
    return resolved[0][1] if resolved else None


def _binding_records(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    bindings_root = _contextspace_root(root) / "bindings" / "projects"
    if not bindings_root.exists():
        return []
    records: list[tuple[Path, dict[str, Any]]] = []
    for binding_path in sorted(bindings_root.glob("*.yaml")):
        try:
            raw = yaml.safe_load(binding_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise StoreMigrationError(
                f"{binding_path}: invalid binding YAML: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise StoreMigrationError(f"{binding_path}: binding must be a mapping")
        records.append((binding_path, raw))
    return records


def _load_binding_by_id(
    root: Path,
    binding_id: str,
) -> tuple[Path, dict[str, Any]] | None:
    for binding_path, raw in _binding_records(root):
        candidate_id = _string(raw.get("id")) or binding_path.stem
        if candidate_id == binding_id:
            return binding_path, raw
    return None


def _discover_project_binding_id(
    root: Path,
    project: dict[str, Any],
    *,
    path: Path,
) -> str | None:
    project_id = _string(project.get("id"))
    root_path = _string(project.get("root_path"))
    owned: list[str] = []
    matched: list[str] = []
    for binding_path, raw in _binding_records(root):
        binding_id = _string(raw.get("id")) or binding_path.stem
        project_raw = _dict(raw.get("project"))
        if project_id and _string(project_raw.get("miniclaw_project_id")) == project_id:
            owned.append(binding_id)
            continue
        if root_path and _binding_matches_root(project_raw, root_path):
            matched.append(binding_id)
    candidates = owned or matched
    unique = sorted(set(candidates))
    if len(unique) > 1:
        raise StoreMigrationError(
            f"{path}: multiple ContextSpace bindings match project: {unique}"
        )
    return unique[0] if unique else None


def _binding_matches_root(project_raw: dict[str, Any], root_path: str) -> bool:
    try:
        expected = Path(root_path).expanduser().resolve()
    except OSError:
        expected = Path(root_path).expanduser()
    local_paths = project_raw.get("local_paths")
    if not isinstance(local_paths, list):
        return False
    for raw in local_paths:
        if not isinstance(raw, str):
            continue
        try:
            candidate = Path(raw).expanduser().resolve()
        except OSError:
            candidate = Path(raw).expanduser()
        if candidate == expected:
            return True
    return False


def _binding_planspace_ids(binding: dict[str, Any]) -> list[str]:
    plugs = binding.get("plugs")
    if not isinstance(plugs, list):
        return []
    out: list[str] = []
    for item in plugs:
        if isinstance(item, str):
            plug_id = item
            enabled = True
        elif isinstance(item, dict):
            plug_id = item.get("id")
            enabled = bool(item.get("enabled", True))
        else:
            continue
        if (
            enabled
            and isinstance(plug_id, str)
            and plug_id.startswith("planspaces.")
            and plug_id not in out
        ):
            out.append(plug_id)
    return out


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
    node_legacy_provider = _string(node.get("provider"))
    provider_session_id = _string(node.get("provider_session_id"))
    if provider_session_id is None:
        provider_session_id = _string(node.get("cli_session_id"))
    if provider_session_id is None:
        provider_session_id = _string(node.get("sdk_session_id"))
    if provider_session_id is not None:
        node["provider_session_id"] = provider_session_id
    else:
        node.pop("provider_session_id", None)
    for key in _OLD_NODE_FIELDS:
        node.pop(key, None)
    if kind != "agent":
        existing = _string(node.get("model_preset_id"))
        if existing:
            try:
                preset = get_model_preset(existing)
            except ValueError as exc:
                raise StoreMigrationError(f"{path}: {exc}") from exc
            node["model_preset_id"] = preset.id
        node["settings_snapshot"] = _clean_snapshot(snapshot)
        return node.get("model_preset_id")

    existing = _string(node.get("model_preset_id"))
    if existing:
        try:
            preset_id = normalize_model_preset_id(existing)
        except ValueError as exc:
            raise StoreMigrationError(f"{path}: {exc}") from exc
    else:
        provider = node_legacy_provider or project_legacy_provider
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
    node["settings_snapshot"] = _clean_snapshot(snapshot)
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


def _migrate_context_bindings(
    root: Path,
    backup_root: Path,
    audit_path: Path,
    *,
    changed: list[str],
) -> None:
    for binding_path, raw in _binding_records(root):
        raw.pop("active_planspace_id", None)
        changed.extend(
            _write_yaml_if_changed(
                binding_path,
                raw,
                root,
                backup_root,
                audit_path,
                action="migrate_context_binding",
            )
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
        if key not in _OLD_SNAPSHOT_KEYS
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
