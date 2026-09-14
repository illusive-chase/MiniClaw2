from __future__ import annotations

from collections.abc import Mapping

from .domain import Node


def node_coordinate_space(node: Node, nodes: Mapping[str, Node]) -> str:
    visited: set[str] = set()
    while node.id not in visited:
        visited.add(node.id)
        if node.planspace_id:
            return f"planspace:{node.planspace_id}"
        snapshot = node.settings_snapshot.get("active_planspace_id")
        if isinstance(snapshot, str) and snapshot:
            return f"planspace:{snapshot}"
        parent = nodes.get(node.parent_node_id or "")
        if parent is None:
            break
        node = parent
    return "canvas"
