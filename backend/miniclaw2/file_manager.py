"""Open a project directory in the host's file manager.

MiniClaw2's UI runs in a browser, which cannot reveal a local directory: the
sandbox has no path-opening capability, and `file://` navigation is blocked
from an http origin. The backend is the only process that both knows the
project's `root_path` and runs on the machine the human is sitting at, so
revealing a folder is a server-side action.

The command is fire-and-forget by design. A file manager is a long-lived
desktop application; waiting on it would either block the request for the
lifetime of the window or return a meaningless exit code once it forks. What
this module verifies is only what it can verify synchronously: that the path
exists, is a directory, and that the launcher binary could be spawned at all.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class RevealUnsupportedError(RuntimeError):
    """No file-manager launcher is known for this platform."""


class RevealError(RuntimeError):
    """The launcher exists but the directory could not be handed to it."""


def reveal_command(path: str) -> list[str]:
    """The argv that hands ``path`` to this platform's file manager.

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
        raise RevealError(f"打开文件夹失败：{exc}") from exc
    logger.info("revealed %s via %s", resolved, argv[0])
