"""Resolve Claude Code's chat submit key from ~/.claude/keybindings.json.

Preference order (matches botmux):

1. ``meta+enter`` / ``alt+enter``  → ``\\x1b\\r``
2. ``enter``                       → ``\\r``
3. ``ctrl+enter`` / ``cmd+enter`` /
   ``shift+enter``                 → **fail fast**. A plain PTY cannot send
   these distinguishably (requires Kitty keyboard protocol / modifyOtherKeys).

If ``enter`` is remapped to ``chat:newline``, the fallback to ``enter`` for
submit is unsafe — we return failure with a clear message rather than
sending a phantom submit.

An environment override ``CLAUDE_CODE_SUBMIT_KEY`` (documented in botmux)
short-circuits the resolver.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SubmitKey:
    raw: str
    label: str


@dataclass(slots=True)
class KeybindingsResult:
    submit: SubmitKey | None
    reason: str = ""
    enter_is_newline: bool = False


def resolve_submit_key(data_dir: Path) -> KeybindingsResult:
    override = os.environ.get("CLAUDE_CODE_SUBMIT_KEY")
    if override:
        return _from_override(override)

    path = data_dir / "keybindings.json"
    if not path.exists():
        return KeybindingsResult(submit=SubmitKey(raw="\r", label="enter"))

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return KeybindingsResult(
            submit=SubmitKey(raw="\r", label="enter"),
            reason=f"failed to parse keybindings.json ({exc}); using enter",
        )

    return _resolve_from_payload(payload)


def _from_override(value: str) -> KeybindingsResult:
    normalized = value.strip().lower()
    if normalized in {"enter", "\\r", "\r"}:
        return KeybindingsResult(submit=SubmitKey(raw="\r", label="enter (env)"))
    if normalized in {"meta+enter", "alt+enter", "\\x1b\\r"}:
        return KeybindingsResult(
            submit=SubmitKey(raw="\x1b\r", label="meta+enter (env)")
        )
    return KeybindingsResult(
        submit=None,
        reason=f"CLAUDE_CODE_SUBMIT_KEY={value!r} is not a PTY-sendable key",
    )


def _resolve_from_payload(payload: object) -> KeybindingsResult:
    entries = _iter_entries(payload)

    chat_bindings: list[tuple[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("context") != "Chat":
            continue
        bindings = entry.get("bindings")
        if not isinstance(bindings, list):
            continue
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            key = _normalize_key(binding.get("key"))
            action = str(binding.get("action") or binding.get("command") or "").strip()
            if key and action:
                chat_bindings.append((key, action))

    submit_keys = {
        key for key, action in chat_bindings if _is_submit_action(action)
    }
    newline_keys = {
        key for key, action in chat_bindings if _is_newline_action(action)
    }
    enter_is_newline = "enter" in newline_keys and "enter" not in submit_keys

    if "meta+enter" in submit_keys or "alt+enter" in submit_keys:
        return KeybindingsResult(
            submit=SubmitKey(raw="\x1b\r", label="meta+enter"),
            enter_is_newline=enter_is_newline,
        )
    if "enter" in submit_keys or (not submit_keys and not enter_is_newline):
        # If we saw no chat bindings at all, treat as default enter=submit.
        return KeybindingsResult(
            submit=SubmitKey(raw="\r", label="enter"),
            enter_is_newline=enter_is_newline,
        )

    unsupported = [
        k for k in submit_keys
        if k in {"ctrl+enter", "control+enter", "cmd+enter", "shift+enter"}
    ]
    if unsupported:
        return KeybindingsResult(
            submit=None,
            reason=(
                f"chat:submit is bound to {sorted(unsupported)!r} which a plain PTY "
                "cannot send distinguishably (requires Kitty keyboard protocol). "
                "Set CLAUDE_CODE_SUBMIT_KEY=enter or meta+enter to override."
            ),
            enter_is_newline=enter_is_newline,
        )

    if enter_is_newline:
        return KeybindingsResult(
            submit=None,
            reason=(
                "enter is remapped to chat:newline and no PTY-sendable "
                "chat:submit binding was found."
            ),
            enter_is_newline=enter_is_newline,
        )

    return KeybindingsResult(
        submit=SubmitKey(raw="\r", label="enter"),
        enter_is_newline=enter_is_newline,
    )


def _iter_entries(payload: object) -> list[object]:
    if isinstance(payload, list):
        return list(payload)
    if isinstance(payload, dict):
        entries = payload.get("keybindings")
        if isinstance(entries, list):
            return list(entries)
    return []


def _normalize_key(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip().lower().replace(" ", "")


def _is_submit_action(action: str) -> bool:
    lowered = action.lower()
    return lowered in {"chat:submit", "submit"}


def _is_newline_action(action: str) -> bool:
    lowered = action.lower()
    return lowered in {"chat:newline", "newline"}
