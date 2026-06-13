"""Per-launch filesystem projection of the active lane.

Per PROPOSAL_VIRTUAL_NODES §3.4: before each agent launch, the framework
copies the durable lane state into a real subtree under the worktree at
``.miniclaw2/graph/lanes/<lane_id>/``. The agent reads previews,
transcripts, and artifacts with native ``Read``; writes its own
``preview.json`` and (if planning/review) virtual previews with native
``Write``. At terminal, the runner walk-diffs against a pre-launch
snapshot and feeds the diff to ``reap.reap_lane``.

This module is library code; the runner integration is in ``runner.py``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .domain import Node, NodeKind, NodeState, Project
from .preview import render_executed_preview, render_virtual_preview
from .store import Store


GRAPH_DIRNAME = ".miniclaw2/graph/lanes"
ARTIFACTS_DIRNAME = ".miniclaw2/outputs"

# Event types that belong in a node transcript. Skips state_change /
# usage / node_started / node_updated which are runner bookkeeping, and
# interaction_request which is a gate handoff (gates have their own
# audit trail in gates.jsonl).
_TRANSCRIPT_TYPES = frozenset({"text_delta", "thinking", "activity", "error"})


def lane_root(project: Project, lane_id: str) -> Path:
    """Absolute path to the materialized lane root for this project."""
    return Path(project.root_path) / GRAPH_DIRNAME / lane_id


def node_dir(lane_root_path: Path, node_id: str) -> Path:
    return lane_root_path / "nodes" / node_id


def _stub_motivation(node: Node) -> str:
    return node.summary or node.prompt[:200] or ""


def _stub_summary(node: Node) -> str:
    if node.error:
        return node.error[:500]
    if node.summary:
        return node.summary
    return "(no agent-written summary)"


def _stub_next_implications(node: Node) -> str:
    return "(framework stub — agent did not write its own preview)"


def render_node_preview(node: Node) -> str:
    """Render the preview.json content for ``node`` based on its state.

    Used during materialization to seed the projection with what the
    durable store already knows.
    """
    if node.state is NodeState.VIRTUAL:
        return render_virtual_preview(node)
    return render_executed_preview(
        node,
        motivation=_stub_motivation(node),
        summary=_stub_summary(node),
        next_implications=_stub_next_implications(node),
    )


def _build_transcript(store: Store, project_id: str, node_id: str) -> list[dict]:
    """Filter events.jsonl into a transcript record list."""
    records = store.replay_events(project_id, node_id, since_seq=0)
    transcript: list[dict] = []
    for record in records:
        event = record.get("event") or {}
        if event.get("type") in _TRANSCRIPT_TYPES:
            transcript.append({"seq": record.get("seq", 0), "event": event})
    return transcript


def _write_transcript(path: Path, transcript: list[dict]) -> None:
    path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")


def _project_artifacts_dir(project: Project, node_id: str) -> Path:
    return Path(project.root_path) / ARTIFACTS_DIRNAME / node_id


def materialize_active_lane(
    project: Project, lane_id: str, store: Store
) -> Path:
    """Build ``.miniclaw2/graph/lanes/<lane_id>/nodes/<nid>/`` for every
    node in the lane and return the lane root path.

    For each node:
        - ``preview.json`` — always (executed or virtual)
        - ``transcript.json`` — executed nodes only (from events.jsonl)
        - ``artifacts/`` — executed nodes only, copied from project
          ``.miniclaw2/outputs/<nid>/`` if present
        - ``human-review.md`` — durable copy if exists in node store

    Lane scoping is by ``node.planspace_id == lane_id``. Nodes without
    a planspace match an empty lane id.
    """
    root = lane_root(project, lane_id)
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)

    nodes = [n for n in store.list_nodes(project.id) if (n.planspace_id or "") == lane_id]
    for node in nodes:
        ndir = node_dir(root, node.id)
        ndir.mkdir(parents=True, exist_ok=True)
        # Prefer durable agent-written preview when present; otherwise
        # render a stub from Node fields.
        durable = store.read_node_preview(project.id, node.id) if node.state is not NodeState.VIRTUAL else None
        text = durable if durable is not None else render_node_preview(node)
        (ndir / "preview.json").write_text(text, encoding="utf-8")
        if node.state is not NodeState.VIRTUAL and node.kind is NodeKind.AGENT:
            transcript = _build_transcript(store, project.id, node.id)
            _write_transcript(ndir / "transcript.json", transcript)
            artifacts_src = _project_artifacts_dir(project, node.id)
            if artifacts_src.exists():
                shutil.copytree(artifacts_src, ndir / "artifacts", dirs_exist_ok=True)
        durable_review = store.node_dir(project.id, node.id) / "human-review.md"
        if durable_review.exists():
            shutil.copy(durable_review, ndir / "human-review.md")
    return root


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return
    for p in root.rglob("*"):
        if p.is_file():
            yield p


def snapshot_lane(lane_root_path: Path) -> dict[str, str]:
    """Return ``{relpath: sha256}`` for every regular file under the
    lane root. Used as the pre-launch baseline for ``diff_lane``.
    """
    snap: dict[str, str] = {}
    if not lane_root_path.exists():
        return snap
    for path in _walk_files(lane_root_path):
        rel = path.relative_to(lane_root_path).as_posix()
        snap[rel] = _sha256_of_file(path)
    return snap


@dataclass
class LaneDiff:
    created: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.created or self.modified or self.deleted)


def diff_lane(pre_snapshot: dict[str, str], lane_root_path: Path) -> LaneDiff:
    """Walk-diff the lane root against a pre-launch snapshot."""
    diff = LaneDiff()
    seen: set[str] = set()
    for path in _walk_files(lane_root_path):
        rel = path.relative_to(lane_root_path).as_posix()
        seen.add(rel)
        current = _sha256_of_file(path)
        prior = pre_snapshot.get(rel)
        if prior is None:
            diff.created.append(rel)
        elif prior != current:
            diff.modified.append(rel)
    for rel in pre_snapshot:
        if rel not in seen:
            diff.deleted.append(rel)
    diff.created.sort()
    diff.modified.sort()
    diff.deleted.sort()
    return diff
