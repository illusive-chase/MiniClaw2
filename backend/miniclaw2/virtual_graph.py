"""Validation helpers for virtual-node dependency DAGs."""

from __future__ import annotations

from collections import deque

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


def is_connected(by_id: dict[str, Node]) -> bool:
    """Return True when nodes form one connected component.

    Edges are the undirected union of ``scheduled_deps`` and
    ``resume_from_node_id``. References that point outside ``by_id`` are
    ignored — connectivity is only checked among the input set.
    """
    if not by_id:
        return False
    adj: dict[str, set[str]] = {nid: set() for nid in by_id}
    for nid, node in by_id.items():
        for dep in node.scheduled_deps:
            if dep in adj:
                adj[nid].add(dep)
                adj[dep].add(nid)
        resume = node.resume_from_node_id
        if resume and resume in adj:
            adj[nid].add(resume)
            adj[resume].add(nid)
    start = next(iter(by_id))
    seen: set[str] = {start}
    queue: deque[str] = deque([start])
    while queue:
        cur = queue.popleft()
        for nxt in adj[cur]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return len(seen) == len(by_id)
