"""Process-wide runtime state shared between ``ClaudeNativeSession`` and
the FastAPI ``/hook/*`` endpoints.

Each ``ClaudeNativeSession`` registers:

- an async ``ask_dispatch`` callback keyed by node id — the hook endpoint
  invokes it to route a Claude ``AskUserQuestion`` payload through
  ``request_gate_handler`` and back;
- a ``session_ready`` event keyed by session id — the hook endpoint sets
  it when Claude Code's ``SessionStart`` hook fires.

The token is generated on first access via ``secrets.token_urlsafe`` and
kept in memory for the daemon's lifetime.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


AskDispatch = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class _State:
    token: str = ""
    port: int = 0
    ask_dispatchers: dict[str, AskDispatch] = field(default_factory=dict)
    session_ready_events: dict[str, asyncio.Event] = field(default_factory=dict)


_STATE = _State()


def ensure_token() -> str:
    if not _STATE.token:
        override = os.environ.get("MINICLAW_HOOK_TOKEN")
        _STATE.token = override or secrets.token_urlsafe(32)
    return _STATE.token


def token() -> str:
    return ensure_token()


def set_port(port: int) -> None:
    _STATE.port = int(port)


def get_port() -> int:
    if _STATE.port:
        return _STATE.port
    env = os.environ.get("MINICLAW2_HOOK_PORT") or os.environ.get("MINICLAW2_PORT")
    if env:
        try:
            _STATE.port = int(env)
        except ValueError:
            pass
    if not _STATE.port:
        _STATE.port = 8000
    return _STATE.port


def hook_url_base() -> str:
    return f"http://127.0.0.1:{get_port()}"


def ask_url() -> str:
    return f"{hook_url_base()}/hook/ask"


def register_ask_dispatcher(node_id: str, dispatcher: AskDispatch) -> None:
    _STATE.ask_dispatchers[node_id] = dispatcher


def unregister_ask_dispatcher(node_id: str) -> None:
    _STATE.ask_dispatchers.pop(node_id, None)


def get_ask_dispatcher(node_id: str) -> AskDispatch | None:
    return _STATE.ask_dispatchers.get(node_id)


def register_session_ready(session_id: str) -> asyncio.Event:
    event = _STATE.session_ready_events.get(session_id)
    if event is None:
        event = asyncio.Event()
        _STATE.session_ready_events[session_id] = event
    return event


def unregister_session_ready(session_id: str) -> None:
    _STATE.session_ready_events.pop(session_id, None)


def signal_session_ready(session_id: str) -> bool:
    event = _STATE.session_ready_events.get(session_id)
    if event is None:
        return False
    if not event.is_set():
        event.set()
    return True
