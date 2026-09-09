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
- a subagent ledger keyed by node id — ``SubagentStart`` adds an agent
  id, ``SubagentStop`` removes it. A node's turn must not end, and must
  not suspend in ``AskUserQuestion``, while that ledger is non-empty
  (see ``_SubagentLedger``).

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
class _SubagentLedger:
    """One node's set of dispatched-but-unreturned subagents.

    The ``Agent`` tool is always asynchronous inside a node: its result
    arrives as a queued notification, never as the tool's return value.
    Two things must therefore not happen while a subagent is still
    running — the node must not suspend in ``AskUserQuestion`` (the
    completion notification and the user's answer then fork the
    conversation, and the CLI can follow the notification branch and
    synthesize a reply without calling the model at all), and the node
    must not end its turn (the subagent's work is discarded when the
    node is reaped).

    ``SubagentStart``/``SubagentStop`` are the CLI's own lifecycle
    events, so membership needs no transcript parsing. ``blocks`` counts
    how many times ``Stop`` has already been refused for this node, so a
    subagent that never returns cannot hold the turn open forever.
    """

    running: dict[str, str] = field(default_factory=dict)
    blocks: int = 0
    abandoned: str = ""


@dataclass(slots=True)
class _State:
    token: str = ""
    port: int = 0
    ask_dispatchers: dict[str, AskDispatch] = field(default_factory=dict)
    session_ready_events: dict[str, asyncio.Event] = field(default_factory=dict)
    turn_complete_slots: dict[str, _TurnCompleteSlot] = field(default_factory=dict)
    subagent_ledgers: dict[str, _SubagentLedger] = field(default_factory=dict)


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


# ---- subagent ledger -----------------------------------------------------

# How many times ``Stop`` may be refused for one node before the turn is
# allowed to end anyway. A subagent that never returns must not strand the
# node: past this budget MiniClaw2 accepts the turn and records the loss
# instead. Kept below Claude Code's own cap of 8 consecutive continuations
# so the decision stays ours — once the CLI overrides the hook, it ends the
# turn without telling us, and the node would wait out the stall timeout.
_MAX_STOP_BLOCKS = 5


def reset_subagent_ledger(node_id: str) -> None:
    """Clear ``node_id``'s ledger at the start of a turn.

    Each turn is a fresh ``claude --resume`` process, so subagents from a
    previous turn are already gone. Carrying their ids over would refuse a
    turn that has nothing outstanding.
    """
    _STATE.subagent_ledgers.pop(node_id, None)


def record_subagent_start(node_id: str, agent_id: str, agent_type: str = "") -> None:
    if not node_id or not agent_id:
        return
    ledger = _STATE.subagent_ledgers.setdefault(node_id, _SubagentLedger())
    ledger.running[agent_id] = agent_type


def record_subagent_stop(node_id: str, agent_id: str) -> None:
    """Retire one subagent.

    A ``SubagentStop`` for an unknown id is ignored rather than treated as
    an error: the ledger only has to end up empty, and an id we never saw
    start is already absent.
    """
    if not node_id or not agent_id:
        return
    ledger = _STATE.subagent_ledgers.get(node_id)
    if ledger is None:
        return
    ledger.running.pop(agent_id, None)


def running_subagents(node_id: str) -> list[str]:
    """Agent types still running for ``node_id``, for a human-readable reason."""
    ledger = _STATE.subagent_ledgers.get(node_id)
    if ledger is None:
        return []
    return [agent_type or agent_id for agent_id, agent_type in ledger.running.items()]


def has_running_subagents(node_id: str) -> bool:
    ledger = _STATE.subagent_ledgers.get(node_id)
    return bool(ledger and ledger.running)


def should_block_stop(node_id: str) -> bool:
    """Whether this ``Stop`` must be refused so the node waits.

    Consumes one unit of the block budget when it returns True, so the
    caller must ask exactly once per ``Stop``. Returning False when
    subagents are still running is the deliberate give-up path: the turn
    ends and the caller records what was lost.
    """
    ledger = _STATE.subagent_ledgers.get(node_id)
    if ledger is None or not ledger.running:
        return False
    if ledger.blocks >= _MAX_STOP_BLOCKS:
        return False
    ledger.blocks += 1
    return True


def is_owned_session(node_id: str, session_id: str) -> bool:
    """Whether ``session_id`` is this node's own PTY rather than a descendant.

    The subagent checks consult this first so a nested ``claude`` session's
    ``Stop`` can neither hold the node's turn open nor spend its budget.
    """
    slot = _STATE.turn_complete_slots.get(node_id)
    if slot is None:
        return False
    return session_id in slot.owned


def note_abandoned_subagents(node_id: str, note: str) -> None:
    """Record that the turn ended with subagents still running.

    Held on the ledger rather than written to the node here: the hook
    route has no node record, and the runner reads this when the turn's
    stream closes.
    """
    ledger = _STATE.subagent_ledgers.get(node_id)
    if ledger is None:
        return
    ledger.abandoned = note


def abandoned_subagent_note(node_id: str) -> str:
    ledger = _STATE.subagent_ledgers.get(node_id)
    return ledger.abandoned if ledger is not None else ""
