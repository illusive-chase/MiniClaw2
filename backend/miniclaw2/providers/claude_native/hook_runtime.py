"""Process-wide runtime state shared between ``ClaudeNativeSession`` and
the FastAPI ``/hook/*`` endpoints.

Each ``ClaudeNativeSession`` registers:

- an async ``ask_dispatch`` callback keyed by node id — the hook endpoint
  invokes it to route a Claude ``AskUserQuestion`` payload through
  ``request_gate_handler`` and back;
- a ``session_ready`` event keyed by session id — the hook endpoint sets
  it when Claude Code's ``SessionStart`` hook fires.
- a ``turn_complete`` event keyed by node id — the hook endpoint sets it
  when Claude Code's ``Stop`` hook fires, but only when the signal proves
  it came from the session that node's PTY owns (see
  ``register_turn_complete``).

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
class _TurnCompleteSlot:
    """One node's turn-complete waiter plus the identities it will accept.

    ``owned`` holds the session ids the node's own PTY has run under
    (more than one only when the child rotates its session id mid-turn).
    ``signaled`` holds every session id that has announced ``Stop`` for
    this node. Matching the two sets — rather than setting the event on
    any arrival — is what keeps a descendant process from ending the
    node's turn.

    Signals are retained rather than accepted-or-discarded because a
    rejected signal is never re-sent: if the node's PTY rotates its
    session id and MiniClaw2 learns the new id only after the ``Stop``
    hook already fired, the recorded signal still matches once
    ``claim_turn_complete_session`` registers that id.
    """

    event: asyncio.Event
    owned: set[str] = field(default_factory=set)
    signaled: set[str] = field(default_factory=set)


@dataclass(slots=True)
class _State:
    token: str = ""
    port: int = 0
    ask_dispatchers: dict[str, AskDispatch] = field(default_factory=dict)
    session_ready_events: dict[str, asyncio.Event] = field(default_factory=dict)
    turn_complete_slots: dict[str, _TurnCompleteSlot] = field(default_factory=dict)


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


def unregister_ask_dispatcher(
    node_id: str, dispatcher: AskDispatch | None = None
) -> None:
    """Release ``node_id``'s slot, but only if ``dispatcher`` still holds it.

    Without the identity check a superseded session's late teardown drops
    the live session's dispatcher, and the next ``AskUserQuestion`` 404s
    into the bridge's passthrough — leaving Claude waiting on a native TUI
    prompt nobody can answer.
    """
    if dispatcher is not None and _STATE.ask_dispatchers.get(node_id) is not dispatcher:
        return
    _STATE.ask_dispatchers.pop(node_id, None)


def get_ask_dispatcher(node_id: str) -> AskDispatch | None:
    return _STATE.ask_dispatchers.get(node_id)


def register_session_ready(session_id: str) -> asyncio.Event:
    """Claim the session-ready slot for ``session_id`` with a fresh event.

    A resumed turn reuses the session id, so returning a previous turn's
    already-set event would let ``start()`` fall through before the new
    child has run its ``SessionStart`` hook.
    """
    event = asyncio.Event()
    _STATE.session_ready_events[session_id] = event
    return event


def unregister_session_ready(
    session_id: str, event: asyncio.Event | None = None
) -> None:
    """Release ``session_id``'s slot, but only if ``event`` still holds it."""
    if event is not None and _STATE.session_ready_events.get(session_id) is not event:
        return
    _STATE.session_ready_events.pop(session_id, None)


def signal_session_ready(session_id: str) -> bool:
    event = _STATE.session_ready_events.get(session_id)
    if event is None:
        return False
    if not event.is_set():
        event.set()
    return True


def register_turn_complete(node_id: str, session_id: str | None = None) -> asyncio.Event:
    """Claim the turn-complete slot for ``node_id`` with a fresh event.

    A superseded session's teardown is finalized late — after the next
    turn on the same node has already claimed the slot — so each
    registration must be a distinct object that only its own owner can
    release. Reusing one event per node would let the outgoing turn
    clear a signal the incoming turn is still waiting for.

    ``session_id`` is the session this node's PTY was spawned under, and
    only a ``Stop`` signal carrying it will set the event. Without that
    check the credential is ``MINICLAW_NODE_ID`` alone, which every
    descendant of the PTY child inherits: a nested ``claude`` session
    launched from a Bash tool call would end its parent's turn on exit,
    stranding the parent's in-flight tool calls. The env var says which
    node to signal; the payload's session id proves who is entitled to.
    """
    slot = _TurnCompleteSlot(event=asyncio.Event())
    _STATE.turn_complete_slots[node_id] = slot
    if session_id:
        claim_turn_complete_session(node_id, session_id, slot.event)
    return slot.event


def claim_turn_complete_session(
    node_id: str,
    session_id: str,
    event: asyncio.Event | None = None,
) -> None:
    """Record ``session_id`` as belonging to ``node_id``'s own PTY.

    Called again when the child rotates its session id mid-turn, so a
    ``Stop`` under the new id is still recognized as the node's own. If
    that id already announced ``Stop`` while it was unknown to us, the
    retained signal is honored now rather than lost.
    """
    slot = _STATE.turn_complete_slots.get(node_id)
    if slot is None:
        return
    if event is not None and slot.event is not event:
        return
    if not session_id:
        return
    slot.owned.add(session_id)
    if session_id in slot.signaled and not slot.event.is_set():
        slot.event.set()


def unregister_turn_complete(
    node_id: str, event: asyncio.Event | None = None
) -> None:
    """Release ``node_id``'s slot, but only if ``event`` still holds it."""
    slot = _STATE.turn_complete_slots.get(node_id)
    if slot is None:
        return
    if event is not None and slot.event is not event:
        return
    _STATE.turn_complete_slots.pop(node_id, None)


def signal_turn_complete(node_id: str, session_id: str | None = None) -> bool:
    """Accept a ``Stop`` signal for ``node_id`` if it proves its identity.

    Returns whether the signal was attributed to a registered slot — not
    whether the turn was ended. A signal from an unrecognized session is
    recorded (its id may be claimed moments later, see
    ``claim_turn_complete_session``) but never sets the event: this is
    fail-closed, so an unproven claim is dropped rather than trusted.

    A signal with no session id at all cannot be attributed to either the
    node's PTY or a descendant, so it is also refused. Missing proof is
    treated as failed proof.
    """
    slot = _STATE.turn_complete_slots.get(node_id)
    if slot is None:
        return False
    if not session_id:
        return False
    slot.signaled.add(session_id)
    if session_id not in slot.owned:
        return False
    if not slot.event.is_set():
        slot.event.set()
    return True
