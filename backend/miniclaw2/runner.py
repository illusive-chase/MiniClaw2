"""NodeRunner — provider-neutral agent node state machine."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from .domain import GateKind, GateState, HumanGate, Node, NodeState, Project
from .events import (
    ErrorEvent,
    InteractionRequest,
    TurnDone,
)
from .providers import AgentProvider, AgentProviderContext, AgentProviderEvent, GateRequest
from .providers.claude import ClaudeProvider
from .providers.codex import CodexProvider
from .store import Store

logger = logging.getLogger(__name__)


class NodeRunner:
    """Drives one agent node from start to terminal state."""

    def __init__(
        self,
        node: Node,
        project: Project,
        store: Store,
        on_event: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.node = node
        self.project = project
        self.store = store
        self.on_event = on_event

        self._seq = 0
        self._gates: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._gate_records: dict[str, HumanGate] = {}
        self._provider: AgentProvider | None = None

    # ---- public surface (used by the WS layer via ProjectRuntime) ----

    def resolve_gate(
        self,
        gate_id: str,
        *,
        allow: bool,
        decision: str | dict[str, Any] | None = None,
        message: str = "",
        updated_input: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        scope: str | None = None,
        interrupt: bool = False,
        permission_mode: str | None = None,
        clear_context: bool = False,
    ) -> bool:
        fut = self._gates.get(gate_id)
        if fut is None or fut.done():
            return False
        fut.get_loop().call_soon_threadsafe(
            fut.set_result,
            {
                "allow": allow,
                "decision": decision,
                "message": message,
                "updated_input": updated_input,
                "response": response,
                "scope": scope,
                "interrupt": interrupt,
                "permission_mode": permission_mode,
                "clear_context": clear_context,
            },
        )
        return True

    async def interrupt(self) -> None:
        if self._provider is not None:
            await self._provider.interrupt()

    # ---- main entry point ----

    async def run(self) -> None:
        self._transition(NodeState.RUNNING, started=True)
        final_state: NodeState = NodeState.DONE
        error_msg: str | None = None

        try:
            provider = _make_provider(self.node.provider or self.project.provider)
            self._provider = provider
            context = AgentProviderContext(
                node=self.node,
                project=self.project,
                request_gate_handler=self._request_gate,
            )
            async for ev in provider.run(context):
                await self._handle_provider_event(ev)
                if ev.kind == "done":
                    final_state = _state_from_provider(ev.final_state) or NodeState.DONE
                    break
                if ev.kind == "error":
                    error_msg = ev.error or "provider error"
                    final_state = NodeState.ERROR
                    break

        except asyncio.CancelledError:
            final_state = NodeState.CANCELLED
            await self.interrupt()
        except Exception as exc:  # noqa: BLE001
            logger.exception("runner failed")
            error_msg = f"Unexpected runner error: {exc}"
            final_state = NodeState.ERROR
            await self._emit(ErrorEvent(message=error_msg))
        finally:
            self._provider = None
            self._resolve_open_gates()
            if error_msg is not None:
                self.node.error = error_msg
            self._transition(final_state, finished=True)
            await self._emit(TurnDone())

    # ---- state transitions ----

    def _transition(
        self,
        state: NodeState,
        *,
        started: bool = False,
        finished: bool = False,
    ) -> None:
        self.node.state = state
        now = time.time()
        if started and self.node.started_at is None:
            self.node.started_at = now
        if finished:
            self.node.finished_at = now
        self.store.update_node(self.node)

    # ---- provider event handling ----

    async def _handle_provider_event(self, ev: AgentProviderEvent) -> None:
        if ev.kind == "event" and ev.event is not None:
            await self._emit(ev.event)
            return
        if ev.kind == "session" and ev.session_id:
            self.node.provider_session_id = ev.session_id
            self.node.sdk_session_id = ev.session_id
            self.store.update_node(self.node)
            return
        if ev.kind == "turn" and ev.turn_id:
            self.node.provider_turn_id = ev.turn_id
            self.store.update_node(self.node)
            return
        if ev.kind == "error" and ev.error:
            await self._emit(ErrorEvent(message=ev.error))
            return

    # ---- emit (persist + push) ----

    async def _emit(self, ev: BaseModel) -> None:
        self._seq += 1
        if hasattr(ev, "seq"):
            ev.seq = self._seq  # type: ignore[attr-defined]
        data = ev.model_dump()
        try:
            self.store.append_event(self.project.id, self.node.id, self._seq, data)
        except Exception:  # noqa: BLE001
            logger.exception("failed to persist event")
        try:
            await self.on_event(data)
        except Exception:  # noqa: BLE001
            logger.exception("on_event handler failed")

    # ---- inline gate flow ----

    async def _request_gate(self, request: GateRequest) -> dict[str, Any]:
        gate = HumanGate(
            id=uuid4().hex[:12],
            node_id=self.node.id,
            kind=GateKind.INLINE,
            subtype=request.subtype,
            tool_name=request.tool_name,
            tool_input=request.tool_input,
            suggestions=request.suggestions,
        )
        self.store.append_gate(self.project.id, gate, "created")
        self._gate_records[gate.id] = gate

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._gates[gate.id] = future

        self._transition(NodeState.WAITING)
        await self._emit(
            InteractionRequest(
                id=gate.id,
                interaction_type=request.subtype.value,  # type: ignore[arg-type]
                tool_name=request.tool_name,
                tool_input=request.tool_input,
                suggestions=request.suggestions,
                response_hint=request.response_hint,
            )
        )

        try:
            response = await future
        finally:
            self._gates.pop(gate.id, None)
            if gate.state is GateState.PENDING:
                gate.state = GateState.RESOLVED
                gate.resolved_at = time.time()
            self._transition(NodeState.RUNNING)

        gate.response = response
        self.store.append_gate(self.project.id, gate, "resolved")
        self._gate_records.pop(gate.id, None)
        return response

    def _resolve_open_gates(self) -> None:
        for gate_id, fut in list(self._gates.items()):
            if not fut.done():
                fut.set_result({"allow": False, "message": "node ended"})
                record = self._gate_records.get(gate_id)
                if record is not None and record.state is GateState.PENDING:
                    record.state = GateState.RESOLVED
                    record.resolved_at = time.time()
                    record.response = {"allow": False, "message": "node ended"}
                    self.store.append_gate(self.project.id, record, "resolved")
        self._gates.clear()
        self._gate_records.clear()


def _make_provider(provider: str) -> AgentProvider:
    normalized = (provider or "claude").lower()
    if normalized == "codex":
        return CodexProvider()
    if normalized == "claude":
        return ClaudeProvider()
    raise ValueError(f"unknown provider: {provider}")


def _state_from_provider(value: str | None) -> NodeState | None:
    if value == "cancelled":
        return NodeState.CANCELLED
    if value == "error":
        return NodeState.ERROR
    if value == "done":
        return NodeState.DONE
    return None
