"""Validation helpers for virtual-node dependency DAGs."""

from __future__ import annotations

from .domain import Node


def has_cycle(by_id: dict[str, Node]) -> bool:
    """Return True when ``scheduled_deps`` introduce a cycle.

    Unknown dependency ids are treated as already-resolved leaves. Callers that
    require every dependency to exist should validate references separately.
    """
    color: dict[str, int] = {nid: 0 for nid in by_id}  # 0=white, 1=gray, 2=black

    def visit(nid: str) -> bool:
        if color.get(nid, 2) == 2:
            return False
        if color.get(nid) == 1:
            return True
        color[nid] = 1
        node = by_id.get(nid)
        if node is not None:
            for dep in node.scheduled_deps:
                if visit(dep):
                    return True
        color[nid] = 2
        return False

    for nid in list(by_id.keys()):
        if color[nid] == 0 and visit(nid):
            return True
    return False
