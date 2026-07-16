"""Domain models — Project, Node, HumanGate, and state enums.

Schema matches PHILOSOPHY §6. The
ontology is two-axis: ``kind`` distinguishes agent, op, and verifier;
``category`` (orthogonal, applies to agent/verifier) distinguishes
planning, regular, and review semantics. Agentic and human reviews are
agents; programmatic reviews are verifiers. ``HumanGate`` is preserved
for inline gates (permission / ask_user) only.
"""

from __future__ import annotations

import time
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StrictInt,
    computed_field,
    model_validator,
)

from .model_catalog import (
    default_model_preset_id,
    normalize_model_preset_id,
    provider_for_model_preset,
)


def _new_id() -> str:
    return uuid4().hex[:12]


def _now() -> float:
    return time.time()


class NodeKind(StrEnum):
    AGENT = "agent"
    OP = "op"
    VERIFIER = "verifier"


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
    PROGRAMMATIC_REVIEW = "programmatic_review"


class PlanspaceMode(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


TERMINAL_NODE_STATES: frozenset[NodeState] = frozenset({
    NodeState.DONE,
    NodeState.ERROR,
    NodeState.CANCELLED,
})


# Whitelist of ``agent_op_kind`` values. Kept here as a plain set rather
# than a StrEnum so a new variant can be added without a schema migration.
KNOWN_AGENT_OP_KINDS: frozenset[str] = frozenset({"skill_edit"})


def normalize_planspace_mode(value: str | None) -> PlanspaceMode:
    """Return a ``PlanspaceMode`` from a string; ``None`` → ``MANUAL``."""
    if value is None:
        return PlanspaceMode.MANUAL
    if not isinstance(value, str):
        raise ValueError(f"unknown planspace mode: {value!r}")
    try:
        return PlanspaceMode(value.lower())
    except ValueError as exc:
        raise ValueError(f"unknown planspace mode: {value!r}") from exc


class GateKind(StrEnum):
    INLINE = "inline"


class GateSubtype(StrEnum):
    PERMISSION = "permission"
    ASK_USER = "ask_user"


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
    model_config = ConfigDict(extra="forbid")
    _model_catalog_root: Path | None = PrivateAttr(default=None)

    id: str = Field(default_factory=_new_id)
    root_path: str
    machine_id: str = ""
    machine_label: str = ""
    name: str = ""
    model_preset_id: str = Field(default_factory=default_model_preset_id)
    concurrency: StrictInt = Field(default=1, ge=1)
    preferred_language: str | None = None
    project_context_binding_id: str | None = None
    active_planspace_id: str | None = None
    settings_override: dict[str, Any] = Field(default_factory=dict)
    temporary: bool = False
    template_id: str | None = None
    created_at: float = Field(default_factory=_now)
    layout_hints: dict[str, dict[str, float]] = Field(default_factory=dict)
    layout_viewport: dict[str, float] | None = None
    planspace_view: dict[str, dict[str, bool]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_project_model_preset(self) -> "Project":
        preset_id = self.model_preset_id.strip()
        if not preset_id:
            raise ValueError("model_preset_id is required")
        object.__setattr__(self, "model_preset_id", preset_id)
        return self

    def bind_model_catalog(self, store_root: Path) -> "Project":
        self._model_catalog_root = store_root
        normalize_model_preset_id(self.model_preset_id, store_root=store_root)
        return self

    @property
    def model_catalog_root(self) -> Path | None:
        return self._model_catalog_root

    @computed_field
    @property
    def provider(self) -> str:
        return provider_for_model_preset(
            self.model_preset_id, store_root=self._model_catalog_root
        )


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")
    _model_catalog_root: Path | None = PrivateAttr(default=None)

    id: str = Field(default_factory=_new_id)
    project_id: str
    kind: NodeKind = NodeKind.AGENT
    op_kind: str | None = None
    # Marks agent-node variants that need special launch handling (e.g.
    # ``"skill_edit"`` for the concierge that authors skill plugs). Kept
    # as ``str | None`` — see ``KNOWN_AGENT_OP_KINDS`` for the whitelist.
    agent_op_kind: str | None = None
    state: NodeState = NodeState.QUEUED
    parent_node_id: str | None = None
    planspace_id: str | None = None
    context_bundle_id: str | None = None
    context_bundle_path: str | None = None
    model_preset_id: str | None = None
    provider_session_id: str | None = None
    provider_turn_id: str | None = None
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
    # Virtual-only intent: skills to attach at promotion, copied into
    # ``settings_snapshot["extra_skills"]`` when the virtual → queued.
    pending_extra_skills: list[str] = Field(default_factory=list)
    resume_from_node_id: str | None = None
    verify_script_ref: str | None = None
    proposed_by: str | None = None
    obsolete_reason: str | None = None
    summary: str | None = None
    error: str | None = None
    usage: TokenUsage | None = None
    system_context_snapshot: str = ""
    settings_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=_now)
    started_at: float | None = None
    finished_at: float | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "Node":
        if self.kind is not NodeKind.AGENT:
            if self.agent_op_kind is not None:
                raise ValueError(
                    "agent_op_kind is only valid on agent nodes"
                )
            if self.pending_extra_skills:
                raise ValueError(
                    "pending_extra_skills is only valid on agent nodes"
                )
        if (
            self.agent_op_kind is not None
            and self.agent_op_kind not in KNOWN_AGENT_OP_KINDS
        ):
            raise ValueError(
                f"unknown agent_op_kind: {self.agent_op_kind!r}"
            )
        if self.kind is NodeKind.OP:
            if self.category is not None:
                raise ValueError("op nodes must not carry a category")
            if self.subtype is not None or self.brief is not None:
                raise ValueError("op nodes must not carry review fields")
            if self.verify_script_ref is not None:
                raise ValueError("op nodes must not carry verify_script_ref")
        elif self.kind is NodeKind.VERIFIER:
            if self.category is not Category.REVIEW:
                raise ValueError("verifier nodes must be category=review")
            if self.subtype is not ReviewSubtype.PROGRAMMATIC_REVIEW:
                raise ValueError(
                    "verifier nodes require subtype=programmatic_review"
                )
            if self.brief is None:
                raise ValueError("verifier nodes require a brief")
            if self.prompt or self.prompt_draft:
                raise ValueError("verifier nodes must not carry prompt text")
            if not self.verify_script_ref:
                raise ValueError("verifier nodes require verify_script_ref")
        else:
            if self.model_preset_id is None or not self.model_preset_id.strip():
                raise ValueError("agent nodes require model_preset_id")
            preset_id = self.model_preset_id.strip()
            object.__setattr__(self, "model_preset_id", preset_id)
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
                if self.subtype is ReviewSubtype.PROGRAMMATIC_REVIEW:
                    raise ValueError("programmatic_review requires kind=verifier")
                if self.brief is None:
                    raise ValueError("review agents require a brief")
            if self.verify_script_ref is not None:
                raise ValueError("agent nodes must not carry verify_script_ref")
        if self.state is NodeState.VIRTUAL:
            if self.started_at is not None or self.finished_at is not None:
                raise ValueError("virtual nodes must not carry started_at/finished_at")
        return self

    def bind_model_catalog(self, store_root: Path) -> "Node":
        self._model_catalog_root = store_root
        if self.model_preset_id is not None:
            normalize_model_preset_id(
                self.model_preset_id, store_root=store_root
            )
        return self

    @property
    def model_catalog_root(self) -> Path | None:
        return self._model_catalog_root

    @computed_field
    @property
    def provider(self) -> str | None:
        if self.model_preset_id is None:
            return None
        return provider_for_model_preset(
            self.model_preset_id, store_root=self._model_catalog_root
        )


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
