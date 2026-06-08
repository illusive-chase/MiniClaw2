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
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from .domain import Node, Project
from .planspace_state import (
    PlanspaceStatus,
    apply_status_update,
    derive_plan_markdown,
    load_planspace_status,
    merge_reviewed_update_markdown,
    refresh_derived_plan,
    render_planspace_status,
    validate_planspace_status,
    write_planspace_status,
)


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
    active_planspace_auto_update: bool = False


def memory_delta_output_relpath(node: Node) -> str:
    """Return the project-relative memory delta artifact path for a node."""

    return f".miniclaw2/outputs/{node.id}/memory-delta.json"


def planspace_update_output_relpath(node: Node) -> str:
    """Return the project-relative transient planspace update path for a node."""

    return f".miniclaw2/outputs/{node.id}/planspace-update.json"


def review_guidance_output_relpath(node: Node) -> str:
    """Return the project-relative transient review-guidance path for a node."""

    return f".miniclaw2/outputs/{node.id}/review-guidance.md"


def review_guidance_launch_contract(node: Node) -> str:
    """Return turn instructions for gate-internal user-facing review guidance."""

    if not node.requires_review:
        return ""
    output_path = review_guidance_output_relpath(node)
    return (
        "# Review handoff contract\n\n"
        "This node will be followed by a passive human review checkpoint. "
        "Before finishing, write one transient markdown review handoff at "
        f"`{output_path}`. MiniClaw2 shows that text inside the review gate; "
        "it is not durable planspace state and should not be treated as the "
        "node's primary output.\n\n"
        "Write for a human who has not been following the session. Keep it "
        "plain, concrete, and self-contained. Include:\n\n"
        "- `# What changed`: what you built, changed, or learned.\n"
        "- `# How to verify`: exact commands, clicks, or files to inspect.\n"
        "- `# What to judge`: the Type-B question the human must decide.\n\n"
        "Do not ask the human for JSON. They will respond in free-form prose.\n"
    )


def planspace_update_launch_contract(
    project: Project,
    node: Node,
    bundle: ComposedContextBundle,
) -> str:
    """Return turn instructions for project-local planspace state writeback."""

    if not bundle.active_planspace_id or not bundle.active_planspace_auto_update:
        return ""
    binding_id = bundle.project_binding_id or ""
    output_path = planspace_update_output_relpath(node)
    return (
        "# Planspace update contract\n\n"
        "MiniClaw2 loaded an active planspace for this node. Before finishing, "
        "write one transient JSON planspace update at "
        f"`{output_path}` if your work changes this direction's durable state. "
        "Do not edit STATUS.md or PLAN.md directly; MiniClaw2 commits the "
        "update after applying the planspace schema.\n\n"
        "Write only stable findings: project facts, decisions made, open "
        "questions discovered, explicit out-of-scope notes, and the current "
        "posture of this planspace. Filter out transient tool errors, one-run "
        "environment quirks, permission hiccups, service failures, and claims "
        "that a tool or reviewer cannot evaluate something unless that is a "
        "reproducible project fact. If there is no stable state change, omit "
        "the artifact.\n\n"
        "Required top-level shape:\n\n"
        "```json\n"
        "{\n"
        '  "version": 1,\n'
        f'  "node_id": "{node.id}",\n'
        f'  "project_id": "{project.id}",\n'
        f'  "binding_id": "{binding_id}",\n'
        f'  "planspace_id": "{bundle.active_planspace_id}",\n'
        '  "created_at": 1234567890,\n'
        '  "terminal_state": "done",\n'
        '  "acceptance_state": "unreviewed",\n'
        '  "updates": [\n'
        "    {\n"
        '      "target": "STATUS.md",\n'
        '      "operation": "append_body",\n'
        '      "policy": "auto",\n'
        '      "confidence": "observed",\n'
        '      "text": "Implemented X; verified Y; remaining blocker Z."\n'
        "    },\n"
        "    {\n"
        '      "target": "PLAN.md",\n'
        '      "operation": "propose_patch",\n'
        '      "policy": "proposed",\n'
        '      "reason": "Why the plan should change.",\n'
        '      "patch": "Proposed PLAN.md patch text."\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n\n"
        "Supported STATUS.md operations are `append_body`, "
        "`rewrite_current_state`, `add_open_question`, `add_decision`, and "
        "`add_out_of_scope`. Legacy `append_observation` is accepted as an "
        "alias for `append_body`. PLAN.md is derived from STATUS.md; PLAN "
        "patches may be recorded as proposals but are not applied directly.\n"
    )


def memory_delta_launch_contract(
    project: Project,
    node: Node,
    bundle: ComposedContextBundle,
) -> str:
    """Compatibility wrapper for the former memory-delta contract API."""

    return planspace_update_launch_contract(project, node, bundle)


def planspace_update_filter_contract(
    node: Node,
    bundle: ComposedContextBundle,
) -> str:
    """Last-instructions filter that keeps transient noise out of STATUS writes.

    Appended after :func:`planspace_update_launch_contract` so it benefits
    from the model's last-instructions-win bias. Returns an empty string
    when no active planspace is bound, since nothing will be committed.
    """

    if not bundle.active_planspace_id or not bundle.active_planspace_auto_update:
        return ""
    return (
        "# Planspace update filter\n\n"
        "Before you write the planspace update artifact, re-read your draft "
        "and remove any line that describes:\n\n"
        "- A transient tool error in this session (HTTP 5xx, timeouts, "
        "rate limiting, retry-able crashes).\n"
        "- A permission denial, sandbox restriction, or auth hiccup that "
        "blocked a single call but is not a project fact.\n"
        "- A single-run environment quirk (a flaky test that passed on "
        "retry, a clock skew, a network blip, a once-off path issue).\n"
        "- A claim that a tool, model, or reviewer \"cannot evaluate\" "
        "something unless you have reproduced it twice or pinned the "
        "underlying cause.\n"
        "- A complaint about the harness, the prompt, or the SDK.\n\n"
        "Keep only **stable findings**: project facts, decisions, open "
        "questions, explicit out-of-scope notes, and the current posture "
        "of this planspace. When in doubt, drop the line — durable state "
        "is expensive; an extra session is cheap.\n"
    )


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
    active_planspace_auto_update = False
    if binding is not None:
        plug_refs = _expand_required_plugs(root, binding.plugs)
        active_planspace = _select_active_planspace(project, binding, plug_refs)
        if active_planspace is not None:
            active_planspace_id = active_planspace.id
            active_planspace_auto_update = active_planspace.auto_update

        active_id = active_planspace.id if active_planspace is not None else None
        plug_refs = _merge_extra_planspace_loads(
            plug_refs,
            _read_extra_planspace_loads(node),
            exclude_id=active_id,
        )

        for ref in plug_refs:
            kind = _plug_kind(ref.id)
            if kind == "planspace" and ref.source == "binding" and (
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
        "active_planspace_auto_update": active_planspace_auto_update,
        "active_planspace": _snapshot_planspace_ref(active_planspace, root=root),
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
        active_planspace_auto_update=active_planspace_auto_update,
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


def describe_project_contextspace(
    project: Project,
    *,
    store_root: Path | None = None,
) -> dict[str, Any]:
    """Return a UI-facing summary of the project's ContextSpace bindings."""

    root = contextspace_root(store_root)
    binding = resolve_project_binding(project, root)
    active = resolve_active_planspace(project, root)
    active_planspace_id = active[1].id if active is not None else None
    project_active_planspace_id = _string_setting(project, "active_planspace_id")

    return {
        "root": str(root),
        "exists": root.exists(),
        "project_context_binding_id": project.project_context_binding_id,
        "project_active_planspace_id": project_active_planspace_id,
        "resolved_binding_id": binding.id if binding else None,
        "active_planspace_id": active_planspace_id,
        "bindings": [
            _binding_summary(project, root, item)
            for item in list_project_bindings(root)
        ],
    }


def bootstrap_project_contextspace(
    project: Project,
    *,
    store_root: Path | None = None,
    title: str | None = None,
    planspace_slug: str | None = None,
    binding_slug: str | None = None,
) -> dict[str, Any]:
    """Create a minimal ContextSpace planspace + project binding.

    Existing files are left intact. Slug collisions get a numeric suffix.
    The caller is responsible for writing the returned binding/planspace ids
    back to the Project.
    """

    root = contextspace_root(store_root)
    root.mkdir(parents=True, exist_ok=True)
    created: list[str] = []

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
        created,
    )
    _write_text_if_missing(
        root / "README.md",
        "# MiniClaw2 ContextSpace\n\n"
        "This repository stores MiniClaw2 planspaces, bindings, skills, "
        "and context snapshots outside code repositories.\n",
        root,
        created,
    )

    project_title = (
        title.strip()
        if isinstance(title, str) and title.strip()
        else project.name.strip()
        if project.name.strip()
        else Path(project.root_path).name
    )
    base_slug = _slugify(planspace_slug or project_title or "project")
    planspace_slug = _unique_child_slug(root / "plugs" / "planspaces", base_slug)
    planspace_id = f"planspaces.{planspace_slug}"
    planspace_dir = root / "plugs" / "planspaces" / planspace_slug
    (planspace_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (planspace_dir / "checkpoints").mkdir(parents=True, exist_ok=True)

    _write_yaml_if_missing(
        planspace_dir / "manifest.yaml",
        {
            "version": 1,
            "id": planspace_id,
            "kind": "planspace",
            "title": f"{project_title} Planspace",
            "description": f"Planning and status track for {project_title}.",
            "color": "indigo",
            "write_policy": {
                "STATUS.md": "auto",
                "PLAN.md": "derived",
                "SKILLS.md": "auto",
                "events.jsonl": "auto",
                "inbox": "auto",
            },
            "injection": {
                "STATUS.md": "turn",
                "PLAN.md": "turn",
                "SKILLS.md": "none",
            },
            "max_chars": {
                "STATUS.md": 4000,
                "PLAN.md": 6000,
            },
        },
        root,
        created,
    )
    initial_status = PlanspaceStatus(
        goal=f"Advance {project_title}.",
        current_state=f"Created for `{Path(project.root_path).name}`; no node has updated this planspace yet.",
        body=f"# Notes\n\n- Created for `{Path(project.root_path).name}`.\n",
    )
    _write_text_if_missing(
        planspace_dir / "STATUS.md",
        render_planspace_status(initial_status),
        root,
        created,
    )
    _write_text_if_missing(
        planspace_dir / "PLAN.md",
        derive_plan_markdown(initial_status),
        root,
        created,
    )
    _write_text_if_missing(
        planspace_dir / "SKILLS.md",
        "# Skills\n\n_No skills explicitly loaded yet._\n",
        root,
        created,
    )
    _touch_if_missing(planspace_dir / "events.jsonl", root, created)

    binding_base_slug = _slugify(binding_slug or project_title or "project")
    binding_slug = _unique_binding_slug(root, binding_base_slug)
    binding_id = f"project.{binding_slug}"
    binding_path = root / "bindings" / "projects" / f"{binding_id}.yaml"
    _write_yaml_if_missing(
        binding_path,
        {
            "version": 1,
            "id": binding_id,
            "active_planspace_id": planspace_id,
            "project": {
                "name": project_title,
                "miniclaw_project_id": project.id,
                "root_fingerprint": {
                    "root_name": Path(project.root_path).name,
                },
                "local_paths": [project.root_path],
            },
            "plugs": [
                {
                    "id": planspace_id,
                    "role": "status-plan",
                    "injection": "turn",
                    "enabled": True,
                    "auto_update": True,
                }
            ],
        },
        root,
        created,
    )

    return {
        "context_root": str(root),
        "binding_id": binding_id,
        "planspace_id": planspace_id,
        "created": created,
    }


def load_planspace_view(
    planspace_id: str,
    *,
    store_root: Path | None = None,
) -> dict[str, Any] | None:
    """Return a UI-shaped view of one planspace's STATUS + manifest color.

    Returns ``None`` when the planspace directory does not exist.
    """

    root = contextspace_root(store_root)
    plug_dir = _plug_dir(root, planspace_id)
    if plug_dir is None or not plug_dir.exists():
        return None
    manifest = _plug_manifest(root, planspace_id)
    status = load_planspace_status(plug_dir / "STATUS.md")
    return {
        "planspace_id": planspace_id,
        "title": _string_value(manifest.get("title")) or planspace_id,
        "color": _string_value(manifest.get("color")),
        "status": {
            "goal": status.goal,
            "current_state": status.current_state,
            "open_questions": status.open_questions,
            "decisions": status.decisions,
            "out_of_scope": status.out_of_scope,
            "body": status.body,
        },
    }


def apply_planspace_status_ops(
    planspace_id: str,
    operations: list[dict[str, Any]],
    *,
    actor: str = "user",
    store_root: Path | None = None,
) -> dict[str, Any] | None:
    """Apply a list of slot operations to one planspace's STATUS.md.

    ``actor`` is recorded as the ``raised_by`` / ``decided_by`` field on new
    Q* / D* entries when the caller does not provide one explicitly.
    Returns the post-write view dict, or ``None`` when the planspace is
    missing.
    """

    from .planspace_state import (
        apply_status_update as _apply,
        remove_status_slot,
        validate_planspace_status,
    )

    root = contextspace_root(store_root)
    plug_dir = _plug_dir(root, planspace_id)
    if plug_dir is None or not plug_dir.exists():
        return None
    status = load_planspace_status(plug_dir / "STATUS.md")
    pseudo_node = Node(project_id="user", id=actor)

    applied: list[dict[str, Any]] = []
    for op in operations:
        if not isinstance(op, dict):
            continue
        operation = op.get("operation")
        if operation in {
            "remove_open_question",
            "remove_decision",
            "remove_out_of_scope",
        }:
            target = op.get("id") if operation != "remove_out_of_scope" else op.get("index")
            if operation == "remove_out_of_scope" and isinstance(target, int) is False:
                continue
            if operation != "remove_out_of_scope" and isinstance(target, str) is False:
                continue
            summary = remove_status_slot(status, operation, target)  # type: ignore[arg-type]
            if summary is not None:
                applied.append(summary)
            continue
        # Additive ops route through apply_status_update.
        update = {"target": "STATUS.md", "policy": "auto", **op}
        summary = _apply(status, update, pseudo_node)
        if summary is not None:
            applied.append(summary)

    errors = validate_planspace_status(status)
    if errors:
        return {
            "planspace_id": planspace_id,
            "applied": applied,
            "errors": errors,
        }
    write_planspace_status(plug_dir / "STATUS.md", status)
    manifest = _plug_manifest(root, planspace_id)
    if _plan_is_derived(manifest):
        refresh_derived_plan(plug_dir)

    view = load_planspace_view(planspace_id, store_root=store_root)
    if view is not None:
        view["applied"] = applied
    return view


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


def apply_planspace_update_inbox(
    project: Project,
    node: Node,
    *,
    store_root: Path | None = None,
) -> dict[str, Any]:
    """Apply auto-approved STATUS.md updates from the active planspace inbox.

    ``PLAN.md`` is derived from ``STATUS.md``; PLAN patches are recorded as
    proposals but are not applied directly.
    """

    root = contextspace_root(store_root)
    bundle = load_context_bundle_for_node(node, store_root=store_root)
    if bundle is None and (node.context_bundle_id or node.context_bundle_path):
        return {"applied": 0, "proposed": 0, "reason": "context_bundle_not_found"}
    if bundle is None:
        active = _memory_delta_route_from_current_binding(project, root)
        if active is None:
            return {"applied": 0, "proposed": 0, "reason": "no_active_planspace"}
    else:
        active = _memory_delta_route_from_bundle(bundle, root)
        if active is None:
            return {
                "applied": 0,
                "proposed": 0,
                "reason": "no_active_planspace_snapshot",
            }

    binding_id, planspace_id, planspace_dir, auto_update = active
    if not auto_update:
        return {
            "applied": 0,
            "proposed": 0,
            "reason": "planspace_auto_update_disabled",
            "binding_id": binding_id,
            "planspace_id": planspace_id,
        }

    delta_path = planspace_dir / "inbox" / f"{node.id}.planspace-update.json"
    if not delta_path.exists():
        legacy_path = planspace_dir / "inbox" / f"{node.id}.memory-delta.json"
        if legacy_path.exists():
            delta_path = legacy_path
    if not delta_path.exists():
        return {
            "applied": 0,
            "proposed": 0,
            "reason": "no_delta",
            "binding_id": binding_id,
            "planspace_id": planspace_id,
            "delta_path": _display_path(delta_path, root),
        }

    try:
        delta = json.loads(delta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "applied": 0,
            "proposed": 0,
            "reason": f"invalid_delta: {exc}",
            "binding_id": binding_id,
            "planspace_id": planspace_id,
            "delta_path": _display_path(delta_path, root),
        }

    return _apply_planspace_update_payload(
        project,
        node,
        binding_id=binding_id,
        planspace_id=planspace_id,
        planspace_dir=planspace_dir,
        delta_path=delta_path,
        delta=delta,
        context_root=root,
        source="contextspace_inbox",
    )


def apply_memory_delta_inbox(
    project: Project,
    node: Node,
    *,
    store_root: Path | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for the former memory-delta inbox API."""

    return apply_planspace_update_inbox(project, node, store_root=store_root)


def apply_planspace_update_artifact(
    project: Project,
    node: Node,
    *,
    store_root: Path | None = None,
) -> dict[str, Any]:
    """Apply a project-local planspace update artifact to the launch planspace.

    Agents write to ``.miniclaw2/outputs/<node-id>/planspace-update.json`` in
    the project workspace. MiniClaw2 validates that artifact, copies it into the
    snapshotted active planspace inbox, then applies only safe STATUS.md
    observations. This keeps the provider from writing ContextSpace directly.
    """

    root = contextspace_root(store_root)
    rel_path = planspace_update_output_relpath(node)
    source_path = _planspace_update_artifact_path(project, node)
    legacy_rel_path = memory_delta_output_relpath(node)
    if not source_path.exists():
        legacy_source_path = _memory_delta_artifact_path(project, node)
        if legacy_source_path.exists():
            rel_path = legacy_rel_path
            source_path = legacy_source_path

    bundle = load_context_bundle_for_node(node, store_root=store_root)
    if bundle is None:
        return {
            "applied": 0,
            "proposed": 0,
            "reason": (
                "context_bundle_not_found"
                if (node.context_bundle_id or node.context_bundle_path)
                else "no_context_bundle"
            ),
            "source": "project_artifact",
            "source_path": rel_path,
        }

    active = _memory_delta_route_from_bundle(bundle, root)
    if active is None:
        return {
            "applied": 0,
            "proposed": 0,
            "reason": "no_active_planspace_snapshot",
            "source": "project_artifact",
            "source_path": rel_path,
        }

    binding_id, planspace_id, planspace_dir, auto_update = active
    base = {
        "binding_id": binding_id,
        "planspace_id": planspace_id,
        "source": "project_artifact",
        "source_path": rel_path,
    }
    if not auto_update:
        return {
            "applied": 0,
            "proposed": 0,
            "reason": "planspace_auto_update_disabled",
            **base,
        }
    if not source_path.exists():
        return {
            "applied": 0,
            "proposed": 0,
            "reason": "no_delta",
            **base,
        }

    try:
        delta = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "applied": 0,
            "proposed": 0,
            "reason": f"invalid_delta: {exc}",
            **base,
        }

    validation_error = _validate_planspace_update_artifact(
        delta,
        project,
        node,
        binding_id=binding_id,
        planspace_id=planspace_id,
    )
    if validation_error:
        return {
            "applied": 0,
            "proposed": 0,
            "reason": f"invalid_delta: {validation_error}",
            **base,
        }

    delta_path = planspace_dir / "inbox" / f"{node.id}.planspace-update.json"
    try:
        delta_path.parent.mkdir(parents=True, exist_ok=True)
        delta_path.write_text(
            json.dumps(delta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        return {
            "applied": 0,
            "proposed": 0,
            "reason": f"inbox_write_failed: {exc}",
            "delta_path": _display_path(delta_path, root),
            **base,
        }

    return _apply_planspace_update_payload(
        project,
        node,
        binding_id=binding_id,
        planspace_id=planspace_id,
        planspace_dir=planspace_dir,
        delta_path=delta_path,
        delta=delta,
        context_root=root,
        source="project_artifact",
        source_path=rel_path,
        copied_to_inbox=True,
    )


def apply_memory_delta_artifact(
    project: Project,
    node: Node,
    *,
    store_root: Path | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for the former memory-delta artifact API."""

    return apply_planspace_update_artifact(project, node, store_root=store_root)


def stage_planspace_update_artifact(
    project: Project,
    node: Node,
    *,
    store_root: Path | None = None,
) -> dict[str, Any]:
    """Stage a gate-bearing node's interim update until the review closes."""

    root = contextspace_root(store_root)
    rel_path = planspace_update_output_relpath(node)
    source_path = _planspace_update_artifact_path(project, node)
    if not source_path.exists():
        legacy_source_path = _memory_delta_artifact_path(project, node)
        if legacy_source_path.exists():
            rel_path = memory_delta_output_relpath(node)
            source_path = legacy_source_path

    bundle = load_context_bundle_for_node(node, store_root=store_root)
    if bundle is None:
        return {
            "staged": False,
            "reason": (
                "context_bundle_not_found"
                if (node.context_bundle_id or node.context_bundle_path)
                else "no_context_bundle"
            ),
            "source": "project_artifact",
            "source_path": rel_path,
        }

    active = _memory_delta_route_from_bundle(bundle, root)
    if active is None:
        return {
            "staged": False,
            "reason": "no_active_planspace_snapshot",
            "source": "project_artifact",
            "source_path": rel_path,
        }

    binding_id, planspace_id, planspace_dir, auto_update = active
    base = {
        "binding_id": binding_id,
        "planspace_id": planspace_id,
        "source": "project_artifact",
        "source_path": rel_path,
    }
    if not auto_update:
        return {"staged": False, "reason": "planspace_auto_update_disabled", **base}
    if not source_path.exists():
        return {"staged": False, "reason": "no_delta", **base}

    try:
        delta = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"staged": False, "reason": f"invalid_delta: {exc}", **base}

    validation_error = _validate_planspace_update_artifact(
        delta,
        project,
        node,
        binding_id=binding_id,
        planspace_id=planspace_id,
    )
    if validation_error:
        return {"staged": False, "reason": f"invalid_delta: {validation_error}", **base}

    staged_path = planspace_dir / "checkpoints" / f"{node.id}.interim-planspace-update.json"
    try:
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        staged_path.write_text(
            json.dumps(delta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        return {
            "staged": False,
            "reason": f"checkpoint_write_failed: {exc}",
            "staged_path": _display_path(staged_path, root),
            **base,
        }

    event = {
        "type": "planspace_update_staged",
        "node_id": node.id,
        "project_id": project.id,
        "binding_id": binding_id,
        "planspace_id": planspace_id,
        "created_at": time.time(),
        "source_path": rel_path,
        "staged_path": _display_path(staged_path, root),
    }
    _append_planspace_event(planspace_dir / "events.jsonl", event)
    return {
        "staged": True,
        "staged_path": _display_path(staged_path, root),
        **base,
    }


def commit_gate_reviewed_planspace_update(
    project: Project,
    gate_node: Node,
    *,
    source_node: Node | None,
    user_judgment: str,
    review_guidance: str,
    promote_interim: bool = True,
    store_root: Path | None = None,
) -> dict[str, Any]:
    """Merge a staged interim update with free-form review judgment."""

    root = contextspace_root(store_root)
    route_node = source_node or gate_node
    bundle = load_context_bundle_for_node(route_node, store_root=store_root)
    if bundle is None:
        return {
            "applied": 0,
            "proposed": 0,
            "reason": (
                "context_bundle_not_found"
                if (route_node.context_bundle_id or route_node.context_bundle_path)
                else "no_context_bundle"
            ),
            "source": "checkpoint_review",
        }

    active = _memory_delta_route_from_bundle(bundle, root)
    if active is None:
        return {
            "applied": 0,
            "proposed": 0,
            "reason": "no_active_planspace_snapshot",
            "source": "checkpoint_review",
        }

    binding_id, planspace_id, planspace_dir, auto_update = active
    base = {
        "binding_id": binding_id,
        "planspace_id": planspace_id,
        "source": "checkpoint_review",
    }
    if not auto_update:
        return {
            "applied": 0,
            "proposed": 0,
            "reason": "planspace_auto_update_disabled",
            **base,
        }

    staged_path: Path | None = None
    interim_delta: Any = None
    if source_node is not None:
        candidate = planspace_dir / "checkpoints" / f"{source_node.id}.interim-planspace-update.json"
        if candidate.exists():
            staged_path = candidate
            try:
                interim_delta = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                interim_delta = None

    merged_text = merge_reviewed_update_markdown(
        interim_delta=interim_delta,
        user_judgment=user_judgment,
        review_guidance=review_guidance,
        gate_node=gate_node,
        source_node=source_node,
    )
    reviewed_updates = (
        _carry_over_structured_updates(interim_delta)
        if promote_interim
        else []
    )
    reviewed_updates.append(
        {
            "target": "STATUS.md",
            "operation": "append_body",
            "policy": "auto",
            "confidence": "reviewed",
            "text": merged_text,
        }
    )
    delta = {
        "version": 1,
        "node_id": gate_node.id,
        "project_id": project.id,
        "binding_id": binding_id,
        "planspace_id": planspace_id,
        "created_at": time.time(),
        "terminal_state": gate_node.state.value,
        "acceptance_state": gate_node.acceptance_state.value,
        "updates": reviewed_updates,
    }
    delta_path = staged_path or planspace_dir / "checkpoints" / f"{gate_node.id}.review-merge"
    result = _apply_planspace_update_payload(
        project,
        gate_node,
        binding_id=binding_id,
        planspace_id=planspace_id,
        planspace_dir=planspace_dir,
        delta_path=delta_path,
        delta=delta,
        context_root=root,
        source="checkpoint_review",
    )
    if staged_path is not None:
        try:
            staged_path.unlink()
        except OSError:
            pass
        result["staged_discarded"] = True
    return result


def _apply_planspace_update_payload(
    project: Project,
    node: Node,
    *,
    binding_id: str | None,
    planspace_id: str,
    planspace_dir: Path,
    delta_path: Path,
    delta: Any,
    context_root: Path,
    source: str,
    source_path: str | None = None,
    copied_to_inbox: bool = False,
) -> dict[str, Any]:
    applied: list[dict[str, Any]] = []
    proposed: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    status_changed = False
    status = load_planspace_status(planspace_dir / "STATUS.md")

    updates = delta.get("updates") if isinstance(delta, dict) else None
    if not isinstance(updates, list):
        return {
            "applied": 0,
            "proposed": 0,
            "ignored": 0,
            "reason": "invalid_delta: updates must be a list",
            "binding_id": binding_id,
            "planspace_id": planspace_id,
            "delta_path": _display_path(delta_path, context_root),
            "source": source,
            **({"source_path": source_path} if source_path else {}),
            **({"copied_to_inbox": True} if copied_to_inbox else {}),
        }

    for update in updates:
        if not isinstance(update, dict):
            ignored.append({"reason": "update_not_object"})
            continue
        if _is_auto_status_update(update):
            summary = apply_status_update(status, update, node)
            if summary is None:
                ignored.append(_planspace_update_summary(update))
                continue
            status_changed = True
            applied.append(summary)
            continue
        if _is_plan_proposal(update):
            proposed.append(_planspace_update_summary(update))
            continue
        ignored.append(_planspace_update_summary(update))

    if status_changed:
        validation_errors = validate_planspace_status(status)
        if validation_errors:
            return {
                "applied": 0,
                "proposed": len(proposed),
                "ignored": len(ignored),
                "reason": "invalid_status: " + "; ".join(validation_errors),
                "binding_id": binding_id,
                "planspace_id": planspace_id,
                "delta_path": _display_path(delta_path, context_root),
                "source": source,
                **({"source_path": source_path} if source_path else {}),
                **({"copied_to_inbox": True} if copied_to_inbox else {}),
            }
        write_planspace_status(planspace_dir / "STATUS.md", status)
        if _plan_is_derived(_plug_manifest(context_root, planspace_id)):
            refresh_derived_plan(planspace_dir)

    event_type: str | None = None
    if applied or proposed:
        event_type = "planspace_update_applied" if applied else "planspace_update_recorded"
        event: dict[str, Any] = {
            "type": event_type,
            "node_id": node.id,
            "project_id": project.id,
            "binding_id": binding_id,
            "planspace_id": planspace_id,
            "created_at": time.time(),
            "terminal_state": node.state.value,
            "acceptance_state": node.acceptance_state.value,
            "delta_path": _display_path(delta_path, context_root),
            "source": source,
            "updates": applied,
            "proposals": proposed,
            "ignored": ignored,
        }
        if source_path:
            event["source_path"] = source_path
        if copied_to_inbox:
            event["copied_to_inbox"] = True
        _append_planspace_event(planspace_dir / "events.jsonl", event)

    result: dict[str, Any] = {
        "applied": len(applied),
        "proposed": len(proposed),
        "ignored": len(ignored),
        "binding_id": binding_id,
        "planspace_id": planspace_id,
        "delta_path": _display_path(delta_path, context_root),
        "source": source,
    }
    if source_path:
        result["source_path"] = source_path
    if copied_to_inbox:
        result["copied_to_inbox"] = True
    if event_type:
        result["event_type"] = event_type
    if not applied and not proposed:
        result["reason"] = "no_applicable_updates"
    return result


def _memory_delta_artifact_path(project: Project, node: Node) -> Path:
    return Path(project.root_path) / memory_delta_output_relpath(node)


def _planspace_update_artifact_path(project: Project, node: Node) -> Path:
    return Path(project.root_path) / planspace_update_output_relpath(node)


def _validate_planspace_update_artifact(
    delta: Any,
    project: Project,
    node: Node,
    *,
    binding_id: str | None,
    planspace_id: str,
) -> str | None:
    if not isinstance(delta, dict):
        return "top-level value must be an object"
    if delta.get("version") != 1:
        return "version must be 1"
    if delta.get("node_id") != node.id:
        return "node_id does not match launch node"
    if delta.get("project_id") != project.id:
        return "project_id does not match project"
    if binding_id is not None and delta.get("binding_id") != binding_id:
        return "binding_id does not match launch snapshot"
    if delta.get("planspace_id") != planspace_id:
        return "planspace_id does not match launch snapshot"
    updates = delta.get("updates")
    if not isinstance(updates, list):
        return "updates must be a list"
    for index, update in enumerate(updates):
        if not isinstance(update, dict):
            return f"updates[{index}] must be an object"
    return None


def _validate_memory_delta_artifact(
    delta: Any,
    project: Project,
    node: Node,
    *,
    binding_id: str | None,
    planspace_id: str,
) -> str | None:
    """Compatibility wrapper for tests/imports using the former helper name."""

    return _validate_planspace_update_artifact(
        delta,
        project,
        node,
        binding_id=binding_id,
        planspace_id=planspace_id,
    )


def _carry_over_structured_updates(interim_delta: Any) -> list[dict[str, Any]]:
    if not isinstance(interim_delta, dict):
        return []
    updates = interim_delta.get("updates")
    if not isinstance(updates, list):
        return []
    out: list[dict[str, Any]] = []
    for update in updates:
        if not isinstance(update, dict):
            continue
        if not _is_auto_status_update(update):
            continue
        carried = dict(update)
        carried["confidence"] = "reviewed"
        out.append(carried)
    return out


def _is_auto_status_update(update: dict[str, Any]) -> bool:
    return (
        update.get("target") == "STATUS.md"
        and update.get("operation")
        in {
            "append_observation",
            "append_body",
            "append_note",
            "rewrite_current_state",
            "add_open_question",
            "add_decision",
            "add_out_of_scope",
        }
        and update.get("policy") == "auto"
    )


def _is_plan_proposal(update: dict[str, Any]) -> bool:
    return update.get("target") == "PLAN.md" and update.get("policy") == "proposed"


def _planspace_update_summary(update: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("target", "operation", "policy", "confidence", "reason"):
        value = update.get(key)
        if isinstance(value, str) and value:
            out[key] = value
    payload = update.get("text")
    if not isinstance(payload, str):
        payload = update.get("patch")
    if isinstance(payload, str):
        out["chars"] = len(payload)
    if not out:
        out["reason"] = "unsupported_update"
    return out


def _memory_delta_update_summary(update: dict[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper for the former helper name."""

    return _planspace_update_summary(update)


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


def _binding_summary(
    project: Project,
    root: Path,
    binding: ProjectBinding,
) -> dict[str, Any]:
    expanded_refs = _expand_required_plugs(root, binding.plugs)
    refs = _merge_plug_refs(binding.plugs, expanded_refs)
    active = _select_active_planspace(project, binding, expanded_refs)
    project_raw = binding.raw.get("project") if isinstance(binding.raw, dict) else {}
    if not isinstance(project_raw, dict):
        project_raw = {}
    local_paths = [
        item for item in (project_raw.get("local_paths") or [])
        if isinstance(item, str)
    ]
    return {
        "id": binding.id,
        "path": _display_path(binding.path, root),
        "title": _string_value(binding.raw.get("title"))
        or _string_value(project_raw.get("name"))
        or binding.id,
        "project_name": _string_value(project_raw.get("name")),
        "local_paths": local_paths,
        "matches_project_path": _binding_matches_project_path(binding, project.root_path),
        "active_planspace_id": active.id if active else None,
        "binding_active_planspace_id": _string_value(binding.raw.get("active_planspace_id")),
        "plugs": [
            _plug_summary(root, ref, active.id if active else None)
            for ref in refs
        ],
    }


def _merge_plug_refs(primary: list[PlugRef], expanded: list[PlugRef]) -> list[PlugRef]:
    out: list[PlugRef] = list(primary)
    seen = {ref.id for ref in out}
    for ref in expanded:
        if ref.id in seen:
            continue
        seen.add(ref.id)
        out.append(ref)
    return out


def _plug_summary(
    root: Path,
    ref: PlugRef,
    active_planspace_id: str | None,
) -> dict[str, Any]:
    manifest = _plug_manifest(root, ref.id)
    plug_dir = _plug_dir(root, ref.id)
    kind = _plug_kind(ref.id)
    return {
        "id": ref.id,
        "kind": kind,
        "slug": _plug_slug(ref.id),
        "role": ref.role,
        "injection": ref.injection,
        "enabled": ref.enabled,
        "auto_update": ref.auto_update,
        "source": ref.source,
        "active": kind == "planspace" and ref.id == active_planspace_id,
        "exists": plug_dir.exists() if plug_dir is not None else False,
        "path": _display_path(plug_dir, root) if plug_dir is not None else None,
        "title": _string_value(manifest.get("title")) or ref.id,
        "description": _string_value(manifest.get("description")),
    }


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
        auto_update=bool(item.get("auto_update", False)),
    )


def _snapshot_planspace_ref(
    ref: PlugRef | None,
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    if ref is None:
        return None
    snapshot: dict[str, Any] = {
        "id": ref.id,
        "role": ref.role,
        "injection": ref.injection,
        "enabled": ref.enabled,
        "auto_update": ref.auto_update,
        "source": ref.source,
    }
    if root is not None:
        manifest = _plug_manifest(root, ref.id)
        color = _string_value(manifest.get("color"))
        if color:
            snapshot["color"] = color
        title = _string_value(manifest.get("title"))
        if title:
            snapshot["title"] = title
    return snapshot


def _read_extra_planspace_loads(node: Node) -> list[str]:
    """Extra planspace ids the user attached via the phantom composer."""

    raw = node.settings_snapshot.get("extra_planspace_loads")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            out.append(entry.strip())
    return out


def _merge_extra_planspace_loads(
    plug_refs: list[PlugRef],
    extras: list[str],
    *,
    exclude_id: str | None,
) -> list[PlugRef]:
    if not extras:
        return plug_refs
    out = list(plug_refs)
    index_by_id = {ref.id: idx for idx, ref in enumerate(out)}
    seen_loaded: set[str] = set()
    for ref in out:
        if (
            _plug_kind(ref.id) == "planspace"
            and ref.source == "binding"
            and ref.id != exclude_id
        ):
            continue
        seen_loaded.add(ref.id)
    for plug_id in extras:
        if plug_id == exclude_id:
            continue
        if _plug_kind(plug_id) != "planspace":
            continue
        if plug_id in seen_loaded:
            continue
        extra_ref = PlugRef(
            id=plug_id,
            role="cross-lane-load",
            injection="turn",
            enabled=True,
            auto_update=False,
            source="extra",
        )
        existing_idx = index_by_id.get(plug_id)
        if existing_idx is None:
            out.append(extra_ref)
            index_by_id[plug_id] = len(out) - 1
        else:
            out[existing_idx] = extra_ref
        seen_loaded.add(plug_id)
    return out


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
    if _plan_is_derived(manifest):
        refresh_derived_plan(plug_dir)
    for filename, kind, default_limit in (
        ("STATUS.md", "status", None),
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
            max_chars=(
                None
                if filename == "STATUS.md"
                else _max_chars_for(manifest, filename, default_limit)
            ),
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
    status = load_planspace_status(path)
    apply_status_update(
        status,
        {
            "target": "STATUS.md",
            "operation": "append_body",
            "policy": "auto",
            "text": text,
        },
        node,
    )
    write_planspace_status(path, status)


def _append_planspace_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


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


def _touch_if_missing(path: Path, root: Path, created: list[str]) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    created.append(_display_path(path, root))


def _plan_is_derived(manifest: dict[str, Any]) -> bool:
    write_policy = manifest.get("write_policy")
    if not isinstance(write_policy, dict):
        return False
    return write_policy.get("PLAN.md") == "derived"


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


def _unique_child_slug(parent: Path, slug: str) -> str:
    candidate = slug
    index = 2
    while (parent / candidate).exists():
        candidate = f"{slug}-{index}"
        index += 1
    return candidate


def _unique_binding_slug(root: Path, slug: str) -> str:
    bindings_dir = root / "bindings" / "projects"
    candidate = slug
    index = 2
    while (bindings_dir / f"project.{candidate}.yaml").exists():
        candidate = f"{slug}-{index}"
        index += 1
    return candidate


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
