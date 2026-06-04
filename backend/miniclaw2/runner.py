"""NodeRunner — provider-neutral agent node state machine."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from .artifacts import (
    load_node_artifact,
    summarize_node_artifact,
    validate_node_output_path,
)
from .contextspace import (
    apply_memory_delta_artifact,
    compose_context_bundle,
    memory_delta_launch_contract,
    memory_delta_output_relpath,
)
from .domain import (
    AcceptanceState,
    GateKind,
    GateState,
    GateSubtype,
    NodeOutputKind,
    HumanGate,
    Node,
    NodeKind,
    NodeState,
    Project,
    default_node_output_path,
    node_output_contract,
    TokenUsage,
    VerdictSource,
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
        if self.node.kind is NodeKind.OP:
            await self._run_op()
        elif self.node.kind is NodeKind.GATE:
            await self._run_passive_gate()
        else:
            await self._run_agent()

    async def _run_agent(self) -> None:
        self.node.commit_before = git_head(self.project.root_path)
        try:
            output_contract = self._snapshot_output_contract()
            context_bundle = self._snapshot_context_bundle()
            self._snapshot_launch_settings(context_bundle)
            launch_instructions = _compose_launch_instructions(
                context_bundle.turn_text,
                output_contract,
                memory_delta_launch_contract(self.project, self.node, context_bundle),
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
                self._finalize_output_artifact()
                if error_msg is not None:
                    self.node.error = error_msg
                self.node.commit_after = git_head(self.project.root_path)
                self._transition(final_state, finished=True)
                self._apply_memory_delta_artifact()
                await self._emit_node_updated()
                await self._emit(TurnDone())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("runner failed before start")
            error_msg = f"Unexpected runner error: {exc}"
            self.node.error = error_msg
            self.node.commit_after = git_head(self.project.root_path)
            self._transition(NodeState.ERROR, started=True, finished=True)
            self._apply_memory_delta_artifact()
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

    async def _run_passive_gate(self) -> None:
        """Run a passive gate node — no provider, straight to awaiting-review.

        The gate's ``contract`` is the brief the human reads (usually
        prepared by the previous agent step via ``output_kind=review_brief``).
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

        final_state: NodeState = NodeState.DONE
        error_msg: str | None = None

        try:
            try:
                await self._handle_checkpoint_review()
            except asyncio.CancelledError:
                final_state = NodeState.CANCELLED
            except Exception as exc:  # noqa: BLE001
                logger.exception("checkpoint review failed")
                error_msg = f"checkpoint review error: {exc}"
                final_state = NodeState.ERROR
                await self._emit(ErrorEvent(message=error_msg))
        finally:
            self._resolve_open_gates()
            if error_msg is not None:
                self.node.error = error_msg
            self.node.commit_after = git_head(self.project.root_path)
            self._transition(final_state, finished=True)
            self._apply_memory_delta_artifact()
            await self._emit_node_updated()
            await self._emit(TurnDone())

    async def _run_op(self) -> None:
        """Run a non-provider op node (currently only ``commit``)."""
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
        finally:
            if error_msg is not None:
                self.node.error = error_msg
            if self.node.commit_after is None:
                self.node.commit_after = git_head(self.project.root_path)
            self._transition(final_state, finished=True)
            self._apply_memory_delta_artifact()
            await self._emit_node_updated()
            await self._emit(TurnDone())

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
        # Backward compatibility: keep this field scoped to root CONTEXT.md.
        self.node.system_context_snapshot = bundle.project_context
        self.store.update_node(self.node)
        return bundle

    def _snapshot_launch_settings(self, context_bundle: Any | None = None) -> None:
        snapshot: dict[str, Any] = dict(self.project.settings_override)
        snapshot["cwd"] = self.project.root_path
        snapshot["provider"] = self.node.provider or self.project.provider
        snapshot["output_kind"] = self.node.output_kind.value
        if self.node.output_path:
            snapshot["output_path"] = self.node.output_path
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
        active_planspace_auto_update = (
            bool(getattr(context_bundle, "active_planspace_auto_update", False))
            if context_bundle is not None
            else False
        )
        if project_binding_id:
            snapshot["project_context_binding_id"] = project_binding_id
        if active_planspace_id:
            snapshot["active_planspace_id"] = active_planspace_id
        if active_planspace_id and active_planspace_auto_update:
            snapshot["memory_delta_output_path"] = memory_delta_output_relpath(self.node)
        if self.node.context_bundle_id:
            snapshot["context_bundle_id"] = self.node.context_bundle_id
        self.node.settings_snapshot = snapshot

    def _snapshot_output_contract(self) -> str:
        if self.node.kind is not NodeKind.AGENT:
            self.node.output_contract_snapshot = ""
            self.node.output_path = None
            return ""
        if self.node.output_kind is NodeOutputKind.FREEFORM:
            self.node.output_contract_snapshot = ""
            self.node.output_path = None
            self.store.update_node(self.node)
            return ""

        if self.node.output_path is None:
            self.node.output_path = default_node_output_path(self.node.id, self.node.output_kind)
        path_error = validate_node_output_path(self.node.output_path)
        if path_error:
            self.node.output_contract_snapshot = ""
            self.store.update_node(self.node)
            raise ValueError(path_error)

        contract = node_output_contract(self.node.output_kind, self.node.output_path)
        self.node.output_contract_snapshot = contract
        self.store.update_node(self.node)
        return contract

    def _finalize_output_artifact(self) -> None:
        if self.node.kind is not NodeKind.AGENT:
            return
        if self.node.output_kind is NodeOutputKind.FREEFORM:
            return

        artifact = load_node_artifact(self.project.root_path, self.node)
        summary = summarize_node_artifact(self.node, artifact)
        if summary:
            self.node.summary = summary
        self.store.update_node(self.node)

    def _apply_memory_delta_artifact(self) -> None:
        try:
            result = apply_memory_delta_artifact(
                self.project,
                self.node,
                store_root=self.store.root,
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to apply ContextSpace memory delta")
            return
        if result.get("planspace_id"):
            snapshot = dict(self.node.settings_snapshot)
            snapshot["memory_delta"] = result
            self.node.settings_snapshot = snapshot
            self.store.update_node(self.node)

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

    # ---- checkpoint gate flow ----

    async def _handle_checkpoint_review(self) -> None:
        """Block on a user response to a checkpoint contract.

        Loops on path-traversal / file-write errors so the user can fix
        the path and resubmit without restarting the node.
        """
        self._transition(NodeState.AWAITING_REVIEW)
        await self._emit_node_updated()

        gate_id = uuid4().hex[:12]
        gate = HumanGate(
            id=gate_id,
            node_id=self.node.id,
            kind=GateKind.CHECKPOINT,
            subtype=GateSubtype.CHECKPOINT_REVIEW,
            tool_name="checkpoint_review",
            tool_input={"contract": self.node.contract},
        )
        self.store.append_gate(self.project.id, gate, "created")
        self._gate_records[gate_id] = gate

        loop = asyncio.get_running_loop()
        last_error: str | None = None
        while True:
            future: asyncio.Future[dict[str, Any]] = loop.create_future()
            self._gates[gate_id] = future

            tool_input: dict[str, Any] = {"contract": self.node.contract}
            if last_error is not None:
                tool_input["last_error"] = last_error
            await self._emit(
                InteractionRequest(
                    id=gate_id,
                    interaction_type="checkpoint_review",
                    tool_name="checkpoint_review",
                    tool_input=tool_input,
                )
            )

            try:
                response = await future
            finally:
                self._gates.pop(gate_id, None)

            decision = response.get("decision")
            resp_payload = response.get("response") or {}
            if not isinstance(resp_payload, dict):
                resp_payload = {}

            if decision == "write-json":
                err = _write_review_json(
                    self.project.root_path,
                    resp_payload.get("path"),
                    resp_payload.get("payload"),
                )
                if err is not None:
                    last_error = err
                    await self._emit(ErrorEvent(message=err))
                    continue

            gate.state = GateState.RESOLVED
            gate.resolved_at = time.time()
            gate.response = response
            self.store.append_gate(self.project.id, gate, "resolved")
            self._gate_records.pop(gate_id, None)
            self.node.review_outcome = _review_outcome_from_payload(decision, resp_payload)
            await self._stamp_source_acceptance(resp_payload)
            return

    async def _stamp_source_acceptance(
        self,
        resp_payload: dict[str, Any],
    ) -> None:
        if self.node.kind is not NodeKind.GATE:
            return
        if not self.node.parent_node_id:
            return
        if self.node.review_outcome not in {"approved", "rejected"}:
            return
        source = self.store.load_node(self.project.id, self.node.parent_node_id)
        if source is None:
            return
        now = time.time()
        if self.node.review_outcome == "approved":
            source.acceptance_state = AcceptanceState.ACCEPTED
            source.accepted_at = now
            source.rejected_at = None
        else:
            source.acceptance_state = AcceptanceState.REJECTED
            source.rejected_at = now
            source.accepted_at = None
        source.verdict_source = VerdictSource.HUMAN
        source.verdict_thread_id = self.node.id
        path = resp_payload.get("path")
        if isinstance(path, str) and path:
            source.verdict_artifact_path = path
        self.store.update_node(source)
        await self._emit(NodeUpdated(node=source.model_dump()))

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


def _write_review_json(root: str, path_str: Any, payload: Any) -> str | None:
    """Write ``payload`` as JSON to ``root/path_str``. Returns an error string or None."""
    if not isinstance(path_str, str) or not path_str:
        return "write-json requires a 'path' string"
    if payload is None:
        return "write-json requires a 'payload'"
    rel = Path(path_str)
    if rel.is_absolute():
        return f"path must be project-relative: {path_str}"
    root_path = Path(root).resolve()
    target = (root_path / rel).resolve()
    try:
        target.relative_to(root_path)
    except ValueError:
        return f"path escapes project root: {path_str}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        return f"failed to write {path_str}: {exc}"
    return None


def _review_outcome_from_payload(decision: Any, payload: dict[str, Any]) -> str | None:
    """Derive scenario-branching outcome from a resolved checkpoint-review gate.

    A ``write-json`` response with ``approved: false`` in its payload is
    treated as ``"rejected"``; any other ``write-json`` (including missing
    ``approved`` or non-bool values) is treated as ``"approved"``. A
    ``no-op`` resolution carries no decision and returns ``None``.
    """
    if decision != "write-json":
        return None
    body = payload.get("payload")
    if isinstance(body, dict) and body.get("approved") is False:
        return "rejected"
    return "approved"


def _compose_launch_instructions(*parts: str) -> str:
    parts = [
        part.strip()
        for part in parts
        if part and part.strip()
    ]
    return "\n\n---\n\n".join(parts)


def _state_from_provider(value: str | None) -> NodeState | None:
    if value == "cancelled":
        return NodeState.CANCELLED
    if value == "error":
        return NodeState.ERROR
    if value == "done":
        return NodeState.DONE
    return None
