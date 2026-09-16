from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote

from .domain import Node


def node_layout_owners(nodes: list[Node]) -> dict[str, Node]:
    owners = {node.id: node for node in nodes}
    for node in nodes:
        if node.kind == "op":
            continue
        if node.state == "error" and node.error:
            owners[f"err:{node.id}"] = node
        published = [artifact for artifact in node.artifacts if artifact.status == "published"]
        for artifact in published:
            name = quote(artifact.name, safe="~!*'()-._")
            owners[f"artifact:{node.id}:{name}"] = node
        if len(published) > 4:
            owners[f"artifact-overflow:{node.id}"] = node
    return owners


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
