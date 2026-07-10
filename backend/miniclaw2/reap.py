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

from .domain import Category, Node, NodeKind, NodeState, Project, _new_id
from .materialize import diff_lane
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
    new_virtuals: list[Node] = field(default_factory=list)
    modified_virtuals: list[Node] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    fatal: bool = False

    def ok(self) -> bool:
        return self.own_preview_ok and not self.fatal and not self.rejection_reasons


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

    # --- Categorize remaining preview writes ---
    new_virtuals: list[tuple[str, VirtualPreview]] = []  # (slug, preview)
    mutated_virtuals: list[tuple[Node, VirtualPreview, str]] = []  # (existing_node, preview, rel)
    for ident, (preview, rel) in parsed.items():
        if ident == node.id:
            continue
        existing = store.load_node(project.id, ident)
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
        draft = virtual_preview_to_node(
            preview,
            project_id=project.id,
            canonical_id=canonical,
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
        updated = virtual_preview_to_node(
            preview,
            project_id=project.id,
            canonical_id=existing.id,
            verify_script_ref=existing.verify_script_ref,
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
