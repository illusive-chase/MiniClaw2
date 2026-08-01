"""Idempotently install our ``PreToolUse``/``SessionStart``/``Stop`` hooks into
``~/.claude/settings.json``.

Matching is by substring — a dev install (``python -m ...`` in a
virtualenv) and a wheel install don't leave duplicate hooks. Same
principle as botmux's ``.includes('cli.js') && endsWith('hook <cliId>')``
check.

Concurrent Claude processes read ``settings.json`` on every request; we
must never leave the file half-written. Writes are atomic via
``os.replace`` on a temp file in the same directory.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


_HOOK_MARKER = "miniclaw2.claude_hook_bridge"
_SESSION_READY_MARKER = "--session-ready"
_TURN_COMPLETE_MARKER = "--turn-complete"
# Claude requires a numeric hook timeout. Use the largest duration that stays
# within the common 32-bit millisecond timer ceiling (about 24.8 days), while
# the MiniClaw2 request itself has no user-decision deadline.
_ASK_HOOK_TIMEOUT_SECONDS = 2_147_000
_SESSION_READY_HOOK_TIMEOUT_SECONDS = 15
_TURN_COMPLETE_HOOK_TIMEOUT_SECONDS = 15


def install_hooks(settings_path: Path | None = None) -> Path:
    """Merge our hook entries into the user's ``settings.json``.

    Returns the settings path we wrote to. Callers should invoke this
    once per daemon start (from FastAPI's startup event).
    """
    target = settings_path or (Path.home() / ".claude" / "settings.json")
    target.parent.mkdir(parents=True, exist_ok=True)

    settings = _read_json(target) or {}
    hooks = settings.setdefault("hooks", {})

    ask_command = _quoted_python() + f" -m {_HOOK_MARKER}"
    ready_command = ask_command + f" {_SESSION_READY_MARKER}"
    turn_complete_command = ask_command + f" {_TURN_COMPLETE_MARKER}"

    ask_entry = {
        "type": "command",
        "command": ask_command,
        "timeout": _ASK_HOOK_TIMEOUT_SECONDS,
    }
    ready_entry = {
        "type": "command",
        "command": ready_command,
        "timeout": _SESSION_READY_HOOK_TIMEOUT_SECONDS,
    }
    turn_complete_entry = {
        "type": "command",
        "command": turn_complete_command,
        "timeout": _TURN_COMPLETE_HOOK_TIMEOUT_SECONDS,
    }

    _replace_group(
        hooks,
        "PreToolUse",
        matcher="AskUserQuestion",
        entry=ask_entry,
        is_ours=lambda e: (
            _HOOK_MARKER in _entry_command(e)
            and _SESSION_READY_MARKER not in _entry_command(e)
            and _TURN_COMPLETE_MARKER not in _entry_command(e)
        ),
    )
    _replace_group(
        hooks,
        "SessionStart",
        matcher=None,
        entry=ready_entry,
        is_ours=lambda e: (
            _HOOK_MARKER in _entry_command(e)
            and _SESSION_READY_MARKER in _entry_command(e)
        ),
    )
    _replace_group(
        hooks,
        "Stop",
        matcher=None,
        entry=turn_complete_entry,
        is_ours=lambda e: (
            _HOOK_MARKER in _entry_command(e)
            and _TURN_COMPLETE_MARKER in _entry_command(e)
        ),
    )

    _atomic_write(target, json.dumps(settings, indent=2))
    return target


def _entry_command(entry: Any) -> str:
    if isinstance(entry, dict):
        cmd = entry.get("command")
        if isinstance(cmd, str):
            return cmd
    return ""


def _quoted_python() -> str:
    exe = sys.executable or "python3"
    if " " in exe or "\t" in exe:
        return f'"{exe}"'
    return exe


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _replace_group(
    hooks: dict[str, Any],
    event_name: str,
    *,
    matcher: str | None,
    entry: dict[str, Any],
    is_ours,
) -> None:
    groups_raw = hooks.get(event_name)
    if not isinstance(groups_raw, list):
        groups_raw = []
    normalized: list[dict[str, Any]] = []
    matched = False
    for group in groups_raw:
        if not isinstance(group, dict):
            normalized.append(group)  # type: ignore[arg-type]
            continue
        group_matcher = group.get("matcher")
        if matcher is None:
            group_match = group_matcher in (None, "", "*")
        else:
            group_match = group_matcher == matcher
        raw_entries = group.get("hooks")
        if isinstance(raw_entries, list):
            new_entries = [e for e in raw_entries if not (isinstance(e, dict) and is_ours(e))]
        else:
            new_entries = []
        if group_match and not matched:
            new_entries.append(entry)
            matched = True
        if new_entries or group.get("matcher"):
            new_group = dict(group)
            new_group["hooks"] = new_entries
            if not new_entries and not group.get("matcher"):
                continue
            normalized.append(new_group)
    if not matched:
        new_group: dict[str, Any] = {"hooks": [entry]}
        if matcher is not None:
            new_group["matcher"] = matcher
        normalized.append(new_group)
    hooks[event_name] = normalized


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".miniclaw2.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
