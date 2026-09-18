"""Native Claude Code CLI provider adapter.

Spawns the ``claude`` binary in a PTY, submits a turn by typing into
its TUI, and streams events observed on Claude Code's on-disk JSONL
transcript. AskUserQuestion is intercepted via a ``PreToolUse`` hook so
we can route it through MiniClaw2's existing gate flow — see
``claude_native/hook_installer.py`` and ``app.py`` /hook endpoints.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from ..domain import GateSubtype
from ..model_catalog import get_model_preset
from .base import (
    AgentProviderContext,
    AgentProviderEvent,
    GateRequest,
    ReviewReport,
    ReviewSpec,
)
from .claude_native import ClaudeNativeError, ClaudeNativeSession
from .claude_native import hook_runtime
from .claude_native.ask_payload import (
    deny_ask_directive,
    format_ask_directive,
    parse_ask_payload,
)

logger = logging.getLogger(__name__)


def _pending_subagent_ask_reason(pending: list[str]) -> str:
    listed = "、".join(pending)
    return (
        f"本轮还有 {len(pending)} 个子代理在运行（{listed}），现在不能向用户提问。"
        "在等待用户回答期间，子代理的完成通知会入队并与用户的回答分叉，"
        "使 CLI 在通知那一支上本地合成回复、完全不调用模型，"
        "整个节点会静默失败。\n\n"
        "请先等待子代理的完成通知，收下结果之后再提问。"
        "如果确认某个子代理不会返回，用 TaskStop 终止它。"
    )


def _patch_review_prompt(patch: str, focus: str | None) -> str:
    focus_text = f"\nReview focus: {' '.join(focus.split())}\n" if focus else ""
    return (
        "Review the following captured uncommitted Git patch. Treat this patch "
        "as the complete audit target and report only actionable defects."
        f"{focus_text}\n```diff\n{patch}\n```"
    )

class ClaudeProvider:
    name = "claude"

    def __init__(self) -> None:
        self._session: ClaudeNativeSession | None = None

    async def run(
        self, context: AgentProviderContext
    ) -> AsyncIterator[AgentProviderEvent]:
        try:
            self._session = ClaudeNativeSession(
                cwd=context.project.root_path,
                node_id=context.node.id,
                project_id=context.project.id,
                ask_dispatcher=lambda payload: self._dispatch_ask(payload, context),
                model=self._resolve_model(context),
                effort=self._resolve_effort(context),
                session_id=self._resume_session_id(context),
                system_prompt_append=context.system_prompt(
                    include_context=not context.minimal_mode
                ),
                tool_allowlist=(
                    list(context.tool_allowlist or [])
                    if context.minimal_mode
                    else None
                ),
                plugin_dir=getattr(
                    getattr(context, "skill_materialization", None),
                    "plugin_dir",
                    None,
                ),
            )
            await self._session.start()
            yield AgentProviderEvent(
                kind="session",
                session_id=self._session.session_id,
            )

            result = await self._session.send(
                context.turn_text(),
                confirmation_text=context.node.prompt,
            )
            if not result.submitted:
                yield AgentProviderEvent(
                    kind="error",
                    error=(
                        "claude submit failed: "
                        + (result.failure_reason or "unknown reason")
                    ),
                )
                return

            terminal_seen = False
            async for event in self._session.stream_events():
                yield event
                if event.kind in {"done", "error"}:
                    terminal_seen = True
                    return
            if not terminal_seen:
                yield AgentProviderEvent(
                    kind="error",
                    error="claude provider stream ended without a terminal event",
                )
        except ClaudeNativeError as exc:
            yield AgentProviderEvent(kind="error", error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("claude native provider failed")
            yield AgentProviderEvent(
                kind="error",
                error=f"unexpected claude provider error: {exc}",
            )
        finally:
            session = self._session
            self._session = None
            if session is not None:
                try:
                    await session.close()
                except Exception:  # noqa: BLE001
                    logger.debug("session close failed", exc_info=True)

    async def interrupt(self) -> None:
        session = self._session
        if session is not None:
            await session.interrupt()

    async def run_review(
        self, context: AgentProviderContext, spec: ReviewSpec
    ) -> AsyncIterator[AgentProviderEvent]:
        if spec.target.type != "uncommitted":
            yield AgentProviderEvent(
                kind="error", error=f"unsupported Claude review target: {spec.target.type}"
            )
            return
        try:
            self._session = ClaudeNativeSession(
                cwd=context.project.root_path,
                node_id=context.node.id,
                project_id=context.project.id,
                ask_dispatcher=lambda payload: self._dispatch_ask(payload, context),
                model=self._resolve_model(context),
                effort=self._resolve_effort(context),
                session_id=self._resume_session_id(context),
                system_prompt_append=context.system_prompt(),
                plugin_dir=getattr(
                    getattr(context, "skill_materialization", None),
                    "plugin_dir",
                    None,
                ),
            )
            await self._session.start()
            yield AgentProviderEvent(kind="session", session_id=self._session.session_id)
            scope = (
                "Review only uncommitted changes: staged, unstaged, and untracked "
                "files (git diff HEAD plus untracked files)."
            )
            if spec.patch is None:
                command = "/code-review " + scope
                if spec.focus:
                    command += " Focus: " + " ".join(spec.focus.split())
            else:
                command = _patch_review_prompt(spec.patch, spec.focus)
            result = await self._session.send(command, confirmation_text=command)
            if not result.submitted:
                yield AgentProviderEvent(
                    kind="error",
                    error="claude code-review submit failed: "
                    + (result.failure_reason or "unknown reason"),
                )
                return
            async for event in self._session.stream_events():
                if event.kind == "done":
                    report = self._session.last_assistant_text.strip()
                    if spec.patch is None and _unknown_code_review_command(report):
                        yield AgentProviderEvent(
                            kind="error",
                            error=(
                                "native code review requires a Claude Code version "
                                "that provides /code-review"
                            ),
                        )
                        return
                    if report:
                        yield AgentProviderEvent(
                            kind="review",
                            report=ReviewReport(raw_markdown=report),
                        )
                    yield event
                    return
                yield event
                if event.kind == "error":
                    return
        except ClaudeNativeError as exc:
            yield AgentProviderEvent(kind="error", error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("claude native review failed")
            yield AgentProviderEvent(
                kind="error", error=f"unexpected claude review error: {exc}"
            )
        finally:
            session = self._session
            self._session = None
            if session is not None:
                try:
                    await session.close()
                except Exception:  # noqa: BLE001
                    logger.debug("review session close failed", exc_info=True)

    async def _dispatch_ask(
        self,
        payload: dict[str, Any],
        context: AgentProviderContext,
    ) -> dict[str, Any]:
        """Route a hook-forwarded AskUserQuestion payload through the runner's
        gate machinery and return the directive Claude expects."""
        pending = hook_runtime.running_subagents(context.node.id)
        if pending:
            # Suspending here while a subagent is still running is what
            # breaks a node: the completion notification enqueues during
            # the wait, forks the conversation against the user's answer,
            # and the CLI can then synthesize replies on the notification
            # branch without ever calling the model. Deny instead — the
            # agent has to collect its subagents first.
            return deny_ask_directive(_pending_subagent_ask_reason(pending))
        parsed = parse_ask_payload(payload)
        if parsed is None:
            # Passthrough shape: an empty dict signals the bridge to fall back
            # to the native TUI prompt.
            return {}
        response = await context.request_gate(
            GateRequest(
                subtype=GateSubtype.ASK_USER,
                tool_name="AskUserQuestion",
                tool_input={
                    "questions": parsed.raw_questions,
                },
                provider_request_id=payload.get("hook_request_id"),
            )
        )
        return format_ask_directive(response, parsed)

    def _resolve_model(self, context: AgentProviderContext) -> str | None:
        return get_model_preset(
            context.node.model_preset_id,
            store_root=getattr(context, "store_root", None),
        ).model

    def _resolve_effort(self, context: AgentProviderContext) -> str | None:
        return get_model_preset(
            context.node.model_preset_id,
            store_root=getattr(context, "store_root", None),
        ).reasoning_effort

    def _resume_session_id(
        self, context: AgentProviderContext
    ) -> str | None:
        node = context.node
        return node.provider_session_id


def _unknown_code_review_command(text: str) -> bool:
    stripped = text.strip().lower()
    if len(stripped) >= 200:
        return False
    return stripped.startswith("unknown slash command") or (
        stripped.startswith("unknown command") and "/code-review" in stripped
    )
