"""Small git helpers for node snapshot metadata and read-only diffs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


MINICLAW_GENERATED_DIR = ".miniclaw2"
MINICLAW_GENERATED_EXCLUDE = f"{MINICLAW_GENERATED_DIR}/"


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
    staging non-framework paths); a string ``error`` indicates a git failure.
    """
    add = _git(
        cwd,
        [
            "add",
            "-A",
            "--",
            ".",
            f":(exclude){MINICLAW_GENERATED_DIR}",
        ],
    )
    if add.returncode != 0:
        return None, add.stderr.strip() or "git add failed"

    unstaged = _unstage_miniclaw_generated(cwd)
    if unstaged is not None:
        return None, unstaged

    staged = _git(cwd, ["diff", "--cached", "--quiet", "--exit-code"])
    if staged.returncode == 0:
        return None, None
    if staged.returncode != 1:
        return None, staged.stderr.strip() or "git diff --cached failed"

    commit = _git(cwd, ["commit", "-m", message])
    if commit.returncode != 0:
        return None, commit.stderr.strip() or "git commit failed"
    return git_head(cwd), None


def ensure_miniclaw_git_excluded(cwd: str) -> str | None:
    """Append MiniClaw2 generated paths to ``.git/info/exclude`` when possible.

    The project root may not be a git repository yet. In that case this is a
    no-op; the commit pathspec in :func:`commit_all` remains the hard guard.
    Returns an error string only for unexpected IO/git failures.
    """
    git_dir = _git(cwd, ["rev-parse", "--git-dir"])
    if git_dir.returncode != 0:
        return None
    exclude_path_result = _git(cwd, ["rev-parse", "--git-path", "info/exclude"])
    if exclude_path_result.returncode != 0:
        return exclude_path_result.stderr.strip() or "git exclude path lookup failed"
    raw_path = exclude_path_result.stdout.strip()
    if not raw_path:
        return "git exclude path lookup returned an empty path"
    exclude_path = Path(raw_path)
    if not exclude_path.is_absolute():
        exclude_path = Path(cwd) / exclude_path
    try:
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        if exclude_path.exists():
            content = exclude_path.read_text(encoding="utf-8")
        else:
            content = ""
        entries = {line.strip() for line in content.splitlines()}
        if MINICLAW_GENERATED_EXCLUDE in entries:
            return None
        prefix = "" if not content or content.endswith("\n") else "\n"
        with exclude_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{prefix}{MINICLAW_GENERATED_EXCLUDE}\n")
    except OSError as exc:
        return str(exc)
    return None


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


def _unstage_miniclaw_generated(cwd: str) -> str | None:
    result = _git(cwd, ["reset", "-q", "--", MINICLAW_GENERATED_DIR])
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        # Git returns an error when the pathspec has never existed in the repo.
        # That is fine; there is simply nothing framework-owned to unstage.
        if "did not match any file" in message:
            return None
        return message or "git reset failed"
    return None
