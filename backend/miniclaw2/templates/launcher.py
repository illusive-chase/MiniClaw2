"""Template launch — stamp a virtual-node lane into a fresh workspace."""

from __future__ import annotations

import shutil
from pathlib import Path

from ..contextspace import create_planspace
from ..domain import Node, NodeKind, NodeState, Project
from ..preview import render_virtual_preview
from ..registry import ProjectRegistry
from .loader import Template, TemplateError, TemplateNodeSpec, load_template


def launch_template(
    name: str,
    provider: str,
    registry: ProjectRegistry,
) -> tuple[Project, Template]:
    template = load_template(name)
    if provider.lower() not in template.providers:
        raise TemplateError(
            f"template {name!r} does not support provider {provider!r}"
        )

    project = registry.create_project(
        cwd=None,
        provider=provider,
        auto_commit=template.auto_commit or None,
        permission_mode=template.permission_mode,
        approval_policy=_approval_policy_for(provider, template.permission_mode),
        sandbox=_sandbox_for(provider, template.permission_mode),
        temporary=True,
        template_id=template.name,
    )

    try:
        _seed_workspace(template, Path(project.root_path))
        planspace_id = create_planspace(
            project,
            title=f"Template: {template.name}",
            mode=template.lane_mode,
            store_root=registry.store.root,
            seed_text=template.brief,
        )
        settings = dict(project.settings_override)
        settings["active_planspace_id"] = planspace_id
        project.settings_override = settings
        registry.store.update_project(project)

        _stamp_lane(
            template,
            project,
            provider.lower(),
            planspace_id,
            registry,
            anchor_node_id=None,
        )
        registry.promote_next_virtual(project.id)
    except Exception:
        registry.delete_project(project.id)
        raise

    return project, template


def apply_user_template(
    template: Template,
    project: Project,
    registry: ProjectRegistry,
    *,
    anchor_node_id: str | None = None,
) -> list[Node]:
    """Stamp ``template`` into ``project``'s active planspace.

    Unlike :func:`launch_template`, this does not create a project, seed a
    workspace, or touch project settings. Callers are expected to have
    verified that the project is not busy. When ``anchor_node_id`` is
    provided, every root virtual (one with no in-template deps) gets an
    implicit ``scheduled_deps=[anchor_node_id]``.

    Returns the list of newly-stamped nodes in slug order.
    """
    active_lane = project.settings_override.get("active_planspace_id") or ""
    if not active_lane:
        raise TemplateError("activate a direction first")

    if anchor_node_id:
        anchor = registry.store.load_node(project.id, anchor_node_id)
        if anchor is None:
            raise TemplateError(f"anchor node {anchor_node_id!r} does not exist")
        if (anchor.planspace_id or "") != active_lane:
            # Anchor lives in another lane — collapse into the active lane by
            # dropping the anchor (matches "cross-lane collapse" semantics).
            anchor_node_id = None

    return _stamp_lane(
        template,
        project,
        project.provider,
        active_lane,
        registry,
        anchor_node_id=anchor_node_id,
    )


def _instantiate_lane(
    template: Template,
    project: Project,
    provider: str,
    planspace_id: str,
    registry: ProjectRegistry,
) -> None:
    """Legacy shim kept for callers outside this module."""
    _stamp_lane(template, project, provider, planspace_id, registry, anchor_node_id=None)


def _stamp_lane(
    template: Template,
    project: Project,
    provider: str,
    planspace_id: str,
    registry: ProjectRegistry,
    *,
    anchor_node_id: str | None,
) -> list[Node]:
    slug_to_node_id: dict[str, str] = {}
    pending: list[tuple[TemplateNodeSpec, Node]] = []
    for spec in template.nodes:
        node = Node(
            project_id=project.id,
            kind=spec.kind,
            category=spec.category,
            subtype=spec.subtype,
            brief=spec.brief,
            state=NodeState.VIRTUAL,
            planspace_id=planspace_id,
            provider=provider,
            prompt="",
            prompt_draft=spec.prompt if spec.kind is NodeKind.AGENT else None,
            scheduled_deps=[],
            proposed_by=f"template:{template.name}",
            summary=spec.summary,
            verify_script_ref=(
                str(spec.script_ref) if spec.kind is NodeKind.VERIFIER and spec.script_ref else None
            ),
        )
        slug_to_node_id[spec.id] = node.id
        pending.append((spec, node))

    created: list[Node] = []
    for spec, node in pending:
        translated = [slug_to_node_id[dep] for dep in (spec.scheduled_deps or [])]
        if anchor_node_id and not translated:
            translated = [anchor_node_id]
        node.scheduled_deps = translated
        if spec.resume_from:
            node.resume_from_node_id = slug_to_node_id[spec.resume_from]
        registry.store.create_node(node)
        registry.store.write_node_preview(
            project.id,
            node.id,
            render_virtual_preview(node),
        )
        created.append(node)
    return created


def _seed_workspace(template: Template, root: Path) -> None:
    for src, dst_rel in template.seed:
        target = (root / dst_rel).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise TemplateError(
                f"seed destination escapes workspace root: {dst_rel}"
            ) from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            shutil.copyfile(src, target)


def _approval_policy_for(provider: str, permission_mode: str | None) -> str | None:
    if provider.lower() != "codex":
        return None
    if permission_mode == "bypassPermissions":
        return "never"
    if permission_mode == "default":
        return "untrusted"
    return None


def _sandbox_for(provider: str, permission_mode: str | None) -> str | None:
    if provider.lower() != "codex":
        return None
    if permission_mode == "bypassPermissions":
        return "workspace-write"
    if permission_mode == "default":
        return "read-only"
    return None
