"""Resolve the current Claude Code session id from the ``sessions/<pid>.json`` state file.

Port of botmux's ``resolveJsonlFromPid``. Session-id rotation happens
when the user runs ``/clear`` in the TUI or when Claude Code writes a
fresh pid-state after resume. MiniClaw2 nodes each spawn a fresh
process, so rotation only matters on explicit ``--resume`` — but we
guard against it anyway.

On Linux the resolver cross-checks ``/proc/<pid>/stat`` starttime
against the ``procStart`` field written by Claude Code. On macOS
``procStart`` is skipped and ``realpath(cwd)`` equality is the only guard.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .paths import jsonl_path, pid_state_path


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass(slots=True)
class ResolvedSession:
    session_id: str
    jsonl_path: Path


def resolve_from_pid(
    pid: int,
    expected_cwd: str,
    data_dir: Path,
) -> ResolvedSession | None:
    path = pid_state_path(pid, data_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("pid") != pid:
        return None
    sid = data.get("sessionId")
    if not isinstance(sid, str) or not _UUID_RE.match(sid):
        return None
    cwd = data.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return None

    proc_start = data.get("procStart")
    if isinstance(proc_start, str):
        live = _read_proc_starttime(pid)
        if live is None and sys.platform == "linux":
            return None
        if live is not None and live != proc_start:
            return None

    if os.path.realpath(cwd) != os.path.realpath(expected_cwd):
        return None

    return ResolvedSession(
        session_id=sid,
        jsonl_path=jsonl_path(cwd, sid, data_dir),
    )


def _read_proc_starttime(pid: int) -> str | None:
    """Return the raw starttime string from ``/proc/<pid>/stat`` (Linux only)."""
    if sys.platform != "linux":
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    # /proc/<pid>/stat has the comm field enclosed in parens which may
    # contain spaces; split on the last ')' to safely index field 22
    # (0-indexed: 21) which is starttime.
    close = raw.rfind(")")
    if close < 0:
        return None
    tail = raw[close + 1 :].split()
    # After the comm field, index 19 in ``tail`` corresponds to overall
    # field 22 (starttime).
    if len(tail) < 20:
        return None
    return tail[19]
