"""Build the structured artifact emitted by a diff-review node."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import MAX_ARTIFACT_BYTES
from .domain import Node
from .git_state import GitFileStatus, tree_diff, tree_file_bytes


DIFF_ARTIFACT_NAME = "run-diff.json"
DIFF_ARTIFACT_KIND = "miniclaw2.diff/v1"
INLINE_CONTENT_BUDGET = 1536 * 1024


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def _status(item: GitFileStatus) -> str:
    return {
        "A": "added",
        "D": "deleted",
        "R": "renamed",
    }.get(item.index_status[:1], "modified")


def _text_blob(cwd: str, tree: str, path: str) -> tuple[str | None, bool]:
    data = tree_file_bytes(cwd, tree, path)
    if data is None or b"\x00" in data:
        return None, True
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        return None, True


def build_diff_artifact(
    cwd: str,
    *,
    base_tree: str,
    head_tree: str,
    started_at: float,
    ended_at: float,
    concurrent_node_ids: list[str],
) -> dict[str, Any]:
    """Build a complete file inventory while inlining content within budget."""
    changed = tree_diff(cwd, base_tree, head_tree)
    files: list[dict[str, Any]] = []
    used = 0
    truncated = False
    for item in changed:
        status = _status(item)
        entry: dict[str, Any] = {
            "path": item.path,
            "status": status,
            "old_path": item.old_path,
            "additions": item.additions,
            "deletions": item.deletions,
            "binary": item.binary,
            "inlined": False,
        }
        before: str | None = None
        after: str | None = None
        binary = item.binary
        if not binary and status != "added":
            before, binary = _text_blob(cwd, base_tree, item.old_path or item.path)
        if not binary and status != "deleted":
            after, binary = _text_blob(cwd, head_tree, item.path)
        entry["binary"] = binary
        if binary:
            entry["omitted_reason"] = "binary file"
        else:
            content = {"before": before, "after": after}
            cost = len(json.dumps(content, ensure_ascii=False).encode("utf-8"))
            if used + cost <= INLINE_CONTENT_BUDGET:
                entry.update(content)
                entry["inlined"] = True
                used += cost
            else:
                entry["omitted_reason"] = "exceeds inline budget"
                truncated = True
        files.append(entry)

    artifact = {
        "kind": DIFF_ARTIFACT_KIND,
        "base": {"tree": base_tree, "at": _iso(started_at)},
        "head": {"tree": head_tree, "at": _iso(ended_at)},
        "concurrent_node_ids": sorted(set(concurrent_node_ids)),
        "totals": {
            "files": len(files),
            "additions": sum(item.additions for item in changed),
            "deletions": sum(item.deletions for item in changed),
        },
        "truncated": truncated,
        "files": files,
    }
    # The content budget is conservative, but keep the publication cap as a
    # hard invariant if paths or metadata are unexpectedly huge.
    if len(json.dumps(artifact, ensure_ascii=False).encode("utf-8")) > MAX_ARTIFACT_BYTES:
        for entry in reversed(files):
            if not entry.get("inlined"):
                continue
            entry.pop("before", None)
            entry.pop("after", None)
            entry["inlined"] = False
            entry["omitted_reason"] = "exceeds artifact size cap"
            artifact["truncated"] = True
            if len(json.dumps(artifact, ensure_ascii=False).encode("utf-8")) <= MAX_ARTIFACT_BYTES:
                break
    return artifact


def write_diff_artifact(output_dir: Path, payload: dict[str, Any]) -> bool:
    """Write the framework artifact unless the agent already owns its name."""
    path = output_dir / DIFF_ARTIFACT_NAME
    if path.exists():
        return False
    output_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True
