"""Drain Claude Code's JSONL transcript and map events to ``AgentProviderEvent``.

The JSONL is the authoritative source of truth for what the model did
in a turn. We tail it incrementally: each ``drain()`` call returns the
records appended since the last offset, handles truncation/rotation,
and keeps any trailing partial line for the next call.

Event mapping mirrors the SDK-based provider's behavior (per §9.2).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...events import Activity, TextDelta, Thinking, Usage
from ..base import AgentProviderEvent

logger = logging.getLogger(__name__)


_STDOUT_TOOLS = {"Bash", "BashOutput"}
# Tools that dispatch a subagent. ``Task`` is the historical name and
# ``Agent`` the current one; both are classified as agent activity so the
# rename does not silently reclassify subagent calls as ordinary tools.
_SUBAGENT_TOOLS = {"Task", "Agent"}
_SYNTHETIC_MODEL = "<synthetic>"

# A completion notification's opening tag, and the fields we read out of
# it. The CLI's own terminal set for a task is completed/failed/killed
# (see the eviction pass in its task registry); ``stopped`` appears in
# the orphan notice a restarted worker emits for agents whose completion
# record was lost. ``blocked`` is deliberately absent — a blocked agent
# is waiting on input and will notify again, so it is still outstanding.
_TASK_NOTIFICATION_OPEN = "<task-notification>"
_TASK_ID_PATTERN = re.compile(r"<task-id>([^<]+)</task-id>")
_TASK_STATUS_PATTERN = re.compile(r"<status>([^<]+)</status>")
_TERMINAL_TASK_STATUSES = {"completed", "failed", "killed", "stopped"}


def finished_task_ids(record: dict[str, Any]) -> list[str]:
    """Task ids a completion notification in ``record`` reports as done.

    The subagent ledger cannot retire an agent on its own
    ``SubagentStop``: that hook fires from inside the agent's query loop,
    while the task is still ``running``, so the agent lists *itself* in
    the ``background_tasks`` snapshot that travels with the stop. The
    status only flips to ``completed`` afterwards, and the sole hook
    carrying a fresh snapshot is the parent's own ``Stop`` — which is
    exactly the gate the ledger is supposed to be able to answer before.

    The completion notification is the missing signal. The CLI enqueues
    it when the agent's result is ready to be delivered, its
    ``<task-id>`` is the same ``agent_id`` the ledger is keyed by (a
    subagent registers under its ``agentId``), and it is visible in the
    parent's transcript, which MiniClaw2 already tails.

    Only the two carriers the CLI itself writes are trusted:

    - ``queue-operation`` — the enqueue/remove bookkeeping records;
    - an ``attachment`` whose ``commandMode`` is ``task-notification``.

    A ``user`` or ``assistant`` record can also contain this XML — the
    model quotes it when reasoning about notifications, and a restart
    notice is delivered as a plain user prompt. Reading those would let
    the model retire its own subagents by talking about them, so they
    are ignored here. The one real signal lost that way (the restart
    orphan notice) concerns agents from a *previous* session, which this
    turn's ledger never held.
    """
    if not _is_task_notification_record(record):
        return []
    text = _task_notification_text(record)
    if _TASK_NOTIFICATION_OPEN not in text:
        return []
    statuses = _TASK_STATUS_PATTERN.findall(text)
    # Retire only on an unambiguous terminal status. A notification with
    # no status, or one still working, leaves the ledger alone.
    if not statuses or not all(
        status.strip().lower() in _TERMINAL_TASK_STATUSES for status in statuses
    ):
        return []
    return [
        task_id.strip()
        for task_id in _TASK_ID_PATTERN.findall(text)
        if task_id.strip()
    ]


def _is_task_notification_record(record: dict[str, Any]) -> bool:
    rtype = record.get("type")
    if rtype == "queue-operation":
        return True
    if rtype != "attachment":
        return False
    attachment = record.get("attachment")
    if not isinstance(attachment, dict):
        return False
    return attachment.get("commandMode") == "task-notification"


def _task_notification_text(record: dict[str, Any]) -> str:
    content = record.get("content")
    if isinstance(content, str) and content:
        return content
    attachment = record.get("attachment")
    if isinstance(attachment, dict):
        prompt = attachment.get("prompt")
        if isinstance(prompt, str):
            return prompt
    return ""


@dataclass(slots=True)
class DrainResult:
    events: list[dict[str, Any]]
    new_offset: int
    pending_tail: str


def drain(path: Path, from_offset: int) -> DrainResult:
    """Return records appended since ``from_offset``.

    - Empty result if the file does not exist.
    - If size < ``from_offset``: the file was rotated or truncated —
      restart from offset 0.
    - The trailing partial line (no ``\\n``) is not parsed; the offset
      is advanced only up to the last complete newline.
    - Malformed JSON lines are silently skipped.
    """
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return DrainResult(events=[], new_offset=from_offset, pending_tail="")

    if size < from_offset:
        from_offset = 0

    if size <= from_offset:
        return DrainResult(events=[], new_offset=from_offset, pending_tail="")

    try:
        with path.open("rb") as f:
            f.seek(from_offset)
            chunk = f.read(size - from_offset)
    except OSError:
        return DrainResult(events=[], new_offset=from_offset, pending_tail="")

    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError:
        text = chunk.decode("utf-8", errors="replace")

    last_newline = text.rfind("\n")
    if last_newline < 0:
        # No complete line yet.
        return DrainResult(events=[], new_offset=from_offset, pending_tail=text)

    complete = text[: last_newline + 1]
    tail = text[last_newline + 1 :]
    new_offset = from_offset + len(complete.encode("utf-8"))

    events: list[dict[str, Any]] = []
    for line in complete.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            events.append(json.loads(stripped))
        except json.JSONDecodeError:
            continue
    return DrainResult(events=events, new_offset=new_offset, pending_tail=tail)


def contains_user_submit_marker(chunk: bytes) -> bool:
    """Return True if ``chunk`` contains an initiated-user-turn marker.

    We check for the string-content ``user`` message shape and the
    ``queued_command`` enqueue attachment shape (which fires for prompts
    submitted while an earlier turn is still active). Tool-result lines
    have array content and never match.
    """
    try:
        text = chunk.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return False
    return (
        '"role":"user","content":"' in text
        or '"role": "user", "content": "' in text
        or '"operation":"enqueue"' in text
    )


def fingerprint_prompt(prompt: str, limit: int = 30) -> str:
    return " ".join(prompt.split())[:limit]


def line_matches_fingerprint(line: str, fingerprint: str) -> bool:
    if not fingerprint:
        return False
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(record, dict):
        return False
    if record.get("type") != "user":
        return False
    message = record.get("message")
    if not isinstance(message, dict):
        return False
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if not isinstance(content, str):
        return False
    return fingerprint in " ".join(content.split())


def _is_api_error_record(record: dict[str, Any]) -> bool:
    return record.get("isApiErrorMessage") is True or bool(record.get("error"))


def _api_error_text(record: dict[str, Any]) -> str:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    text = "\n".join(
        str(block.get("text") or "").strip()
        for block in content or []
        if isinstance(block, dict)
        and block.get("type") == "text"
        and str(block.get("text") or "").strip()
    )
    if text:
        return text
    error = record.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    if error:
        return json.dumps(error, ensure_ascii=False)
    return "Claude API 错误"


def _is_synthetic_message(message: dict[str, Any]) -> bool:
    """Whether the CLI produced this assistant message without the model.

    Both halves are required. The model marker alone also covers locally
    rendered API-error notices, which are a different and already-handled
    condition; pairing it with all-zero token counts isolates the case
    where a reply was manufactured for a branch the model never saw.
    """
    if message.get("model") != _SYNTHETIC_MODEL:
        return False
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return False
    return not any(
        _int(usage, key)
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    )


class TranscriptTranslator:
    """Stateful translator: JSONL records → ``AgentProviderEvent`` stream.

    Maintains the ``pending_tools`` map so a ``tool_use`` block's start
    event can be mutated when its ``tool_result`` arrives, matching the
    SDK-based provider's behavior.
    """

    def __init__(self) -> None:
        self._pending_tools: dict[str, Activity] = {}
        self._usage_by_message_id: dict[str, Usage] = {}
        self._session_id_emitted = False
        self._last_assistant_text = ""
        self._synthetic_turns = 0

    @property
    def synthetic_turns(self) -> int:
        """Assistant records the CLI produced without calling the model.

        A conversation branch whose input is a bare attachment — which is
        what a background subagent's completion notification looks like
        when it forks against a suspended question — gets answered
        locally: ``model`` is ``<synthetic>`` and every usage counter is
        zero, yet it ends the turn as though the model had replied. The
        count is diagnostic only. It never changes the turn's outcome,
        because the guardrails that prevent the fork sit upstream and a
        detector that judged turns could kill healthy ones instead.
        """
        return self._synthetic_turns

    def translate(self, record: dict[str, Any]) -> list[AgentProviderEvent]:
        rtype = record.get("type")
        out: list[AgentProviderEvent] = []

        sid = record.get("sessionId")
        if isinstance(sid, str) and sid and not self._session_id_emitted:
            self._session_id_emitted = True
            out.append(AgentProviderEvent(kind="session", session_id=sid))

        if rtype == "assistant" and _is_api_error_record(record):
            out.append(
                AgentProviderEvent(kind="error", error=_api_error_text(record))
            )
        elif rtype == "assistant":
            out.extend(self._assistant(record))
        elif rtype == "user":
            out.extend(self._user(record))
        return out

    def observed_session_id(self, record: dict[str, Any]) -> str | None:
        sid = record.get("sessionId")
        return sid if isinstance(sid, str) and sid else None

    @property
    def has_pending_tools(self) -> bool:
        return bool(self._pending_tools)

    def final_usage(self) -> Usage | None:
        if not self._usage_by_message_id:
            return None
        return Usage(
            input_tokens=sum(
                usage.input_tokens for usage in self._usage_by_message_id.values()
            ),
            output_tokens=sum(
                usage.output_tokens for usage in self._usage_by_message_id.values()
            ),
            cache_read_tokens=sum(
                usage.cache_read_tokens for usage in self._usage_by_message_id.values()
            ),
            cache_creation_tokens=sum(
                usage.cache_creation_tokens
                for usage in self._usage_by_message_id.values()
            ),
            final=True,
        )

    @property
    def last_assistant_text(self) -> str:
        return self._last_assistant_text

    def _assistant(self, record: dict[str, Any]) -> list[AgentProviderEvent]:
        message = record.get("message")
        if not isinstance(message, dict):
            return []
        if _is_synthetic_message(message):
            self._synthetic_turns += 1
            logger.warning(
                "assistant record synthesized locally by the CLI "
                "(model=%r, zero usage): the model was not called for this "
                "turn, which points at a forked conversation branch",
                message.get("model"),
            )
        self._record_usage(message)
        content = message.get("content")
        if not isinstance(content, list):
            return []
        out: list[AgentProviderEvent] = []
        text_blocks = [
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if any(text_blocks) and not record.get("isSidechain"):
            assistant_text = "\n".join(
                block for block in text_blocks if block
            ).strip()
            if len(assistant_text) > len(self._last_assistant_text):
                self._last_assistant_text = assistant_text
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = str(block.get("text") or "")
                if not text:
                    continue
                if not text.endswith("\n"):
                    text = text + "\n"
                out.append(
                    AgentProviderEvent(kind="event", event=TextDelta(text=text))
                )
            elif btype == "thinking":
                thinking = str(block.get("thinking") or "")
                if thinking:
                    out.append(
                        AgentProviderEvent(
                            kind="event", event=Thinking(text=thinking)
                        )
                    )
            elif btype == "tool_use":
                name = str(block.get("name") or "tool")
                block_id = str(block.get("id") or f"tool:{len(self._pending_tools)}")
                tool_input = block.get("input")
                summary = _truncate(_stringify_input(tool_input))
                is_subagent = name in _SUBAGENT_TOOLS
                kind = "agent" if is_subagent else "tool"
                activity = Activity(
                    kind=kind,  # type: ignore[arg-type]
                    status="start",
                    id=block_id,
                    name=name,
                    summary=summary,
                    parameters=_stringify_input(tool_input),
                    command=_tool_command(name, tool_input),
                )
                if not is_subagent:
                    # Subagent progress is many events over one tool call —
                    # no cache.
                    self._pending_tools[block_id] = activity
                out.append(AgentProviderEvent(kind="event", event=activity))
        return out

    def _user(self, record: dict[str, Any]) -> list[AgentProviderEvent]:
        message = record.get("message")
        if not isinstance(message, dict):
            return []
        content = message.get("content")
        if not isinstance(content, list):
            # String-content user messages are prompts; no event emission.
            return []
        out: list[AgentProviderEvent] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            tool_use_id = str(block.get("tool_use_id") or "")
            pending = self._pending_tools.pop(tool_use_id, None)
            if pending is None:
                continue
            is_error = bool(block.get("is_error"))
            pending.status = "failed" if is_error else "finish"
            result_text = _flatten_tool_result(block.get("content"))
            if result_text:
                pending.result = _truncate(result_text, 4096)
                pending.result_kind = _kind_for_tool(
                    pending.name, result_text, is_error=is_error
                )
            out.append(AgentProviderEvent(kind="event", event=pending))
        return out

    def _record_usage(self, message: dict[str, Any]) -> None:
        message_id = message.get("id")
        usage_raw = message.get("usage")
        if not isinstance(message_id, str) or not message_id:
            return
        if not isinstance(usage_raw, dict):
            return
        self._usage_by_message_id[message_id] = Usage(
            input_tokens=_int(usage_raw, "input_tokens"),
            output_tokens=_int(usage_raw, "output_tokens"),
            cache_read_tokens=_int(usage_raw, "cache_read_input_tokens"),
            cache_creation_tokens=_int(usage_raw, "cache_creation_input_tokens"),
        )


# ---------------------------------------------------------------------------
# Helpers copied verbatim from providers/claude.py so the diff/stdout/text
# discrimination stays identical.


def _truncate(value: str, limit: int = 200) -> str:
    return value if len(value) <= limit else value[:limit] + "..."


def _stringify_input(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _tool_command(name: str, value: Any) -> str | None:
    if name.lower() not in {"bash", "shell", "command"}:
        return None
    if isinstance(value, dict) and isinstance(value.get("command"), str):
        return value["command"]
    return value if isinstance(value, str) else None


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


def _int(source: dict[str, Any], key: str) -> int:
    value = source.get(key)
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0
