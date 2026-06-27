"""ContextSpace bindings, plugs, and per-launch context bundles.

Planspace plugs are manifest-only; the agent-facing lane state is the
materialized filesystem projection under ``.miniclaw2/graph/lanes/``
(see ``materialize.py`` / ``reap.py``). This module:

  - Resolves the contextspace root and the project's binding.
  - Composes the per-launch ``ComposedContextBundle``: project-root
    ``CONTEXT.md`` plus injection-mode markdown from global and skill
    plugs. The active planspace id is propagated so the runner knows
    which lane to materialize.
  - Persists an audit snapshot of every bundle under
    ``snapshots/<bundle-id>.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from .domain import Node, PlanspaceMode, Project, normalize_planspace_mode


@dataclass(slots=True)
class PlugRef:
    id: str
    role: str = ""
    injection: str | None = None
    enabled: bool = True
    source: str = "binding"


@dataclass(slots=True)
class ProjectBinding:
    id: str
    path: Path
    plugs: list[PlugRef] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ComposedContextBundle:
    bundle_id: str
    project_context: str
    system_text: str
    turn_text: str
    sources: list[dict[str, Any]]
    project_binding_id: str | None
    context_root: Path
    bundle_path: Path
    active_planspace_id: str | None = None


def contextspace_root(store_root: Path | None = None) -> Path:
    """Resolve the contextspace root from env or store root."""
    explicit = os.environ.get("MINICLAW_CONTEXT_HOME")
    if explicit:
        return Path(explicit).expanduser()
    if store_root is not None:
        return Path(store_root).expanduser() / "contextspace"
    base = os.environ.get("MINICLAW_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".miniclaw2"
    return root / "contextspace"


def compose_context_bundle(
    project: Project,
    node: Node,
    *,
    store_root: Path | None = None,
) -> ComposedContextBundle:
    """Compose and persist the context bundle seen at node launch.

    Loads project-root ``CONTEXT.md`` and any markdown from bound
    ``global`` / ``skill`` plugs. The active planspace id is carried in
    the returned bundle so the runner can materialize the lane;
    planspace plug content itself is no longer injected into the LLM
    projection (the materialized filesystem subtree replaces that).
    """
    root = contextspace_root(store_root)
    binding = resolve_project_binding(project, root)

    sources: list[dict[str, Any]] = []
    system_parts: list[str] = []
    turn_sections: list[str] = []

    project_context = ""
    project_context_source = _read_context_source(
        Path(project.root_path) / "CONTEXT.md",
        scope="project-root",
        kind="code-guidance",
        injection="system",
        context_root=root,
    )
    if project_context_source is not None:
        source, text = project_context_source
        project_context = text
        sources.append(source)
        if text:
            system_parts.append(text)

    active_planspace_id: str | None = None
    if binding is not None:
        plug_refs = _expand_required_plugs(root, binding.plugs)
        active_planspace = _select_active_planspace(project, binding, plug_refs)
        if active_planspace is not None:
            active_planspace_id = active_planspace.id

        for ref in plug_refs:
            kind = _plug_kind(ref.id)
            if kind == "planspace":
                # Planspace plugs are manifest-only — the agent reads
                # node previews via the materialized filesystem.
                continue
            if kind in {"skill", "global"}:
                loaded = _load_context_markdown_source(root, ref, kind)
                if loaded is None:
                    continue
                source, text = loaded
                sources.append(source)
                if source.get("injection") == "system":
                    system_parts.append(_section_text(source, text))
                elif source.get("injection") == "turn":
                    turn_sections.append(_section_text(source, text))

    system_text = _join_nonempty(system_parts)
    turn_text = _join_nonempty(turn_sections)
    bundle_id = uuid4().hex[:12]
    bundle_path = root / "snapshots" / f"{bundle_id}.json"
    bundle = {
        "bundle_id": bundle_id,
        "created_at": time.time(),
        "project_id": project.id,
        "node_id": node.id,
        "project_binding_id": binding.id if binding else None,
        "active_planspace_id": active_planspace_id,
        "sources": sources,
        "system_text": system_text,
        "turn_text": turn_text,
    }
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return ComposedContextBundle(
        bundle_id=bundle_id,
        project_context=project_context,
        system_text=system_text,
        turn_text=turn_text,
        sources=sources,
        project_binding_id=binding.id if binding else None,
        context_root=root,
        bundle_path=bundle_path,
        active_planspace_id=active_planspace_id,
    )


def load_context_bundle_for_node(
    node: Node,
    *,
    store_root: Path | None = None,
) -> dict[str, Any] | None:
    """Load a persisted context bundle referenced by ``node``."""
    root = contextspace_root(store_root)
    if node.context_bundle_path:
        path = Path(node.context_bundle_path)
        if not path.is_absolute():
            path = root / path
    elif node.context_bundle_id:
        path = root / "snapshots" / f"{node.context_bundle_id}.json"
    else:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def ensure_contextspace_root(
    store_root: Path | None = None,
    *,
    created: list[str] | None = None,
) -> Path:
    """Idempotently create the contextspace root files."""
    root = contextspace_root(store_root)
    root.mkdir(parents=True, exist_ok=True)
    created_items = created if created is not None else []

    _write_yaml_if_missing(
        root / "contextspace.yaml",
        {
            "version": 1,
            "kind": "contextspace",
            "name": "default",
            "created_by": "miniclaw2",
            "git": {"expected": True},
            "defaults": {
                "context_budget": {
                    "system_max_chars": 8000,
                    "turn_max_chars": 6000,
                },
                "auto_commit": False,
            },
        },
        root,
        created_items,
    )
    _write_text_if_missing(
        root / "README.md",
        "# MiniClaw2 ContextSpace\n\n"
        "This repository stores MiniClaw2 planspaces, bindings, skills, "
        "and context snapshots outside code repositories.\n",
        root,
        created_items,
    )
    return root


def ensure_project_binding(
    project: Project,
    *,
    store_root: Path | None = None,
    binding_slug: str | None = None,
    created: list[str] | None = None,
) -> ProjectBinding:
    """Return the project's binding, creating an empty one when needed."""
    root = ensure_contextspace_root(store_root, created=created)
    existing = resolve_project_binding(project, root)
    if existing is not None:
        return existing

    project_title = _project_title(project)
    base_slug = _slugify(binding_slug or project_title or "project")
    slug = _unique_binding_slug(root, base_slug)
    binding_id = f"project.{slug}"
    binding_path = root / "bindings" / "projects" / f"{binding_id}.yaml"
    raw = {
        "version": 1,
        "id": binding_id,
        "project": {
            "name": project_title,
            "miniclaw_project_id": project.id,
            "root_fingerprint": {
                "root_name": Path(project.root_path).name,
            },
            "local_paths": [project.root_path],
        },
        "plugs": [],
    }
    _write_yaml_if_missing(binding_path, raw, root, created if created is not None else [])
    binding = _load_binding_file(binding_path)
    if binding is None:
        raise RuntimeError(f"failed to create binding: {binding_id}")
    return binding


def list_project_bindings(root: Path) -> list[ProjectBinding]:
    bindings_dir = root / "bindings" / "projects"
    if not bindings_dir.exists():
        return []
    out: list[ProjectBinding] = []
    for path in sorted(bindings_dir.glob("*.yaml")):
        binding = _load_binding_file(path)
        if binding is not None:
            out.append(binding)
    return out


def delete_project_contextspace(
    project: Project,
    *,
    store_root: Path | None = None,
) -> dict[str, Any]:
    """Delete ContextSpace bindings and (private) planspace plugs owned by
    the project. Planspaces that another binding still references are
    retained.
    """
    root = contextspace_root(store_root)
    summary: dict[str, Any] = {
        "root": str(root),
        "deleted_bindings": [],
        "deleted_planspaces": [],
        "retained_shared_planspaces": [],
        "skipped_planspaces": [],
    }
    if not root.exists():
        return summary

    bindings = list_project_bindings(root)
    target_bindings = _bindings_for_project_contextspace_delete(project, bindings)
    if not target_bindings:
        return summary

    target_binding_ids = {binding.id for binding in target_bindings}
    target_planspace_ids = sorted(
        {
            ref.id
            for binding in target_bindings
            for ref in binding.plugs
            if _plug_kind(ref.id) == "planspace"
        }
    )
    referenced_by_other_bindings = {
        ref.id
        for binding in bindings
        if binding.id not in target_binding_ids
        for ref in binding.plugs
        if _plug_kind(ref.id) == "planspace"
    }

    planspaces_root = root / "plugs" / "planspaces"
    for planspace_id in target_planspace_ids:
        if planspace_id in referenced_by_other_bindings:
            summary["retained_shared_planspaces"].append(planspace_id)
            continue
        plug_dir = _plug_dir(root, planspace_id)
        if plug_dir is None:
            summary["skipped_planspaces"].append(planspace_id)
            continue
        try:
            resolved_dir = plug_dir.resolve()
            resolved_root = planspaces_root.resolve()
        except OSError:
            summary["skipped_planspaces"].append(planspace_id)
            continue
        if resolved_dir.parent != resolved_root:
            summary["skipped_planspaces"].append(planspace_id)
            continue
        if plug_dir.exists():
            shutil.rmtree(plug_dir)
            summary["deleted_planspaces"].append(planspace_id)

    for binding in target_bindings:
        if binding.path.exists():
            binding.path.unlink()
            summary["deleted_bindings"].append(binding.id)

    return summary


def describe_project_contextspace(
    project: Project,
    *,
    store_root: Path | None = None,
) -> dict[str, Any]:
    """Return a slim UI-facing summary of the project's contextspace."""
    from .context_refresh import context_refresh_status

    root = contextspace_root(store_root)
    binding = resolve_project_binding(project, root)
    active = resolve_active_planspace(project, root)
    active_planspace_id = active[1].id if active is not None else None
    all_bindings = list_project_bindings(root)
    bindings = [binding] if binding is not None else []
    return {
        "root": str(root),
        "exists": root.exists(),
        "project_context_binding_id": project.project_context_binding_id,
        "project_active_planspace_id": _string_setting(
            project, "active_planspace_id"
        ),
        "resolved_binding_id": binding.id if binding else None,
        "active_planspace_id": active_planspace_id,
        "planspace_view": project.planspace_view,
        "context_file": {
            "exists": (Path(project.root_path) / "CONTEXT.md").exists(),
        },
        "context_refresh": context_refresh_status(project.id),
        "bindings": [
            _binding_summary(
                root,
                project,
                item,
                resolved_binding_id=binding.id if binding else None,
                active_planspace_id=active_planspace_id,
            )
            for item in bindings
        ],
        "selectable_bindings": [
            _binding_summary(
                root,
                project,
                item,
                resolved_binding_id=binding.id if binding else None,
                active_planspace_id=active_planspace_id,
            )
            for item in all_bindings
        ],
    }


def read_project_context(project: Project) -> dict[str, Any] | None:
    """Read the project-root ``CONTEXT.md`` for UI display.

    Returns ``{role, path, text, mtime, last_writer}`` or ``None`` if the
    file is missing.
    """
    path = Path(project.root_path) / "CONTEXT.md"
    try:
        stat = path.stat()
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return {
        "role": "context",
        "path": str(path),
        "text": text,
        "mtime": stat.st_mtime,
        "last_writer": _project_context_last_writer(
            Path(project.root_path),
            stat.st_mtime,
        ),
    }


def _project_context_last_writer(root: Path, context_mtime: float) -> dict[str, Any]:
    meta_path = root / ".miniclaw2" / "context.meta.json"
    try:
        meta_stat = meta_path.stat()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"kind": "hand"}
    source = meta.get("source")
    updated_at = meta.get("updated_at")
    if (
        isinstance(source, str)
        and source in {"init", "refresh"}
        and isinstance(updated_at, (int, float))
        and meta_stat.st_mtime + 1.0 >= context_mtime
    ):
        return {
            "kind": "context-refresh",
            "updated_at": float(updated_at),
            "source": source,
        }
    previous = source if isinstance(source, str) else None
    out: dict[str, Any] = {"kind": "hand"}
    if previous:
        out["previous"] = previous
    return out


def read_planspace_mode(
    project: Project,
    lane_id: str,
    *,
    store_root: Path | None = None,
) -> PlanspaceMode:
    """Return the planspace plug's ``mode``.

    ``lane_id`` is the plug id (e.g. ``planspaces.foo``). Defaults to
    ``MANUAL`` when the manifest is missing or the field is unset.
    """
    del project  # reserved for future per-project override
    if not lane_id:
        return PlanspaceMode.MANUAL
    root = contextspace_root(store_root)
    manifest = _plug_manifest(root, lane_id)
    raw = manifest.get("mode") if isinstance(manifest, dict) else None
    try:
        return normalize_planspace_mode(raw if isinstance(raw, str) else None)
    except ValueError:
        return PlanspaceMode.MANUAL


def set_planspace_mode(
    project: Project,
    lane_id: str,
    mode: PlanspaceMode | str,
    *,
    store_root: Path | None = None,
) -> PlanspaceMode:
    """Persist ``mode`` to a planspace plug manifest and return it."""
    del project  # reserved for future per-project override
    if not lane_id:
        raise ValueError("planspace id is required")
    normalized = (
        mode if isinstance(mode, PlanspaceMode) else normalize_planspace_mode(mode)
    )
    root = contextspace_root(store_root)
    plug_dir = _plug_dir(root, lane_id)
    if plug_dir is None or _plug_kind(lane_id) != "planspace":
        raise ValueError(f"unknown planspace: {lane_id}")
    manifest_path = plug_dir / "manifest.yaml"
    raw = _read_yaml(manifest_path)
    if not isinstance(raw, dict):
        raise ValueError(f"unknown planspace: {lane_id}")
    raw["mode"] = normalized.value
    _write_yaml(manifest_path, raw)
    return normalized


def create_planspace(
    project: Project,
    *,
    title: str,
    mode: PlanspaceMode | str = PlanspaceMode.MANUAL,
    store_root: Path | None = None,
    seed_text: str | None = None,
) -> str:
    """Create a new planspace plug + add it to the project's binding.

    Returns the new plug id (``planspaces.<slug>``). The binding is
    created if it does not exist. Manifest carries ``mode`` for the
    auto-promotion scheduler.
    """
    mode_value = (
        mode if isinstance(mode, PlanspaceMode) else normalize_planspace_mode(mode)
    )
    binding = ensure_project_binding(project, store_root=store_root)
    root = contextspace_root(store_root)
    slug = _unique_planspace_slug(root, _slugify(title or "direction"))
    plug_id = f"planspaces.{slug}"
    plug_dir = root / "plugs" / "planspaces" / slug
    plug_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "version": 1,
        "kind": "planspace",
        "id": plug_id,
        "title": title,
        "mode": mode_value.value,
        "created_at": time.time(),
    }
    if isinstance(seed_text, str) and seed_text.strip():
        manifest["seed"] = seed_text
    _write_yaml(plug_dir / "manifest.yaml", manifest)
    add_planspace_to_binding(binding, plug_id)
    return plug_id


def add_planspace_to_binding(
    binding: ProjectBinding,
    plug_id: str,
) -> None:
    """Append a planspace plug ref to ``binding`` if not already present."""
    raw = dict(binding.raw)
    plugs = list(raw.get("plugs") or [])
    if any(_extract_plug_id(item) == plug_id for item in plugs):
        return
    plugs.append({"id": plug_id, "enabled": True})
    raw["plugs"] = plugs
    binding.raw = raw
    binding.plugs.append(PlugRef(id=plug_id))
    _write_yaml(binding.path, raw)


def resolve_project_binding(project: Project, root: Path) -> ProjectBinding | None:
    explicit = (
        project.project_context_binding_id
        or _string_setting(project, "project_context_binding_id")
        or _string_setting(project, "context_binding_id")
    )
    if explicit:
        return _load_binding_by_id(root, explicit)
    return _find_binding_for_project_path(root, project.root_path)


def resolve_active_planspace(
    project: Project,
    root: Path,
) -> tuple[ProjectBinding, PlugRef, Path] | None:
    binding = resolve_project_binding(project, root)
    if binding is None:
        return None
    refs = _expand_required_plugs(root, binding.plugs)
    ref = _select_active_planspace(project, binding, refs)
    if ref is None:
        return None
    plug_dir = _plug_dir(root, ref.id)
    if plug_dir is None:
        return None
    return binding, ref, plug_dir


# ----------------- internal helpers -----------------


def _load_binding_by_id(root: Path, binding_id: str) -> ProjectBinding | None:
    direct = root / "bindings" / "projects" / f"{binding_id}.yaml"
    if direct.exists():
        return _load_binding_file(direct)
    for path in sorted((root / "bindings" / "projects").glob("*.yaml")):
        binding = _load_binding_file(path)
        if binding is not None and binding.id == binding_id:
            return binding
    return None


def _find_binding_for_project_path(root: Path, root_path: str) -> ProjectBinding | None:
    for binding in list_project_bindings(root):
        if _binding_matches_project_path(binding, root_path):
            return binding
    return None


def _load_binding_file(path: Path) -> ProjectBinding | None:
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        return None
    binding_id = raw.get("id")
    if not isinstance(binding_id, str) or not binding_id:
        binding_id = path.stem
    plugs = [_plug_ref(item) for item in (raw.get("plugs") or [])]
    return ProjectBinding(
        id=binding_id,
        path=path,
        plugs=[plug for plug in plugs if plug is not None],
        raw=raw,
    )


def _bindings_for_project_contextspace_delete(
    project: Project,
    bindings: list[ProjectBinding],
) -> list[ProjectBinding]:
    explicit_ids = _project_context_binding_ids(project)
    out: list[ProjectBinding] = []
    for binding in bindings:
        owner_id = _binding_project_owner_id(binding)
        if owner_id == project.id:
            out.append(binding)
            continue
        if binding.id in explicit_ids and owner_id is None:
            out.append(binding)
    if out:
        return out
    return [
        binding
        for binding in bindings
        if _binding_project_owner_id(binding) is None
        and _binding_matches_project_path(binding, project.root_path)
    ]


def _project_context_binding_ids(project: Project) -> set[str]:
    out: set[str] = set()
    for value in (
        project.project_context_binding_id,
        _string_setting(project, "project_context_binding_id"),
        _string_setting(project, "context_binding_id"),
    ):
        if value:
            out.add(value)
    return out


def _binding_project_owner_id(binding: ProjectBinding) -> str | None:
    project_raw = binding.raw.get("project") if isinstance(binding.raw, dict) else None
    if not isinstance(project_raw, dict):
        return None
    return _string_value(project_raw.get("miniclaw_project_id"))


def _binding_matches_project_path(binding: ProjectBinding, root_path: str) -> bool:
    try:
        project_root = Path(root_path).resolve()
    except OSError:
        project_root = Path(root_path)
    project = binding.raw.get("project") or {}
    if not isinstance(project, dict):
        return False
    local_paths = project.get("local_paths") or []
    for candidate in local_paths:
        if not isinstance(candidate, str):
            continue
        try:
            if Path(candidate).expanduser().resolve() == project_root:
                return True
        except OSError:
            if Path(candidate).expanduser() == project_root:
                return True
    return False


def _plug_ref(item: Any) -> PlugRef | None:
    if isinstance(item, str):
        return PlugRef(id=item)
    if not isinstance(item, dict):
        return None
    plug_id = item.get("id")
    if not isinstance(plug_id, str) or not plug_id:
        return None
    role = item.get("role")
    injection = item.get("injection")
    return PlugRef(
        id=plug_id,
        role=role if isinstance(role, str) else "",
        injection=injection if isinstance(injection, str) else None,
        enabled=bool(item.get("enabled", True)),
    )


def _expand_required_plugs(root: Path, plugs: list[PlugRef]) -> list[PlugRef]:
    out = [plug for plug in plugs if plug.enabled]
    seen = {plug.id for plug in out}
    idx = 0
    while idx < len(out):
        plug = out[idx]
        idx += 1
        manifest = _plug_manifest(root, plug.id)
        requires = manifest.get("requires") or []
        if not isinstance(requires, list):
            continue
        for req_id in requires:
            if not isinstance(req_id, str) or not req_id or req_id in seen:
                continue
            seen.add(req_id)
            out.append(PlugRef(id=req_id, source=f"requires:{plug.id}"))
    return out


def _select_active_planspace(
    project: Project,
    binding: ProjectBinding,
    plugs: list[PlugRef],
) -> PlugRef | None:
    planspaces = [plug for plug in plugs if _plug_kind(plug.id) == "planspace"]
    if not planspaces:
        return None
    requested = (
        _string_setting(project, "active_planspace_id")
        or _string_value(binding.raw.get("active_planspace_id"))
    )
    if requested:
        for plug in planspaces:
            if plug.id == requested or _plug_slug(plug.id) == requested:
                return plug
        return None
    if len(planspaces) == 1:
        return planspaces[0]
    return None


def _binding_summary(
    root: Path,
    project: Project,
    binding: ProjectBinding,
    *,
    resolved_binding_id: str | None,
    active_planspace_id: str | None,
) -> dict[str, Any]:
    project_raw = binding.raw.get("project")
    if not isinstance(project_raw, dict):
        project_raw = {}
    local_paths = [
        value for value in (project_raw.get("local_paths") or [])
        if isinstance(value, str)
    ]
    binding_active = _string_value(binding.raw.get("active_planspace_id"))
    is_resolved = binding.id == resolved_binding_id
    active_for_binding = active_planspace_id if is_resolved else binding_active
    plug_refs = _expand_required_plugs(root, binding.plugs)
    return {
        "id": binding.id,
        "path": _display_path(binding.path, root),
        "title": (
            _string_value(binding.raw.get("title"))
            or _string_value(project_raw.get("name"))
            or binding.id
        ),
        "project_name": _string_value(project_raw.get("name")),
        "local_paths": local_paths,
        "matches_project_path": _binding_matches_project_path(
            binding, project.root_path
        ),
        "active_planspace_id": active_for_binding,
        "binding_active_planspace_id": binding_active,
        "plugs": [
            _plug_summary(
                root,
                project,
                ref,
                active=(ref.id == active_for_binding),
            )
            for ref in plug_refs
        ],
    }


def _plug_summary(
    root: Path,
    project: Project,
    ref: PlugRef,
    *,
    active: bool,
) -> dict[str, Any]:
    kind = _plug_kind(ref.id)
    slug = _plug_slug(ref.id)
    plug_dir = _plug_dir(root, ref.id)
    manifest = _plug_manifest(root, ref.id)
    title = (
        _string_value(manifest.get("title"))
        or _string_value(manifest.get("name"))
        or slug
    )
    mode: str | None = None
    if kind == "planspace":
        raw_mode = manifest.get("mode") if isinstance(manifest, dict) else None
        try:
            mode = normalize_planspace_mode(
                raw_mode if isinstance(raw_mode, str) else None
            ).value
        except ValueError:
            mode = PlanspaceMode.MANUAL.value
    summary: dict[str, Any] = {
        "id": ref.id,
        "kind": kind,
        "slug": slug,
        "role": ref.role,
        "injection": ref.injection,
        "enabled": ref.enabled,
        "auto_update": False,
        "source": ref.source,
        "active": active,
        "hidden": bool(project.planspace_view.get(ref.id, {}).get("hidden")),
        "exists": bool(plug_dir and plug_dir.exists()),
        "path": _display_path(plug_dir, root) if plug_dir is not None else None,
        "title": title,
        "description": _string_value(manifest.get("description")),
        "color": _string_value(manifest.get("color")),
    }
    if mode is not None:
        summary["mode"] = mode
    return summary


def _load_context_markdown_source(
    root: Path,
    ref: PlugRef,
    kind: str,
) -> tuple[dict[str, Any], str] | None:
    plug_dir = _plug_dir(root, ref.id)
    if plug_dir is None:
        return None
    manifest = _plug_manifest(root, ref.id)
    default_injection = "system" if kind == "global" else "turn"
    injection = _injection_for(ref, manifest, "CONTEXT.md", default_injection)
    if injection == "none":
        return None
    return _read_context_source(
        plug_dir / "CONTEXT.md",
        scope="contextspace",
        kind=kind,
        injection=injection,
        context_root=root,
        plug_id=ref.id,
        max_chars=_max_chars_for(manifest, "CONTEXT.md", 6000),
    )


def _read_context_source(
    path: Path,
    *,
    scope: str,
    kind: str,
    injection: str,
    context_root: Path,
    plug_id: str | None = None,
    max_chars: int | None = None,
) -> tuple[dict[str, Any], str] | None:
    try:
        raw = path.read_bytes()
        full_text = raw.decode("utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None
    text = full_text
    truncated = False
    if max_chars is not None and max_chars >= 0 and len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    source: dict[str, Any] = {
        "scope": scope,
        "kind": kind,
        "path": _display_path(path, context_root) if scope == "contextspace" else str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "chars": len(text),
        "raw_chars": len(full_text),
        "injection": injection,
    }
    if plug_id is not None:
        source["plug_id"] = plug_id
    if truncated:
        source["truncated"] = True
    return source, text


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def _write_yaml_if_missing(
    path: Path,
    data: dict[str, Any],
    root: Path,
    created: list[str],
) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    created.append(_display_path(path, root))


def _write_text_if_missing(
    path: Path,
    text: str,
    root: Path,
    created: list[str],
) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    created.append(_display_path(path, root))


def _project_title(project: Project, override: str | None = None) -> str:
    if isinstance(override, str) and override.strip():
        return override.strip()
    if project.name.strip():
        return project.name.strip()
    return Path(project.root_path).name or "Project"


def _plug_manifest(root: Path, plug_id: str) -> dict[str, Any]:
    plug_dir = _plug_dir(root, plug_id)
    if plug_dir is None:
        return {}
    raw = _read_yaml(plug_dir / "manifest.yaml")
    return raw if isinstance(raw, dict) else {}


def _plug_dir(root: Path, plug_id: str) -> Path | None:
    kind = _plug_kind(plug_id)
    slug = _plug_slug(plug_id)
    if kind == "planspace":
        return root / "plugs" / "planspaces" / slug
    if kind == "skill":
        return root / "plugs" / "skills" / slug
    if kind == "global":
        if slug == "default":
            return root / "plugs" / "global"
        return root / "plugs" / "global" / slug
    return None


def _plug_kind(plug_id: str) -> str:
    if plug_id.startswith("planspaces."):
        return "planspace"
    if plug_id.startswith("skills."):
        return "skill"
    if plug_id.startswith("global."):
        return "global"
    return "unknown"


def _plug_slug(plug_id: str) -> str:
    return plug_id.split(".", 1)[1] if "." in plug_id else plug_id


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "project"


def _unique_binding_slug(root: Path, slug: str) -> str:
    bindings_dir = root / "bindings" / "projects"
    candidate = slug
    index = 2
    while (bindings_dir / f"project.{candidate}.yaml").exists():
        candidate = f"{slug}-{index}"
        index += 1
    return candidate


def _unique_planspace_slug(root: Path, slug: str) -> str:
    planspaces_dir = root / "plugs" / "planspaces"
    candidate = slug
    index = 2
    while (planspaces_dir / candidate).exists():
        candidate = f"{slug}-{index}"
        index += 1
    return candidate


def _extract_plug_id(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        value = item.get("id")
        if isinstance(value, str) and value:
            return value
    return None


def _injection_for(
    ref: PlugRef,
    manifest: dict[str, Any],
    filename: str,
    default: str,
) -> str:
    if ref.injection:
        return ref.injection
    raw = manifest.get("injection")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        value = raw.get(filename)
        if isinstance(value, str):
            return value
    return default


def _max_chars_for(
    manifest: dict[str, Any],
    filename: str,
    default: int | None,
) -> int | None:
    raw = manifest.get("max_chars")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, dict):
        value = raw.get(filename)
        if isinstance(value, int):
            return value
    return default


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, yaml.YAMLError):
        return None


def _section_text(source: dict[str, Any], text: str) -> str:
    label = source.get("plug_id") or source.get("path") or source.get("kind")
    kind = source.get("kind") or "context"
    return f"# Loaded Context: {label} ({kind})\n\n{text.strip()}\n"


def _join_nonempty(parts: list[str]) -> str:
    return "\n\n".join(part for part in parts if part and part.strip())


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _string_setting(project: Project, key: str) -> str | None:
    return _string_value(project.settings_override.get(key))


def _string_value(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
