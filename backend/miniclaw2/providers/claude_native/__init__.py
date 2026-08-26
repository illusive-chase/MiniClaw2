"""Native Claude Code CLI adapter — public surface.

Consumers use ``ClaudeNativeSession`` to spawn ``claude`` in a PTY,
submit a turn, and stream ``AgentProviderEvent``s drained from Claude's
on-disk JSONL transcript.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..base import AgentProviderEvent
from . import hook_runtime
from .input import InputWriter, SubmitResult
from .keybindings import resolve_submit_key
from .paths import default_data_dir, jsonl_path
from .session_resolver import resolve_from_pid
from .spawn import (
    PTY_COLS,
    PTY_ROWS,
    ClaudeBinaryNotFoundError,
    build_argv,
    build_env,
    resolve_claude_binary,
)
from .transcript import TranscriptTranslator, drain

logger = logging.getLogger(__name__)

AskDispatcher = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_SESSION_READY_TIMEOUT = 45.0
_STREAM_POLL_INTERVAL = 0.1
_STREAM_STALL_TIMEOUT_SECONDS = 30 * 60.0
# How long a verified turn-complete waits for in-flight tool calls to land
# their results before the stream closes anyway.
_TURN_COMPLETE_TOOL_DRAIN_SECONDS = 10.0
_CLOSE_TIMEOUT = 3.0


class ClaudeNativeError(RuntimeError):
    """Raised for spawn / lifecycle failures within ``ClaudeNativeSession``."""


class ClaudeNativeSession:
    """Owns one ``claude`` PTY child + its JSONL tail + AskUserQuestion routing."""

    def __init__(
        self,
        *,
        cwd: str,
        node_id: str,
        project_id: str,
        ask_dispatcher: AskDispatcher,
        model: str | None = None,
        effort: str | None = None,
        session_id: str | None = None,
        system_prompt_append: str = "",
        tool_allowlist: list[str] | None = None,
        plugin_dir: Path | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self._cwd = cwd
        self._node_id = node_id
        self._project_id = project_id
        self._ask_dispatcher = ask_dispatcher
        self._model = model
        self._effort = effort
        self._resume_session_id = session_id
        self._system_prompt_append = system_prompt_append
        self._tool_allowlist = tool_allowlist
        self._plugin_dir = plugin_dir
        self._data_dir = data_dir or default_data_dir()

        self._session_id: str = session_id or str(uuid4())
        self._pty: Any = None
        self._pty_reader_task: asyncio.Task[None] | None = None
        self._last_pty_output_ts: float = 0.0
        self._input: InputWriter | None = None
        self._jsonl_path: Path = jsonl_path(
            cwd, self._session_id, self._data_dir
        )
        # Resume: seed offset from current EOF so drain() does not replay prior
        # turns as current output or include their usage in this turn.
        self._jsonl_offset: int = 0
        if session_id is not None:
            try:
                self._jsonl_offset = self._jsonl_path.stat().st_size
            except OSError:
                self._jsonl_offset = 0
        self._translator = TranscriptTranslator()
        self._closed = False
        self._interrupt_requested = False
        self._dispatch_registered = False
        self._session_ready_event: asyncio.Event | None = None
        self._session_ready_key: str = self._session_id
        self._turn_complete_event: asyncio.Event | None = None

    @property
    def session_id(self) -> str:
        return self._session_id

    async def start(self) -> None:
        """Spawn ``claude`` and wait for its SessionStart hook (or 45s timeout)."""
        binary = resolve_claude_binary()
        argv = build_argv(
            binary=binary,
            session_id=self._session_id,
            resume=self._resume_session_id is not None,
            model=self._model,
            effort=self._effort,
            system_prompt_append=self._system_prompt_append,
            tool_allowlist=self._tool_allowlist,
            plugin_dir=str(self._plugin_dir) if self._plugin_dir else None,
        )
        env = build_env(
            hook_url=hook_runtime.ask_url(),
            hook_token=hook_runtime.token(),
            node_id=self._node_id,
            project_id=self._project_id,
            session_id=self._session_id,
        )

        # Register the session-ready waiter before spawning: the child can
        # run its SessionStart hook and POST /hook/* immediately after fork,
        # and signal_session_ready() drops signals for unregistered ids.
        self._session_ready_key = self._session_id
        self._session_ready_event = hook_runtime.register_session_ready(
            self._session_id
        )
        self._turn_complete_event = hook_runtime.register_turn_complete(
            self._node_id, self._session_id
        )

        loop = asyncio.get_running_loop()
        try:
            self._pty = await loop.run_in_executor(
                None, _spawn_pty, argv, self._cwd, env
            )
        except ClaudeBinaryNotFoundError:
            self._release_hook_registrations()
            raise
        except Exception as exc:  # noqa: BLE001
            self._release_hook_registrations()
            raise ClaudeNativeError(f"failed to spawn claude PTY: {exc}") from exc

        submit_result = resolve_submit_key(self._data_dir)
        if submit_result.submit is None:
            await self.close()
            raise ClaudeNativeError(
                submit_result.reason
                or "unable to resolve a PTY-sendable chat:submit binding"
            )

        self._input = InputWriter(
            pty_write=self._pty_write,
            submit_key=submit_result.submit,
            jsonl_path=self._jsonl_path,
            expected_cwd=self._cwd,
            data_dir=self._data_dir,
            enter_is_newline=submit_result.enter_is_newline,
        )
        self._pty_reader_task = asyncio.create_task(self._drain_pty_output())

        hook_runtime.register_ask_dispatcher(self._node_id, self._ask_dispatcher)
        self._dispatch_registered = True

        try:
            await asyncio.wait_for(
                self._session_ready_event.wait(),
                timeout=_SESSION_READY_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "claude SessionStart hook did not fire within %.0fs; "
                "proceeding with a best-effort submit",
                _SESSION_READY_TIMEOUT,
            )

    async def send(
        self,
        prompt: str,
        *,
        confirmation_text: str | None = None,
    ) -> SubmitResult:
        if self._input is None:
            raise ClaudeNativeError("session.start() has not been called")
        result = await self._input.send(
            prompt,
            confirmation_text=confirmation_text,
        )
        if not result.submitted:
            resolved = self._resolve_from_pid_state()
            if resolved is not None and resolved.session_id != self._session_id:
                self._retarget(resolved.session_id, resolved.jsonl_path)
                recheck = result.recheck
                rechecked = await recheck() if recheck is not None else None
                if rechecked is not None and rechecked.submitted:
                    if (
                        rechecked.session_id is not None
                        and rechecked.session_id != self._session_id
                    ):
                        self._retarget(
                            rechecked.session_id,
                            jsonl_path(
                                self._cwd,
                                rechecked.session_id,
                                self._data_dir,
                            ),
                            stream_offset=rechecked.stream_offset,
                        )
                    elif rechecked.stream_offset is not None:
                        self._jsonl_offset = rechecked.stream_offset
                    return SubmitResult(
                        submitted=True,
                        session_id=self._session_id,
                        stream_offset=self._jsonl_offset,
                    )
        elif result.session_id and result.session_id != self._session_id:
            self._retarget(
                result.session_id,
                jsonl_path(self._cwd, result.session_id, self._data_dir),
                stream_offset=result.stream_offset,
            )
        return result

    async def stream_events(self) -> AsyncIterator[AgentProviderEvent]:
        if self._input is None:
            raise ClaudeNativeError("session.start() has not been called")

        loop = asyncio.get_running_loop()
        last_transcript_progress_ts = loop.time()
        drain_deadline: float | None = None
        while not self._closed:
            result = drain(self._jsonl_path, self._jsonl_offset)
            self._jsonl_offset = result.new_offset
            if result.events:
                last_transcript_progress_ts = loop.time()

            for record in result.events:
                sid = self._translator.observed_session_id(record)
                if sid and sid != self._session_id:
                    new_jsonl = jsonl_path(self._cwd, sid, self._data_dir)
                    self._retarget(
                        sid,
                        new_jsonl,
                        stream_offset=_matching_record_end_offset(
                            new_jsonl,
                            record,
                        ),
                    )
                    yield AgentProviderEvent(kind="session", session_id=sid)

                for ev in self._translator.translate(record):
                    if ev.kind == "error":
                        usage_event = self._final_usage_event()
                        if usage_event is not None:
                            yield usage_event
                        yield ev
                        return
                    yield ev

            if (
                self._turn_complete_event is not None
                and self._turn_complete_event.is_set()
            ):
                # A verified Stop can still land a poll or two before the
                # matching tool_result reaches the transcript, so hold the
                # stream open briefly rather than dropping the tail of the
                # turn. The wait is bounded: a tool_result that never
                # arrives must not strand the node, and the stall check
                # below is itself skipped while tools are pending.
                if not self._translator.has_pending_tools:
                    drain_deadline = None
                else:
                    if drain_deadline is None:
                        drain_deadline = (
                            loop.time() + _TURN_COMPLETE_TOOL_DRAIN_SECONDS
                        )
                    if loop.time() >= drain_deadline:
                        logger.warning(
                            "turn-complete signaled with tool calls still "
                            "pending after %.0fs; closing the stream",
                            _TURN_COMPLETE_TOOL_DRAIN_SECONDS,
                        )
                        drain_deadline = None

                if drain_deadline is None:
                    usage_event = self._final_usage_event()
                    if usage_event is not None:
                        yield usage_event
                    yield AgentProviderEvent(
                        kind="done",
                        final_state=(
                            "cancelled" if self._interrupt_requested else "done"
                        ),
                    )
                    return

            if not _pty_child_alive(self._pty):
                usage_event = self._final_usage_event()
                if usage_event is not None:
                    yield usage_event
                if self._interrupt_requested:
                    yield AgentProviderEvent(kind="done", final_state="cancelled")
                else:
                    yield AgentProviderEvent(
                        kind="error",
                        error=(
                            "claude PTY exited before a turn-complete signal"
                            + _pty_exit_detail(self._pty)
                        ),
                    )
                return

            if not self._translator.has_pending_tools:
                last_progress_ts = max(
                    last_transcript_progress_ts,
                    self._last_pty_output_ts,
                )
                stall_timeout = _stream_stall_timeout_seconds()
                if loop.time() - last_progress_ts > stall_timeout:
                    usage_event = self._final_usage_event()
                    if usage_event is not None:
                        yield usage_event
                    if self._interrupt_requested:
                        yield AgentProviderEvent(kind="done", final_state="cancelled")
                    else:
                        yield AgentProviderEvent(
                            kind="error",
                            error=(
                                "claude stream stalled for "
                                f"{stall_timeout:g}s before a turn-complete signal"
                            ),
                        )
                    return
            await asyncio.sleep(_STREAM_POLL_INTERVAL)

    @property
    def last_assistant_text(self) -> str:
        return self._translator.last_assistant_text

    def _final_usage_event(self) -> AgentProviderEvent | None:
        usage = self._translator.final_usage()
        if usage is None:
            return None
        return AgentProviderEvent(kind="event", event=usage)

    async def interrupt(self) -> None:
        self._interrupt_requested = True
        if self._pty is None:
            return
        try:
            self._pty_write(b"\x03")
        except Exception:  # noqa: BLE001
            logger.debug("Ctrl-C write to claude PTY failed", exc_info=True)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._dispatch_registered:
            hook_runtime.unregister_ask_dispatcher(self._node_id, self._ask_dispatcher)
            self._dispatch_registered = False
        self._release_hook_registrations()

        pty = self._pty
        if pty is not None:
            self._pty = None
            loop = asyncio.get_running_loop()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, _terminate_pty, pty),
                    timeout=_CLOSE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                await loop.run_in_executor(None, _kill_pty, pty)
            except Exception:  # noqa: BLE001
                logger.debug("PTY terminate raised", exc_info=True)

        task = self._pty_reader_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    # ---- internals ------------------------------------------------------

    def _release_hook_registrations(self) -> None:
        """Drop this session's hook slots without disturbing a successor's.

        Teardown of a superseded session is finalized after the next turn
        on the same node has already registered, so we release by the key
        we registered under and only when our own event still occupies it.
        """
        if self._session_ready_event is not None:
            hook_runtime.unregister_session_ready(
                self._session_ready_key, self._session_ready_event
            )
            self._session_ready_event = None
        if self._turn_complete_event is not None:
            hook_runtime.unregister_turn_complete(
                self._node_id, self._turn_complete_event
            )
            self._turn_complete_event = None

    def _pty_write(self, data: bytes) -> None:
        pty = self._pty
        if pty is None:
            raise ClaudeNativeError("PTY is closed")
        try:
            pty.write(data)
        except Exception as exc:  # noqa: BLE001
            raise ClaudeNativeError(f"PTY write failed: {exc}") from exc

    def _retarget(
        self,
        new_session_id: str,
        new_jsonl: Path,
        *,
        stream_offset: int | None = None,
    ) -> None:
        self._session_id = new_session_id
        self._jsonl_path = new_jsonl
        self._jsonl_offset = _retarget_offset(new_jsonl, stream_offset)
        # A rotated session id is still this node's own PTY, so its Stop
        # signal has to keep counting as ours — otherwise the turn-complete
        # check would reject the very session we are now streaming.
        if self._turn_complete_event is not None:
            hook_runtime.claim_turn_complete_session(
                self._node_id, new_session_id, self._turn_complete_event
            )
        if self._input is not None:
            self._input.update_jsonl_path(new_jsonl)

    async def _drain_pty_output(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._closed and self._pty is not None:
            try:
                chunk = await loop.run_in_executor(None, _read_pty, self._pty)
            except Exception:  # noqa: BLE001
                return
            if not chunk:
                await asyncio.sleep(0.05)
                continue
            self._last_pty_output_ts = loop.time()

    def _resolve_from_pid_state(self):
        if self._pty is None:
            return None
        pid = getattr(self._pty, "pid", None)
        if pid is None:
            return None
        return resolve_from_pid(int(pid), self._cwd, self._data_dir)


# ---------------------------------------------------------------------------
# Blocking PTY helpers — invoked via ``run_in_executor`` so we don't stall the
# event loop while ``ptyprocess`` calls into ``os.read`` / waitpid.


def _spawn_pty(argv: list[str], cwd: str, env: dict[str, str]) -> Any:
    from ptyprocess import PtyProcess  # local import: heavy + unix-only

    return PtyProcess.spawn(
        argv,
        cwd=cwd,
        env=env,
        dimensions=(PTY_ROWS, PTY_COLS),
    )


def _read_pty(pty: Any) -> bytes:
    try:
        return pty.read(4096)
    except EOFError:
        return b""
    except OSError:
        return b""


def _terminate_pty(pty: Any) -> None:
    try:
        pty.terminate()
    except Exception:  # noqa: BLE001
        pass


def _kill_pty(pty: Any) -> None:
    try:
        pty.terminate(force=True)
    except Exception:  # noqa: BLE001
        pass


def _pty_child_alive(pty: Any) -> bool:
    if pty is None:
        return False
    isalive = getattr(pty, "isalive", None)
    if not callable(isalive):
        return True
    try:
        return bool(isalive())
    except Exception:  # noqa: BLE001
        return False


def _retarget_offset(path: Path, stream_offset: int | None) -> int:
    """Offset to resume draining from after a session retarget.

    The head of a rotated session file is copied prior history; resuming from
    0 would replay it as current-turn events. Without a known marker/record
    offset the safe fallback is EOF.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return max(0, stream_offset or 0)
    if stream_offset is None:
        return size
    return max(0, min(stream_offset, size))


def _matching_record_end_offset(
    path: Path,
    target: dict[str, Any],
) -> int | None:
    latest_end_offset: int | None = None
    try:
        with path.open("rb") as f:
            offset = 0
            for raw_line in f:
                end_offset = offset + len(raw_line)
                try:
                    record = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    offset = end_offset
                    continue
                if record == target:
                    latest_end_offset = end_offset
                offset = end_offset
    except OSError:
        return None
    return latest_end_offset


def _pty_exit_detail(pty: Any) -> str:
    if pty is None:
        return ""
    parts: list[str] = []
    exitstatus = getattr(pty, "exitstatus", None)
    signalstatus = getattr(pty, "signalstatus", None)
    if exitstatus is not None:
        parts.append(f"exit status {exitstatus}")
    if signalstatus is not None:
        parts.append(f"signal {signalstatus}")
    return f" ({', '.join(parts)})" if parts else ""


def _stream_stall_timeout_seconds() -> float:
    raw = os.environ.get("MINICLAW_CLAUDE_STREAM_STALL_SECONDS")
    if raw is None:
        return _STREAM_STALL_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _STREAM_STALL_TIMEOUT_SECONDS
    return value if value > 0 else _STREAM_STALL_TIMEOUT_SECONDS


__all__ = [
    "AskDispatcher",
    "ClaudeNativeError",
    "ClaudeNativeSession",
    "SubmitResult",
]
