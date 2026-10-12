"""Observation hooks around inline human gates.

``NodeRunner._request_gate`` is the single place every provider's question or
approval passes through, so an observer registered there sees every gate open
and every way it closes (answered, node ended, cancelled). Observers are
additional answer channels such as the Feishu bridge; they must never be able
to affect the gate itself, which is why both hooks are synchronous and the
runner swallows anything they raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class GateNotice:
    """What an observer needs to present a gate away from the canvas."""

    gate_id: str
    project_id: str
    node_id: str
    subtype: str
    tool_name: str
    questions: list[dict[str, Any]] = field(default_factory=list)
    project_name: str = ""
    lane_title: str | None = None
    node_label: str = ""
    created_at: float = 0.0


@dataclass(frozen=True)
class GateOutcome:
    """How a gate closed.

    ``answered`` carries the channel that won (``source``, e.g. ``web`` or
    ``feishu``); ``ended`` covers node end, cancellation and timeouts.
    """

    kind: Literal["answered", "ended"]
    source: str | None = None
    via: str | None = None
    response: dict[str, Any] | None = None


class GateObserver(Protocol):
    def opened(self, notice: GateNotice) -> None: ...

    def closed(self, gate_id: str, outcome: GateOutcome) -> None: ...


class NullGateObserver:
    def opened(self, notice: GateNotice) -> None:
        return None

    def closed(self, gate_id: str, outcome: GateOutcome) -> None:
        return None


NULL_GATE_OBSERVER = NullGateObserver()
