"""Which project files the code-reading page may list and read.

Everything that decides "may these bytes leave the machine" for the reading
page lives here. The rule is the Git-visible set — tracked files plus
untracked files that are not ignored — so ``.env``, ``node_modules`` and the
like stay out without a second list to maintain. ``.miniclaw2/`` is dropped
unconditionally: it is already in ``.git/info/exclude``, but a project root
need not be a Git repository at all.

Every read re-runs the whole verdict instead of trusting the tree the client
was shown, for the same reason ``read_session_markdown`` does: a client may
call the endpoint directly, and a path can be swapped for a symlink between
two requests.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .code_analysis import decode_source

TREE_LIMIT = 20_000
READ_CAP = 2 * 1024 * 1024
#: Python files above this size are listed and readable but never analysed.
ANALYSIS_CAP = 1024 * 1024
BINARY_SNIFF = 8192
PYTHON_SUFFIXES = frozenset({".py", ".pyi"})
MINICLAW_DIR = ".miniclaw2"
#: Without a .gitignore to lean on, these are the directories nobody wants
#: to read and the ones most likely to hold secrets or vendored code. Every
#: dot-named file or directory is dropped as well: that is where credentials
#: live (``.env``, ``.npmrc``, ``.pypirc``, ``.netrc``, ...), and a list of
#: their names would never be complete.
NON_GIT_EXCLUDED_DIRS = frozenset({
    ".git", MINICLAW_DIR, "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".tox", ".idea",
})


class CodeFileError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True, slots=True)
class TreeEntry:
    path: str
    size: int
    #: Submodules and nested repositories: shown as a folder, never expanded.
    directory: bool = False


def is_python_path(path: str) -> bool:
    return PurePosixPath(path).suffix in PYTHON_SUFFIXES


def _git(root: Path, args: list[str], *, timeout: float = 30) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=root,
            check=False,
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def is_git_worktree(root: Path) -> bool:
    result = _git(root, ["rev-parse", "--is-inside-work-tree"], timeout=10)
    return result is not None and result.returncode == 0 and result.stdout.strip() == b"true"


def _hidden_by_miniclaw(path: str) -> bool:
    return path == MINICLAW_DIR or path.startswith(MINICLAW_DIR + "/")


def _git_visible(root: Path, pathspec: str | None = None) -> list[str] | None:
    args = ["ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    if pathspec is not None:
        args = ["--literal-pathspecs", *args, "--", pathspec]
    result = _git(root, args)
    if result is None or result.returncode != 0:
        return None
    seen: dict[str, None] = {}
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8", errors="surrogateescape").rstrip("/")
        if path and not _hidden_by_miniclaw(path):
            seen.setdefault(path, None)
    return list(seen)


def _non_git_hidden(parts: tuple[str, ...]) -> bool:
    return any(part in NON_GIT_EXCLUDED_DIRS or part.startswith(".") for part in parts)


def list_tree(root: Path) -> tuple[list[TreeEntry], bool, bool]:
    """Return ``(entries, is_git, truncated)`` for the project root."""
    is_git = is_git_worktree(root)
    entries: list[TreeEntry] = []
    truncated = False
    if is_git:
        paths = _git_visible(root) or []
        for path in sorted(paths):
            try:
                stat = (root / path).lstat()
            except OSError:
                # Deleted from the worktree but still in the index.
                continue
            directory = (root / path).is_dir() and not (root / path).is_symlink()
            entries.append(TreeEntry(path, 0 if directory else stat.st_size, directory))
            if len(entries) >= TREE_LIMIT:
                truncated = len(paths) > TREE_LIMIT
                break
        return entries, True, truncated
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        parent = Path(directory)
        dirnames[:] = sorted(
            name for name in dirnames
            if name not in NON_GIT_EXCLUDED_DIRS and not name.startswith(".")
        )
        for name in sorted(filenames):
            relative = (parent / name).relative_to(root)
            if _non_git_hidden(relative.parts):
                continue
            try:
                size = (parent / name).lstat().st_size
            except OSError:
                continue
            if len(entries) >= TREE_LIMIT:
                return entries, False, True
            entries.append(TreeEntry(relative.as_posix(), size))
    return entries, False, truncated


def list_python_files(root: Path) -> list[str]:
    entries, _is_git, _truncated = list_tree(root)
    return [entry.path for entry in entries if not entry.directory and is_python_path(entry.path)]


def resolve_visible(root: Path, path: str) -> tuple[Path, str]:
    """Resolve a client path to ``(absolute, relative)`` or raise.

    The relative path returned is the *resolved* one, so a symlink inside
    the project is judged — and analysed — as the file it points at.
    """
    if not path or Path(path).is_absolute() or ".." in PurePosixPath(path).parts:
        raise CodeFileError(403, "path must be relative to the project root")
    real_root = root.resolve()
    candidate = root / path
    if not os.path.lexists(candidate):
        raise CodeFileError(404, "文件不存在")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(real_root):
        raise CodeFileError(403, "该文件被 .gitignore 忽略或不在项目内")
    if not resolved.exists():
        raise CodeFileError(404, "文件不存在")
    relative = resolved.relative_to(real_root).as_posix()
    if _hidden_by_miniclaw(relative) or not _is_visible(real_root, relative):
        raise CodeFileError(403, "该文件被 .gitignore 忽略或不在项目内")
    if not resolved.is_file():
        raise CodeFileError(403, "只能读取普通文件")
    return resolved, relative


def _is_visible(root: Path, relative: str) -> bool:
    if is_git_worktree(root):
        listed = _git_visible(root, relative)
        return listed is not None and relative in listed
    return not _non_git_hidden(PurePosixPath(relative).parts)


def read_code_file(root: Path, path: str) -> dict[str, object]:
    resolved, relative = resolve_visible(root, path)
    language = "python" if is_python_path(relative) else "other"
    base: dict[str, object] = {
        "path": relative,
        "text": "",
        "sha256": "",
        "size": 0,
        "language": language,
        "binary": False,
        "too_large": False,
    }
    try:
        size = resolved.stat().st_size
        base["size"] = size
        if size > READ_CAP:
            base["too_large"] = True
            return base
        raw = resolved.read_bytes()
    except OSError as exc:
        raise CodeFileError(400, f"读取文件失败：{exc}") from exc
    if b"\0" in raw[:BINARY_SNIFF]:
        base["binary"] = True
        return base
    base["text"] = decode_source(raw, python=language == "python")
    base["sha256"] = hashlib.sha256(raw).hexdigest()
    base["size"] = len(raw)
    return base
