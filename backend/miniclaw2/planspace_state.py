"""Structured planspace STATUS.md state and derived PLAN.md helpers."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .domain import Node


UNKNOWN = "unknown"
_FRONTMATTER_RE = re.compile(r"\A---\n(?P<yaml>.*?)\n---\n?(?P<body>.*)\Z", re.S)


@dataclass(slots=True)
class PlanspaceStatus:
    goal: str = UNKNOWN
    current_state: str = UNKNOWN
    open_questions: list[dict[str, str]] = field(default_factory=list)
    decisions: list[dict[str, str]] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    body: str = "# Notes\n\n"


def parse_planspace_status(text: str) -> PlanspaceStatus:
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return PlanspaceStatus(body=_normalize_body(text))

    try:
        raw = yaml.safe_load(match.group("yaml")) or {}
    except yaml.YAMLError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    return PlanspaceStatus(
        goal=_coerce_string(raw.get("goal")),
        current_state=_coerce_string(raw.get("current_state")),
        open_questions=_coerce_dict_slots(
            raw.get("open_questions"),
            required=("id", "summary", "raised_at", "raised_by"),
        ),
        decisions=_coerce_dict_slots(
            raw.get("decisions"),
            required=("id", "summary", "decided_at", "decided_by"),
        ),
        out_of_scope=_coerce_string_list(raw.get("out_of_scope")),
        body=_normalize_body(match.group("body")),
    )


def render_planspace_status(status: PlanspaceStatus) -> str:
    frontmatter = {
        "goal": _coerce_string(status.goal),
        "current_state": _coerce_string(status.current_state),
        "open_questions": status.open_questions,
        "decisions": status.decisions,
        "out_of_scope": status.out_of_scope,
    }
    yaml_text = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).strip()
    return f"---\n{yaml_text}\n---\n\n{_normalize_body(status.body).strip()}\n"


def load_planspace_status(path: Path) -> PlanspaceStatus:
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return PlanspaceStatus()
    return parse_planspace_status(text)


def write_planspace_status(path: Path, status: PlanspaceStatus) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_planspace_status(status), encoding="utf-8")


def validate_planspace_status(status: PlanspaceStatus) -> list[str]:
    errors: list[str] = []
    if not status.goal.strip():
        errors.append("goal must be a non-empty string or 'unknown'")
    if not status.current_state.strip():
        errors.append("current_state must be a non-empty string or 'unknown'")
    errors.extend(_validate_slot_ids(status.open_questions, "Q", "open_questions"))
    errors.extend(_validate_slot_ids(status.decisions, "D", "decisions"))
    for index, item in enumerate(status.out_of_scope):
        if not item.strip():
            errors.append(f"out_of_scope[{index}] must be non-empty")
    return errors


def derive_plan_markdown(status: PlanspaceStatus) -> str:
    lines = [
        "# Plan",
        "",
        "## Current State",
        "",
        status.current_state.strip() or UNKNOWN,
        "",
        "## Open Questions",
        "",
    ]
    if status.open_questions:
        for item in status.open_questions:
            lines.append(f"- [ ] [{item['id']}] {item['summary']}")
    else:
        lines.append("_No open questions._")

    lines.extend(["", "## Decisions To Carry Forward", ""])
    if status.decisions:
        for item in status.decisions:
            lines.append(f"- [ ] [from {item['id']}] {item['summary']}")
    else:
        lines.append("_No decisions awaiting downstream work._")

    lines.extend(["", "## Not Addressing", ""])
    if status.out_of_scope:
        for item in status.out_of_scope:
            lines.append(f"- {item}")
    else:
        lines.append("_Nothing explicitly out of scope._")

    return "\n".join(lines).rstrip() + "\n"


def refresh_derived_plan(planspace_dir: Path) -> None:
    status = load_planspace_status(planspace_dir / "STATUS.md")
    plan_path = planspace_dir / "PLAN.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(derive_plan_markdown(status), encoding="utf-8")


def remove_status_slot(
    status: PlanspaceStatus,
    operation: str,
    target_id: str | int,
) -> dict[str, Any] | None:
    """Remove a single entry from one of STATUS's list slots.

    Operations:

    - ``remove_open_question`` / ``remove_decision`` — ``target_id`` is the
      ``Q*`` / ``D*`` id string. The matching entry is dropped; remaining
      ids are kept stable (no renumber) to honor the append-only id rule.
    - ``remove_out_of_scope`` — ``target_id`` is the integer index.

    Returns a summary dict the caller can echo back, or ``None`` if the
    requested entry does not exist or the operation is unknown.
    """
    if operation == "remove_open_question":
        before = len(status.open_questions)
        status.open_questions = [
            item for item in status.open_questions if item.get("id") != target_id
        ]
        if len(status.open_questions) == before:
            return None
        return {"target": "STATUS.md", "operation": operation, "id": target_id}
    if operation == "remove_decision":
        before = len(status.decisions)
        status.decisions = [
            item for item in status.decisions if item.get("id") != target_id
        ]
        if len(status.decisions) == before:
            return None
        return {"target": "STATUS.md", "operation": operation, "id": target_id}
    if operation == "remove_out_of_scope":
        if not isinstance(target_id, int):
            return None
        if target_id < 0 or target_id >= len(status.out_of_scope):
            return None
        status.out_of_scope.pop(target_id)
        return {"target": "STATUS.md", "operation": operation, "index": target_id}
    return None


def apply_status_update(
    status: PlanspaceStatus,
    update: dict[str, Any],
    node: Node,
) -> dict[str, Any] | None:
    operation = update.get("operation")
    if operation in {"append_observation", "append_body", "append_note"}:
        text = _text_payload(update)
        if text is None:
            return None
        append_node_body_update(status, node, text)
        return {"target": "STATUS.md", "operation": operation, "chars": len(text)}

    if operation == "rewrite_current_state":
        text = _text_payload(update) or _coerce_optional_string(update.get("current_state"))
        if text is None:
            return None
        status.current_state = text
        return {"target": "STATUS.md", "operation": operation, "chars": len(text)}

    if operation == "add_open_question":
        summary = _summary_payload(update)
        if summary is None:
            return None
        entry = {
            "id": _next_slot_id(status.open_questions, "Q", update.get("id")),
            "summary": summary,
            "raised_at": _coerce_optional_string(update.get("raised_at")) or _today(),
            "raised_by": _coerce_optional_string(update.get("raised_by")) or node.id,
        }
        status.open_questions.append(entry)
        return {"target": "STATUS.md", "operation": operation, "id": entry["id"]}

    if operation == "add_decision":
        summary = _summary_payload(update)
        if summary is None:
            return None
        entry = {
            "id": _next_slot_id(status.decisions, "D", update.get("id")),
            "summary": summary,
            "decided_at": _coerce_optional_string(update.get("decided_at")) or _today(),
            "decided_by": _coerce_optional_string(update.get("decided_by")) or node.id,
        }
        status.decisions.append(entry)
        return {"target": "STATUS.md", "operation": operation, "id": entry["id"]}

    if operation == "add_out_of_scope":
        text = _summary_payload(update)
        if text is None:
            return None
        status.out_of_scope.append(text)
        return {"target": "STATUS.md", "operation": operation, "chars": len(text)}

    return None


def append_node_body_update(status: PlanspaceStatus, node: Node, text: str) -> None:
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    block = (
        f"## {timestamp} - node {node.id}\n\n"
        f"- terminal_state: {node.state.value}\n"
        f"- acceptance_state: {node.acceptance_state.value}\n\n"
        f"{text.strip()}\n"
    )
    status.body = _append_body_block(status.body, block)


def render_update_payload_as_markdown(delta: Any) -> str:
    if not isinstance(delta, dict):
        return "_No structured interim update was available._"
    updates = delta.get("updates")
    if not isinstance(updates, list) or not updates:
        return json.dumps(delta, indent=2, ensure_ascii=False)

    sections: list[str] = []
    for index, update in enumerate(updates, start=1):
        if not isinstance(update, dict):
            sections.append(f"### Update {index}\n\n{json.dumps(update, ensure_ascii=False)}")
            continue
        title_parts = [
            str(update.get("target") or "STATUS.md"),
            str(update.get("operation") or "update"),
        ]
        heading = " / ".join(title_parts)
        payload = (
            _text_payload(update)
            or _summary_payload(update)
            or _coerce_optional_string(update.get("patch"))
            or json.dumps(update, indent=2, ensure_ascii=False)
        )
        sections.append(f"### Update {index}: {heading}\n\n{payload.strip()}")
    return "\n\n".join(sections).strip()


def merge_reviewed_update_markdown(
    *,
    interim_delta: Any,
    user_judgment: str,
    review_guidance: str,
    gate_node: Node,
    source_node: Node | None,
) -> str:
    source = source_node.id if source_node is not None else "unknown"
    judgment = user_judgment.strip() or "_No free-form user judgment was provided._"
    guidance = review_guidance.strip() or "_No review guidance was provided._"
    return (
        f"# Review checkpoint merge\n\n"
        f"Source node: `{source}`\n"
        f"Gate node: `{gate_node.id}`\n\n"
        f"## Interim agent state update\n\n"
        f"{render_update_payload_as_markdown(interim_delta)}\n\n"
        f"## Review guidance shown to user\n\n"
        f"{guidance}\n\n"
        f"## Review (user)\n\n"
        f"{judgment}\n\n"
        f"## Resulting decision\n\n"
        "The next agent must treat the user judgment above as the controlling "
        "decision when continuing this planspace.\n"
    )


def _normalize_body(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "# Notes\n\n"
    if stripped.startswith("# Notes"):
        return stripped + "\n"
    return f"# Notes\n\n{stripped}\n"


def _append_body_block(body: str, block: str) -> str:
    base = _normalize_body(body).rstrip()
    return f"{base}\n\n{block.strip()}\n"


def _coerce_string(value: Any) -> str:
    text = _coerce_optional_string(value)
    return text if text is not None else UNKNOWN


def _coerce_optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _coerce_optional_string(item)
        if text is not None:
            out.append(text)
    return out


def _coerce_dict_slots(value: Any, *, required: tuple[str, ...]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        entry: dict[str, str] = {}
        for key in required:
            entry[key] = _coerce_string(item.get(key))
        out.append(entry)
    return out


def _validate_slot_ids(items: list[dict[str, str]], prefix: str, slot_name: str) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(rf"^{prefix}\d+$")
    for index, item in enumerate(items):
        item_id = item.get("id", "")
        if not pattern.match(item_id):
            errors.append(f"{slot_name}[{index}].id must look like {prefix}1")
        if item_id in seen:
            errors.append(f"{slot_name}[{index}].id duplicates {item_id}")
        seen.add(item_id)
        if not item.get("summary", "").strip():
            errors.append(f"{slot_name}[{index}].summary must be non-empty")
    return errors


def _summary_payload(update: dict[str, Any]) -> str | None:
    return (
        _coerce_optional_string(update.get("summary"))
        or _coerce_optional_string(update.get("text"))
        or _coerce_optional_string(update.get("question"))
        or _coerce_optional_string(update.get("decision"))
    )


def _text_payload(update: dict[str, Any]) -> str | None:
    return _coerce_optional_string(update.get("text"))


def _next_slot_id(items: list[dict[str, str]], prefix: str, requested: Any) -> str:
    requested_text = _coerce_optional_string(requested)
    existing = {item.get("id") for item in items}
    if requested_text and requested_text not in existing and re.match(rf"^{prefix}\d+$", requested_text):
        return requested_text
    highest = 0
    for item in items:
        item_id = item.get("id", "")
        match = re.match(rf"^{prefix}(\d+)$", item_id)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}{highest + 1}"


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())
