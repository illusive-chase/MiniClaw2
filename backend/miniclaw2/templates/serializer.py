"""Serialize a canvas selection of nodes into a user-authored template.

The output is a directory under
``$MINICLAW_CONTEXT_HOME/templates/<slug>/`` shaped like the bundled
templates so ``loader._load_from_root`` can read it back.

Design constraints (agreed in the design discussion; see the plan file):

- User templates contain **only agent-kind nodes**. Verifiers are template-
  only in the domain model and the UI has no affordance for authoring
  scripts, so any verifier in the selection triggers a rejection.
- ``op`` nodes are silently filtered — they are framework-injected and
  never author-owned.
- The selection must be **stable**: nodes in transient states (queued,
  running, waiting, awaiting_human_input) are rejected.
- ``resume_from_node_id`` leaving the selection is a semantic corruption,
  so those saves are rejected. External ``scheduled_deps`` are dropped
  silently — the template becomes topologically self-contained.
- The selection must form exactly one connected component under
  ``scheduled_deps ∪ resume_from_node_id``.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from ..domain import (
    Category,
    Node,
    NodeKind,
    NodeState,
    ReviewSubtype,
    TERMINAL_NODE_STATES,
)
from ..store import Store
from ..virtual_graph import is_connected
from .loader import Template, TemplateError, user_templates_root, _load_from_root


TRANSIENT_STATES = frozenset(
    {
        NodeState.QUEUED,
        NodeState.RUNNING,
        NodeState.WAITING,
        NodeState.AWAITING_HUMAN_INPUT,
    }
)


class SerializerError(TemplateError):
    """Raised when a save-as-template request is invalid."""


def serialize_selection(
    store: Store,
    project_id: str,
    node_ids: list[str],
    *,
    name: str,
    brief: str,
    store_root: Path | None = None,
) -> Template:
    """Persist ``node_ids`` from ``project_id`` as a new user template.

    Returns the freshly loaded ``Template``. Raises ``SerializerError`` on
    validation failure or name collision.
    """
    display_name = (name or "").strip()
    if not display_name:
        raise SerializerError("template name is required")
    brief_text = (brief or "").strip()

    slug = _slugify(display_name)
    if not slug:
        raise SerializerError("template name must contain letters or digits")

    root_dir = user_templates_root(store_root or store.root)
    target_dir = root_dir / slug
    if target_dir.exists():
        raise SerializerError(f"a template named {display_name!r} already exists")

    nodes = _load_and_validate_nodes(store, project_id, node_ids)
    ordered = _topological_order(nodes)
    slug_map = {node.id: f"n{idx}" for idx, node in enumerate(ordered)}
    selection_ids = set(slug_map)
    allowed_model_preset_ids: list[str] = []
    for node in ordered:
        if node.model_preset_id and node.model_preset_id not in allowed_model_preset_ids:
            allowed_model_preset_ids.append(node.model_preset_id)
    if not allowed_model_preset_ids:
        raise SerializerError("selection has no model preset metadata")

    lane_entries: list[dict[str, Any]] = []
    prompt_writes: list[tuple[str, str]] = []
    for node in ordered:
        node_slug = slug_map[node.id]
        entry: dict[str, Any] = {
            "id": node_slug,
            "kind": NodeKind.AGENT.value,
            "category": (node.category or Category.REGULAR).value,
        }
        if node.subtype is not None:
            entry["subtype"] = node.subtype.value
        if node.brief is not None:
            entry["brief"] = node.brief.model_dump()

        prompt_text = _prompt_text(node)
        prompt_path_rel = f"prompts/{node_slug}.md"
        entry["prompt_file"] = prompt_path_rel
        prompt_writes.append((prompt_path_rel, prompt_text))

        translated_deps = [
            slug_map[dep] for dep in node.scheduled_deps if dep in selection_ids
        ]
        if node.resume_from_node_id:
            # Validated in _load_and_validate_nodes to be in-selection.
            resume_slug = slug_map[node.resume_from_node_id]
            if resume_slug not in translated_deps:
                translated_deps.append(resume_slug)
            entry["resume_from"] = resume_slug
        if translated_deps:
            entry["scheduled_deps"] = translated_deps

        motivation = (node.summary or "").strip()
        if motivation:
            entry["motivation"] = motivation

        lane_entries.append(entry)

    template_yaml: dict[str, Any] = {
        "name": display_name,
        "brief": brief_text or f"User template: {display_name}",
        "allowed_model_preset_ids": allowed_model_preset_ids,
        "lane_mode": "manual",
    }
    lane_yaml = {"nodes": lane_entries}

    # Materialise the directory. On any failure clean up so the loader
    # never sees a half-built template.
    try:
        target_dir.mkdir(parents=True, exist_ok=False)
        (target_dir / "prompts").mkdir(parents=True, exist_ok=True)
        for rel, text in prompt_writes:
            _atomic_write_text(target_dir / rel, text)
        _atomic_write_yaml(target_dir / "template.yaml", template_yaml)
        _atomic_write_yaml(target_dir / "lane.yaml", lane_yaml)
    except Exception:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        raise

    try:
        return _load_from_root(target_dir, slug)
    except TemplateError:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise


def _load_and_validate_nodes(
    store: Store,
    project_id: str,
    node_ids: list[str],
) -> list[Node]:
    if not node_ids:
        raise SerializerError("selection is empty")

    seen: set[str] = set()
    loaded: list[Node] = []
    for raw in node_ids:
        nid = (raw or "").strip()
        if not nid or nid in seen:
            continue
        seen.add(nid)
        node = store.load_node(project_id, nid)
        if node is None:
            raise SerializerError(f"node {nid!r} does not exist in this project")
        loaded.append(node)

    # Silently drop op nodes; they are framework-injected.
    filtered = [n for n in loaded if n.kind is not NodeKind.OP]
    if not filtered:
        raise SerializerError(
            "selection contains no agent nodes to save"
        )

    for node in filtered:
        if node.kind is NodeKind.VERIFIER:
            raise SerializerError(
                "verifier nodes cannot be saved into user templates"
            )
        if node.state in TRANSIENT_STATES:
            raise SerializerError(
                f"node {node.id!r} is still running — wait for it to reach"
                " a terminal state before saving"
            )
        if node.state is not NodeState.VIRTUAL and node.state not in TERMINAL_NODE_STATES:
            raise SerializerError(
                f"node {node.id!r} is in state {node.state.value!r}; only virtual"
                " and terminal nodes can be saved"
            )

    ids_in_set = {n.id for n in filtered}
    for node in filtered:
        if node.resume_from_node_id and node.resume_from_node_id not in ids_in_set:
            raise SerializerError(
                f"node {node.id!r} resumes from {node.resume_from_node_id!r}, which"
                " is not in the selection — include it or drop the resume link"
            )

    by_id = {n.id: n for n in filtered}
    if not is_connected(by_id):
        raise SerializerError(
            "selection must form one connected component (via scheduled_deps"
            " or resume links)"
        )

    return filtered


def _topological_order(nodes: list[Node]) -> list[Node]:
    """Order nodes so every dep appears before its dependents.

    Ties break on original creation time; fully-independent nodes retain
    their input order. Only in-set deps count (matches serialization).
    """
    ids = {n.id for n in nodes}
    by_id = {n.id: n for n in nodes}
    indegree = {n.id: 0 for n in nodes}
    successors: dict[str, list[str]] = {n.id: [] for n in nodes}
    for node in nodes:
        for dep in node.scheduled_deps:
            if dep in ids:
                indegree[node.id] += 1
                successors[dep].append(node.id)
        if node.resume_from_node_id and node.resume_from_node_id in ids:
            # Resume treated as an ordering constraint too — the resume
            # target must be earlier so its provider session id is
            # discoverable when the runner promotes the resumer.
            indegree[node.id] += 1
            successors[node.resume_from_node_id].append(node.id)

    ready = sorted(
        (nid for nid, deg in indegree.items() if deg == 0),
        key=lambda i: (by_id[i].created_at, i),
    )
    out: list[Node] = []
    while ready:
        cur = ready.pop(0)
        out.append(by_id[cur])
        for nxt in successors[cur]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
        ready.sort(key=lambda i: (by_id[i].created_at, i))
    if len(out) != len(nodes):
        # is_connected passed but there's a cycle purely inside the
        # selection — should be impossible because the runtime rejects
        # cycles at create time, but guard anyway.
        raise SerializerError("selection contains a dependency cycle")
    return out


def _prompt_text(node: Node) -> str:
    text = node.prompt_draft if node.state is NodeState.VIRTUAL else node.prompt
    return (text or "").rstrip() + "\n"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_yaml(path: Path, data: dict[str, Any]) -> None:
    _atomic_write_text(
        path,
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
    )


def delete_user_template(slug: str, store_root: Path | None = None) -> bool:
    """Delete the user template directory. Returns True if it was removed."""
    if not slug or "/" in slug or ".." in slug:
        return False
    target = user_templates_root(store_root) / slug
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    return True
