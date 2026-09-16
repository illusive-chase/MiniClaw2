from __future__ import annotations

from pathlib import Path

from .domain import ContextLayout, GitLayout, LaneLayout
from .migrations.errors import MigrationError
from .migrations.validation import read_object


def check_git_layout_conflicts(base: Path, local: Path, remote: Path) -> None:
    _check_layout_conflicts(base, local, remote, "git-layout.json", GitLayout, "Git 节点")


def check_lane_layout_conflicts(base: Path, local: Path, remote: Path) -> None:
    _check_layout_conflicts(base, local, remote, "lane-layout.json", LaneLayout, "方向")


def check_context_layout_conflicts(base: Path, local: Path, remote: Path) -> None:
    _check_layout_conflicts(base, local, remote, "context-layout.json", ContextLayout, "上下文")


def _check_layout_conflicts(
    base: Path, local: Path, remote: Path, filename: str,
    model: type[GitLayout] | type[LaneLayout] | type[ContextLayout], label: str,
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
        for node_id in sorted(ancestor.keys() | ours.keys() | theirs.keys()):
            before, left, right = ancestor.get(node_id), ours.get(node_id), theirs.get(node_id)
            deletion_conflict = before is not None and (left is None) != (right is None)
            if deletion_conflict or (left != before and right != before and left != right):
                raise MigrationError("schema_conflict", f"{label}位置冲突：{node_id}", local / relative)
