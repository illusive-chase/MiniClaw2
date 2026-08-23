"""Reap pipeline — terminal-time walk-diff of the materialized lane.

The runner snapshots the lane before agent launch and calls ``reap_lane``
at the agent's terminal transition.
The reap pipeline:

1. Diffs the materialized subtree against the pre-launch snapshot.
2. Parses each created/modified ``preview.json`` (strict whitelist).
3. Enforces the own-preview contract (the running node must write its
   own preview).
4. Enforces category rights (regular agents cannot write virtual
   previews).
5. Canonicalizes virtual-id slugs into framework-assigned ids.
6. Validates that every ``scheduled_deps`` reference resolves.
7. Detects cycles in the lane's dep DAG.
8. Persists atomically — all-or-nothing — to the durable node store.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .domain import ArtifactMode, Category, Node, NodeKind, NodeState, Project, _new_id
from .materialize import diff_lane
from .model_catalog import (
    normalize_active_model_preset_id,
    normalize_model_preset_id,
)
from .preview import (
    ExecutedPreview,
    Preview,
    PreviewValidationError,
    VirtualPreview,
    parse_preview,
    validate_preview_for_node,
    virtual_preview_to_node,
)
from .store import Store
from .virtual_graph import has_cycle


_PREVIEW_RE = re.compile(r"^nodes/([^/]+)/preview\.json$")


@dataclass
class ReapResult:
    own_preview_ok: bool = False
    own_preview: ExecutedPreview | None = None
    new_virtuals: list[Node] = field(default_factory=list)
    modified_virtuals: list[Node] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    fatal: bool = False

    def ok(self) -> bool:
        return self.own_preview_ok and not self.fatal and not self.rejection_reasons


def _resolve_planner_model_preset(
    preview: VirtualPreview,
    *,
    inherited_model_preset_id: str | None,
    store: Store,
) -> str | None:
    """Resolve an optional planner selection against the configured catalog."""
    if "model_preset_id" not in preview.model_fields_set:
        return inherited_model_preset_id
    if preview.model_preset_id is None:
        raise ValueError("model_preset_id is required when explicitly set")
    selected = normalize_model_preset_id(
        preview.model_preset_id,
        store_root=store.root,
    )
    if selected != inherited_model_preset_id:
        selected = normalize_active_model_preset_id(
            selected,
            store_root=store.root,
        )
    return selected


def _resolve_preview_artifact(
    preview: VirtualPreview,
    *,
    inherited_mode: ArtifactMode,
    inherited_spec: str,
) -> tuple[ArtifactMode, str]:
    """Resolve artifact intent; an absent field inherits the current value."""
    if "artifact_mode" not in preview.model_fields_set:
        return inherited_mode, inherited_spec
    mode = ArtifactMode(preview.artifact_mode)
    if mode is ArtifactMode.CUSTOM:
        return mode, preview.artifact_spec or ""
    return mode, ""


def _rewrite_scheduled_deps(
    deps: list[str],
    slug_to_canonical: dict[str, str],
    store: Store,
    project_id: str,
    lane_id: str,
    label: str,
    result: ReapResult,
) -> list[str]:
    """Resolve slug references to canonical ids; flag unresolved as fatal."""
    rewritten: list[str] = []
    for dep in deps:
        if dep in slug_to_canonical:
            rewritten.append(slug_to_canonical[dep])
        else:
            dep_node = store.load_node(project_id, dep)
            if dep_node is None:
                result.rejection_reasons.append(
                    f"{label}: scheduled_dep {dep!r} does not resolve"
                )
                result.fatal = True
            elif (dep_node.planspace_id or "") != lane_id:
                result.rejection_reasons.append(
                    f"{label}: scheduled_dep {dep!r} is outside this lane"
                )
                result.fatal = True
            else:
                rewritten.append(dep)
    return rewritten


def reap_lane(
    project: Project,
    node: Node,
    lane_root_path: Path,
    pre_snapshot: dict[str, str],
    store: Store,
) -> ReapResult:
    """Walk-diff the lane, validate writes, persist atomically.

    Returns a ``ReapResult`` describing the outcome. Caller (runner) is
    responsible for re-prompt orchestration on ``own_preview_ok=False``
    and stub generation on ``fatal=True``.
    """
    result = ReapResult()
    diff = diff_lane(pre_snapshot, lane_root_path)

    if diff.deleted:
        result.fatal = True
        result.rejection_reasons.append(
            f"tracked preview files were deleted: {', '.join(diff.deleted)} — "
            "obsoletion must set obsolete_reason, not rm"
        )
        return result

    changed_preview_paths: list[tuple[str, str]] = []
    for rel in diff.created + diff.modified:
        m = _PREVIEW_RE.match(rel)
        if m:
            changed_preview_paths.append((rel, m.group(1)))
        else:
            # Out-of-band file write in the lane subtree. Surface but
            # don't fail the reap — non-preview writes are deferred
            # policy.
            result.rejection_reasons.append(
                f"ignored non-preview write under the lane: {rel}"
            )

    # Parse each preview. Track by node-id-or-slug.
    parsed: dict[str, tuple[Preview, str]] = {}  # id-or-slug -> (preview, rel-path)
    for rel, ident in changed_preview_paths:
        path = lane_root_path / rel
        try:
            text = path.read_text(encoding="utf-8")
            preview = parse_preview(text)
        except (OSError, PreviewValidationError) as exc:
            if isinstance(exc, PreviewValidationError):
                for issue in exc.issues:
                    result.rejection_reasons.append(f"{rel}: {issue}")
            else:
                result.rejection_reasons.append(f"{rel}: cannot read ({exc})")
            continue
        parsed[ident] = (preview, rel)

    # --- Own-preview check ---
    own_entry = parsed.get(node.id)
    if own_entry is None:
        result.rejection_reasons.append(
            f"the running node did not write its own preview at nodes/{node.id}/preview.json"
        )
        return result
    own_preview, own_rel = own_entry
    if isinstance(own_preview, VirtualPreview):
        result.rejection_reasons.append(
            f"{own_rel}: running node wrote a virtual preview as its own"
        )
        return result
    own_issues = validate_preview_for_node(own_preview, node)
    if own_issues:
        for issue in own_issues:
            result.rejection_reasons.append(f"{own_rel}: {issue}")
        return result
    result.own_preview_ok = True
    result.own_preview = own_preview

    # --- Categorize remaining preview writes ---
    new_virtuals: list[tuple[str, VirtualPreview]] = []  # (slug, preview)
    mutated_virtuals: list[tuple[Node, VirtualPreview, str]] = []  # (existing_node, preview, rel)
    for ident, (preview, rel) in parsed.items():
        if ident == node.id:
            continue
        existing = store.load_node(project.id, ident)
        if existing is None and rel in pre_snapshot:
            # The materialized preview existed at launch, so this is a stale
            # rewrite of a node the user deleted while the run was active.
            # The user deletion wins, even if the stale payload no longer
            # satisfies current agent-authoring rules.
            continue
        if (
            isinstance(preview, VirtualPreview)
            and "model_preset_id" in preview.model_fields_set
            and node.category is not Category.PLANNING
        ):
            result.rejection_reasons.append(
                f"{rel}: only planning agents may set model_preset_id on "
                "virtual previews"
            )
            result.fatal = True
            return result
        if (
            isinstance(preview, VirtualPreview)
            and preview.kind != NodeKind.AGENT.value
            and "model_preset_id" in preview.model_fields_set
        ):
            result.rejection_reasons.append(
                f"{rel}: model_preset_id is only valid on agent virtuals"
            )
            result.fatal = True
            return result
        if existing is None:
            # Treat ident as a new slug. Must be a virtual preview.
            if not isinstance(preview, VirtualPreview):
                result.rejection_reasons.append(
                    f"{rel}: cannot create new executed node — only virtuals may be written"
                )
                result.fatal = True
                return result
            new_virtuals.append((ident, preview))
        else:
            # Existing node — must be a virtual still, and writer must
            # have rights, and must be a virtual preview.
            if existing.state is not NodeState.VIRTUAL:
                result.rejection_reasons.append(
                    f"{rel}: cannot modify preview of executed node {existing.id} (state={existing.state.value})"
                )
                result.fatal = True
                return result
            lane_id = node.planspace_id or ""
            if (existing.planspace_id or "") != lane_id:
                result.rejection_reasons.append(
                    f"{rel}: cannot modify virtual {existing.id} outside this lane"
                )
                result.fatal = True
                return result
            if not isinstance(preview, VirtualPreview):
                result.rejection_reasons.append(
                    f"{rel}: existing virtual {existing.id} cannot be rewritten as executed"
                )
                result.fatal = True
                return result
            mutated_virtuals.append((existing, preview, rel))

    # --- Category enforcement ---
    if (new_virtuals or mutated_virtuals) and node.category is not Category.PLANNING \
            and node.category is not Category.REVIEW:
        result.rejection_reasons.append(
            f"category={node.category.value if node.category else None!r} "
            "may not propose or mutate virtual previews; only planning and review may"
        )
        result.fatal = True
        return result

    # --- Slug canonicalization ---
    lane_id = node.planspace_id or ""
    slug_to_canonical: dict[str, str] = {}
    new_node_drafts: dict[str, Node] = {}
    for slug, preview in new_virtuals:
        if preview.kind == NodeKind.VERIFIER.value:
            result.rejection_reasons.append(
                f"new virtual {slug}: verifier virtuals may only come from templates"
            )
            result.fatal = True
            return result
        canonical = _new_id()
        slug_to_canonical[slug] = canonical
        try:
            model_preset_id = _resolve_planner_model_preset(
                preview,
                inherited_model_preset_id=node.model_preset_id,
                store=store,
            )
        except ValueError as exc:
            result.rejection_reasons.append(
                f"new virtual {slug}: invalid model_preset_id ({exc})"
            )
            result.fatal = True
            return result
        draft = virtual_preview_to_node(
            preview,
            project_id=project.id,
            canonical_id=canonical,
            model_preset_id_override=model_preset_id,
            artifact_override=_resolve_preview_artifact(
                preview,
                inherited_mode=ArtifactMode.DEFAULT,
                inherited_spec="",
            ),
        )
        # Provenance + lane are framework-controlled; the agent's claim is overridden.
        draft.proposed_by = f"node:{node.id}"
        draft.planspace_id = lane_id
        new_node_drafts[canonical] = draft

    for canonical, draft in new_node_drafts.items():
        draft.scheduled_deps = _rewrite_scheduled_deps(
            draft.scheduled_deps,
            slug_to_canonical,
            store,
            project.id,
            lane_id,
            f"new virtual {canonical}",
            result,
        )
    if result.fatal:
        return result

    mutated_node_updates: list[Node] = []
    for existing, preview, rel in mutated_virtuals:
        try:
            model_preset_id = _resolve_planner_model_preset(
                preview,
                inherited_model_preset_id=existing.model_preset_id,
                store=store,
            )
        except ValueError as exc:
            result.rejection_reasons.append(
                f"{rel}: invalid model_preset_id ({exc})"
            )
            result.fatal = True
            return result
        updated = virtual_preview_to_node(
            preview,
            project_id=project.id,
            canonical_id=existing.id,
            verify_script_ref=existing.verify_script_ref,
            model_preset_id_override=model_preset_id,
            artifact_override=_resolve_preview_artifact(
                preview,
                inherited_mode=existing.artifact_mode,
                inherited_spec=existing.artifact_spec,
            ),
        )
        if existing.resume_from_node_id:
            resume_source = store.load_node(project.id, existing.resume_from_node_id)
            expected_preset = (
                resume_source.model_preset_id
                if resume_source is not None and resume_source.model_preset_id
                else existing.model_preset_id
            )
            if updated.model_preset_id != expected_preset:
                result.rejection_reasons.append(
                    f"{rel}: resume virtuals inherit model_preset_id from their source node"
                )
                result.fatal = True
                return result
        # Framework-controlled metadata follows the original, not the rewritten preview.
        updated.proposed_by = existing.proposed_by
        updated.created_at = existing.created_at
        updated.planspace_id = existing.planspace_id
        updated.resume_from_node_id = existing.resume_from_node_id
        # qa_mode is deliberately absent from the preview contract (it is the
        # user's own consent to be interrupted), so a planner rewrite would
        # otherwise silently clear it.
        updated.qa_mode = existing.qa_mode
        updated.scheduled_deps = _rewrite_scheduled_deps(
            updated.scheduled_deps,
            slug_to_canonical,
            store,
            project.id,
            lane_id,
            f"modified virtual {existing.id}",
            result,
        )
        mutated_node_updates.append(updated)
    if result.fatal:
        return result

    # --- Cycle detection ---
    # Build the full dep graph (existing lane virtuals + new + mutated).
    lane_nodes = [n for n in store.list_nodes(project.id) if (n.planspace_id or "") == lane_id]
    by_id: dict[str, Node] = {n.id: n for n in lane_nodes}
    for canonical, draft in new_node_drafts.items():
        by_id[canonical] = draft
    for updated in mutated_node_updates:
        by_id[updated.id] = updated

    if has_cycle(by_id):
        result.rejection_reasons.append("scheduled_deps would introduce a cycle in the lane DAG")
        result.fatal = True
        return result

    # --- Atomic persistence ---
    # Past this point, no validation errors expected. Commit all writes.
    for canonical, draft in new_node_drafts.items():
        store.create_node(draft)
        result.new_virtuals.append(draft)
    for updated in mutated_node_updates:
        store.update_node(updated)
        result.modified_virtuals.append(updated)

    return result
