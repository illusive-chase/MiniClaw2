"""Domain models — Project, Node, HumanGate, ContextBundle.

Schema matches PHILOSOPHY §6 and PROPOSAL_VIRTUAL_NODES §3.1. The
ontology is two-axis: ``kind`` distinguishes agent from op; ``category``
(orthogonal, applies to agent only) distinguishes planning, regular,
and review semantics. Reviews are agents — there is no gate node kind.
``HumanGate`` is preserved for inline gates (permission / ask_user /
plan_approval) only.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


def _new_id() -> str:
    return uuid4().hex[:12]


def _now() -> float:
    return time.time()


class NodeKind(StrEnum):
    AGENT = "agent"
    OP = "op"


class NodeState(StrEnum):
    VIRTUAL = "virtual"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    AWAITING_HUMAN_INPUT = "awaiting_human_input"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class Category(StrEnum):
    PLANNING = "planning"
    REGULAR = "regular"
    REVIEW = "review"


class ReviewSubtype(StrEnum):
    AGENTIC_REVIEW = "agentic_review"
    HUMAN_INTERACT_REVIEW = "human_interact_review"


class GateKind(StrEnum):
    INLINE = "inline"


class GateSubtype(StrEnum):
    PERMISSION = "permission"
    ASK_USER = "ask_user"
    PLAN_APPROVAL = "plan_approval"


class GateState(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"


class ReviewBrief(BaseModel):
    check_what: str
    expected: str
    abnormal: str


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
    preferred_language: str | None = None
    head_commit: str | None = None
    parent_project_id: str | None = None
    parent_commit: str | None = None
    project_context_binding_id: str | None = None
    settings_override: dict[str, Any] = Field(default_factory=dict)
    temporary: bool = False
    scenario_name: str | None = None
    scenario_step_history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: float = Field(default_factory=_now)
    layout_hints: dict[str, dict[str, float]] = Field(default_factory=dict)
    layout_viewport: dict[str, float] | None = None
    planspace_view: dict[str, dict[str, bool]] = Field(default_factory=dict)


class Node(BaseModel):
    id: str = Field(default_factory=_new_id)
    project_id: str
    kind: NodeKind = NodeKind.AGENT
    op_kind: str | None = None
    state: NodeState = NodeState.QUEUED
    parent_node_id: str | None = None
    planspace_id: str | None = None
    context_sources: list[str] = Field(default_factory=list)
    context_bundle_id: str | None = None
    context_bundle_path: str | None = None
    provider: str = "claude"
    provider_session_id: str | None = None
    provider_turn_id: str | None = None
    sdk_session_id: str | None = None
    commit_before: str | None = None
    commit_after: str | None = None
    prompt: str = ""
    # Category axis (orthogonal to kind). Required for AGENT; must be
    # None for OP. Defaulted to REGULAR for AGENT in the after-validator.
    category: Category | None = None
    subtype: ReviewSubtype | None = None
    brief: ReviewBrief | None = None
    # Virtual-node-only fields. ``prompt_draft`` becomes ``prompt`` at
    # promotion; ``scheduled_deps`` are node ids that must reach a
    # terminal state before this virtual is eligible to promote.
    prompt_draft: str | None = None
    scheduled_deps: list[str] = Field(default_factory=list)
    proposed_by: str | None = None
    obsolete_reason: str | None = None
    summary: str | None = None
    error: str | None = None
    usage: TokenUsage | None = None
    system_context_snapshot: str = ""
    settings_snapshot: dict[str, Any] = Field(default_factory=dict)
    scenario_step_id: str | None = None
    created_at: float = Field(default_factory=_now)
    started_at: float | None = None
    finished_at: float | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "Node":
        if self.kind is NodeKind.OP:
            if self.category is not None:
                raise ValueError("op nodes must not carry a category")
            if self.subtype is not None or self.brief is not None:
                raise ValueError("op nodes must not carry review fields")
        else:
            # AGENT — category required, default to REGULAR
            if self.category is None:
                object.__setattr__(self, "category", Category.REGULAR)
            if self.category is Category.REGULAR:
                if self.subtype is not None:
                    raise ValueError("regular agents must not carry a subtype")
                if self.brief is not None:
                    raise ValueError("regular agents must not carry a brief")
            elif self.category is Category.REVIEW:
                if self.subtype is None:
                    raise ValueError("review agents require a subtype")
                if self.brief is None:
                    raise ValueError("review agents require a brief")
        if self.state is NodeState.VIRTUAL:
            if self.started_at is not None or self.finished_at is not None:
                raise ValueError("virtual nodes must not carry started_at/finished_at")
        return self


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
    """Legacy context edge shape used by ``compose_context_bundle``."""
    id: str = Field(default_factory=_new_id)
    source_node_id: str
    claude_md: str = ""
    memory: dict[str, str] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    agents: list[dict[str, Any]] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=_now)
