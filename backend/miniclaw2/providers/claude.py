"""Claude Agent SDK provider adapter."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
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
)
from claude_agent_sdk import (
    UserMessage as SDKUserMessage,
)

from ..domain import GateSubtype
from ..events import Activity, TextDelta, Thinking, Usage
from .base import AgentProviderContext, AgentProviderEvent, GateRequest, compose_turn_text


class ClaudeProvider:
    name = "claude"

    async def run(self, context: AgentProviderContext) -> AsyncIterator[AgentProviderEvent]:
        pending_tools: dict[str, Activity] = {}
        try:
            async with ClaudeSDKClient(options=self._build_options(context)) as client:
                await client.query(compose_turn_text(context.node.prompt, context.launch_instructions))
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
                        is_error = bool(block.is_error)
                        pending.status = "failed" if is_error else "finish"
                        result_text = _flatten_tool_result(block.content)
                        if result_text:
                            pending.result = _truncate(result_text, 4096)
                            pending.result_kind = _kind_for_tool(
                                pending.name,
                                result_text,
                                is_error=is_error,
                            )
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
        if context.minimal_mode:
            allowlist = set(context.tool_allowlist or [])

            async def minimal_callback(
                tool_name: str,
                tool_input: dict[str, Any],
                permission_context: ToolPermissionContext,
            ) -> PermissionResultAllow | PermissionResultDeny:
                if tool_name not in allowlist:
                    return PermissionResultDeny(
                        message=f"tool '{tool_name}' not allowed in minimal mode",
                    )
                if tool_name == "Write" and not _is_root_context_write(context, tool_input):
                    return PermissionResultDeny(
                        message="Write is allowed only for the project root CONTEXT.md in minimal mode",
                    )
                return PermissionResultAllow()

            return minimal_callback

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
            or os.environ.get("MINICLAW_ANTHROPIC_MODEL")
            or "claude-sonnet-4-6"
        )
        system_prompt: dict[str, Any] = {"type": "preset", "preset": "claude_code"}
        # Minimal mode (out-of-band framework agent) deliberately does not
        # inject the project's own CONTEXT.md into the system prompt — the
        # agent reads it as a tool when needed.
        if context.system_context and not context.minimal_mode:
            system_prompt["append"] = context.system_context
        opts: dict[str, Any] = {
            "system_prompt": system_prompt,
            "model": model,
            "cwd": context.project.root_path,
            "can_use_tool": self._make_can_use_tool(context),
            # MiniClaw2 owns permission flow; avoid implicit CLI settings
            # auto-allowing tools.
            "setting_sources": [],
        }
        if context.minimal_mode:
            opts["permission_mode"] = "default"
        else:
            permission_mode = context.project.settings_override.get("permission_mode")
            if isinstance(permission_mode, str) and permission_mode:
                opts["permission_mode"] = permission_mode
            else:
                opts["permission_mode"] = "default"
        session_id = context.node.provider_session_id or context.node.sdk_session_id
        if session_id:
            opts["resume"] = session_id
        return ClaudeAgentOptions(**opts)


def _is_root_context_write(
    context: AgentProviderContext,
    tool_input: dict[str, Any],
) -> bool:
    raw_path = tool_input.get("file_path")
    if raw_path is None:
        raw_path = tool_input.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return False

    root = Path(context.project.root_path).resolve(strict=False)
    target = Path(raw_path)
    if not target.is_absolute():
        target = root / target
    return target.resolve(strict=False) == (root / "CONTEXT.md").resolve(strict=False)


def _truncate(value: str, limit: int = 200) -> str:
    return value if len(value) <= limit else value[:limit] + "..."


_STDOUT_TOOLS = {"Bash", "BashOutput"}


def _kind_for_tool(name: str, text: str, *, is_error: bool) -> str:
    if is_error:
        return "text"
    if _looks_like_diff(text):
        return "diff"
    if name in _STDOUT_TOOLS:
        return "stdout"
    return "text"


def _looks_like_diff(text: str) -> bool:
    lines = text.splitlines()
    return any(line.startswith("@@") for line in lines) and any(
        line.startswith("+++") or line.startswith("---") for line in lines
    )


def _flatten_tool_result(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for entry in content:
            if isinstance(entry, dict):
                if entry.get("type") == "text" and isinstance(entry.get("text"), str):
                    parts.append(entry["text"])
                elif isinstance(entry.get("text"), str):
                    parts.append(entry["text"])
            else:
                text = getattr(entry, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)
