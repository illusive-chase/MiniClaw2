"""Git helpers used by node snapshots and the project-level Git surface."""

from __future__ import annotations

import logging
import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


MINICLAW_GENERATED_DIR = ".miniclaw2"
MINICLAW_GENERATED_EXCLUDE = f"{MINICLAW_GENERATED_DIR}/"
GIT_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GitDiff:
    kind: str
    text: str
    error: str | None = None


@dataclass(slots=True)
class GitFileStatus:
    path: str
    index_status: str = "."
    worktree_status: str = "."
    old_path: str | None = None
    additions: int = 0
    deletions: int = 0
    binary: bool = False


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
    files: list[GitFileStatus] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class GitReviewSnapshot:
    head_sha: str | None
    dirty_paths: tuple[str, ...]
    patch: str
    diff_sha256: str


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
    result = _git(
        cwd,
        ["status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"],
    )
    if result.returncode != 0:
        return GitStatus()
    status = GitStatus(is_repo=True)
    records = result.stdout.split("\x00")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if record.startswith("# branch.head "):
            branch = record.removeprefix("# branch.head ").strip()
            status.branch = None if branch in {"(detached)", ""} else branch
            status.detached = branch == "(detached)"
            continue
        if record.startswith("# branch.oid "):
            oid = record.removeprefix("# branch.oid ").strip()
            status.head = None if oid in {"(initial)", ""} else oid
            continue
        if record.startswith("# branch.upstream "):
            status.upstream = record.removeprefix("# branch.upstream ").strip() or None
            continue
        if record.startswith("# branch.ab "):
            parts = record.removeprefix("# branch.ab ").split()
            if len(parts) == 2:
                try:
                    status.ahead = int(parts[0].removeprefix("+"))
                    status.behind = int(parts[1].removeprefix("-"))
                except ValueError:
                    pass
            continue
        file_status: GitFileStatus | None = None
        if record.startswith("1 "):
            parts = record.split(" ", 8)
            if len(parts) == 9:
                file_status = GitFileStatus(
                    path=parts[8],
                    index_status=parts[1][0],
                    worktree_status=parts[1][1],
                )
        elif record.startswith("2 "):
            parts = record.split(" ", 9)
            if len(parts) == 10:
                old_path = records[index] if index < len(records) else None
                index += 1
                file_status = GitFileStatus(
                    path=parts[9],
                    old_path=old_path or None,
                    index_status=parts[1][0],
                    worktree_status=parts[1][1],
                )
        elif record.startswith("u "):
            parts = record.split(" ", 10)
            if len(parts) == 11:
                file_status = GitFileStatus(
                    path=parts[10],
                    index_status=parts[1][0],
                    worktree_status=parts[1][1],
                )
        elif record.startswith("? "):
            file_status = GitFileStatus(
                path=record[2:], index_status="?", worktree_status="?"
            )
        if file_status is None:
            continue
        if _is_generated_path(file_status.path) and (
            file_status.old_path is None or _is_generated_path(file_status.old_path)
        ):
            continue
        status.files.append(file_status)

    stats: dict[str, tuple[int, int, bool]] = {}
    _merge_numstat(stats, _git(cwd, ["diff", "--cached", "--numstat", "-z", "--find-renames"]))
    _merge_numstat(stats, _git(cwd, ["diff", "--numstat", "-z", "--find-renames"]))
    for item in status.files:
        if item.index_status == "?" and item.worktree_status == "?":
            item.additions, item.binary = _untracked_file_stat(Path(cwd) / item.path)
            continue
        additions, deletions, binary = stats.get(item.path, (0, 0, False))
        item.additions = additions
        item.deletions = deletions
        item.binary = binary
    status.dirty_count = len(status.files)
    return status


def is_git_repo(cwd: str) -> bool:
    """Return whether ``cwd`` belongs to a Git worktree without scanning it."""
    return _git(cwd, ["rev-parse", "--git-dir"]).returncode == 0


def git_review_snapshot(cwd: str) -> GitReviewSnapshot:
    """Capture the uncommitted tree as an auditable patch and fingerprint."""
    status = git_status(cwd)
    if not status.is_repo:
        raise RuntimeError("code review requires a Git repository")
    dirty_paths = tuple(sorted(item.path for item in status.files))
    base = status.head or GIT_EMPTY_TREE_SHA
    tracked = _git_bytes(
        cwd,
        ["diff", base, "--binary", "--no-ext-diff"],
        timeout=60,
    )
    if tracked.returncode != 0:
        raise RuntimeError(_git_failure_message("git diff", tracked))
    tracked_text = tracked.stdout.decode("utf-8", errors="replace")
    parts = [tracked_text] if tracked_text else []
    untracked = sorted(
        item.path
        for item in status.files
        if item.index_status == "?" and item.worktree_status == "?"
    )
    if untracked:
        parts.append(
            "\n# Untracked files\n" + "".join(f"# {path}\n" for path in untracked)
        )
    for path in untracked:
        result = _git_bytes(
            cwd,
            [
                "diff",
                "--no-index",
                "--binary",
                "--no-ext-diff",
                "--",
                "/dev/null",
                path,
            ],
            timeout=60,
        )
        if result.returncode not in {0, 1}:
            raise RuntimeError(_git_failure_message(f"git diff for {path}", result))
        text = result.stdout.decode("utf-8", errors="replace")
        if text:
            parts.append(text)
    patch = "\n".join(part.rstrip("\n") for part in parts if part).rstrip() + "\n"
    return GitReviewSnapshot(
        head_sha=status.head,
        dirty_paths=dirty_paths,
        patch=patch,
        diff_sha256=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
    )


def _is_generated_path(path: str) -> bool:
    return path == MINICLAW_GENERATED_DIR or path.startswith(MINICLAW_GENERATED_EXCLUDE)


def _merge_numstat(
    target: dict[str, tuple[int, int, bool]],
    result: subprocess.CompletedProcess[str],
) -> None:
    """Merge a NUL-delimited ``git diff --numstat`` result by destination path."""
    if result.returncode != 0:
        return
    records = result.stdout.split("\x00")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        parts = record.split("\t", 2)
        if len(parts) != 3:
            continue
        raw_additions, raw_deletions, path = parts
        if not path:
            # With -z, renamed paths are emitted as an empty path followed by
            # separate source and destination records.
            index += 1
            if index >= len(records):
                break
            path = records[index]
            index += 1
        binary = raw_additions == "-" or raw_deletions == "-"
        try:
            additions = 0 if binary else int(raw_additions)
            deletions = 0 if binary else int(raw_deletions)
        except ValueError:
            continue
        previous = target.get(path, (0, 0, False))
        target[path] = (
            previous[0] + additions,
            previous[1] + deletions,
            previous[2] or binary,
        )


def _untracked_file_stat(path: Path) -> tuple[int, bool]:
    """Return Git-like added-line and binary estimates for an untracked path."""
    try:
        if path.is_symlink():
            return 1, False
        line_count = 0
        ends_with_newline = True
        inspected = bytearray()
        with path.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                if len(inspected) < 8000:
                    inspected.extend(chunk[: 8000 - len(inspected)])
                line_count += chunk.count(b"\n")
                ends_with_newline = chunk.endswith(b"\n")
        if b"\x00" in inspected:
            return 0, True
        if path.stat().st_size and not ends_with_newline:
            line_count += 1
        return line_count, False
    except OSError:
        return 0, False


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
    resolved_timestamps: dict[str, float] = {}
    for sha, ts in (ref_timestamps or {}).items():
        resolved = resolve(sha)
        current_ts = resolved_timestamps.get(resolved)
        if current_ts is None or ts < current_ts:
            resolved_timestamps[resolved] = ts
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
            for sha in sorted(
                refs,
                key=lambda candidate: (
                    candidate not in resolved_timestamps,
                    resolved_timestamps.get(candidate, 0.0),
                    candidate,
                ),
            )
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
    # Do not pass the ignored framework directory as an exclusion pathspec.
    # Older Git versions still reject that explicit path even though it is
    # excluded, leaving valid files staged while reporting a failed add.
    add = _git(cwd, ["add", "-A", "--", "."])
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
    no-op; the explicit unstage in :func:`commit_all` remains the hard guard.
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


def _git_bytes(
    cwd: str, args: list[str], *, timeout: float = 10
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=1,
            stdout=b"",
            stderr=str(exc).encode("utf-8", errors="replace"),
        )


def _git_failure_message(
    operation: str, result: subprocess.CompletedProcess[bytes]
) -> str:
    detail = (result.stderr or result.stdout).decode(
        "utf-8", errors="replace"
    ).strip()
    return f"{operation} failed: {detail or f'exit code {result.returncode}'}"


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
