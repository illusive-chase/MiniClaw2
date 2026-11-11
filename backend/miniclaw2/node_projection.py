from __future__ import annotations

from typing import Any

from .domain import Node


NODE_DETAIL_FIELDS = {"system_context_snapshot", "launch_instructions_snapshot"}
PROMPT_PREVIEW_LENGTH = 120
NODE_EVENTS = {"node_started", "node_updated", "turn_done"}


def node_list_projection(node: Node | dict[str, Any]) -> dict[str, Any]:
    from .templates.loader import _scan_placeholders

    data = (
        node.model_dump(exclude=NODE_DETAIL_FIELDS)
        if isinstance(node, Node)
        else {key: value for key, value in node.items() if key not in NODE_DETAIL_FIELDS}
    )
    prompt = data.get("prompt") or ""
    if "prompt_argument_names" not in data:
        data["prompt_argument_names"] = _scan_placeholders(data.get("prompt_draft") or prompt)[0]
    data["prompt_truncated"] = (
        bool(data.get("prompt_truncated")) or len(prompt) > PROMPT_PREVIEW_LENGTH
    )
    data["prompt"] = prompt[:PROMPT_PREVIEW_LENGTH]
    return data


def node_event_projection(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("type") in NODE_EVENTS and isinstance(event.get("node"), dict):
        return {**event, "node": node_list_projection(event["node"])}
    return event


def node_event_persisted(event: dict[str, Any]) -> dict[str, Any]:
    """Drop launch snapshots from node events before they are written to disk.

    The snapshots stay in ``node.json``; repeating them in every event only
    inflates ``events.jsonl``. Unlike ``node_event_projection`` this keeps the
    node otherwise intact, since the log is durable data, not a display view.
    """
    if event.get("type") in NODE_EVENTS and isinstance(event.get("node"), dict):
        node = {key: value for key, value in event["node"].items() if key not in NODE_DETAIL_FIELDS}
        return {**event, "node": node}
    return event
