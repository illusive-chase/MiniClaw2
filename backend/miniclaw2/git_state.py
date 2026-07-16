"""Git helpers used by node snapshots and the project-level Git surface."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path


MINICLAW_GENERATED_DIR = ".miniclaw2"
MINICLAW_GENERATED_EXCLUDE = f"{MINICLAW_GENERATED_DIR}/"

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GitDiff:
    kind: str
    text: str
    error: str | None = None


@dataclass(slots=True)
class GitStatus:
    is_repo: bool = False
    head: str | None = None
    branch: str | None = None
    detached: bool = False
    upstream: str | None = None
    ahead: int | None = None
    behind: int | None = None
    dirty_count: int = 0


@dataclass(slots=True)
class CommitDescriptor:
    sha: str
    live: bool
    message: str
    ts: float | None = None
    external_count_before: int = 0
    aliases: list[str] | None = None


def git_status(cwd: str) -> GitStatus:
    """Return branch, upstream and worktree state in one porcelain call."""
    result = _git(cwd, ["status", "--porcelain=v2", "--branch", "--untracked-files=all"])
    if result.returncode != 0:
        return GitStatus()
    status = GitStatus(is_repo=True)
    for line in result.stdout.splitlines():
        if line.startswith("# branch.head "):
            branch = line.removeprefix("# branch.head ").strip()
            status.branch = None if branch in {"(detached)", ""} else branch
            status.detached = branch == "(detached)"
        elif line.startswith("# branch.oid "):
            oid = line.removeprefix("# branch.oid ").strip()
            status.head = None if oid in {"(initial)", ""} else oid
        elif line.startswith("# branch.upstream "):
            status.upstream = line.removeprefix("# branch.upstream ").strip() or None
        elif line.startswith("# branch.ab "):
            parts = line.removeprefix("# branch.ab ").split()
            if len(parts) == 2:
                try:
                    status.ahead = int(parts[0].removeprefix("+"))
                    status.behind = int(parts[1].removeprefix("-"))
                except ValueError:
                    pass
        elif line and not line.startswith("#"):
            # Porcelain v2 ordinary/untracked/renamed records all carry a path.
            if line.startswith(("? ", "! ")):
                path = line[2:]
            elif line.startswith("1 "):
                path = line.split(" ", 8)[-1]
            elif line.startswith("2 "):
                path = line.split(" ", 9)[-1].split("\t", 1)[0]
            else:
                path = line.rsplit(" ", 1)[-1]
            if path == MINICLAW_GENERATED_DIR or path.startswith(MINICLAW_GENERATED_EXCLUDE):
                continue
            status.dirty_count += 1
    return status


def _rebase_in_progress(cwd: str) -> bool:
    result = _git(
        cwd,
        [
            "rev-parse",
            "--git-path",
            "rebase-merge",
            "--git-path",
            "rebase-apply",
        ],
    )
    if result.returncode != 0:
        return False
    for raw_path in result.stdout.splitlines():
        path = Path(raw_path.strip())
        if not raw_path.strip():
            continue
        if not path.is_absolute():
            path = Path(cwd) / path
        if path.exists():
            return True
    return False


def git_pull_rebase(cwd: str) -> tuple[str | None, str | None]:
    """Pull with rebase without disturbing a pre-existing rebase."""
    if _rebase_in_progress(cwd):
        return (
            git_head(cwd),
            "a rebase is already in progress in the worktree; resolve or abort it first",
        )
    result = _git(cwd, ["pull", "--rebase"], timeout=120)
    if result.returncode == 0:
        return git_head(cwd), None
    conflicts = _git(cwd, ["diff", "--name-only", "--diff-filter=U"], timeout=30)
    if _rebase_in_progress(cwd):
        _git(cwd, ["rebase", "--abort"], timeout=30)
    detail = result.stderr.strip() or result.stdout.strip() or "git pull --rebase failed"
    if conflicts.stdout.strip():
        detail = f"{detail}; conflicting files: {', '.join(conflicts.stdout.splitlines())}"
    return git_head(cwd), detail


def git_push(cwd: str) -> tuple[GitStatus, str | None]:
    result = _git(cwd, ["push"], timeout=120)
    if result.returncode != 0:
        return git_status(cwd), result.stderr.strip() or result.stdout.strip() or "git push failed"
    return git_status(cwd), None


def local_only_shas(cwd: str) -> list[str] | None:
    """Return oldest-first commits ahead of upstream, or None without upstream."""
    upstream = _git(cwd, ["rev-parse", "--abbrev-ref", "@{upstream}"])
    if upstream.returncode != 0:
        return None
    result = _git(cwd, ["rev-list", "--reverse", "@{upstream}..HEAD"], timeout=30)
    if result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def commit_graph(
    cwd: str,
    referenced_shas: set[str] | list[str],
    alias_map: dict[str, str] | None = None,
    ref_timestamps: dict[str, float] | None = None,
    status: GitStatus | None = None,
) -> list[CommitDescriptor]:
    """Derive referenced commit hubs from Git history without storing mirrors."""
    aliases = alias_map or {}
    def resolve(sha: str) -> str:
        seen: set[str] = set()
        while sha in aliases and sha not in seen:
            seen.add(sha)
            sha = aliases[sha]
        return sha
    refs = {resolve(sha) for sha in referenced_shas if sha}
    current = status or git_status(cwd)
    if current.head:
        refs.add(resolve(current.head))
    if not current.is_repo or not current.head:
        return [
            CommitDescriptor(
                sha=sha,
                live=False,
                message="",
                aliases=sorted(
                    old
                    for old in referenced_shas
                    if resolve(old) == sha and old != sha
                ),
            )
            for sha in sorted(refs)
        ]
    rev = _git(
        cwd,
        [
            "rev-list",
            "--topo-order",
            "--no-commit-header",
            "--format=%H%x00%s%x00%ct",
            current.head,
        ],
        timeout=30,
    )
    history: dict[str, tuple[int, str, float | None]] = {}
    newest_first: list[str] = []
    if rev.returncode == 0:
        for line in rev.stdout.splitlines():
            fields = line.split("\x00")
            if len(fields) != 3:
                continue
            sha, message, raw_ts = fields
            try:
                ts = float(raw_ts)
            except ValueError:
                ts = None
            newest_first.append(sha)
            history[sha] = (0, message, ts)
    else:
        logger.warning(
            "failed to load git commit graph for %s: %s",
            cwd,
            rev.stderr.strip() or rev.stdout.strip() or "git rev-list failed",
        )
    live_order = list(reversed(newest_first))
    history = {
        sha: (index, history[sha][1], history[sha][2])
        for index, sha in enumerate(live_order)
    }
    live_set = set(live_order)
    ordered_refs = [sha for sha in live_order if sha in refs]
    stale = [sha for sha in refs if sha not in live_set]
    resolved_timestamps: dict[str, float] = {}
    for sha, ts in (ref_timestamps or {}).items():
        resolved = resolve(sha)
        current_ts = resolved_timestamps.get(resolved)
        if current_ts is None or ts < current_ts:
            resolved_timestamps[resolved] = ts
    stale_buckets: list[list[str]] = [[] for _ in range(len(ordered_refs) + 1)]
    for sha in stale:
        stale_ts = resolved_timestamps.get(sha)
        insertion = len(ordered_refs)
        if stale_ts is not None:
            for index, live_sha in enumerate(ordered_refs):
                live_ts = history[live_sha][2]
                if live_ts is not None and live_ts > stale_ts:
                    insertion = index
                    break
        stale_buckets[insertion].append(sha)
    ordered: list[str] = []
    for index, live_sha in enumerate(ordered_refs):
        ordered.extend(sorted(stale_buckets[index]))
        ordered.append(live_sha)
    ordered.extend(sorted(stale_buckets[-1]))
    descriptors: list[CommitDescriptor] = []
    previous_index = -1
    for sha in ordered:
        message = "stale commit"
        ts: float | None = None
        if sha in live_set:
            index, message, ts = history[sha]
            external = max(0, index - previous_index - 1) if previous_index >= 0 else 0
            previous_index = index
        else:
            external = 0
        descriptors.append(CommitDescriptor(
            sha=sha, live=sha in live_set, message=message, ts=ts,
            external_count_before=external,
            aliases=sorted(old for old in referenced_shas if resolve(old) == sha and old != sha) or [],
        ))
    return descriptors


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


def _git(cwd: str, args: list[str], *, timeout: float = 10) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
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
