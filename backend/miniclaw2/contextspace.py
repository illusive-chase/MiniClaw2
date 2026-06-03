"""ContextSpace bindings, context bundle snapshots, and memory deltas.

The first implementation slice is deliberately filesystem-first:
bindings and plugs are small YAML/Markdown files under
``$MINICLAW_CONTEXT_HOME`` or ``$MINICLAW_HOME/contextspace``. The
runtime remains tolerant of missing files so projects without an
explicit ContextSpace binding keep the legacy ``CONTEXT.md`` behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from .domain import Node, Project


@dataclass(slots=True)
class PlugRef:
    id: str
    role: str = ""
    injection: str | None = None
    enabled: bool = True
    auto_update: bool = False
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
    """Return the configured ContextSpace root.

    ``MINICLAW_CONTEXT_HOME`` wins. Otherwise, when a custom Store root is
    used (common in tests), the ContextSpace lives beside its projects
    directory. Finally we fall back to ``$MINICLAW_HOME/contextspace`` or
    ``~/.miniclaw2/contextspace``.
    """

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
    """Compose and persist the context bundle seen at node launch."""

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

    active_planspace: PlugRef | None = None
    active_planspace_id: str | None = None
    if binding is not None:
        plug_refs = _expand_required_plugs(root, binding.plugs)
        active_planspace = _select_active_planspace(project, binding, plug_refs)
        if active_planspace is not None:
            active_planspace_id = active_planspace.id

        for ref in plug_refs:
            kind = _plug_kind(ref.id)
            if kind == "planspace" and (
                active_planspace is None or ref.id != active_planspace.id
            ):
                continue
            if kind == "planspace":
                for source, text in _load_planspace_sources(root, ref):
                    sources.append(source)
                    if source.get("injection") == "system":
                        system_parts.append(_section_text(source, text))
                    elif source.get("injection") == "turn":
                        turn_sections.append(_section_text(source, text))
            elif kind in {"skill", "global"}:
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
        "active_planspace": _snapshot_planspace_ref(active_planspace),
        "sources": sources,
        "system_text": system_text,
        "turn_text": turn_text,
    }
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

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


def apply_memory_delta_inbox(
    project: Project,
    node: Node,
    *,
    store_root: Path | None = None,
) -> dict[str, Any]:
    """Apply auto-approved STATUS.md updates from the active planspace inbox.

    Only ``STATUS.md`` / ``append_observation`` / ``policy: auto`` updates
    are applied in this first slice. PLAN.md and durable plug updates are
    intentionally ignored until an approval workflow exists.
    """

    root = contextspace_root(store_root)
    bundle = load_context_bundle_for_node(node, store_root=root.parent)
    if bundle is None and (node.context_bundle_id or node.context_bundle_path):
        return {"applied": 0, "reason": "context_bundle_not_found"}
    if bundle is None:
        active = _memory_delta_route_from_current_binding(project, root)
        if active is None:
            return {"applied": 0, "reason": "no_active_planspace"}
    else:
        active = _memory_delta_route_from_bundle(bundle, root)
        if active is None:
            return {"applied": 0, "reason": "no_active_planspace_snapshot"}

    binding_id, planspace_id, planspace_dir, auto_update = active
    if not auto_update:
        return {
            "applied": 0,
            "reason": "planspace_auto_update_disabled",
            "binding_id": binding_id,
            "planspace_id": planspace_id,
        }

    delta_path = planspace_dir / "inbox" / f"{node.id}.memory-delta.json"
    if not delta_path.exists():
        return {
            "applied": 0,
            "reason": "no_delta",
            "binding_id": binding_id,
            "planspace_id": planspace_id,
        }

    try:
        delta = json.loads(delta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "applied": 0,
            "reason": f"invalid_delta: {exc}",
            "binding_id": binding_id,
            "planspace_id": planspace_id,
        }

    applied: list[dict[str, Any]] = []
    for update in delta.get("updates") or []:
        if not isinstance(update, dict):
            continue
        if update.get("target") != "STATUS.md":
            continue
        if update.get("operation") != "append_observation":
            continue
        if update.get("policy") != "auto":
            continue
        text = update.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        _append_status_observation(planspace_dir / "STATUS.md", node, text)
        applied.append({
            "target": "STATUS.md",
            "operation": "append_observation",
            "chars": len(text),
        })

    if applied:
        _append_planspace_event(
            planspace_dir / "events.jsonl",
            {
                "type": "memory_delta_applied",
                "node_id": node.id,
                "project_id": project.id,
                "binding_id": binding_id,
                "planspace_id": planspace_id,
                "created_at": time.time(),
                "updates": applied,
            },
        )

    return {
        "applied": len(applied),
        "binding_id": binding_id,
        "planspace_id": planspace_id,
        "delta_path": _display_path(delta_path, root),
    }


def resolve_project_binding(project: Project, root: Path) -> ProjectBinding | None:
    explicit = (
        project.project_context_binding_id
        or _string_setting(project, "project_context_binding_id")
        or _string_setting(project, "context_binding_id")
    )
    if explicit:
        return _load_binding_by_id(root, explicit)
    return _find_binding_for_project_path(root, project.root_path)


def _memory_delta_route_from_bundle(
    bundle: dict[str, Any],
    root: Path,
) -> tuple[str | None, str, Path, bool] | None:
    planspace_id = _string_value(bundle.get("active_planspace_id"))
    if planspace_id is None:
        return None
    planspace_dir = _plug_dir(root, planspace_id)
    if planspace_dir is None:
        return None

    active = bundle.get("active_planspace")
    auto_update = False
    if isinstance(active, dict) and active.get("id") == planspace_id:
        auto_update = bool(active.get("auto_update", False))

    return (
        _string_value(bundle.get("project_binding_id")),
        planspace_id,
        planspace_dir,
        auto_update,
    )


def _memory_delta_route_from_current_binding(
    project: Project,
    root: Path,
) -> tuple[str | None, str, Path, bool] | None:
    active = resolve_active_planspace(project, root)
    if active is None:
        return None
    binding, ref, planspace_dir = active
    return binding.id, ref.id, planspace_dir, ref.auto_update


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
    planspace_dir = _plug_dir(root, ref.id)
    if planspace_dir is None:
        return None
    return binding, ref, planspace_dir


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
    bindings_dir = root / "bindings" / "projects"
    if not bindings_dir.exists():
        return None
    try:
        project_root = Path(root_path).resolve()
    except OSError:
        project_root = Path(root_path)
    for path in sorted(bindings_dir.glob("*.yaml")):
        binding = _load_binding_file(path)
        if binding is None:
            continue
        project = binding.raw.get("project") or {}
        local_paths = project.get("local_paths") or []
        for candidate in local_paths:
            if not isinstance(candidate, str):
                continue
            try:
                if Path(candidate).expanduser().resolve() == project_root:
                    return binding
            except OSError:
                if Path(candidate).expanduser() == project_root:
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
        auto_update=bool(item.get("auto_update", False)),
    )


def _snapshot_planspace_ref(ref: PlugRef | None) -> dict[str, Any] | None:
    if ref is None:
        return None
    return {
        "id": ref.id,
        "role": ref.role,
        "injection": ref.injection,
        "enabled": ref.enabled,
        "auto_update": ref.auto_update,
        "source": ref.source,
    }


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


def _load_planspace_sources(root: Path, ref: PlugRef) -> list[tuple[dict[str, Any], str]]:
    out: list[tuple[dict[str, Any], str]] = []
    plug_dir = _plug_dir(root, ref.id)
    if plug_dir is None:
        return out
    manifest = _plug_manifest(root, ref.id)
    for filename, kind, default_limit in (
        ("STATUS.md", "status", 4000),
        ("PLAN.md", "plan", 6000),
    ):
        injection = _injection_for(ref, manifest, filename, "turn")
        if injection == "none":
            continue
        loaded = _read_context_source(
            plug_dir / filename,
            scope="contextspace",
            kind=kind,
            injection=injection,
            context_root=root,
            plug_id=ref.id,
            max_chars=_max_chars_for(manifest, filename, default_limit),
        )
        if loaded is not None:
            out.append(loaded)
    return out


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


def _append_status_observation(path: Path, node: Node, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
    prefix = "" if existing else "# Status\n"
    needs_newline = bool(existing) and not existing.endswith("\n")
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    block = (
        ("\n" if needs_newline else "")
        + f"\n## {timestamp} - node {node.id}\n\n"
        + f"- terminal_state: {node.state.value}\n"
        + f"- acceptance_state: {node.acceptance_state.value}\n\n"
        + text.strip()
        + "\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        if prefix:
            handle.write(prefix)
        handle.write(block)


def _append_planspace_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


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
