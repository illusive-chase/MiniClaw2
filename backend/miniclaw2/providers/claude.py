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
from .base import AgentProviderContext, AgentProviderEvent, GateRequest
from .claude_native import ClaudeNativeError, ClaudeNativeSession
from .claude_native.ask_payload import format_ask_directive, parse_ask_payload

logger = logging.getLogger(__name__)

# Ask gates ride the PreToolUse hook bridge, whose transport has hard
# deadlines. Each layer must give up before the layer beneath it kills the
# transport, or the user's answer lands on a dead socket:
#   this runner-side gate supervision (570s)
#   < /hook/ask dispatcher wait_for (app._HOOK_ASK_TIMEOUT_SECONDS, 590s)
#   < hook bridge HTTP timeout (claude_hook_bridge._ASK_TIMEOUT_SECONDS, 600s)
#   < installed hook entry timeout (hook_installer, 700s)
# test_hook_routes asserts this ordering.
_ASK_GATE_TIMEOUT_SECONDS = 570.0


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
                system_prompt_append=(
                    context.system_context if not context.minimal_mode else ""
                ),
                tool_allowlist=(
                    list(context.tool_allowlist or [])
                    if context.minimal_mode
                    else None
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

    async def _dispatch_ask(
        self,
        payload: dict[str, Any],
        context: AgentProviderContext,
    ) -> dict[str, Any]:
        """Route a hook-forwarded AskUserQuestion payload through the runner's
        gate machinery and return the directive Claude expects."""
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
                timeout_seconds=_ASK_GATE_TIMEOUT_SECONDS,
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
