"""Temporary workspace helper — general feature, first used by test templates.

Creates a fresh git-initialised tempdir to serve as a Project's ``root_path``.
The empty initial commit guarantees that downstream commit-op nodes can
produce real two-commit diffs against it.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


TEMP_PREFIX = "miniclaw2-tmp-"


def create_temporary_root() -> str:
    """Make a fresh tempdir, init git in it, and add an empty initial commit.

    Returns the absolute path. Raises ``RuntimeError`` if git is missing or
    the init sequence fails — callers should treat that as a hard failure;
    a half-initialised workspace would silently break commit-op diffs.
    """
    root = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    try:
        _run(["git", "init", "-q", "--initial-branch=main"], root)
        _run(["git", "config", "user.email", "miniclaw2@local"], root)
        _run(["git", "config", "user.name", "miniclaw2"], root)
        _run(
            ["git", "commit", "-q", "--allow-empty", "-m", "miniclaw:init"],
            root,
        )
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return str(root)


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


def _run(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(
        cmd, cwd=cwd, check=False, capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
