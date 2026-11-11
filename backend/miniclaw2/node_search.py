"""Keyword search across one project's nodes.

Two sources are searched, matching what the node inspector shows:

* the preview — ``motivation`` / ``summary`` / ``next_implications`` of an
  executed node's agent-written ``preview.json``, or for a virtual node the
  motivation and prompt draft its materialized preview is rendered from;
* the agent's plain text responses — the ``text_delta`` stream in
  ``events.jsonl``, cut into blocks at the same boundaries the Activity
  transcript uses (tool calls, thinking, questions, errors, turn ends).

Op nodes are left out: they are framework state transitions whose previews
are stubs, and the inspector does not open them as agents.

The query is split on whitespace and common CJK punctuation; a node matches
when every term occurs somewhere in those sources (case-insensitive).

Cost matters because event logs are large: the biggest real project carries
~430 MB of ``events.jsonl`` of which ~5% are text deltas. The index therefore
keeps the extracted text blocks per node in memory and, since the log is
append-only, resumes from the last consumed byte offset on the next query.
Only ``text_delta`` lines are JSON-decoded; every other line is classified
from its fixed-shape prefix. A steady-state query costs a ``stat`` per node.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .domain import Node, NodeKind, NodeState

#: Separators between terms: whitespace plus the CJK punctuation people type
#: between words when they paste a phrase from a summary.
_TERM_SPLIT = re.compile(r"[\s，。、；：！？,;]+")
MAX_TERMS = 8
DEFAULT_LIMIT = 200
MAX_TEXT_SNIPPETS = 3
SNIPPET_BEFORE = 48
SNIPPET_AFTER = 120

#: Events that close the current text block. ``usage`` and ``node_updated``
#: do not: the transcript builder ignores them, so text on either side of
#: one still renders as a single block.
_TEXT_BOUNDARY_TYPES = frozenset(
    {
        "node_started",
        "thinking",
        "activity",
        "interaction_request",
        "error",
        "turn_done",
    }
)
_EVENT_TYPE_PREFIX = re.compile(rb'"event": \{"type": "([a-z_]+)"')

PREVIEW_FIELDS = ("motivation", "summary", "next_implications")


def split_terms(query: str) -> list[str]:
    """Lower-cased, de-duplicated search terms, in typed order."""
    out: list[str] = []
    for raw in _TERM_SPLIT.split(query):
        term = raw.strip().lower()
        if term and term not in out:
            out.append(term)
    return out[:MAX_TERMS]


@dataclass
class _TextState:
    """Text blocks extracted from one ``events.jsonl``, resumable."""

    inode: int = 0
    offset: int = 0
    blocks: list[str] = field(default_factory=list)
    #: Whether the last block may still grow with the next ``text_delta``.
    open_block: bool = False
    _pending: list[str] = field(default_factory=list)

    def feed(self, event_type: str, text: str | None) -> None:
        if event_type == "text_delta":
            if text:
                self._pending.append(text)
            return
        if event_type in _TEXT_BOUNDARY_TYPES:
            self._flush()
            self.open_block = False

    def _flush(self) -> None:
        if not self._pending:
            return
        chunk = "".join(self._pending)
        self._pending.clear()
        if self.open_block and self.blocks:
            self.blocks[-1] += chunk
        else:
            self.blocks.append(chunk)
        self.open_block = True

    def settle(self) -> None:
        """Commit buffered deltas; the block stays open for later appends."""
        self._flush()


def _read_text_blocks(path: Path, state: _TextState | None) -> _TextState | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    if (
        state is None
        or state.inode != stat.st_ino
        or stat.st_size < state.offset
    ):
        state = _TextState(inode=stat.st_ino)
    if stat.st_size == state.offset:
        return state
    with path.open("rb") as handle:
        handle.seek(state.offset)
        data = handle.read(stat.st_size - state.offset)
    # Consume complete lines only: a writer mid-append leaves a partial line
    # that the next query picks up whole.
    end = data.rfind(b"\n")
    if end < 0:
        return state
    for line in data[: end + 1].splitlines():
        if not line.strip():
            continue
        match = _EVENT_TYPE_PREFIX.search(line, 0, 160)
        event_type = match.group(1).decode() if match else None
        if event_type is not None and event_type != "text_delta":
            state.feed(event_type, None)
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        event = record.get("event") if isinstance(record, dict) else None
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if not isinstance(kind, str):
            continue
        text = event.get("text")
        state.feed(kind, text if isinstance(text, str) else None)
    state.settle()
    state.offset += end + 1
    return state


def _snippet(text: str, terms: list[str]) -> str:
    lowered = text.lower()
    # ``str.lower`` can change length for a handful of code points; offsets
    # only carry over from the lowered copy when it did not.
    source = text if len(lowered) == len(text) else lowered
    hits = [index for index in (lowered.find(term) for term in terms) if index >= 0]
    first = min(hits) if hits else 0
    start = max(0, first - SNIPPET_BEFORE)
    end = min(len(source), first + SNIPPET_AFTER)
    snippet = " ".join(source[start:end].split())
    if start > 0:
        snippet = "…" + snippet
    if end < len(source):
        snippet += "…"
    return snippet


def _preview_fields(text: str | None) -> dict[str, str]:
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        name: value
        for name in PREVIEW_FIELDS
        if isinstance((value := payload.get(name)), str) and value.strip()
    }


def _node_label(node: Node) -> str:
    for value in (node.summary, node.prompt_draft, node.prompt):
        if value and value.strip():
            text = " ".join(value.split())
            return text[:119] + "…" if len(text) > 120 else text
    return ""


class NodeSearchIndex:
    """Per-app cache of searchable node text.

    Owned by the app, like ``ActiveNodesIndex``, so tests do not share state.
    One lock serializes queries: the endpoint runs in the threadpool and
    two overlapping queries would otherwise resume the same log twice.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._text: dict[str, dict[str, _TextState]] = {}
        self._previews: dict[str, dict[str, tuple[int, int, dict[str, str]]]] = {}

    def forget_project(self, project_id: str) -> None:
        with self._lock:
            self._text.pop(project_id, None)
            self._previews.pop(project_id, None)

    def _preview(self, store: Any, pid: str, node: Node) -> dict[str, str]:
        if node.state is NodeState.VIRTUAL:
            fields: dict[str, str] = {}
            if node.summary and node.summary.strip():
                fields["motivation"] = node.summary
            if node.prompt_draft and node.prompt_draft.strip():
                fields["prompt_draft"] = node.prompt_draft
            return fields
        path = store.node_dir(pid, node.id) / "preview.json"
        cache = self._previews.setdefault(pid, {})
        try:
            stat = path.stat()
        except OSError:
            cache.pop(node.id, None)
            return {}
        hit = cache.get(node.id)
        if hit is not None and hit[:2] == (stat.st_mtime_ns, stat.st_size):
            return hit[2]
        try:
            fields = _preview_fields(path.read_text(encoding="utf-8"))
        except OSError:
            return {}
        cache[node.id] = (stat.st_mtime_ns, stat.st_size, fields)
        return fields

    def _text_blocks(self, store: Any, pid: str, node: Node) -> list[str]:
        if node.state is NodeState.VIRTUAL:
            return []
        cache = self._text.setdefault(pid, {})
        state = _read_text_blocks(
            store.node_dir(pid, node.id) / "events.jsonl",
            cache.get(node.id),
        )
        if state is None:
            cache.pop(node.id, None)
            return []
        cache[node.id] = state
        return state.blocks

    def search(
        self,
        store: Any,
        pid: str,
        nodes: list[Node],
        query: str,
        *,
        limit: int = DEFAULT_LIMIT,
        planspace_titles: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        terms = split_terms(query)
        if not terms:
            return {"terms": [], "results": [], "total": 0, "truncated": False}
        titles = planspace_titles or {}
        matched: list[tuple[float, dict[str, Any]]] = []
        with self._lock:
            live = {node.id for node in nodes}
            for cache in (self._text.get(pid), self._previews.get(pid)):
                for stale in [nid for nid in (cache or {}) if nid not in live]:
                    del cache[stale]  # type: ignore[union-attr]
            for node in nodes:
                if node.kind is NodeKind.OP:
                    continue
                result = self._match_node(store, pid, node, terms)
                if result is None:
                    continue
                result["planspace_title"] = (
                    titles.get(node.planspace_id) if node.planspace_id else None
                )
                stamp = node.finished_at or node.started_at or node.created_at
                matched.append((stamp, result))
        matched.sort(key=lambda item: item[0], reverse=True)
        return {
            "terms": terms,
            "results": [result for _, result in matched[:limit]],
            "total": len(matched),
            "truncated": len(matched) > limit,
        }

    def _match_node(
        self, store: Any, pid: str, node: Node, terms: list[str]
    ) -> dict[str, Any] | None:
        preview = self._preview(store, pid, node)
        blocks = self._text_blocks(store, pid, node)
        lowered_preview = {name: value.lower() for name, value in preview.items()}
        lowered_blocks = [block.lower() for block in blocks]
        for term in terms:
            if not any(term in value for value in lowered_preview.values()) and not any(
                term in block for block in lowered_blocks
            ):
                return None
        matches: list[dict[str, str]] = []
        for name, value in preview.items():
            if any(term in lowered_preview[name] for term in terms):
                matches.append({"field": name, "snippet": _snippet(value, terms)})
        text_hits = 0
        for block, lowered in zip(blocks, lowered_blocks):
            if text_hits >= MAX_TEXT_SNIPPETS:
                break
            if any(term in lowered for term in terms):
                matches.append({"field": "text", "snippet": _snippet(block, terms)})
                text_hits += 1
        return {
            "node_id": node.id,
            "kind": node.kind.value,
            "state": node.state.value,
            "category": node.category.value if node.category else None,
            "planspace_id": node.planspace_id,
            "label": _node_label(node),
            "matches": matches,
            "text_match_count": sum(
                1 for lowered in lowered_blocks if any(term in lowered for term in terms)
            ),
        }
