"""Domain models — Project, Node, HumanGate, ContextBundle."""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _new_id() -> str:
    return uuid4().hex[:12]


def _now() -> float:
    return time.time()


class NodeKind(StrEnum):
    AGENT = "agent"
    GATE = "gate"
    OP = "op"


class NodeState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    AWAITING_REVIEW = "awaiting_review"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class AcceptanceState(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    UNREVIEWED = "unreviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"


class VerdictSource(StrEnum):
    NONE = "none"
    HUMAN = "human"
    DETERMINISTIC = "deterministic"
    CROSS_PROVIDER = "cross_provider"
    SAME_PROVIDER_ADVISORY = "same_provider_advisory"


class GateKind(StrEnum):
    INLINE = "inline"
    CHECKPOINT = "checkpoint"


class GateSubtype(StrEnum):
    PERMISSION = "permission"
    ASK_USER = "ask_user"
    PLAN_APPROVAL = "plan_approval"
    CHECKPOINT_REVIEW = "checkpoint_review"


class GateState(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"


class NodeOutputKind(StrEnum):
    FREEFORM = "freeform"
    SUMMARY = "summary"
    INTERFACE = "interface"
    REVIEW_BRIEF = "review_brief"


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cumulative_output_tokens: int | None = None
    cumulative_cache_creation_tokens: int | None = None


class Project(BaseModel):
    id: str = Field(default_factory=_new_id)
    root_path: str
    name: str = ""
    provider: str = "claude"
    head_commit: str | None = None
    parent_project_id: str | None = None
    parent_commit: str | None = None
    project_context_binding_id: str | None = None
    settings_override: dict[str, Any] = Field(default_factory=dict)
    temporary: bool = False
    scenario_name: str | None = None
    scenario_step_history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: float = Field(default_factory=_now)


class Node(BaseModel):
    id: str = Field(default_factory=_new_id)
    project_id: str
    kind: NodeKind = NodeKind.AGENT
    op_kind: str | None = None
    state: NodeState = NodeState.QUEUED
    parent_node_id: str | None = None
    context_sources: list[str] = Field(default_factory=list)
    context_bundle_id: str | None = None
    context_bundle_path: str | None = None
    provider: str = "claude"
    provider_session_id: str | None = None
    provider_turn_id: str | None = None
    sdk_session_id: str | None = None
    commit_before: str | None = None
    commit_after: str | None = None
    output_kind: NodeOutputKind = NodeOutputKind.FREEFORM
    output_path: str | None = None
    output_contract_snapshot: str = ""
    prompt: str = ""
    contract: str = ""
    summary: str | None = None
    error: str | None = None
    usage: TokenUsage | None = None
    system_context_snapshot: str = ""
    settings_snapshot: dict[str, Any] = Field(default_factory=dict)
    scenario_step_id: str | None = None
    review_outcome: str | None = None  # "approved" | "rejected" | None — gate nodes only
    acceptance_state: AcceptanceState = AcceptanceState.UNREVIEWED
    verdict_source: VerdictSource = VerdictSource.NONE
    verdict_artifact_path: str | None = None
    verdict_thread_id: str | None = None
    accepted_at: float | None = None
    rejected_at: float | None = None
    created_at: float = Field(default_factory=_now)
    started_at: float | None = None
    finished_at: float | None = None


class HumanGate(BaseModel):
    id: str = Field(default_factory=_new_id)
    node_id: str
    kind: GateKind = GateKind.INLINE
    subtype: GateSubtype
    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    suggestions: list[Any] = Field(default_factory=list)
    state: GateState = GateState.PENDING
    response: dict[str, Any] | None = None
    created_at: float = Field(default_factory=_now)
    resolved_at: float | None = None


class ContextBundle(BaseModel):
    """Legacy context edge shape.

    ContextSpace launch snapshots are currently persisted as JSON files
    by ``miniclaw2.contextspace.compose_context_bundle`` and referenced
    from ``Node.context_bundle_id`` / ``Node.context_bundle_path``.
    """
    id: str = Field(default_factory=_new_id)
    source_node_id: str
    claude_md: str = ""
    memory: dict[str, str] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    agents: list[dict[str, Any]] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=_now)


def default_node_output_path(node_id: str, kind: NodeOutputKind) -> str | None:
    if kind is NodeOutputKind.SUMMARY:
        return f".miniclaw2/outputs/{node_id}/result.md"
    if kind is NodeOutputKind.INTERFACE:
        return f".miniclaw2/outputs/{node_id}/result.json"
    if kind is NodeOutputKind.REVIEW_BRIEF:
        return f".miniclaw2/outputs/{node_id}/brief.md"
    return None


def node_output_contract(kind: NodeOutputKind, path: str | None) -> str:
    if kind is NodeOutputKind.REVIEW_BRIEF:
        output_path = path or ".miniclaw2/outputs/<node-id>/brief.md"
        return (
            "# Node output contract\n\n"
            "This node will be followed by a human review checkpoint. The reviewer reads what you write here verbatim before responding. Make it concrete and minimal.\n\n"
            "## Required output\n"
            f"- Write a markdown file at `{output_path}`.\n"
            "- The file must include these sections, in this order:\n"
            "  - `# How to run`: explicit commands or steps the human should use to exercise what you built. Include exact CLI invocations and any setup. If there is no runnable artifact, say what to read instead.\n"
            "  - `# What to verify`: a checklist of specific behaviors the human should look for (e.g., \"clicking `1 + 2 =` shows `3`\"). Be specific to what you actually built, not generic.\n"
            "  - `# Response schema`: the JSON keys and shapes the human should put in their review response (e.g., `{ \"approved\": boolean, \"notes\": string }`). The human's response will be written to a JSON file in the repo.\n"
            "- Your work is not done until that file exists.\n"
        )
    if kind is NodeOutputKind.SUMMARY:
        output_path = path or ".miniclaw2/outputs/<node-id>/result.md"
        return (
            "# Node output contract\n\n"
            "This node must produce a markdown summary file for the dashboard and any downstream human readers.\n\n"
            "## Required output\n"
            f"- Write a markdown file at `{output_path}`.\n"
            "- The file must include these sections, in this order:\n"
            "  - `# Purpose`: one concise sentence describing what this node was trying to do.\n"
            "  - `# Method`: what it did to do it, including important tools or edits.\n"
            "  - `# Result`: what happened, including files touched, commands run, and the final outcome.\n"
            "- Keep the file concise but complete.\n"
            "- The dashboard should treat this file as the primary result artifact.\n"
        )
    if kind is NodeOutputKind.INTERFACE:
        output_path = path or ".miniclaw2/outputs/<node-id>/result.json"
        return (
            "# Node output contract\n\n"
            "This node must produce a JSON interface file for downstream parsing.\n\n"
            "## Required output\n"
            f"- Write JSON to `{output_path}`.\n"
            "- The top-level value must be an object.\n"
            "- Include these stable keys: `kind`, `summary`, `purpose`, `method`, `result`, and `files`.\n"
            "- `kind` must be `interface`.\n"
            "- `summary` must be one short sentence.\n"
            "- `result` should hold the machine-readable payload for downstream nodes or programs.\n"
            "- `files` should list the project-relative paths this node created or changed, if any.\n"
            "- The dashboard should treat this file as the primary result artifact.\n"
        )
    return (
        "# Node output contract\n\n"
        "This node has no required artifact. The transcript and workspace changes remain the primary evidence.\n"
    )
