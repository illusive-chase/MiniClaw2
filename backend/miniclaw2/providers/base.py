"""Provider interface used by NodeRunner."""

from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from ..domain import GateSubtype, Node, Project
from ..events import (
    Activity,
    ErrorEvent,
    InteractionRequest,
    NodeUpdated,
    TextDelta,
    Thinking,
    Usage,
)

ProviderWireEvent = (
    TextDelta | Thinking | Activity | InteractionRequest | Usage | ErrorEvent | NodeUpdated
)


@dataclass(slots=True)
class GateRequest:
    subtype: GateSubtype
    tool_name: str
    tool_input: dict[str, Any] = field(default_factory=dict)
    suggestions: list[Any] = field(default_factory=list)
    provider_request_id: str | None = None
    response_hint: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentProviderEvent:
    kind: str
    event: ProviderWireEvent | None = None
    gate: GateRequest | None = None
    session_id: str | None = None
    turn_id: str | None = None
    error: str | None = None
    final_state: str | None = None


@dataclass(slots=True)
class AgentProviderContext:
    node: Node
    project: Project
    request_gate_handler: Callable[[GateRequest], Awaitable[dict[str, Any]]]
    system_context: str = ""
    launch_instructions: str = ""

    async def request_gate(self, gate: GateRequest) -> dict[str, Any]:
        return await self.request_gate_handler(gate)

    def turn_text(self) -> str:
        return compose_turn_text(self.node.prompt, self.launch_instructions)


class AgentProvider(Protocol):
    name: str

    async def run(self, context: AgentProviderContext) -> AsyncIterator[AgentProviderEvent]:
        ...

    async def interrupt(self) -> None:
        ...


def dump_model(value: BaseModel) -> dict[str, Any]:
    return value.model_dump()


def compose_turn_text(prompt: str, launch_instructions: str = "") -> str:
    if not launch_instructions:
        return prompt
    return f"{launch_instructions}\n\n---\n\n{prompt}"
