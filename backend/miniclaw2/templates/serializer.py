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
  so those saves are rejected. External ``scheduled_deps`` are dropped: a
  node id carries no stable, human-meaningful port name, so inventing
  ``in:dep1``-style inputs would publish a poor template interface. The
  template editor is where authors explicitly name and connect input ports.
- The selection must form exactly one connected component under
  ``scheduled_deps ∪ resume_from_node_id``.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from ..domain import (
    Category,
    Node,
    NodeKind,
    NodeState,
    TERMINAL_NODE_STATES,
)
from ..store import Store
from ..virtual_graph import is_connected
from .loader import (
    SCHEMA_VERSION,
    Template,
    TemplateError,
    user_templates_root,
    _load_from_root,
    _scan_placeholders,
)


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

    arguments = _merge_scanned_arguments(
        [text for _, text in prompt_writes],
        [],
    )
    template_yaml: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "name": display_name,
        "brief": brief_text or f"User template: {display_name}",
        "allowed_model_preset_ids": allowed_model_preset_ids,
        "lane_mode": "manual",
        # Most canvas prompts are finalized text, but an intentional
        # ``{{name}}`` must obey exactly the loader's parameter semantics.
        "arguments": arguments,
        "inputs": [],
    }
    lane_yaml = {"nodes": lane_entries}

    return _materialize_template(
        target_dir,
        slug,
        template_yaml=template_yaml,
        lane_yaml=lane_yaml,
        prompt_writes=prompt_writes,
        store_root=store.root,
        overwrite=False,
    )


def rewrite_user_template(
    slug: str,
    *,
    name: str,
    brief: str,
    nodes: list[dict[str, Any]],
    arguments: list[dict[str, Any]],
    inputs: list[dict[str, Any]],
    store_root: Path,
) -> Template:
    """Replace an existing user template from the editor's complete state.

    The candidate is written to a sibling directory and loaded through
    ``loader._load_from_root`` before it can replace the current template.
    This keeps editor validation identical to runtime loading and preserves
    the old directory byte-for-byte when validation fails.
    """
    if not _valid_user_template_slug(slug):
        raise SerializerError(f"用户模板 slug 非法: {slug!r}")

    target_dir = user_templates_root(store_root) / slug
    if not target_dir.is_dir() or target_dir.is_symlink():
        raise SerializerError(f"未找到用户模板: {slug}")

    current = _load_from_root(target_dir, slug, store_root=store_root)
    lane_entries: list[dict[str, Any]] = []
    prompt_writes: list[tuple[str, str]] = []
    for index, node in enumerate(nodes):
        kind = node.get("kind")
        if kind != NodeKind.AGENT.value:
            raise SerializerError(
                "用户模板只能包含 agent 节点；不支持 verifier 节点"
            )

        prompt_path_rel = f"prompts/node-{index}.md"
        entry: dict[str, Any] = {
            "id": node.get("id"),
            "kind": kind,
            "category": node.get("category"),
            "prompt_file": prompt_path_rel,
        }
        if node.get("subtype") is not None:
            entry["subtype"] = node["subtype"]
        if node.get("brief") is not None:
            entry["brief"] = node["brief"]
        if node.get("scheduled_deps"):
            entry["scheduled_deps"] = node["scheduled_deps"]
        if node.get("resume_from"):
            entry["resume_from"] = node["resume_from"]
        if node.get("motivation"):
            entry["motivation"] = node["motivation"]

        prompt = node.get("prompt")
        if not isinstance(prompt, str):
            raise SerializerError(
                f"模板节点 {node.get('id')!r} 的 prompt 必须是字符串"
            )
        prompt_writes.append((prompt_path_rel, prompt))
        lane_entries.append(entry)

    merged_arguments = _merge_scanned_arguments(
        [text for _, text in prompt_writes],
        arguments,
    )
    template_yaml: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "brief": brief,
        "allowed_model_preset_ids": list(current.allowed_model_preset_ids),
        "lane_mode": "manual",
        "arguments": merged_arguments,
        "inputs": inputs,
    }
    return _materialize_template(
        target_dir,
        slug,
        template_yaml=template_yaml,
        lane_yaml={"nodes": lane_entries},
        prompt_writes=prompt_writes,
        store_root=store_root,
        overwrite=True,
    )


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


def _merge_scanned_arguments(
    prompts: list[str],
    declared: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Append prompt arguments using the loader's scanner and ordering."""
    merged = [dict(argument) for argument in declared]
    declared_names = {
        argument.get("name")
        for argument in merged
        if isinstance(argument.get("name"), str)
    }
    for prompt in prompts:
        scanned_arguments, _ = _scan_placeholders(prompt)
        for name in scanned_arguments:
            if name in declared_names:
                continue
            merged.append({"name": name, "description": "", "default": None})
            declared_names.add(name)
    return merged


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


def _materialize_template(
    target_dir: Path,
    slug: str,
    *,
    template_yaml: dict[str, Any],
    lane_yaml: dict[str, Any],
    prompt_writes: list[tuple[str, str]],
    store_root: Path,
    overwrite: bool,
) -> Template:
    """Write, loader-validate, and install one complete template directory.

    New templates and editor rewrites deliberately share this path. Each file
    uses the existing atomic ``.tmp`` writer; the complete candidate is then
    validated in an isolated sibling directory. For an overwrite, the old
    directory is retained as a rollback backup until the validated candidate
    has been installed and loaded from its final path.
    """
    parent = target_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    nonce = uuid4().hex
    candidate = parent / f".{slug}.{nonce}.tmp"
    backup = parent / f".{slug}.{nonce}.bak"

    try:
        candidate.mkdir(parents=False, exist_ok=False)
        (candidate / "prompts").mkdir(parents=True, exist_ok=True)
        for rel, text in prompt_writes:
            _atomic_write_text(candidate / rel, text)
        _atomic_write_yaml(candidate / "template.yaml", template_yaml)
        _atomic_write_yaml(candidate / "lane.yaml", lane_yaml)
        _load_from_root(candidate, slug, store_root=store_root)
    except Exception:
        if candidate.exists():
            shutil.rmtree(candidate, ignore_errors=True)
        raise

    if not overwrite:
        installed = False
        try:
            candidate.replace(target_dir)
            installed = True
            return _load_from_root(target_dir, slug, store_root=store_root)
        except Exception:
            if candidate.exists():
                shutil.rmtree(candidate, ignore_errors=True)
            if installed and target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            raise

    moved_old = False
    try:
        target_dir.replace(backup)
        moved_old = True
        candidate.replace(target_dir)
        result = _load_from_root(target_dir, slug, store_root=store_root)
    except Exception:
        # Until the first rename succeeds, target_dir is still the original.
        # Only remove a target created by this installation after the old
        # directory is known to be safely held in backup.
        if moved_old and target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        if moved_old and backup.exists():
            backup.replace(target_dir)
        if candidate.exists():
            shutil.rmtree(candidate, ignore_errors=True)
        raise
    else:
        shutil.rmtree(backup, ignore_errors=True)
        return result


def _valid_user_template_slug(slug: str) -> bool:
    return bool(slug) and "/" not in slug and ".." not in slug


def delete_user_template(slug: str, store_root: Path | None = None) -> bool:
    """Delete the user template directory. Returns True if it was removed."""
    if not _valid_user_template_slug(slug):
        return False
    target = user_templates_root(store_root) / slug
    if not target.is_dir() or target.is_symlink():
        return False
    shutil.rmtree(target)
    return True
