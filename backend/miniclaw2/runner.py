"""NodeRunner — owns a ClaudeSDKClient for a single node's lifetime.

One node = one Claude session (DESIGN §2.1). The runner:

- transitions the node through ``queued → running [↔ waiting] → done|error|cancelled``,
- persists every event to ``events.jsonl`` before pushing to the WS callback,
- creates a :class:`HumanGate` record for each inline interaction and
  resolves it via :meth:`resolve_gate` from the WS layer.

Resume semantics: if ``node.sdk_session_id`` is set, the SDK client is
opened with ``resume=<sid>``; otherwise a fresh session is started and
the resulting session-id is captured back onto the node.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import uuid4

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    CLIConnectionError,
    CLINotFoundError,
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionUpdate,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TextBlock,
    ThinkingBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage as SDKUserMessage,
)
from pydantic import BaseModel

from .domain import GateKind, GateState, GateSubtype, HumanGate, Node, NodeState, Project
from .events import (
    Activity,
    ErrorEvent,
    InteractionRequest,
    TextDelta,
    Thinking,
    TurnDone,
    Usage,
)
from .store import Store

logger = logging.getLogger(__name__)

Event = TextDelta | Thinking | Activity | InteractionRequest | Usage | TurnDone | ErrorEvent


def _default_model() -> str:
    return (
        os.environ.get("ANTHROPIC_MODEL")
        or os.environ.get("MINICLAW_ANTHROPIC_MODEL")
        or "claude-sonnet-4-6"
    )


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
        self._sdk_queue: asyncio.Queue[tuple[str, Any]] | None = None

    # ---- public surface (used by the WS layer via ProjectRuntime) ----

    def resolve_gate(
        self,
        gate_id: str,
        *,
        allow: bool,
        message: str = "",
        updated_input: dict[str, Any] | None = None,
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
                "message": message,
                "updated_input": updated_input,
                "permission_mode": permission_mode,
                "clear_context": clear_context,
            },
        )
        return True

    # ---- main entry point ----

    async def run(self) -> None:
        self._transition(NodeState.RUNNING, started=True)

        options = self._build_options()
        pending_tools: dict[str, Activity] = {}
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self._sdk_queue = queue

        async def reader() -> None:
            try:
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(self.node.prompt)
                    async for message in client.receive_response():
                        await queue.put(("sdk", message))
                    await queue.put(("done", None))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                await queue.put(("error", exc))

        reader_task = asyncio.create_task(reader())
        final_state: NodeState = NodeState.DONE
        error_msg: str | None = None

        try:
            while True:
                tag, payload = await queue.get()

                if tag == "done":
                    break

                if tag == "error":
                    exc = payload
                    if isinstance(exc, (CLINotFoundError, CLIConnectionError)):
                        error_msg = f"Claude Agent SDK error: {exc}"
                    else:
                        logger.exception("SDK reader failed", exc_info=exc)
                        error_msg = f"Unexpected error: {exc}"
                    await self._emit(ErrorEvent(message=error_msg))
                    final_state = NodeState.ERROR
                    break

                if tag == "interaction":
                    await self._emit(payload)
                    continue

                # tag == "sdk"
                async for ev in self._translate(payload, pending_tools):
                    await self._emit(ev)

        except asyncio.CancelledError:
            final_state = NodeState.CANCELLED
            # Absorb the cancellation; we still want to persist final
            # state and emit TurnDone. The frontend's `streaming` flag
            # depends on TurnDone, and the on-disk node state must
            # reflect the cancel.
        finally:
            self._sdk_queue = None
            if not reader_task.done():
                reader_task.cancel()
                try:
                    await reader_task
                except (asyncio.CancelledError, Exception):
                    pass
            for gid, fut in list(self._gates.items()):
                if not fut.done():
                    fut.set_result({"allow": False, "message": "node ended"})
                    record = self._gate_records.get(gid)
                    if record is not None and record.state is GateState.PENDING:
                        record.state = GateState.RESOLVED
                        record.resolved_at = time.time()
                        record.response = {"allow": False, "message": "node ended"}
                        self.store.append_gate(self.project.id, record, "resolved")
            self._gates.clear()
            self._gate_records.clear()

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

    # ---- SDK -> Event translation ----

    async def _translate(
        self, message: Any, pending_tools: dict[str, Activity]
    ) -> AsyncIterator[Event]:
        if isinstance(message, SystemMessage):
            if message.subtype == "init":
                sid = message.data.get("session_id")
                if sid:
                    self.node.sdk_session_id = sid
                    self.store.update_node(self.node)
            return

        if isinstance(message, TaskStartedMessage):
            yield Activity(
                kind="agent", status="start",
                id=message.task_id, name=message.task_type or "agent",
                summary=message.description or "",
            )
            return

        if isinstance(message, TaskProgressMessage):
            yield Activity(
                kind="agent", status="progress",
                id=message.task_id, name=message.last_tool_name or "",
                summary=message.description or "",
            )
            return

        if isinstance(message, TaskNotificationMessage):
            yield Activity(
                kind="agent",
                status="finish" if message.status == "completed" else "failed",
                id=message.task_id, name="", summary=message.summary or "",
            )
            return

        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    t = block.text if block.text.endswith("\n") else block.text + "\n"
                    yield TextDelta(text=t)
                elif isinstance(block, ToolUseBlock):
                    ev = Activity(
                        kind="tool", status="start",
                        id=block.id, name=block.name,
                        summary=_truncate(str(block.input)),
                    )
                    pending_tools[block.id] = ev
                    yield ev
                elif isinstance(block, ThinkingBlock):
                    yield Thinking(text=block.thinking)
            return

        if isinstance(message, SDKUserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    pending = pending_tools.pop(block.tool_use_id, None)
                    if pending is not None:
                        pending.status = "failed" if block.is_error else "finish"
                        yield pending
            return

        if isinstance(message, ResultMessage):
            u = message.usage or {}
            yield Usage(
                input_tokens=u.get("input_tokens", 0),
                output_tokens=u.get("output_tokens", 0),
                cache_read_tokens=u.get("cache_read_input_tokens", 0),
                cache_creation_tokens=u.get("cache_creation_input_tokens", 0),
                final=True,
            )
            return

    # ---- can_use_tool — inline gate flow ----

    def _make_can_use_tool(self) -> Any:
        async def callback(
            tool_name: str,
            tool_input: dict[str, Any],
            context: ToolPermissionContext,
        ) -> PermissionResultAllow | PermissionResultDeny:
            queue = self._sdk_queue
            if queue is None:
                return PermissionResultAllow()

            if tool_name == "AskUserQuestion":
                subtype = GateSubtype.ASK_USER
                itype = "ask_user"
            elif tool_name == "ExitPlanMode":
                subtype = GateSubtype.PLAN_APPROVAL
                itype = "plan_approval"
            else:
                subtype = GateSubtype.PERMISSION
                itype = "permission"

            suggestions = [
                s.model_dump() if hasattr(s, "model_dump") else s
                for s in (context.suggestions or [])
            ]
            gate = HumanGate(
                id=uuid4().hex[:12],
                node_id=self.node.id,
                kind=GateKind.INLINE,
                subtype=subtype,
                tool_name=tool_name,
                tool_input=tool_input,
                suggestions=suggestions,
            )
            self.store.append_gate(self.project.id, gate, "created")
            self._gate_records[gate.id] = gate

            loop = asyncio.get_running_loop()
            future: asyncio.Future[dict[str, Any]] = loop.create_future()
            self._gates[gate.id] = future

            self._transition(NodeState.WAITING)
            request = InteractionRequest(
                id=gate.id,
                interaction_type=itype,  # type: ignore[arg-type]
                tool_name=tool_name,
                tool_input=tool_input,
                suggestions=suggestions,
            )
            await queue.put(("interaction", request))

            try:
                response = await future
            finally:
                self._gates.pop(gate.id, None)
                if gate.state is GateState.PENDING:
                    gate.state = GateState.RESOLVED
                    gate.resolved_at = time.time()

            self._transition(NodeState.RUNNING)

            allow = response["allow"]
            message = response.get("message") or ""
            updated_input = response.get("updated_input")
            permission_mode = response.get("permission_mode")
            gate.response = {
                "allow": allow,
                "message": message,
                "updated_input": updated_input,
                "permission_mode": permission_mode,
            }
            self.store.append_gate(self.project.id, gate, "resolved")
            self._gate_records.pop(gate.id, None)

            if itype == "plan_approval":
                # Plan-mode happy-path fix: approve flips the SDK into
                # ``acceptEdits`` (or whichever mode the user picked) and
                # the turn continues. ``clear_context`` is currently a
                # no-op; an "Approve in fresh context" flow can be added
                # later as an explicit affordance.
                if not allow:
                    return PermissionResultDeny(message=message or "Plan rejected")
                mode = permission_mode or "acceptEdits"
                return PermissionResultAllow(
                    updated_permissions=[
                        PermissionUpdate(
                            type="setMode",
                            mode=mode,
                            destination="session",
                        )
                    ],
                )

            if allow:
                return PermissionResultAllow(updated_input=updated_input)
            return PermissionResultDeny(message=message)

        return callback

    # ---- SDK options ----

    def _build_options(self) -> ClaudeAgentOptions:
        model = self.project.settings_override.get("model") or _default_model()
        opts: dict[str, Any] = {
            "system_prompt": {"type": "preset", "preset": "claude_code"},
            "model": model,
            "permission_mode": "default",
            "cwd": self.project.root_path,
            "can_use_tool": self._make_can_use_tool(),
        }
        if self.node.sdk_session_id:
            opts["resume"] = self.node.sdk_session_id
        return ClaudeAgentOptions(**opts)


def _truncate(s: str, limit: int = 200) -> str:
    return s if len(s) <= limit else s[:limit] + "…"
