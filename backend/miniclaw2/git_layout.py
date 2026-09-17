from __future__ import annotations

from pathlib import Path

from .domain import ContextLayout, GitLayout, LaneLayout
from .migrations.errors import MigrationError
from .migrations.transaction import atomic_json
from .migrations.validation import read_object


GHOST_GIT_NODE_ID = "commit:ghost"


def discard_transient_git_positions(root: Path) -> None:
    """Remove host-local working-tree positions from a shared snapshot."""
    for path in sorted(root.glob("projects/*/git-layout.json")):
        layout = GitLayout.model_validate(read_object(path))
        if GHOST_GIT_NODE_ID not in layout.nodes:
            continue
        layout.nodes.pop(GHOST_GIT_NODE_ID)
        atomic_json(path, layout.model_dump())


def check_git_layout_conflicts(base: Path, local: Path, remote: Path) -> None:
    _check_layout_conflicts(
        base,
        local,
        remote,
        "git-layout.json",
        GitLayout,
        "Git 节点",
        ignored_node_ids=frozenset({GHOST_GIT_NODE_ID}),
    )


def merge_git_layouts(base: Path, local: Path, remote: Path) -> None:
    """Materialize the conflict-checked per-node merge into both Git inputs."""
    _merge_layouts(
        base,
        local,
        remote,
        "git-layout.json",
        GitLayout,
        "Git 节点",
        ignored_node_ids=frozenset({GHOST_GIT_NODE_ID}),
    )


def merge_lane_layouts(base: Path, local: Path, remote: Path) -> None:
    """Merge cosmetic lane positions without blocking metadata sync."""
    _merge_layouts(
        base,
        local,
        remote,
        "lane-layout.json",
        LaneLayout,
        "方向",
        prefer_local_on_conflict=True,
    )


def _merge_layouts(
    base: Path, local: Path, remote: Path, filename: str,
    model: type[GitLayout] | type[LaneLayout] | type[ContextLayout], label: str,
    *, ignored_node_ids: frozenset[str] = frozenset(),
    prefer_local_on_conflict: bool = False,
) -> None:
    paths = {
        path.relative_to(root)
        for root in (base, local, remote)
        for path in root.glob(f"projects/*/{filename}")
    }
    for relative in sorted(paths):
        project = relative.parent / "project.json"
        if not all((root / project).is_file() for root in (local, remote)):
            continue
        ancestor, ours, theirs = (
            model.model_validate(read_object(root / relative)).nodes
            if (root / relative).exists()
            else {}
            for root in (base, local, remote)
        )
        merged = {}
        for node_id in sorted(
            (ancestor.keys() | ours.keys() | theirs.keys()) - ignored_node_ids
        ):
            before, left, right = ancestor.get(node_id), ours.get(node_id), theirs.get(node_id)
            if left == right:
                value = left
            elif left == before:
                value = right
            elif right == before:
                value = left
            elif prefer_local_on_conflict:
                value = left
            else:
                raise MigrationError("schema_conflict", f"{label}位置冲突：{node_id}", local / relative)
            if value is not None:
                merged[node_id] = value
        payload = model.model_validate({"schema_version": 1, "nodes": merged}).model_dump()
        atomic_json(local / relative, payload)
        atomic_json(remote / relative, payload)


def check_context_layout_conflicts(base: Path, local: Path, remote: Path) -> None:
    _check_layout_conflicts(base, local, remote, "context-layout.json", ContextLayout, "上下文")


def _check_layout_conflicts(
    base: Path, local: Path, remote: Path, filename: str,
    model: type[GitLayout] | type[LaneLayout] | type[ContextLayout], label: str,
    *, ignored_node_ids: frozenset[str] = frozenset(),
) -> None:
    paths = {
        path.relative_to(root)
        for root in (base, local, remote)
        for path in root.glob(f"projects/*/{filename}")
    }
    for relative in sorted(paths):
        project = relative.parent / "project.json"
        if not all((root / project).is_file() for root in (local, remote)):
            continue
        ancestor, ours, theirs = (
            model.model_validate(read_object(root / relative)).nodes if (root / relative).exists() else {}
            for root in (base, local, remote)
        )
        for node_id in sorted((ancestor.keys() | ours.keys() | theirs.keys()) - ignored_node_ids):
            before, left, right = ancestor.get(node_id), ours.get(node_id), theirs.get(node_id)
            deletion_conflict = before is not None and (left is None) != (right is None)
            if deletion_conflict or (left != before and right != before and left != right):
                raise MigrationError("schema_conflict", f"{label}位置冲突：{node_id}", local / relative)
