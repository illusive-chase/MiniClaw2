"""Template launch — stamp a virtual-node lane into a fresh workspace."""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..contextspace import (
    append_template_instance,
    create_planspace,
    read_template_instances,
    write_template_ports,
)
from ..domain import Node, NodeKind, NodeState, Project
from ..materialize import GRAPH_RUNS_DIRNAME
from ..model_catalog import (
    normalize_active_model_preset_id,
    provider_for_model_preset,
)
from ..preview import render_virtual_preview
from ..registry import ProjectRegistry
from ..virtual_graph import has_cycle
from .loader import (
    PARAM_NAME_RE,
    Template,
    TemplateError,
    TemplateNodeSpec,
    _PLACEHOLDER_RE,
    load_template,
)


def launch_template(
    name: str,
    model_preset_id: str,
    registry: ProjectRegistry,
) -> tuple[Project, Template]:
    template = load_template(name, store_root=registry.store.root)
    model_preset_id = _require_template_model_preset(
        template, model_preset_id, store_root=registry.store.root
    )
    provider = provider_for_model_preset(
        model_preset_id, store_root=registry.store.root
    )

    project = registry.create_project(
        cwd=None,
        model_preset_id=model_preset_id,
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
        project.active_planspace_id = planspace_id
        registry.store.update_project(project)

        _stamp_lane(
            template,
            project,
            model_preset_id,
            planspace_id,
            registry,
            anchor_node_id=None,
            arguments={},
            input_bindings={},
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
    arguments: dict[str, str] | None = None,
    input_bindings: dict[str, str] | None = None,
) -> list[Node]:
    """Stamp ``template`` into ``project``'s active planspace.

    Unlike :func:`launch_template`, this does not create a project, seed a
    workspace, or touch project settings. Like ordinary virtual creation, it
    may run while another node is active. When ``anchor_node_id`` is provided,
    every root virtual (one with no in-template deps) gets an implicit
    ``scheduled_deps=[anchor_node_id]``.

    Returns the list of newly-stamped nodes in slug order.
    """
    active_lane = project.active_planspace_id or ""
    if not active_lane:
        raise TemplateError("activate a direction first")

    if anchor_node_id and not template.inputs:
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
        None,
        active_lane,
        registry,
        anchor_node_id=anchor_node_id,
        arguments=arguments or {},
        input_bindings=input_bindings or {},
    )


#: ``Project.template_id`` prefix marking an embedded editing session.
#: ``launch_template`` stores a bare template name for its bundled test runs,
#: and every bundled template's display name equals its directory name — so a
#: bare slug comparison cannot tell a test run from an editing session. The
#: prefix makes the distinction structural instead of coincidental.
#:
#: Mirrored by ``EMBEDDED_SESSION_PREFIX`` in ``frontend/src/types.ts``, which
#: reads the same field to decide whether to show template affordances;
#: ``test_embedded_session`` asserts the two stay identical.
EMBEDDED_SESSION_PREFIX = "embedded:"


def embedded_session_template_id(slug: str) -> str:
    """The ``Project.template_id`` an embedded session for ``slug`` carries."""
    return f"{EMBEDDED_SESSION_PREFIX}{slug}"


def embedded_session_slug(template_id: str | None) -> str | None:
    """Inverse of :func:`embedded_session_template_id`; ``None`` if unrelated."""
    if not template_id or not template_id.startswith(EMBEDDED_SESSION_PREFIX):
        return None
    return template_id[len(EMBEDDED_SESSION_PREFIX) :] or None


def materialize_embedded_session(
    template: Template,
    registry: ProjectRegistry,
) -> tuple[Project, str]:
    """Open ``template`` as a real, editable project — its embedded session.

    Returns ``(project, planspace_id)``. The project is ``temporary=True``, so
    it stays out of sync, records no host head, cannot be rebound, and has its
    workspace cleaned up on delete.

    Unlike :func:`launch_template` this stamps the *definition*, not an
    instance of it: prompts keep their ``{{placeholder}}`` text and the input
    ports go to the planspace manifest instead of being bound to nodes. That
    asymmetry is the whole point — ``_render_prompt`` is one-way, so a rendered
    stamp can never be serialized back into a template without silently losing
    every argument and port.

    The accepted cost: running a node in this session hands the agent the
    literal ``{{focus}}`` text, because nothing has filled the arguments in.
    The session is for authoring, and an unfilled definition is what authoring
    operates on.
    """
    if any(node.kind is not NodeKind.AGENT for node in template.nodes):
        raise TemplateError("embedded sessions support agent-only templates")

    # Tagged with the `embedded:` prefix rather than a bare name: bundled test
    # runs write `template.name`, and every bundled template's display name is
    # also its slug, so a bare value could not tell the two apart.
    project = registry.create_project(
        cwd=None,
        model_preset_id=None,
        auto_commit=template.auto_commit or None,
        permission_mode=template.permission_mode,
        temporary=True,
        template_id=embedded_session_template_id(template.slug),
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
        project.active_planspace_id = planspace_id
        registry.store.update_project(project)

        stamped = _stamp_lane(
            template,
            project,
            None,
            planspace_id,
            registry,
            anchor_node_id=None,
            render=False,
        )
        write_template_ports(
            project,
            planspace_id,
            _port_records_for(template, stamped),
            store_root=registry.store.root,
        )
    except Exception:
        registry.delete_project(project.id)
        raise

    return project, planspace_id


def _port_records_for(
    template: Template,
    stamped: list[Node],
) -> list[dict[str, Any]]:
    """Build manifest port records, mapping each port's consumers to node ids.

    ``_stamp_lane`` returns its nodes in ``template.nodes`` order, which is the
    only bridge back from a generated node id to the slug it came from.
    """
    if len(stamped) != len(template.nodes):
        raise TemplateError("stamped node count does not match the template")
    node_id_by_slug = {
        spec.id: node.id for spec, node in zip(template.nodes, stamped)
    }
    consumers_by_port: dict[str, list[str]] = {
        template_input.name: [] for template_input in template.inputs
    }
    for spec in template.nodes:
        node_id = node_id_by_slug.get(spec.id)
        if node_id is None:
            continue
        for port in spec.input_deps:
            # A dep naming an undeclared port is possible in a hand-edited
            # template; carry it so the round trip does not drop the edge.
            consumers_by_port.setdefault(port, []).append(node_id)
    declared = {template_input.name for template_input in template.inputs}
    records = [
        {
            "name": template_input.name,
            "description": template_input.description,
            "consumers": consumers_by_port.get(template_input.name, []),
        }
        for template_input in template.inputs
    ]
    records.extend(
        {"name": port, "description": "", "consumers": consumers}
        for port, consumers in consumers_by_port.items()
        if port not in declared
    )
    return records


def _stamp_lane(
    template: Template,
    project: Project,
    fallback_model_preset_id: str | None,
    planspace_id: str,
    registry: ProjectRegistry,
    *,
    anchor_node_id: str | None,
    arguments: dict[str, str] | None = None,
    input_bindings: dict[str, str] | None = None,
    render: bool = True,
) -> list[Node]:
    """Stamp every node of ``template`` as a virtual in ``planspace_id``.

    ``fallback_model_preset_id`` applies only to agent nodes that declare no
    model of their own; ``None`` means "use the target project's preset".

    ``render=False`` stamps the *definition itself* rather than an instance of
    it: prompts keep their ``{{placeholder}}`` text and ``in:<port>`` deps are
    left out of the graph. That is what an embedded editing session needs —
    rendering is one-way (see ``_render_prompt``), so a rendered stamp cannot
    be serialized back into a template without losing every argument and port.
    Callers that pass ``render=False`` own persisting the ports themselves
    (``contextspace.write_template_ports``).
    """
    fallback_model_preset_id = fallback_model_preset_id or project.model_preset_id
    if render:
        resolved_arguments = _resolve_arguments(template, arguments or {})
        resolved_inputs = _validate_input_bindings(
            template,
            project,
            planspace_id,
            registry,
            input_bindings or {},
        )
    else:
        if arguments or input_bindings:
            raise TemplateError(
                "an unrendered stamp takes no arguments or input bindings"
            )
        resolved_arguments = {}
        resolved_inputs = {}
    existing_instances = read_template_instances(
        project,
        planspace_id,
        store_root=registry.store.root,
    )
    existing_instance_ids = {
        record.get("instance_id")
        for record in existing_instances
        if isinstance(record.get("instance_id"), str)
    }
    instance_id = uuid4().hex[:12]
    while instance_id in existing_instance_ids:
        instance_id = uuid4().hex[:12]

    slug_to_node_id: dict[str, str] = {}
    #: Resolved model per template slug, so a resume node can inherit from its
    #: source the way ``create_virtual`` demands.
    model_by_slug: dict[str, str] = {}
    pending: list[tuple[TemplateNodeSpec, Node]] = []
    for spec in template.nodes:
        node_model_preset_id: str | None = None
        if spec.kind is NodeKind.AGENT:
            # A resume node continues an existing provider session, so its
            # model is not a free choice — the runtime rejects a resume
            # virtual whose model differs from its source. The template's own
            # value is ignored here rather than raised on: the editor may have
            # been pointed at a new resume source after the model was set.
            inherited = (
                model_by_slug.get(spec.resume_from) if spec.resume_from else None
            )
            node_model_preset_id = inherited or _resolve_node_model_preset(
                template,
                spec,
                fallback_model_preset_id,
                store_root=registry.store.root,
            )
            model_by_slug[spec.id] = node_model_preset_id
        node = Node(
            project_id=project.id,
            kind=spec.kind,
            category=spec.category,
            subtype=spec.subtype,
            brief=spec.brief,
            state=NodeState.VIRTUAL,
            planspace_id=planspace_id,
            model_preset_id=node_model_preset_id,
            prompt="",
            prompt_draft=spec.prompt if spec.kind is NodeKind.AGENT else None,
            scheduled_deps=[],
            proposed_by=f"template:{template.name}",
            template_instance_id=instance_id,
            summary=spec.summary,
            artifact_mode=spec.artifact_mode,
            artifact_spec=spec.artifact_spec,
            verify_script_ref=(
                str(spec.script_ref) if spec.kind is NodeKind.VERIFIER and spec.script_ref else None
            ),
        )
        if spec.kind is NodeKind.AGENT and render:
            lane_path = f"{GRAPH_RUNS_DIRNAME}/{node.id}/lanes/{planspace_id}"
            node.prompt_draft = _render_prompt(
                spec.prompt,
                arguments=resolved_arguments,
                input_bindings=resolved_inputs,
                lane_path=lane_path,
            )
        slug_to_node_id[spec.id] = node.id
        pending.append((spec, node))

    for spec, node in pending:
        translated = [slug_to_node_id[dep] for dep in spec.internal_deps]
        if render:
            translated.extend(resolved_inputs[port] for port in spec.input_deps)
        # An unrendered stamp keeps ports out of the graph entirely: the
        # registry resolves every scheduled_dep through `load_node`, and an
        # `in:<port>` literal resolves to nothing. The port edge is carried on
        # the planspace manifest instead.
        if not template.inputs and anchor_node_id and not translated:
            translated = [anchor_node_id]
        node.scheduled_deps = translated
        if spec.resume_from:
            node.resume_from_node_id = slug_to_node_id[spec.resume_from]

    lane_nodes = {
        node.id: node
        for node in registry.store.list_nodes(project.id)
        if (node.planspace_id or "") == planspace_id
    }
    lane_nodes.update({node.id: node for _, node in pending})
    if has_cycle(lane_nodes):
        raise TemplateError("input bindings would introduce a cycle in the lane DAG")

    instance_record = {
        "instance_id": instance_id,
        "template_slug": template.root.name,
        "template_name": template.name,
        "arguments": dict(resolved_arguments),
        "input_bindings": dict(resolved_inputs),
        "created_at": time.time(),
        "parent_instance_id": None,
    }

    created: list[Node] = []
    try:
        for spec, node in pending:
            if node.kind is NodeKind.AGENT:
                stamped = registry.create_virtual(
                    project.id,
                    node_id=node.id,
                    prompt_draft=node.prompt_draft or "",
                    category=node.category,
                    subtype=node.subtype,
                    brief=node.brief,
                    motivation=node.summary,
                    scheduled_deps=node.scheduled_deps,
                    model_preset_id=node.model_preset_id,
                    artifact_mode=node.artifact_mode,
                    artifact_spec=node.artifact_spec,
                    planspace_id=planspace_id,
                    resume_from_node_id=node.resume_from_node_id,
                    _allow_nonterminal_resume=True,
                    _proposed_by=node.proposed_by or f"template:{template.name}",
                    _template_instance_id=instance_id,
                    _template_source_node_id=spec.id if not render else None,
                    _template_source_model_preset_id=(
                        spec.model_preset_id if not render else None
                    ),
                    _template_source_motivation=(spec.summary if not render else None),
                    _defer_auto_promotion=True,
                    _created_at=node.created_at,
                )
                if stamped is None:
                    raise TemplateError("project is unavailable")
                created.append(stamped)
            else:
                created.append(node)
                registry.store.create_node(node)
                registry.store.write_node_preview(
                    project.id,
                    node.id,
                    render_virtual_preview(node),
                )
        append_template_instance(
            project,
            planspace_id,
            instance_record,
            store_root=registry.store.root,
        )
    except ValueError as exc:
        _rollback_stamped_nodes(project.id, created, registry)
        raise TemplateError(str(exc)) from exc
    except Exception:
        _rollback_stamped_nodes(project.id, created, registry)
        raise
    registry.store.sync.schedule_commit(f"create template instance {instance_id}")
    return created


def _rollback_stamped_nodes(
    project_id: str,
    created: list[Node],
    registry: ProjectRegistry,
) -> None:
    """Undo a partial stamp through the same runtime path used to create it."""
    for node in reversed(created):
        if node.kind is NodeKind.AGENT:
            deleted, _ = registry.delete_virtual(project_id, node.id)
            if deleted:
                continue
        registry.store.delete_node(project_id, node.id)


def _resolve_arguments(
    template: Template,
    supplied: dict[str, str],
) -> dict[str, str]:
    known = {argument.name for argument in template.arguments}
    unknown = sorted(set(supplied) - known)
    if unknown:
        raise TemplateError(f"unknown template argument: {unknown[0]}")

    resolved: dict[str, str] = {}
    for argument in template.arguments:
        if argument.name in supplied:
            resolved[argument.name] = supplied[argument.name]
        elif argument.required:
            raise TemplateError(f"missing required template argument: {argument.name}")
        else:
            resolved[argument.name] = argument.default or ""
    return resolved


def _validate_input_bindings(
    template: Template,
    project: Project,
    planspace_id: str,
    registry: ProjectRegistry,
    supplied: dict[str, str],
) -> dict[str, str]:
    known = {template_input.name for template_input in template.inputs}
    unknown = sorted(set(supplied) - known)
    if unknown:
        raise TemplateError(f"unknown template input: {unknown[0]}")

    resolved: dict[str, str] = {}
    for template_input in template.inputs:
        node_id = supplied.get(template_input.name)
        if not node_id:
            raise TemplateError(f"missing template input binding: {template_input.name}")
        node = registry.store.load_node(project.id, node_id)
        if node is None:
            raise TemplateError(f"input binding node {node_id!r} does not exist")
        if (node.planspace_id or "") != planspace_id:
            raise TemplateError(
                f"input binding node {node_id!r} is outside the active planspace"
            )
        resolved[template_input.name] = node.id
    return resolved


def _render_prompt(
    prompt: str,
    *,
    arguments: dict[str, str],
    input_bindings: dict[str, str],
    lane_path: str,
) -> str:
    def replace(match: re.Match[str]) -> str:
        placeholder = match.group(1)
        if placeholder.startswith("input."):
            port = placeholder[len("input.") :]
            if PARAM_NAME_RE.fullmatch(port) and port in input_bindings:
                return f"{lane_path}/nodes/{input_bindings[port]}/preview.json"
            return match.group(0)
        if PARAM_NAME_RE.fullmatch(placeholder) and placeholder in arguments:
            return arguments[placeholder]
        return match.group(0)

    rendered = _PLACEHOLDER_RE.sub(replace, prompt)
    for match in _PLACEHOLDER_RE.finditer(rendered):
        placeholder = match.group(1)
        if PARAM_NAME_RE.fullmatch(placeholder):
            raise TemplateError(f"unresolved template argument: {placeholder}")
        if placeholder.startswith("input.") and PARAM_NAME_RE.fullmatch(
            placeholder[len("input.") :]
        ):
            raise TemplateError(f"unresolved template input: {placeholder}")
    return rendered


def _require_template_model_preset(
    template: Template,
    model_preset_id: str,
    *,
    store_root: Path | None = None,
) -> str:
    """Validate the model a *bundled* template was asked to run on.

    ``allowed_model_preset_ids`` is the bundled test matrix — the Tests panel
    offers one run button per entry. User templates carry their model per node
    and never reach this check.
    """
    try:
        normalized = normalize_active_model_preset_id(
            model_preset_id, store_root=store_root
        )
    except ValueError as exc:
        raise TemplateError(str(exc)) from exc
    if (
        template.allowed_model_preset_ids
        and normalized not in template.allowed_model_preset_ids
    ):
        allowed = ", ".join(template.allowed_model_preset_ids)
        raise TemplateError(
            f"模板 {template.name!r} 未为模型 {normalized!r} 配置运行入口；"
            f"该模板可运行的模型为：{allowed}"
        )
    return normalized


def _resolve_node_model_preset(
    template: Template,
    spec: TemplateNodeSpec,
    fallback_model_preset_id: str,
    *,
    store_root: Path | None = None,
) -> str:
    """Pick the model one stamped agent node runs on.

    The node's authored model wins; a node that declares none inherits
    ``fallback_model_preset_id`` (the target project's preset). A declared but
    unusable model is an error the author must fix in the editor — silently
    substituting the project preset would run the node on a model the template
    did not ask for.
    """
    declared = spec.model_preset_id
    if not declared:
        return fallback_model_preset_id
    try:
        return normalize_active_model_preset_id(declared, store_root=store_root)
    except ValueError as exc:
        raise TemplateError(
            f"模板 {template.name!r} 的节点 {spec.id!r} 指定的模型"
            f" {declared!r} 当前不可用：{exc}；请在模板编辑器中为该节点重新选择模型"
        ) from exc


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
