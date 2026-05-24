"""Claude Agent SDK provider adapter."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

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

from ..domain import GateSubtype
from ..events import Activity, TextDelta, Thinking, Usage
from .base import AgentProviderContext, AgentProviderEvent, GateRequest


class ClaudeProvider:
    name = "claude"

    async def run(self, context: AgentProviderContext) -> AsyncIterator[AgentProviderEvent]:
        pending_tools: dict[str, Activity] = {}
        try:
            async with ClaudeSDKClient(options=self._build_options(context)) as client:
                await client.query(context.node.prompt)
                async for message in client.receive_response():
                    async for ev in self._translate(message, pending_tools, context):
                        yield ev
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, (CLINotFoundError, CLIConnectionError)):
                message = f"Claude Agent SDK error: {exc}"
            else:
                message = f"Unexpected Claude provider error: {exc}"
            yield AgentProviderEvent(kind="error", error=message)

    async def interrupt(self) -> None:
        # The Claude SDK is interrupted by cancelling the provider task.
        return None

    async def _translate(
        self,
        message: Any,
        pending_tools: dict[str, Activity],
        context: AgentProviderContext,
    ) -> AsyncIterator[AgentProviderEvent]:
        if isinstance(message, SystemMessage):
            if message.subtype == "init":
                sid = message.data.get("session_id")
                if sid:
                    yield AgentProviderEvent(kind="session", session_id=sid)
            return

        if isinstance(message, TaskStartedMessage):
            yield AgentProviderEvent(
                kind="event",
                event=Activity(
                    kind="agent",
                    status="start",
                    id=message.task_id,
                    name=message.task_type or "agent",
                    summary=message.description or "",
                ),
            )
            return

        if isinstance(message, TaskProgressMessage):
            yield AgentProviderEvent(
                kind="event",
                event=Activity(
                    kind="agent",
                    status="progress",
                    id=message.task_id,
                    name=message.last_tool_name or "",
                    summary=message.description or "",
                ),
            )
            return

        if isinstance(message, TaskNotificationMessage):
            yield AgentProviderEvent(
                kind="event",
                event=Activity(
                    kind="agent",
                    status="finish" if message.status == "completed" else "failed",
                    id=message.task_id,
                    name="",
                    summary=message.summary or "",
                ),
            )
            return

        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text = block.text if block.text.endswith("\n") else block.text + "\n"
                    yield AgentProviderEvent(kind="event", event=TextDelta(text=text))
                elif isinstance(block, ToolUseBlock):
                    ev = Activity(
                        kind="tool",
                        status="start",
                        id=block.id,
                        name=block.name,
                        summary=_truncate(str(block.input)),
                    )
                    pending_tools[block.id] = ev
                    yield AgentProviderEvent(kind="event", event=ev)
                elif isinstance(block, ThinkingBlock):
                    yield AgentProviderEvent(kind="event", event=Thinking(text=block.thinking))
            return

        if isinstance(message, SDKUserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    pending = pending_tools.pop(block.tool_use_id, None)
                    if pending is not None:
                        pending.status = "failed" if block.is_error else "finish"
                        yield AgentProviderEvent(kind="event", event=pending)
            return

        if isinstance(message, ResultMessage):
            usage = message.usage or {}
            yield AgentProviderEvent(
                kind="event",
                event=Usage(
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                    cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
                    final=True,
                ),
            )
            return

    def _make_can_use_tool(self, context: AgentProviderContext) -> Any:
        async def callback(
            tool_name: str,
            tool_input: dict[str, Any],
            permission_context: ToolPermissionContext,
        ) -> PermissionResultAllow | PermissionResultDeny:
            if tool_name == "AskUserQuestion":
                subtype = GateSubtype.ASK_USER
            elif tool_name == "ExitPlanMode":
                subtype = GateSubtype.PLAN_APPROVAL
            else:
                subtype = GateSubtype.PERMISSION

            suggestions = [
                s.model_dump() if hasattr(s, "model_dump") else s
                for s in (permission_context.suggestions or [])
            ]
            response = await context.request_gate(
                GateRequest(
                    subtype=subtype,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    suggestions=suggestions,
                )
            )

            allow = response["allow"]
            message = response.get("message") or ""
            updated_input = response.get("updated_input")
            permission_mode = response.get("permission_mode")

            if subtype is GateSubtype.PLAN_APPROVAL:
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

    def _build_options(self, context: AgentProviderContext) -> ClaudeAgentOptions:
        model = (
            context.project.settings_override.get("model")
            or os.environ.get("ANTHROPIC_MODEL")
            or os.environ.get("MINICLAW_ANTHROPIC_MODEL")
            or "claude-sonnet-4-6"
        )
        opts: dict[str, Any] = {
            "system_prompt": {"type": "preset", "preset": "claude_code"},
            "model": model,
            "permission_mode": "default",
            "cwd": context.project.root_path,
            "can_use_tool": self._make_can_use_tool(context),
        }
        session_id = context.node.provider_session_id or context.node.sdk_session_id
        if session_id:
            opts["resume"] = session_id
        return ClaudeAgentOptions(**opts)


def _truncate(value: str, limit: int = 200) -> str:
    return value if len(value) <= limit else value[:limit] + "..."
