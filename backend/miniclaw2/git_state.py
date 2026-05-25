"""Small git helpers for node snapshot metadata and read-only diffs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(slots=True)
class GitDiff:
    kind: str
    text: str
    error: str | None = None


def git_head(cwd: str) -> str | None:
    result = _git(cwd, ["rev-parse", "HEAD"])
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return text or None


def commit_all(cwd: str, message: str) -> tuple[str | None, str | None]:
    """Stage every change and commit. Returns ``(new_head, error)``.

    ``(None, None)`` means there was nothing to commit (clean tree after
    ``git add -A``); a string ``error`` indicates a git failure.
    """
    add = _git(cwd, ["add", "-A"])
    if add.returncode != 0:
        return None, add.stderr.strip() or "git add failed"

    status = _git(cwd, ["status", "--porcelain"])
    if status.returncode != 0:
        return None, status.stderr.strip() or "git status failed"
    if not status.stdout.strip():
        return None, None

    commit = _git(cwd, ["commit", "-m", message])
    if commit.returncode != 0:
        return None, commit.stderr.strip() or "git commit failed"
    return git_head(cwd), None


def node_diff(cwd: str, before: str | None, after: str | None) -> GitDiff:
    if before and after and before != after:
        result = _git(cwd, ["diff", "--no-ext-diff", before, after])
        return _diff_result("commit_diff", result)
    if before:
        result = _git(cwd, ["diff", "--no-ext-diff", before])
        return _diff_result("working_tree", result)
    result = _git(cwd, ["diff", "--no-ext-diff"])
    return _diff_result("working_tree", result)


def _diff_result(kind: str, result: subprocess.CompletedProcess[str]) -> GitDiff:
    if result.returncode != 0:
        return GitDiff(kind=kind, text="", error=result.stderr.strip() or "git diff failed")
    return GitDiff(kind=kind, text=result.stdout)


def _git(cwd: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=1,
            stdout="",
            stderr=str(exc),
        )
