"""Subprocess entrypoint invoked by Claude Code as a hook.

Five behaviors:

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

  The backend's reply decides whether the turn may end. When subagents
  the node dispatched are still running it answers with a ``Stop``
  block directive, which we echo to stdout: Claude keeps the turn open
  and receives ``reason`` as its next instruction. In that case the
  backend has *not* recorded turn-complete, so the two sides agree the
  turn is still live.

  ``background_tasks`` travels with the signal for that decision. It is
  the CLI's own parent-scoped registry of in-flight work, sampled at
  the moment the turn wants to end, which makes it a better answer to
  "is anything still running" than a ledger accumulated from earlier
  hook calls.

- ``python -m miniclaw2.claude_hook_bridge --subagent-start`` and
  ``--subagent-stop`` — POST the payload's ``agent_id`` to
  ``/hook/subagent`` so the backend can track which subagents the node
  has outstanding. Output is never used; these hooks only observe.

  Both forward the payload's ``session_id`` for the same reason
  ``--turn-complete`` does: a nested ``claude`` inherits
  ``MINICLAW_NODE_ID``, so without it a descendant's subagents land in
  this node's ledger and hold its turn open on a stranger's behalf.

  ``--subagent-stop`` also forwards ``background_tasks``, the CLI's own
  parent-scoped registry of in-flight work. ``SubagentStop`` does not
  mean the subagent is finished: it fires whenever the subagent yields —
  after backgrounding a shell command, for instance — and the same
  ``agent_id`` starts again when the subagent is woken to finish. At
  that first stop the payload still lists the agent as ``running``, so
  the snapshot is what says whether it is really done.

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
_SUBAGENT_TIMEOUT_SECONDS = 10


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if "--turn-complete" in args:
        return _post_turn_complete()
    if "--session-ready" in args:
        return _post_session_ready()
    if "--subagent-start" in args:
        return _post_subagent("start")
    if "--subagent-stop" in args:
        return _post_subagent("stop")
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
    stop_hook_active = False
    running_agent_ids: list[str] | None = None
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
        stop_hook_active = bool(payload.get("stop_hook_active"))
        running_agent_ids = _running_subagent_ids(payload)

    node_id = os.environ.get("MINICLAW_NODE_ID")
    token = os.environ.get("MINICLAW_HOOK_TOKEN")
    url = _derive_hook_url("turn-complete")
    if not (node_id and token and url):
        return 0

    signal: dict[str, object] = {"node_id": node_id}
    if session_id:
        signal["session_id"] = session_id
    signal["stop_hook_active"] = stop_hook_active
    signal["running_agent_ids"] = running_agent_ids
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
        with urlrequest.urlopen(req, timeout=_TURN_COMPLETE_TIMEOUT_SECONDS) as resp:
            resp_body = resp.read()
    except (urlerror.URLError, TimeoutError, OSError):
        # Fail open: let the turn end. Holding it open on a failed request
        # would strand the node, and the backend has its own stall timeout.
        return 0

    directive = _stop_directive(resp_body)
    if directive is not None:
        sys.stdout.write(json.dumps(directive))
    return 0


def _stop_directive(resp_body: bytes) -> dict[str, object] | None:
    """Extract a ``Stop`` block directive from the backend's reply.

    Only a well-formed block is echoed. Anything else — an ordinary
    acknowledgement, a malformed body, a block with no reason — lets the
    turn end, because Claude requires ``reason`` whenever ``decision`` is
    ``"block"`` and a half-formed directive is worse than none.
    """
    try:
        data = json.loads(resp_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("decision") != "block":
        return None
    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return None
    return {"decision": "block", "reason": reason}


def _post_subagent(phase: str) -> int:
    """Report a ``SubagentStart``/``SubagentStop`` to the backend's ledger.

    Purely observational: the return value is ignored, and any failure is
    silent. ``agent_id`` comes from the payload — the CLI's own identifier
    for the subagent — so start and stop pair up without transcript
    parsing.

    ``session_id`` is forwarded so the backend can reject a descendant's
    subagents, and on stop so is ``background_tasks``: that array is the
    CLI's parent-scoped registry of in-flight work, and it is the only
    thing that distinguishes a subagent that has finished from one that
    merely yielded and will be resumed under the same ``agent_id``.
    """
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        return 0
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    agent_id = payload.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        return 0
    agent_type = payload.get("agent_type")
    session_id = payload.get("session_id")

    node_id = os.environ.get("MINICLAW_NODE_ID")
    token = os.environ.get("MINICLAW_HOOK_TOKEN")
    url = _derive_hook_url("subagent")
    if not (node_id and token and url):
        return 0

    signal: dict[str, object] = {
        "node_id": node_id,
        "phase": phase,
        "agent_id": agent_id,
        "agent_type": agent_type if isinstance(agent_type, str) else "",
    }
    if isinstance(session_id, str) and session_id:
        signal["session_id"] = session_id
    if phase == "stop":
        signal["running_agent_ids"] = _running_subagent_ids(payload)
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
        urlrequest.urlopen(req, timeout=_SUBAGENT_TIMEOUT_SECONDS).close()
    except (urlerror.URLError, TimeoutError, OSError):
        pass
    return 0


def _running_subagent_ids(payload: dict) -> list[str] | None:
    """Ids of subagents the payload still reports as in flight.

    ``None`` means the payload carried no usable ``background_tasks``
    array — the CLI documents it as present only when the task registry
    is reachable — and the backend then falls back to retiring the id
    that stopped. An empty list is a real answer, not a missing one: it
    says the registry was reachable and nothing is running.

    Only ``type == "subagent"`` entries are collected. A ``shell`` task
    left running is an ordinary pattern (a spawned server, a tail) and
    must not hold the node's turn open.
    """
    tasks = payload.get("background_tasks")
    if not isinstance(tasks, list):
        return None
    running: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if task.get("type") != "subagent":
            continue
        task_id = task.get("id")
        if isinstance(task_id, str) and task_id:
            running.append(task_id)
    return running


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
