"""Show a local path in the host's file manager.

MiniClaw2's UI runs in a browser, which cannot reveal a local path: the
sandbox has no path-opening capability, and `file://` navigation is blocked
from an http origin. The backend is the only process that both knows the
project's `root_path` and runs on the machine the human is sitting at, so
revealing a folder is a server-side action.

Directories and files need *different* verbs, and the difference is a
security boundary rather than a nicety. Every launcher here ("open",
"explorer", "xdg-open") means "hand this to its default handler"; for a
directory that is the file manager, but for a file it is whatever program
runs it. Since these commands are reachable from an HTTP endpoint, files go
through :func:`select_command`, which locates without opening.

The command is fire-and-forget by design. A file manager is a long-lived
desktop application; waiting on it would either block the request for the
lifetime of the window or return a meaningless exit code once it forks. What
this module verifies is only what it can verify synchronously: that the path
exists, what kind of node it is, and that the launcher binary could be
spawned at all.
"""

from __future__ import annotations

import logging
import os
import stat as stat_module
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class RevealUnsupportedError(RuntimeError):
    """No file-manager launcher is known for this platform."""


class RevealError(RuntimeError):
    """The launcher exists but the directory could not be handed to it."""


def reveal_command(path: str) -> list[str]:
    """The argv that opens a *directory* in this platform's file manager.

    Only ever call this on a directory. On every platform here the bare
    launcher means "hand this to the default handler", which for a directory
    is the file manager but for a file is *the application that runs it* —
    see :func:`select_command` for what files must use instead.

    Raises :class:`RevealUnsupportedError` on a platform without a known
    launcher, so the caller can report that rather than spawning something
    arbitrary.
    """
    if sys.platform == "darwin":
        return ["open", path]
    if sys.platform == "win32":
        # `explorer` is resolved through the shell on Windows and reports
        # success via a non-zero exit code, which is why nothing here reads
        # the return value.
        return ["explorer", path]
    if sys.platform.startswith("linux"):
        return ["xdg-open", path]
    raise RevealUnsupportedError(
        f"不支持在当前平台（{sys.platform}）打开文件管理器"
    )


def select_command(path: str) -> list[str]:
    """The argv that *locates* a file in the file manager without opening it.

    This exists because :func:`reveal_command` is not safe for files. macOS
    `open <file>` is documented as opening it "just as if you had
    double-clicked the file's icon", so a `.command`, `.app`, or `.scpt`
    would be executed; `explorer <file>` behaves the same way on Windows.
    Any endpoint that accepts a caller-supplied file path must therefore use
    this function, never ``reveal_command``.

    The one property that may not be traded away here is that the target is
    never executed. Highlighting the entry is the nice-to-have.
    """
    if sys.platform == "darwin":
        return ["open", "-R", path]  # -R: reveal in Finder instead of opening
    if sys.platform == "win32":
        # A single argv token: `explorer` parses "/select,<path>" itself and
        # rejects the two split apart.
        return ["explorer", f"/select,{path}"]
    if sys.platform.startswith("linux"):
        # No cross-distro "select this entry" verb exists. Opening the parent
        # directory loses the highlight but keeps the guarantee that matters:
        # the target file is never handed to a handler.
        return ["xdg-open", str(Path(path).expanduser().parent)]
    raise RevealUnsupportedError(
        f"不支持在当前平台（{sys.platform}）打开文件管理器"
    )


def _spawn(argv: list[str], *, what: str) -> None:
    """Fire-and-forget ``argv``, translating spawn failures to ``RevealError``."""
    try:
        subprocess.Popen(  # noqa: S603 - argv is built here, never user-composed
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise RevealError(f"未找到文件管理器命令 {argv[0]}") from exc
    except OSError as exc:
        raise RevealError(f"{what}失败：{exc}") from exc


def reveal_directory(path: str) -> None:
    """Open ``path`` in the host file manager, or raise explaining why not.

    ``RevealError`` covers every reason the human would need to act on: the
    binding points at a directory that is gone, points at a file, or the
    platform launcher is not installed.
    """
    target = Path(path).expanduser()
    if not target.exists():
        raise RevealError(f"目录不存在：{target}")
    if not target.is_dir():
        raise RevealError(f"不是目录：{target}")
    resolved = str(target.resolve())
    argv = reveal_command(resolved)
    _spawn(argv, what="打开文件夹")
    logger.info("revealed %s via %s", resolved, argv[0])


def reveal_path(path: str) -> str:
    """Show ``path`` in the host file manager, picking the safe verb for it.

    A directory is opened; a regular file is only *selected*, never handed to
    a handler. Anything else (FIFO, socket, device node) is refused rather
    than guessed at, because there is no meaningful file-manager action for it
    and the launchers would fall back to their default handler.

    Returns the resolved path that was acted on.
    """
    target = Path(path).expanduser()
    try:
        # follow_symlinks: acting on the link's target is what the human means
        # by "show me this", and it is also what decides which verb is safe.
        stat = target.stat()
    except OSError as exc:
        raise RevealError(f"路径不存在：{target}") from exc
    resolved = str(target.resolve())
    if stat_is_dir(stat):
        argv = reveal_command(resolved)
        what = "打开文件夹"
    elif stat_is_regular_file(stat):
        argv = select_command(resolved)
        what = "定位文件"
    else:
        raise RevealError(f"不是文件或目录：{target}")
    _spawn(argv, what=what)
    logger.info("revealed %s via %s", resolved, " ".join(argv[:2]))
    return resolved


def stat_is_dir(stat: os.stat_result) -> bool:
    return stat_module.S_ISDIR(stat.st_mode)


def stat_is_regular_file(stat: os.stat_result) -> bool:
    return stat_module.S_ISREG(stat.st_mode)
