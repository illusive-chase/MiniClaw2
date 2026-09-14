from __future__ import annotations

import math
from typing import Any

from ..sdk import Migration, MigrationContext


def coordinate_space(node: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> str:
    visited: set[str] = set()
    while node["id"] not in visited:
        visited.add(node["id"])
        if node.get("planspace_id"):
            return "planspace:" + node["planspace_id"]
        snapshot = (node.get("settings_snapshot") or {}).get("active_planspace_id")
        if isinstance(snapshot, str) and snapshot:
            return "planspace:" + snapshot
        parent = nodes.get(node.get("parent_node_id"))
        if parent is None:
            break
        node = parent
    return "canvas"


def upgrade(context: MigrationContext) -> None:
    for project_file in context.paths("projects/*/project.json"):
        project_dir = project_file.removesuffix("project.json")
        nodes: dict[str, dict[str, Any]] = {}
        owners: dict[str, str] = {}
        for relative in context.paths(project_dir + "hosts/*/nodes/*/node.json"):
            node = context.read(relative)
            parts = relative.split("/")
            node_id = parts[5]
            if node.get("id") != node_id or node.get("project_id") != parts[1] or node_id in nodes:
                raise ValueError(f"节点路径或跨 host 唯一性无效：{relative}")
            nodes[node_id] = node
            owners[node_id] = parts[3]
        for relative in context.paths(project_dir + "hosts/*/layout.json"):
            payload = context.read(relative)
            hints = payload.get("layout_hints", {})
            if not isinstance(hints, dict):
                raise ValueError(f"布局必须是对象：{relative}")
            owner = relative.split("/")[3]
            positions = {}
            for node_id, position in sorted(hints.items()):
                if owners.get(node_id) != owner:
                    continue
                if not isinstance(position, dict) or any(
                    type(position.get(axis)) not in (int, float) or not math.isfinite(position[axis])
                    for axis in ("x", "y")
                ):
                    raise ValueError(f"节点坐标不是有限数值：{relative}:{node_id}")
                positions[node_id] = {"x": float(position["x"]), "y": float(position["y"]),
                                      "space": coordinate_space(nodes[node_id], nodes)}
            target = relative.removesuffix("layout.json") + "node-layout.json"
            if context.path(target).exists():
                raise ValueError(f"迁移目标已经存在：{target}")
            context.replace(target, {"schema_version": 1, "nodes": positions})
            context.delete(relative)


def verify(context: MigrationContext) -> None:
    if list(context.paths("projects/*/hosts/*/layout.json")):
        raise ValueError("旧布局文件尚未全部转换")


MIGRATION = Migration(
    source=15,
    target=16,
    scopes=("shared",),
    summary="按节点 owner 保留唯一位置；丢弃 foreign 副本、合成图元坐标与旧 viewport，原始文件保留在事务备份中",
    contract="miniclaw2/store/v16:owner-node-layout-v1;explicit-coordinate-space;browser-viewport",
    upgrade=upgrade,
    verify=verify,
    destructive=True,
)
