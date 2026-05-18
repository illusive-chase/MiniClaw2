"""WebSocket protocol — Pydantic envelopes for client/server messages."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------- Server -> Client ----------

class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class Activity(BaseModel):
    type: Literal["activity"] = "activity"
    kind: Literal["tool", "agent"]
    status: Literal["start", "finish", "failed", "progress"]
    id: str
    name: str
    summary: str = ""


class InteractionRequest(BaseModel):
    type: Literal["interaction_request"] = "interaction_request"
    id: str
    interaction_type: Literal["permission", "ask_user", "plan_approval"]
    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    suggestions: list[Any] = Field(default_factory=list)


class Usage(BaseModel):
    type: Literal["usage"] = "usage"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    final: bool = False


class TurnDone(BaseModel):
    type: Literal["turn_done"] = "turn_done"


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


# ---------- Client -> Server ----------

class UserMessage(BaseModel):
    type: Literal["user_message"]
    text: str


class InteractionResponse(BaseModel):
    type: Literal["interaction_response"]
    id: str
    allow: bool = True
    message: str = ""
    updated_input: dict[str, Any] | None = None
    permission_mode: str | None = None
    clear_context: bool = False


class Interrupt(BaseModel):
    type: Literal["interrupt"]
