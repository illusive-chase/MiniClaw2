"""Provider interface used by NodeRunner."""

from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..domain import GateSubtype, Node, Project, RemoteAccessConfig, ReviewTarget
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
    report: ReviewReport | None = None
    settings: dict[str, Any] | None = None


@dataclass(slots=True)
class ReviewSpec:
    target: ReviewTarget
    focus: str | None = None
    # Remote projects have no local .git directory. Their runner supplies the
    # exact first snapshot so providers review the same bytes used for stale
    # detection instead of inspecting the disposable projection as authority.
    patch: str | None = None


@dataclass(slots=True)
class ReviewFinding:
    title: str
    body: str
    file: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    priority: str | None = None
    confidence: float | None = None


@dataclass(slots=True)
class ReviewReport:
    raw_markdown: str
    findings: list[ReviewFinding] | None = None
    verdict: str | None = None
    explanation: str | None = None


@dataclass(slots=True)
class AgentProviderContext:
    node: Node
    project: Project
    request_gate_handler: Callable[[GateRequest], Awaitable[dict[str, Any]]]
    system_context: str = ""
    launch_instructions: str = ""
    minimal_mode: bool = False
    tool_allowlist: list[str] | None = None
    store_root: Path | None = None
    skill_materialization: Any | None = None
    remote_access: RemoteAccessConfig | None = None
    remote_environment_id: str | None = None
    graph_tools: Any | None = None

    async def request_gate(self, gate: GateRequest) -> dict[str, Any]:
        return await self.request_gate_handler(gate)

    def turn_text(self) -> str:
        return self.node.prompt

    def system_prompt(self, *, include_context: bool = True) -> str:
        return compose_system_prompt(
            self.launch_instructions,
            self.system_context if include_context else "",
        )


class AgentProvider(Protocol):
    name: str

    async def run(self, context: AgentProviderContext) -> AsyncIterator[AgentProviderEvent]:
        ...

    async def run_review(
        self, context: AgentProviderContext, spec: ReviewSpec
    ) -> AsyncIterator[AgentProviderEvent]:
        ...

    async def interrupt(self) -> None:
        ...


def compose_system_prompt(
    launch_instructions: str,
    system_context: str,
) -> str:
    """Place framework node instructions before project/context guidance."""
    parts = [
        part.strip()
        for part in (launch_instructions, system_context)
        if part and part.strip()
    ]
    return "\n\n---\n\n".join(parts)
