"""Helpers for node output artifacts.

Node output contracts write to project-relative files under
``.miniclaw2/outputs/<node-id>/``. This module resolves those paths
safely, loads the produced artifact for the dashboard, and derives a
short summary when possible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import Node, NodeKind, NodeOutputKind, default_node_output_path


@dataclass(slots=True)
class NodeArtifact:
    kind: str
    path: str | None
    exists: bool
    content: str | None = None
    data: Any | None = None
    error: str | None = None


def node_output_relpath(node: Node) -> str | None:
    if node.kind is not NodeKind.AGENT:
        return None
    return node.output_path or default_node_output_path(node.id, node.output_kind)


def validate_node_output_path(path_str: str | None) -> str | None:
    """Return an error string when a custom output path is not project-relative."""
    if path_str is None or not path_str:
        return None
    path = Path(path_str)
    if path.is_absolute():
        return "output path must be project-relative"
    if any(part == ".." for part in path.parts):
        return "output path must not contain '..'"
    return None


def node_output_path_escapes_root(path_str: str | None) -> bool:
    return validate_node_output_path(path_str) is not None


def resolve_node_output_path(root_path: str, node: Node) -> Path | None:
    rel = node_output_relpath(node)
    if rel is None:
        return None
    if node_output_path_escapes_root(rel):
        return None
    root = Path(root_path).resolve()
    target = (root / Path(rel)).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def load_node_artifact(root_path: str, node: Node) -> NodeArtifact:
    rel = node_output_relpath(node)
    if rel is None or node.output_kind is NodeOutputKind.FREEFORM:
        return NodeArtifact(kind=node.output_kind.value, path=rel, exists=False)

    target = resolve_node_output_path(root_path, node)
    if target is None:
        return NodeArtifact(
            kind=node.output_kind.value,
            path=rel,
            exists=False,
            error="output path escapes project root",
        )
    if not target.exists():
        return NodeArtifact(kind=node.output_kind.value, path=rel, exists=False)

    try:
        content = target.read_text(encoding="utf-8")
    except OSError as exc:
        return NodeArtifact(
            kind=node.output_kind.value,
            path=rel,
            exists=False,
            error=str(exc),
        )

    data: Any | None = None
    if node.output_kind is NodeOutputKind.INTERFACE:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            return NodeArtifact(
                kind=node.output_kind.value,
                path=rel,
                exists=True,
                content=content,
                error=f"invalid JSON: {exc}",
            )
        schema_error = _validate_interface_data(data)
        if schema_error:
            return NodeArtifact(
                kind=node.output_kind.value,
                path=rel,
                exists=True,
                content=content,
                data=data,
                error=schema_error,
            )
    elif node.output_kind is NodeOutputKind.SUMMARY:
        schema_error = _validate_summary_markdown(content)
        if schema_error:
            return NodeArtifact(
                kind=node.output_kind.value,
                path=rel,
                exists=True,
                content=content,
                error=schema_error,
            )

    return NodeArtifact(
        kind=node.output_kind.value,
        path=rel,
        exists=True,
        content=content,
        data=data,
    )


def summarize_node_artifact(node: Node, artifact: NodeArtifact) -> str | None:
    if not artifact.exists:
        if artifact.path:
            return f"{node.output_kind.value} output missing at {artifact.path}"
        return f"{node.output_kind.value} output missing"

    if artifact.error:
        return f"{node.output_kind.value} output invalid: {artifact.error}"

    if node.output_kind is NodeOutputKind.INTERFACE and isinstance(artifact.data, dict):
        summary = artifact.data.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        purpose = artifact.data.get("purpose")
        if isinstance(purpose, str) and purpose.strip():
            return purpose.strip()
        result = artifact.data.get("result")
        if isinstance(result, str) and result.strip():
            return result.strip()
        return f"{node.output_kind.value} output ready"

    if node.output_kind is NodeOutputKind.SUMMARY and artifact.content:
        return _markdown_summary(artifact.content)

    if artifact.path:
        return f"{node.output_kind.value} output ready at {artifact.path}"
    return f"{node.output_kind.value} output ready"


def _markdown_summary(text: str) -> str | None:
    lines = text.splitlines()
    in_purpose = False
    collected: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            heading = stripped[2:].strip().lower()
            if in_purpose and heading != "purpose":
                break
            in_purpose = heading == "purpose"
            continue
        if not in_purpose:
            continue
        if not stripped:
            if collected:
                break
            continue
        collected.append(stripped)

    if not collected:
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                collected = [stripped]
                break

    if not collected:
        return None

    text_out = " ".join(collected).strip()
    return text_out if len(text_out) <= 140 else text_out[:137].rstrip() + "..."


def _validate_interface_data(data: Any) -> str | None:
    if not isinstance(data, dict):
        return "interface output must be a JSON object"
    missing = [
        key
        for key in ("kind", "summary", "purpose", "method", "result", "files")
        if key not in data
    ]
    if missing:
        return f"interface output missing required keys: {', '.join(missing)}"
    if data.get("kind") != "interface":
        return "interface output kind must be 'interface'"
    for key in ("summary", "purpose", "method"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            return f"interface output {key} must be a non-empty string"
    if not isinstance(data.get("files"), list):
        return "interface output files must be a list"
    return None


def _validate_summary_markdown(text: str) -> str | None:
    headings = {
        line.strip()[2:].strip().lower()
        for line in text.splitlines()
        if line.strip().startswith("# ")
    }
    missing = [name for name in ("purpose", "method", "result") if name not in headings]
    if missing:
        return f"summary output missing required sections: {', '.join(missing)}"
    return None
