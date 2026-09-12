"""Decide what a Markdown link inside agent prose actually points at.

Agent-written Markdown is full of links like ``[app.py](backend/miniclaw2/app.py)``
or ``[design](../notes/design.md)``. The browser cannot act on those: it does
not know what a relative path is relative to, whether the target exists, or
whether it sits inside the project at all. All three answers live on the
machine the backend runs on, so the verdict is computed here and the frontend
only carries it out.

There are exactly three verdicts, and the split is drawn along *what leaves
the machine*:

- ``markdown`` — a regular ``.md`` file inside the project root. Its bytes may
  be sent to the browser and rendered.
- ``reveal``  — anything else that can be located: a non-Markdown file, a
  directory, or a path outside the root. Nothing is read; the file manager is
  merely asked to show it.
- ``missing`` — the path does not resolve to anything.

``missing`` is a distinct verdict rather than a silent no-op because a wrong
relative path is a routine mistake in agent prose, and a reveal that quietly
does nothing is indistinguishable from a slow file manager.
"""

from __future__ import annotations

import re
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
LINE_SUFFIX_RE = re.compile(r"^(?P<path>.+?):[1-9]\d*(?::[1-9]\d*)?$")

Verdict = Literal["markdown", "reveal", "missing"]


@dataclass(frozen=True)
class LinkBase:
    """Where a relative href is resolved from.

    ``kind`` mirrors the rendering context, because "``../FUTURES.md``" points
    somewhere different depending on which text it appeared in:

    - ``project-root``: in-memory text (agent output, review handoff, preview
      fields). The agent runs with the project root as its working directory,
      so paths it writes are relative to the root.
    - ``project-file``: a file being displayed; ``path`` is that file and the
      base is its parent directory.
    - ``artifact``: a published artifact; ``path`` is the artifact directory.
    """

    kind: Literal["project-root", "project-file", "artifact"]
    path: str | None = None


@dataclass(frozen=True)
class LinkVerdict:
    verdict: Verdict
    #: Absolute resolved path; ``None`` only when the verdict is ``missing``.
    path: str | None = None
    #: Root-relative POSIX path; set only for ``markdown``, and the only form
    #: the read endpoint accepts.
    relative_path: str | None = None
    #: Human-readable cause; set only for ``missing``.
    reason: str | None = None


def strip_href_scheme(href: str) -> str:
    """Reduce an href to a filesystem path, or return "" if it is not one.

    ``file://`` URLs are accepted because agents do emit them; every other
    scheme (http, mailto, …) is the browser's business and returns "". A bare
    Windows drive letter would otherwise parse as a scheme, hence the length
    check on ``scheme``.
    """
    candidate = href.strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    if parsed.scheme == "file":
        # urlparse puts a leading-slash path in `path` and leaves `netloc`
        # empty for the local-host form file:///a/b.
        return unquote(parsed.path)
    # Fragment-only and query suffixes are display concerns, not path parts.
    path_part = unquote(candidate.split("#", 1)[0].split("?", 1)[0])
    # ``urlparse`` mistakes ``app.py:12`` for a custom URL scheme. A trailing
    # source location is still a local path and is resolved below.
    if len(parsed.scheme) > 1 and path_without_line_suffix(path_part) is None:
        return ""
    return path_part


def base_directory(root: Path, base: LinkBase | None) -> Path:
    """The directory a relative href is resolved against."""
    if base is None or base.kind == "project-root" or not base.path:
        return root
    candidate = Path(base.path)
    if not candidate.is_absolute():
        candidate = root / candidate
    if base.kind == "project-file":
        return candidate.parent
    return candidate


def path_without_line_suffix(raw: str) -> str | None:
    """Strip a trailing ``:line`` or ``:line:column`` file reference."""
    match = LINE_SUFFIX_RE.fullmatch(raw)
    return match.group("path") if match else None


def resolve_markdown_href(
    root: str | Path,
    base: LinkBase | None,
    href: str,
) -> LinkVerdict:
    """Classify ``href`` as ``markdown`` / ``reveal`` / ``missing``.

    The step order below is load-bearing; each step assumes the previous one
    already holds.
    """
    root_resolved = Path(root).expanduser().resolve()

    raw = strip_href_scheme(href)
    if not raw:
        return LinkVerdict("missing", reason=f"无法解析为本地路径：{href}")

    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = base_directory(root_resolved, base) / candidate

    # Resolve *before* testing containment. A symlink inside the root can
    # point outside it, so a textual "starts with root" check on the
    # unresolved path would call an external file `markdown` and then read it.
    try:
        target = candidate.resolve()
    except OSError:
        return LinkVerdict("missing", reason=f"无法解析路径：{raw}")

    try:
        stat = target.stat()
    except FileNotFoundError:
        fallback_raw = path_without_line_suffix(raw)
        if fallback_raw is None:
            return LinkVerdict("missing", reason=f"找不到该路径：{raw}")
        fallback = Path(fallback_raw).expanduser()
        if not fallback.is_absolute():
            fallback = base_directory(root_resolved, base) / fallback
        try:
            target = fallback.resolve()
            stat = target.stat()
        except OSError:
            return LinkVerdict("missing", reason=f"找不到该路径：{raw}")
    except OSError:
        return LinkVerdict("missing", reason=f"找不到该路径：{raw}")

    absolute = str(target)

    # A directory is always revealed: "open this folder in the file manager"
    # is exactly what reveal already does correctly today.
    if stat_module.S_ISDIR(stat.st_mode):
        return LinkVerdict("reveal", path=absolute)

    if not stat_module.S_ISREG(stat.st_mode):
        # A FIFO would block a reader and a device node is meaningless here.
        return LinkVerdict("missing", reason=f"不是常规文件或目录：{raw}")

    if not target.is_relative_to(root_resolved):
        return LinkVerdict("reveal", path=absolute)

    if target.suffix.lower() not in MARKDOWN_SUFFIXES:
        return LinkVerdict("reveal", path=absolute)

    return LinkVerdict(
        "markdown",
        path=absolute,
        relative_path=target.relative_to(root_resolved).as_posix(),
    )
