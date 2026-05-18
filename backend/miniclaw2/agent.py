"""Thin CCAgent — wraps claude-agent-sdk and yields web-protocol events."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
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

from .events import (
    Activity,
    ErrorEvent,
    InteractionRequest,
    TextDelta,
    TurnDone,
    Usage,
)

logger = logging.getLogger(__name__)

Event = TextDelta | Activity | InteractionRequest | Usage | TurnDone | ErrorEvent


def _default_model() -> str:
    return (
        os.environ.get("ANTHROPIC_MODEL")
        or os.environ.get("MINICLAW_ANTHROPIC_MODEL")
        or "claude-sonnet-4-6"
    )


class CCAgent:
    """One agent per session. Stateful via SDK session_id resume."""

    def __init__(self, cwd: str | None = None, model: str | None = None) -> None:
        self._cwd = cwd or os.getcwd()
        self._model = model or _default_model()
        self._sdk_session_id: str | None = None

        # Pending interaction requests keyed by id — resolved by Session
        # from the WebSocket layer.
        self._pending: dict[str, asyncio.Future] = {}

    # ---- interaction plumbing (called from Session/WS layer) ----

    def resolve_interaction(
        self,
        request_id: str,
        *,
        allow: bool,
        message: str = "",
        updated_input: dict[str, Any] | None = None,
        permission_mode: str | None = None,
        clear_context: bool = False,
    ) -> bool:
        fut = self._pending.get(request_id)
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

    # ---- turn loop ----

    async def run_turn(self, text: str) -> AsyncIterator[Event]:
        options = self._build_options()
        pending_tools: dict[str, Activity] = {}

        # Inter-task queue: SDK reader pushes ("sdk", msg) and
        # can_use_tool pushes ("interaction", InteractionRequest).
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        async def reader() -> None:
            try:
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(text)
                    async for message in client.receive_response():
                        await queue.put(("sdk", message))
                    await queue.put(("done", None))
            except Exception as exc:  # noqa: BLE001
                await queue.put(("error", exc))

        # can_use_tool needs the queue to publish interaction requests.
        self._tool_queue = queue
        task = asyncio.create_task(reader())

        try:
            while True:
                tag, payload = await queue.get()

                if tag == "done":
                    break

                if tag == "error":
                    exc = payload
                    if isinstance(exc, (CLINotFoundError, CLIConnectionError)):
                        yield ErrorEvent(message=f"Claude Agent SDK error: {exc}")
                    else:
                        logger.exception("SDK reader failed", exc_info=exc)
                        yield ErrorEvent(message=f"Unexpected error: {exc}")
                    break

                if tag == "interaction":
                    yield payload
                    continue

                # tag == "sdk"
                async for ev in self._translate(payload, pending_tools):
                    yield ev

        finally:
            self._tool_queue = None  # type: ignore[assignment]
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            # Reject any unresolved interactions so we don't leak futures.
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_result({"allow": False, "message": "turn ended"})
            self._pending.clear()

        yield TurnDone()

    async def _translate(
        self, message: Any, pending_tools: dict[str, Activity]
    ) -> AsyncIterator[Event]:
        if isinstance(message, SystemMessage):
            if message.subtype == "init":
                sid = message.data.get("session_id")
                if sid:
                    self._sdk_session_id = sid
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
                    pass
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

    # ---- can_use_tool callback ----

    def _make_can_use_tool(self):
        async def callback(
            tool_name: str,
            tool_input: dict[str, Any],
            context: ToolPermissionContext,
        ) -> PermissionResultAllow | PermissionResultDeny:
            queue = getattr(self, "_tool_queue", None)
            if queue is None:
                return PermissionResultAllow()

            if tool_name == "AskUserQuestion":
                itype = "ask_user"
            elif tool_name == "ExitPlanMode":
                itype = "plan_approval"
            else:
                itype = "permission"

            req_id = str(uuid4())
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            self._pending[req_id] = future

            request = InteractionRequest(
                id=req_id,
                interaction_type=itype,  # type: ignore[arg-type]
                tool_name=tool_name,
                tool_input=tool_input,
                suggestions=[
                    s.model_dump() if hasattr(s, "model_dump") else s
                    for s in (context.suggestions or [])
                ],
            )
            await queue.put(("interaction", request))

            try:
                response = await future
            finally:
                self._pending.pop(req_id, None)

            allow = response["allow"]
            message = response.get("message") or ""
            updated_input = response.get("updated_input")
            permission_mode = response.get("permission_mode")
            clear_context = response.get("clear_context", False)

            if itype == "plan_approval":
                if clear_context:
                    # Minimal example: deny+interrupt so the SDK turn unwinds.
                    # A future iteration could clear history and re-issue the plan.
                    return PermissionResultDeny(
                        message=message or "Plan execution requested.",
                        interrupt=True,
                    )
                if allow and permission_mode:
                    return PermissionResultAllow(
                        updated_permissions=[
                            PermissionUpdate(
                                type="setMode",
                                mode=permission_mode,
                                destination="session",
                            )
                        ],
                    )
                if not allow:
                    return PermissionResultDeny(message=message)

            if allow:
                return PermissionResultAllow(updated_input=updated_input)
            return PermissionResultDeny(message=message)

        return callback

    # ---- options ----

    def _build_options(self) -> ClaudeAgentOptions:
        opts: dict[str, Any] = {
            "system_prompt": {"type": "preset", "preset": "claude_code"},
            "model": self._model,
            "permission_mode": "default",
            "cwd": self._cwd,
            "can_use_tool": self._make_can_use_tool(),
        }
        if self._sdk_session_id:
            opts["resume"] = self._sdk_session_id
        return ClaudeAgentOptions(**opts)


def _truncate(s: str, limit: int = 200) -> str:
    return s if len(s) <= limit else s[:limit] + "…"
