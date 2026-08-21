"""Cross-project view of nodes that are running, need a human, or just ended.

The UI is structurally single-project: the WebSocket is per-project
(``/ws/{sid}``) and pending gates live only in frontend memory, cleared on
every project switch. A node blocked on a human in project A is therefore
invisible while the user is looking at project B. This module answers two
questions for the whole workspace at once: where is something running or
waiting for me, and what recently finished while I was elsewhere?

Terminal nodes are listed for a bounded window (``TERMINAL_RECENCY_SECONDS``)
so the second question has an answer. Deciding whether the user still *needs*
to see one is not this module's job — the frontend tracks that per device with
a read set, because "have I looked at this" is a property of the person at
this screen, not of the project.

Cost matters here because the endpoint is polled. ``Store.list_nodes``
re-reads and re-validates every ``node.json`` in a project (~1.1ms per node
measured on a 358-node store), so a naive full sweep costs ~400ms and would
burn that every few seconds forever. Instead we keep an mtime/size-keyed
cache of the handful of fields this view needs and re-read only the files
that actually changed, which keeps a steady-state sweep at roughly the cost
of a glob plus stat. Admitting terminal nodes costs no extra I/O: their
records were already read into that cache and merely discarded here.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contextspace import contextspace_root, planspace_display_title, resolve_active_planspace
from .domain import NodeState, Project

logger = logging.getLogger(__name__)

#: States that mean "the machine is busy here" or "a human is needed here".
#: ``virtual`` is deliberately excluded: proposals routinely outnumber
#: executed nodes and would drown the signal this view exists to carry.
ACTIVE_STATES: frozenset[str] = frozenset(
    {
        NodeState.RUNNING.value,
        NodeState.WAITING.value,
        NodeState.AWAITING_HUMAN_INPUT.value,
        NodeState.QUEUED.value,
    }
)

#: Terminal states. Included in the view so "what finished while I was away"
#: is answerable, not just "what is still going".
TERMINAL_STATES: frozenset[str] = frozenset(
    {
        NodeState.DONE.value,
        NodeState.ERROR.value,
        NodeState.CANCELLED.value,
    }
)

#: How far back the panel looks, *not* how long a node keeps nagging. Whether
#: the user still needs to see something is answered client-side by a read set
#: keyed on (node_id, state); this window only bounds the list's depth so it
#: stays scannable. Eight hours covers one work session — leave for lunch and
#: everything that finished meanwhile is still listed.
TERMINAL_RECENCY_SECONDS = 8 * 3600

#: Node prompts can be arbitrarily long; the row shows one line.
LABEL_MAX_CHARS = 120


@dataclass(frozen=True)
class NodeFacts:
    """The subset of a node record this view reads.

    Deliberately not a ``Node``: constructing one runs full Pydantic
    validation plus model-catalog binding, which is exactly the cost this
    module exists to avoid paying on every poll.
    """

    id: str
    state: str
    category: str | None
    kind: str
    planspace_id: str | None
    label: str
    started_at: float | None
    created_at: float
    finished_at: float | None
    owner_host_id: str


@dataclass(frozen=True)
class ActiveEntry:
    project_id: str
    project_name: str
    node_id: str
    state: str
    category: str | None
    planspace_id: str | None
    planspace_title: str | None
    is_active_planspace: bool
    label: str
    started_at: float | None
    #: When the node reached its terminal state. Only set for terminal rows,
    #: which read as "finished 12m ago" — ``started_at`` alone would render a
    #: node that is no longer running as having run continuously since.
    finished_at: float | None
    gate: dict[str, Any] | None


def _label_for(payload: dict[str, Any]) -> str:
    """Prefer the node's own summary, falling back to its prompt."""
    for key in ("summary", "prompt"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            if len(text) > LABEL_MAX_CHARS:
                return text[: LABEL_MAX_CHARS - 1] + "…"
            return text
    return ""


def _facts_from_payload(payload: dict[str, Any], owner_host_id: str) -> NodeFacts | None:
    node_id = payload.get("id")
    state = payload.get("state")
    if not isinstance(node_id, str) or not isinstance(state, str):
        return None

    def _float_or_none(key: str) -> float | None:
        value = payload.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    category = payload.get("category")
    kind = payload.get("kind")
    planspace_id = payload.get("planspace_id")
    return NodeFacts(
        id=node_id,
        state=state,
        category=category if isinstance(category, str) else None,
        kind=kind if isinstance(kind, str) else "agent",
        planspace_id=planspace_id if isinstance(planspace_id, str) else None,
        label=_label_for(payload),
        started_at=_float_or_none("started_at"),
        created_at=_float_or_none("created_at") or 0.0,
        finished_at=_float_or_none("finished_at"),
        owner_host_id=owner_host_id,
    )


class ActiveNodesIndex:
    """Reads node facts across projects, re-parsing only changed files.

    Owned by the app so tests get a fresh cache per instance rather than
    inheriting one another's state through a module global.
    """

    def __init__(self) -> None:
        # Nested per project id, so evicting one project's vanished nodes
        # never has to reason about another project's keys.
        self._cache: dict[str, dict[str, tuple[int, int, NodeFacts]]] = {}

    def forget_project(self, project_id: str) -> None:
        self._cache.pop(project_id, None)

    def retain_projects(self, project_ids: set[str]) -> None:
        """Drop caches for projects that no longer exist."""
        for known in list(self._cache):
            if known not in project_ids:
                self._cache.pop(known, None)

    def _node_files(self, store_root: Path, project: Project) -> list[tuple[Path, str]]:
        """Return ``(node.json path, owning machine id)`` for one project.

        Mirrors ``Store._list_nodes_for_project``'s layout split. Current
        projects use per-host storage; legacy projects may still use the flat
        nodes directory.
        """
        project_dir = store_root / "projects" / project.id
        hosts_dir = project_dir / "hosts"
        if hosts_dir.is_dir():
            return [
                (path, path.parents[2].name)
                for path in hosts_dir.glob("*/nodes/*/node.json")
            ]
        return [
            (path, project.machine_id)
            for path in (project_dir / "nodes").glob("*/node.json")
        ]

    def facts_for_project(self, store_root: Path, project: Project) -> list[NodeFacts]:
        cached = self._cache.get(project.id) or {}
        fresh: dict[str, tuple[int, int, NodeFacts]] = {}
        out: list[NodeFacts] = []
        for path, owner in self._node_files(store_root, project):
            key = str(path)
            try:
                stat = path.stat()
            except OSError:
                continue
            signature = (stat.st_mtime_ns, stat.st_size)
            hit = cached.get(key)
            if hit is not None and (hit[0], hit[1]) == signature:
                fresh[key] = hit
                out.append(hit[2])
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A node being rewritten right now reappears on the next poll.
                logger.debug("skipping unreadable node record %s", path, exc_info=True)
                continue
            if not isinstance(payload, dict):
                continue
            facts = _facts_from_payload(payload, owner)
            if facts is None:
                continue
            fresh[key] = (signature[0], signature[1], facts)
            out.append(facts)
        # Rebuilding from what this pass actually saw drops records for
        # deleted nodes, so the cache cannot grow without bound.
        self._cache[project.id] = fresh
        return out


def _is_visible(facts: NodeFacts, *, now: float) -> bool:
    if facts.state in ACTIVE_STATES:
        return True
    if facts.state in TERMINAL_STATES:
        stamp = facts.finished_at or facts.started_at or facts.created_at
        return bool(stamp) and (now - stamp) <= TERMINAL_RECENCY_SECONDS
    return False


def collect_active_entries(
    registry: Any,
    index: ActiveNodesIndex,
    *,
    now: float | None = None,
) -> list[ActiveEntry]:
    """Gather active nodes across every project this device can act on.

    Non-native nodes are omitted: the backend refuses ``interaction_response``
    for them, so listing one would offer the user a row they cannot resolve.
    A read-only store empties the view for the same reason — gate resolution
    goes through ``assert_writable()``, so every row would be un-actionable.
    """
    moment = time.time() if now is None else now
    store = registry.store
    # Evaluated once per sweep, not per node: the property stats the schema
    # file and the machine record, and this endpoint is polled.
    if store.read_only_reason is not None:
        return []
    store_root = store.root
    context_root = contextspace_root(store_root)
    entries: list[ActiveEntry] = []

    projects = registry.list_projects()
    index.retain_projects({project.id for project in projects})

    for project in projects:
        try:
            facts_list = index.facts_for_project(store_root, project)
        except OSError:
            logger.debug("skipping project %s during sweep", project.id, exc_info=True)
            continue
        visible = [
            facts
            for facts in facts_list
            if _is_visible(facts, now=moment) and _is_native(registry, project, facts)
        ]
        if not visible:
            continue

        # Resolving the active lane touches the contextspace on disk, so only
        # pay for it on projects that actually contribute a row.
        active_planspace_id: str | None = None
        try:
            active = resolve_active_planspace(project, context_root)
            active_planspace_id = active[1].id if active is not None else None
        except Exception:  # noqa: BLE001
            logger.debug(
                "active planspace unresolved for %s", project.id, exc_info=True
            )

        titles: dict[str, str | None] = {}
        for facts in visible:
            planspace_id = facts.planspace_id
            if planspace_id and planspace_id not in titles:
                try:
                    titles[planspace_id] = planspace_display_title(
                        context_root, planspace_id
                    )
                except Exception:  # noqa: BLE001
                    titles[planspace_id] = None
            entries.append(
                ActiveEntry(
                    project_id=project.id,
                    project_name=project.name,
                    node_id=facts.id,
                    state=facts.state,
                    category=facts.category,
                    planspace_id=planspace_id,
                    planspace_title=titles.get(planspace_id) if planspace_id else None,
                    is_active_planspace=bool(
                        planspace_id and planspace_id == active_planspace_id
                    ),
                    label=facts.label,
                    started_at=facts.started_at or facts.created_at,
                    finished_at=facts.finished_at,
                    gate=_gate_summary(registry, project.id, facts),
                )
            )
    return entries


def _is_native(registry: Any, project: Project, facts: NodeFacts) -> bool:
    return facts.owner_host_id == registry.store.machine.id


def _gate_summary(registry: Any, project_id: str, facts: NodeFacts) -> dict[str, Any] | None:
    """Read the open gate's content from the live runner, if there is one.

    The *fact* that a node is blocked is persisted (``_request_gate``
    transitions to WAITING and that write hits the store), but the gate's
    content lives only in ``NodeRunner._gate_records``. When no runner is
    present the row degrades to "waiting" without saying what for, which is
    still the signal the user needs.

    The gate JSONL is deliberately not consulted: it is an append-only event
    log, so replaying it would report gates whose futures died with a previous
    process as though they were still open.
    """
    if facts.state != NodeState.WAITING.value:
        return None
    runtime = getattr(registry, "_runtimes", {}).get(project_id)
    runner = runtime.get_runner(facts.id) if runtime is not None else None
    if runner is None:
        return None
    try:
        return runner.current_gate_summary()
    except Exception:  # noqa: BLE001
        logger.debug("gate summary unavailable for %s", facts.id, exc_info=True)
        return None
