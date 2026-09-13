"""Temporary workspace helpers.

Temporary projects use an OS temporary directory as an execution cache.  The
directory is deliberately not a repository: ephemeral sessions must not gain
Git or host binding state as a side effect of creation.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .git_state import is_git_repo


TEMP_PREFIX = "miniclaw2-tmp-"


def create_temporary_root() -> str:
    """Make and return a fresh, non-Git temporary execution directory."""
    for directory in dict.fromkeys([tempfile.gettempdir(), "/tmp", "/var/tmp"]):
        if not Path(directory).is_dir():
            continue
        try:
            root = tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=directory)
        except OSError:
            continue
        if not is_git_repo(root):
            return root
        remove_temporary_root(root)
    raise ValueError("无法创建独立于 Git 仓库的临时执行目录")


def remove_temporary_root(path: str) -> None:
    """Best-effort rmtree of a temporary workspace. Guards against deleting
    paths that don't carry the temp prefix (defensive — callers should only
    pass paths returned by :func:`create_temporary_root`).
    """
    target = Path(path)
    if not target.exists():
        return
    if TEMP_PREFIX not in target.name:
        return
    shutil.rmtree(target, ignore_errors=True)
