"""Path helpers for Claude Code's on-disk layout.

The JSONL path uses ``realpath(cwd)`` — symlinked project roots would
otherwise write to a project-hash we never watch.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def project_hash(cwd: str) -> str:
    resolved = os.path.realpath(cwd)
    return re.sub(r"[^A-Za-z0-9-]", "-", resolved)


def jsonl_path(cwd: str, session_id: str, data_dir: Path) -> Path:
    return data_dir / "projects" / project_hash(cwd) / f"{session_id}.jsonl"


def project_dir(cwd: str, data_dir: Path) -> Path:
    return data_dir / "projects" / project_hash(cwd)


def pid_state_path(pid: int, data_dir: Path) -> Path:
    return data_dir / "sessions" / f"{pid}.json"


def default_data_dir() -> Path:
    return Path.home() / ".claude"
