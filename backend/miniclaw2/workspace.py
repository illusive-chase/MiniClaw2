"""Temporary workspace helpers.

Temporary projects use an OS temporary directory as an execution cache.  The
directory is deliberately not a repository: ephemeral sessions must not gain
Git or host binding state as a side effect of creation.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


TEMP_PREFIX = "miniclaw2-tmp-"


def create_temporary_root() -> str:
    """Make and return a fresh, non-Git temporary execution directory."""
    return tempfile.mkdtemp(prefix=TEMP_PREFIX)


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
