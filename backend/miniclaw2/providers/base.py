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


class GateTimeoutError(RuntimeError):
    """A supervised gate expired before a human response arrived."""


@dataclass(slots=True)
class GateRequest:
    subtype: GateSubtype
    tool_name: str
    tool_input: dict[str, Any] = field(default_factory=dict)
    suggestions: list[Any] = field(default_factory=list)
    provider_request_id: str | None = None
    response_hint: dict[str, Any] = field(default_factory=dict)
    # When set, the runner supervises the gate: if no response arrives within
    # this many seconds it interrupts the session and raises GateTimeoutError.
    # Providers whose gate transport has its own hard deadline (the Claude
    # PreToolUse hook bridge) must set this below that deadline; gates on
    # deadline-free transports leave it None and wait indefinitely.
    timeout_seconds: float | None = None


@dataclass(slots=True)
class AgentProviderEvent:
    """Event emitted by an agent provider.

    Provider streams must terminate explicitly: before ``run()`` exhausts it
    must yield either ``kind="done"`` (optionally with ``final_state`` set to
    ``"done"`` or ``"cancelled"``) or ``kind="error"``. Consumers should treat
    bare generator exhaustion as a provider failure.
    """

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
    minimal_mode: bool = False
    tool_allowlist: list[str] | None = None

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
