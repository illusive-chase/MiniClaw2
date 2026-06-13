"""NodeRunner — provider-neutral agent node state machine.

After the virtual-nodes redesign, the runner:

  - Materializes the active lane to ``.miniclaw2/graph/lanes/<lane>/``
    before launching the provider (read by the agent via native
    ``Read``).
  - Snapshots the lane's filesystem state for later walk-diff.
  - Runs the provider exactly as before.
  - At terminal, walk-diffs the lane against the pre-launch snapshot
    via :func:`reap.reap_lane`, validates preview writes, persists new
    or mutated virtuals atomically.
  - Writes the running node's own ``preview.json`` to the durable node
    store (or a framework stub if reap fails).

Inline gates (permission / ask_user / plan_approval) are unchanged. The
passive-gate / checkpoint-review path is gone — reviews are now agents
with ``category=review`` (a later slice).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from .contextspace import compose_context_bundle
from .domain import (
    GateKind,
    GateState,
    HumanGate,
    Node,
    NodeKind,
    NodeState,
    Project,
    TokenUsage,
)
from .events import (
    ErrorEvent,
    InteractionRequest,
    NodeStarted,
    NodeUpdated,
    TurnDone,
    Usage,
)
from .git_state import commit_all, git_head
from .language import language_launch_instruction, project_preferred_language
from .launch_prompt import anti_self_poisoning_block, build_category_launch_block
from .materialize import materialize_active_lane, snapshot_lane
from .preview import render_executed_preview
from .providers import AgentProvider, AgentProviderContext, AgentProviderEvent, GateRequest
from .providers.claude import ClaudeProvider
from .providers.codex import CodexProvider
from .reap import reap_lane
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
        self._lane_root: Path | None = None
        self._pre_snapshot: dict[str, str] = {}

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
        if self.node.kind is NodeKind.OP:
            await self._run_op()
        else:
            await self._run_agent()

    async def _run_agent(self) -> None:
        self.node.commit_before = git_head(self.project.root_path)
        try:
            context_bundle = self._snapshot_context_bundle()
            self._snapshot_launch_settings(context_bundle)
            self._materialize_lane()
            launch_instructions = _compose_launch_instructions(
                build_category_launch_block(self.node),
                context_bundle.turn_text,
                language_launch_instruction(project_preferred_language(self.project)),
                anti_self_poisoning_block(),
            )
            self._transition(NodeState.RUNNING, started=True)
            await self._emit(
                NodeStarted(
                    node_id=self.node.id,
                    parent_node_id=self.node.parent_node_id,
                    kind=self.node.kind.value,
                    prompt=self.node.prompt,
                )
            )
            await self._emit_node_updated()
            final_state: NodeState = NodeState.DONE
            error_msg: str | None = None

            try:
                try:
                    provider = _make_provider(self.node.provider or self.project.provider)
                    self._provider = provider
                    context = AgentProviderContext(
                        node=self.node,
                        project=self.project,
                        request_gate_handler=self._request_gate,
                        system_context=context_bundle.system_text,
                        launch_instructions=launch_instructions,
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
            finally:
                self._resolve_open_gates()
                if error_msg is not None:
                    self.node.error = error_msg
                self.node.commit_after = git_head(self.project.root_path)
                final_state = self._reap_and_finalize(final_state)
                self._transition(final_state, finished=True)
                await self._emit_node_updated()
                await self._emit(TurnDone())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("runner failed before start")
            error_msg = f"Unexpected runner error: {exc}"
            self.node.error = error_msg
            self.node.commit_after = git_head(self.project.root_path)
            self._write_stub_preview(NodeState.ERROR, reason=error_msg)
            self._transition(NodeState.ERROR, started=True, finished=True)
            await self._emit(
                NodeStarted(
                    node_id=self.node.id,
                    parent_node_id=self.node.parent_node_id,
                    kind=self.node.kind.value,
                    prompt=self.node.prompt,
                )
            )
            await self._emit(ErrorEvent(message=error_msg))
            await self._emit_node_updated()
            await self._emit(TurnDone())

    async def _run_op(self) -> None:
        """Run a non-provider op node (currently only ``commit``).

        Ops do not participate in the reap pipeline. The op runner
        writes its own ``preview.json`` to the durable store directly so
        the lane projection has a complete record.
        """
        self.node.commit_before = git_head(self.project.root_path)
        context_bundle = self._snapshot_context_bundle()
        self._snapshot_launch_settings(context_bundle)
        self._transition(NodeState.RUNNING, started=True)
        await self._emit(
            NodeStarted(
                node_id=self.node.id,
                parent_node_id=self.node.parent_node_id,
                kind=self.node.kind.value,
            )
        )
        await self._emit_node_updated()

        final_state = NodeState.DONE
        error_msg: str | None = None

        try:
            if self.node.op_kind == "commit":
                message = f"miniclaw:node:{self.node.parent_node_id or self.node.id}"
                new_head, err = commit_all(self.project.root_path, message)
                if err is not None:
                    error_msg = err
                    final_state = NodeState.ERROR
                elif new_head is None:
                    self.node.summary = "no changes to commit"
                    self.node.commit_after = self.node.commit_before
                else:
                    self.node.summary = f"commit {new_head[:8]}"
                    self.node.commit_after = new_head
            else:
                error_msg = f"unknown op_kind: {self.node.op_kind}"
                final_state = NodeState.ERROR
        except asyncio.CancelledError:
            final_state = NodeState.CANCELLED
        except Exception as exc:  # noqa: BLE001
            logger.exception("op runner failed")
            error_msg = f"Unexpected op runner error: {exc}"
            final_state = NodeState.ERROR
            await self._emit(ErrorEvent(message=error_msg))
        if error_msg is not None:
            self.node.error = error_msg
        if self.node.commit_after is None:
            self.node.commit_after = git_head(self.project.root_path)
        self._write_op_preview(final_state)
        self._transition(final_state, finished=True)
        await self._emit_node_updated()
        await self._emit(TurnDone())

    # ---- materialization + reap ----

    def _materialize_lane(self) -> None:
        lane_id = self.node.planspace_id or ""
        if not lane_id:
            self._lane_root = None
            self._pre_snapshot = {}
            return
        self._lane_root = materialize_active_lane(self.project, lane_id, self.store)
        self._pre_snapshot = snapshot_lane(self._lane_root)

    def _append_error(self, reason: str) -> None:
        self.node.error = (self.node.error + "\n" if self.node.error else "") + reason

    def _reap_and_finalize(self, provider_final_state: NodeState) -> NodeState:
        """Run reap (when applicable) and persist the node's own preview.

        Returns the effective terminal state. Cancelled / error runs
        skip reap entirely — virtual writes from failed sessions are
        discarded.
        """
        if provider_final_state is not NodeState.DONE or self._lane_root is None:
            self._write_stub_preview(provider_final_state)
            return provider_final_state
        try:
            result = reap_lane(
                self.project, self.node, self._lane_root, self._pre_snapshot, self.store
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("reap pipeline raised")
            reason = f"reap pipeline error: {exc}"
            self._append_error(reason)
            self._write_stub_preview(NodeState.ERROR, reason=reason)
            return NodeState.ERROR
        if result.fatal:
            reason = "; ".join(result.rejection_reasons)
            self._append_error(f"Reap fatal: {reason}")
            self._write_stub_preview(NodeState.ERROR, reason=reason)
            return NodeState.ERROR
        if not result.own_preview_ok:
            reason = "; ".join(result.rejection_reasons)
            self._append_error(f"Missing own preview: {reason}")
            self._write_stub_preview(NodeState.ERROR, reason=reason)
            return NodeState.ERROR
        # Accepted. Promote the agent's own preview into the durable store.
        own_path = self._lane_root / "nodes" / self.node.id / "preview.json"
        try:
            text = own_path.read_text(encoding="utf-8")
            self.store.write_node_preview(self.project.id, self.node.id, text)
        except OSError:
            logger.exception("failed to persist own preview to durable store")
        # Emit updates for new and mutated virtuals so the canvas refreshes.
        for virtual in result.new_virtuals + result.modified_virtuals:
            self.store.update_node(virtual)
        return provider_final_state

    def _persist_executed_preview(
        self,
        final_state: NodeState,
        *,
        motivation: str,
        summary: str,
        next_implications: str,
    ) -> None:
        original_state = self.node.state
        if final_state not in (NodeState.DONE, NodeState.ERROR, NodeState.CANCELLED):
            final_state = NodeState.ERROR
        self.node.state = final_state
        try:
            text = render_executed_preview(
                self.node,
                motivation=motivation,
                summary=summary,
                next_implications=next_implications,
            )
            self.store.write_node_preview(self.project.id, self.node.id, text)
        except Exception:  # noqa: BLE001
            logger.exception("failed to write preview")
        finally:
            self.node.state = original_state

    def _write_stub_preview(self, final_state: NodeState, *, reason: str = "") -> None:
        self._persist_executed_preview(
            final_state,
            motivation=self.node.prompt[:200] if self.node.prompt else "(no motivation recorded)",
            summary=reason or "(framework stub — agent did not write its own preview)",
            next_implications="(framework stub — agent did not record next implications)",
        )

    def _write_op_preview(self, final_state: NodeState) -> None:
        self._persist_executed_preview(
            final_state,
            motivation=f"Auto-commit op for parent {self.node.parent_node_id or 'unknown'}",
            summary=self.node.summary or self.node.error or "op completed",
            next_implications="(commit op — completes the lane's filesystem record)",
        )

    # ---- bundle + settings snapshots ----

    def _snapshot_context_bundle(self):
        bundle = compose_context_bundle(
            self.project,
            self.node,
            store_root=self.store.root,
        )
        self.node.context_bundle_id = bundle.bundle_id
        try:
            self.node.context_bundle_path = str(
                bundle.bundle_path.relative_to(bundle.context_root)
            )
        except ValueError:
            self.node.context_bundle_path = str(bundle.bundle_path)
        self.node.context_sources = [
            str(source.get("path") or "")
            for source in bundle.sources
            if source.get("path")
        ]
        self.node.system_context_snapshot = bundle.project_context
        self.store.update_node(self.node)
        return bundle

    def _snapshot_launch_settings(self, context_bundle: Any | None = None) -> None:
        snapshot: dict[str, Any] = dict(self.project.settings_override)
        snapshot["cwd"] = self.project.root_path
        snapshot["provider"] = self.node.provider or self.project.provider
        project_binding_id = (
            getattr(context_bundle, "project_binding_id", None)
            if context_bundle is not None
            else self.project.project_context_binding_id
        )
        active_planspace_id = (
            getattr(context_bundle, "active_planspace_id", None)
            if context_bundle is not None
            else None
        )
        if project_binding_id:
            snapshot["project_context_binding_id"] = project_binding_id
        if active_planspace_id:
            self.node.planspace_id = active_planspace_id
            snapshot["active_planspace_id"] = active_planspace_id
        if self.node.context_bundle_id:
            snapshot["context_bundle_id"] = self.node.context_bundle_id
        preferred_language = project_preferred_language(self.project)
        if preferred_language:
            snapshot["preferred_language"] = preferred_language
        if self.node.category is not None:
            snapshot["category"] = self.node.category.value
        self.node.settings_snapshot = snapshot

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
            if isinstance(ev.event, Usage):
                self._record_usage(ev.event)
            await self._emit(ev.event)
            if isinstance(ev.event, Usage):
                await self._emit_node_updated()
            return
        if ev.kind == "session" and ev.session_id:
            self.node.provider_session_id = ev.session_id
            self.node.sdk_session_id = ev.session_id
            self.store.update_node(self.node)
            await self._emit_node_updated()
            return
        if ev.kind == "turn" and ev.turn_id:
            self.node.provider_turn_id = ev.turn_id
            self.store.update_node(self.node)
            await self._emit_node_updated()
            return
        if ev.kind == "error" and ev.error:
            await self._emit(ErrorEvent(message=ev.error))
            return

    def _record_usage(self, usage: Usage) -> None:
        self.node.usage = TokenUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
            cumulative_output_tokens=usage.cumulative_output_tokens,
            cumulative_cache_creation_tokens=usage.cumulative_cache_creation_tokens,
        )
        self.store.update_node(self.node)

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

    async def _emit_node_updated(self) -> None:
        await self._emit(NodeUpdated(node=self.node.model_dump()))

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
        await self._emit_node_updated()
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
            await self._emit_node_updated()

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


def _compose_launch_instructions(*parts: str) -> str:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return "\n\n---\n\n".join(cleaned)


def _state_from_provider(value: str | None) -> NodeState | None:
    if value == "cancelled":
        return NodeState.CANCELLED
    if value == "error":
        return NodeState.ERROR
    if value == "done":
        return NodeState.DONE
    return None
