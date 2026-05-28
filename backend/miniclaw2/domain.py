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
    settings_override: dict[str, Any] = Field(default_factory=dict)
    temporary: bool = False
    scenario_name: str | None = None
    created_at: float = Field(default_factory=_now)


class Node(BaseModel):
    id: str = Field(default_factory=_new_id)
    project_id: str
    kind: NodeKind = NodeKind.AGENT
    op_kind: str | None = None
    state: NodeState = NodeState.QUEUED
    parent_node_id: str | None = None
    context_sources: list[str] = Field(default_factory=list)
    provider: str = "claude"
    provider_session_id: str | None = None
    provider_turn_id: str | None = None
    sdk_session_id: str | None = None
    commit_before: str | None = None
    commit_after: str | None = None
    prompt: str = ""
    contract: str = ""
    summary: str | None = None
    error: str | None = None
    usage: TokenUsage | None = None
    system_context_snapshot: str = ""
    settings_snapshot: dict[str, Any] = Field(default_factory=dict)
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
    """Snapshotted on-disk context inherited via a context edge.

    Defined for forward-compat; no producers in Phase 0.
    """
    id: str = Field(default_factory=_new_id)
    source_node_id: str
    claude_md: str = ""
    memory: dict[str, str] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    agents: list[dict[str, Any]] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=_now)
