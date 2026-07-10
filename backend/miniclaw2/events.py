"""WebSocket protocol — Pydantic envelopes for client/server messages."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------- Server -> Client ----------

class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str
    node_id: str = ""
    seq: int = 0


class Thinking(BaseModel):
    type: Literal["thinking"] = "thinking"
    text: str
    node_id: str = ""
    seq: int = 0


class Activity(BaseModel):
    type: Literal["activity"] = "activity"
    kind: Literal["tool", "agent"]
    status: Literal["start", "finish", "failed", "progress"]
    id: str
    name: str
    summary: str = ""
    result: str | None = None
    result_kind: Literal["stdout", "diff", "text", "json"] | None = None
    node_id: str = ""
    seq: int = 0


class InteractionRequest(BaseModel):
    type: Literal["interaction_request"] = "interaction_request"
    id: str
    interaction_type: Literal[
        "permission",
        "ask_user",
        "human_review_prose",
    ]
    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    suggestions: list[Any] = Field(default_factory=list)
    response_hint: dict[str, Any] = Field(default_factory=dict)
    node_id: str = ""
    seq: int = 0


class Usage(BaseModel):
    type: Literal["usage"] = "usage"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cumulative_output_tokens: int | None = None
    cumulative_cache_creation_tokens: int | None = None
    final: bool = False
    node_id: str = ""
    seq: int = 0


class NodeStarted(BaseModel):
    type: Literal["node_started"] = "node_started"
    node_id: str
    parent_node_id: str | None = None
    kind: str = "agent"
    provider: str | None = None
    model_preset_id: str | None = None
    category: str | None = None
    subtype: str | None = None
    agent_op_kind: str | None = None
    prompt: str = ""
    seq: int = 0


class NodeUpdated(BaseModel):
    type: Literal["node_updated"] = "node_updated"
    node: dict[str, Any]
    node_id: str = ""
    seq: int = 0


class NodeRemoved(BaseModel):
    type: Literal["node_removed"] = "node_removed"
    id: str
    seq: int = 0


class TurnDone(BaseModel):
    type: Literal["turn_done"] = "turn_done"
    node_id: str = ""
    seq: int = 0


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str
    node_id: str = ""
    seq: int = 0


# ---------- Client -> Server ----------

class UserMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["user_message"]
    text: str
    resume_from_node_id: str | None = None
    extra_skills: list[str] | None = None
    agent_op_kind: str | None = None
    model_preset_id: str | None = None


class InteractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["interaction_response"]
    id: str
    node_id: str | None = None
    allow: bool = True
    message: str = ""
    updated_input: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    scope: str | None = None
    interrupt: bool = False
    permission_mode: str | None = None
    clear_context: bool = False


class Interrupt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["interrupt"]
    node_id: str


class ReplayRequest(BaseModel):
    type: Literal["replay_request"]
    node_id: str
    since_seq: int = 0
