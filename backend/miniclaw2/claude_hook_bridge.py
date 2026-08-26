"""Subprocess entrypoint invoked by Claude Code as a hook.

Three behaviors:

- ``python -m miniclaw2.claude_hook_bridge --session-ready`` — POSTs
  ``{"session_id": <MINICLAW_SESSION_ID>}`` to ``/hook/session-ready``
  so ``ClaudeNativeSession.start()`` can unblock.

- ``python -m miniclaw2.claude_hook_bridge --turn-complete`` — POSTs
  ``{"node_id": <MINICLAW_NODE_ID>, "session_id": <payload session_id>}``
  to ``/hook/turn-complete`` when Claude Code's ``Stop`` hook fires.

  ``session_id`` comes from the hook payload, never from the environment,
  and that distinction is the whole point. ``MINICLAW_NODE_ID`` is
  inherited by every descendant of the PTY child, so a nested ``claude``
  session started from a Bash tool call used to end its parent's turn:
  its ``Stop`` hook announced the *parent's* node id, the backend
  collected the stream, and the parent lost every tool call still in
  flight. The payload's ``session_id`` is assigned by the CLI process
  that is actually stopping, so the backend can tell the node's own PTY
  from one of its descendants.

- ``python -m miniclaw2.claude_hook_bridge`` (no flag) — reads the
  Claude ``PreToolUse`` payload from stdin, POSTs it to ``/hook/ask``
  along with the ``MINICLAW_NODE_ID``, and echoes the returned directive
  as JSON to stdout. On any failure we print nothing (Claude falls back
  to its native TUI prompt — this is the passthrough invariant, matching
  botmux's ``src/core/ask-hook/claude-code.ts``).

Never invent an empty ``allow`` — that submits an empty answer to the tool.
"""

from __future__ import annotations

import json
import os
import sys
from urllib import error as urlerror
from urllib import request as urlrequest


_READY_TIMEOUT_SECONDS = 10
_TURN_COMPLETE_TIMEOUT_SECONDS = 10


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if "--turn-complete" in args:
        return _post_turn_complete()
    if "--session-ready" in args:
        return _post_session_ready()
    return _handle_ask()


def _handle_ask() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        return _passthrough()
    if not raw.strip():
        return _passthrough()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _passthrough()

    if payload.get("hook_event_name") != "PreToolUse":
        return _passthrough()
    if payload.get("tool_name") != "AskUserQuestion":
        return _passthrough()

    url = os.environ.get("MINICLAW_HOOK_URL")
    token = os.environ.get("MINICLAW_HOOK_TOKEN")
    node = os.environ.get("MINICLAW_NODE_ID")
    if not (url and token and node):
        return _passthrough()

    body = json.dumps({"node_id": node, "payload": payload}).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=None) as resp:
            resp_body = resp.read()
    except (urlerror.URLError, TimeoutError, OSError):
        return _passthrough()

    try:
        directive = json.loads(resp_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _passthrough()
    if not isinstance(directive, dict):
        return _passthrough()

    sys.stdout.write(json.dumps(directive))
    return 0


def _post_session_ready() -> int:
    session_id = os.environ.get("MINICLAW_SESSION_ID")
    token = os.environ.get("MINICLAW_HOOK_TOKEN")
    url_base = _derive_ready_url()
    if not (session_id and token and url_base):
        return 0

    body = json.dumps({"session_id": session_id}).encode("utf-8")
    req = urlrequest.Request(
        url_base,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        urlrequest.urlopen(req, timeout=_READY_TIMEOUT_SECONDS).close()
    except (urlerror.URLError, TimeoutError, OSError):
        pass
    return 0


def _post_turn_complete() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        return 0
    session_id: str | None = None
    if raw.strip():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return 0
        if payload.get("hook_event_name") != "Stop":
            return 0
        claimed = payload.get("session_id")
        if isinstance(claimed, str) and claimed:
            session_id = claimed

    node_id = os.environ.get("MINICLAW_NODE_ID")
    token = os.environ.get("MINICLAW_HOOK_TOKEN")
    url = _derive_hook_url("turn-complete")
    if not (node_id and token and url):
        return 0

    signal: dict[str, str] = {"node_id": node_id}
    if session_id:
        signal["session_id"] = session_id
    body = json.dumps(signal).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        urlrequest.urlopen(req, timeout=_TURN_COMPLETE_TIMEOUT_SECONDS).close()
    except (urlerror.URLError, TimeoutError, OSError):
        pass
    return 0


def _derive_ready_url() -> str | None:
    return _derive_hook_url("session-ready")


def _derive_hook_url(endpoint: str) -> str | None:
    ask_url = os.environ.get("MINICLAW_HOOK_URL")
    if not ask_url:
        return None
    if ask_url.endswith("/hook/ask"):
        return ask_url[: -len("/hook/ask")] + f"/hook/{endpoint}"
    if "/hook/" in ask_url:
        base, _sep, _tail = ask_url.rpartition("/hook/")
        return base + f"/hook/{endpoint}"
    return ask_url.rstrip("/") + f"/hook/{endpoint}"


def _passthrough() -> int:
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
