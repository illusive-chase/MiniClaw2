"""Type prompts into Claude Code's TUI via a PTY.

Faithful port of botmux's ``writeInput`` (§8 of the proposal).

- Split on ``\\n``, insert a soft-newline (backslash + CR) between lines.
- Throttle between writes to avoid Ink's paste-burst detector.
- Send the submit key derived from ``keybindings.json`` at the end.
- Confirm the submit by tailing the JSONL for a fresh user marker;
  retry up to 3 times, then fall back to a fingerprint scan.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .keybindings import SubmitKey
from .paths import project_dir
from .transcript import (
    contains_user_submit_marker,
    fingerprint_prompt,
    line_matches_fingerprint,
)


_FIRST_WRITE_THROTTLE = 0.08
_STEADY_WRITE_THROTTLE = 0.03
_SUBMIT_DELAY = 0.5
_SUBMIT_DELAY_WITH_IMAGE = 0.8
_CONFIRM_POLL_INTERVAL = 0.1
_CONFIRM_WINDOW = 0.8
_CONFIRM_RETRIES = 3
_FINGERPRINT_MTIME_WINDOW = 60.0


@dataclass(slots=True)
class SubmitResult:
    submitted: bool
    session_id: str | None = None
    stream_offset: int | None = None
    failure_reason: str | None = None
    recheck: Callable[[], Awaitable["SubmitResult"]] | None = None


@dataclass(slots=True)
class _FingerprintMatch:
    session_id: str
    stream_offset: int


class InputWriter:
    """Owns the write path for a single ``ClaudeNativeSession``."""

    def __init__(
        self,
        *,
        pty_write: Callable[[bytes], None],
        submit_key: SubmitKey,
        jsonl_path: Path,
        expected_cwd: str,
        data_dir: Path,
        enter_is_newline: bool = False,
    ) -> None:
        self._pty_write = pty_write
        self._submit_key = submit_key
        self._jsonl_path = jsonl_path
        self._expected_cwd = expected_cwd
        self._data_dir = data_dir
        self._enter_is_newline = enter_is_newline
        self._sent_first = False

    def update_jsonl_path(self, path: Path) -> None:
        self._jsonl_path = path

    async def send(
        self,
        content: str,
        *,
        confirmation_text: str | None = None,
    ) -> SubmitResult:
        if not content:
            return SubmitResult(submitted=False, failure_reason="empty content")

        fingerprint_text = confirmation_text or content
        throttle = (
            _FIRST_WRITE_THROTTLE if not self._sent_first else _STEADY_WRITE_THROTTLE
        )
        self._sent_first = True

        base_path = self._jsonl_path
        base_offset = self._current_size()
        await self._type(content, throttle=throttle)
        submit_delay = (
            _SUBMIT_DELAY_WITH_IMAGE
            if _looks_like_image_path(content)
            else _SUBMIT_DELAY
        )
        await asyncio.sleep(submit_delay)

        confirmed_offset: int | None = None
        for attempt in range(_CONFIRM_RETRIES):
            self._pty_write(self._submit_key.raw.encode("utf-8"))
            confirmed_offset = await self._await_marker(base_offset)
            if confirmed_offset is not None:
                break

        if confirmed_offset is not None:
            return SubmitResult(submitted=True, stream_offset=confirmed_offset)

        # Fallback: fingerprint scan across sibling jsonl files for this
        # project hash. Use the node prompt, not the composed framework wrapper,
        # when callers can provide it.
        matched = self._fingerprint_scan(content, confirmation_text=fingerprint_text)
        if matched is not None:
            return SubmitResult(
                submitted=True,
                session_id=matched.session_id,
                stream_offset=matched.stream_offset,
            )

        return SubmitResult(
            submitted=False,
            failure_reason=(
                "submit key sent but no fresh user marker appeared in the JSONL "
                "within the confirmation window"
            ),
            recheck=lambda: self._recheck(
                content,
                fingerprint_text,
                base_path,
                base_offset,
            ),
        )

    async def _type(self, content: str, *, throttle: float) -> None:
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line:
                self._pty_write(line.encode("utf-8"))
                await asyncio.sleep(throttle)
            if i < len(lines) - 1:
                if not self._enter_is_newline:
                    self._pty_write(b"\\")
                    await asyncio.sleep(throttle)
                self._pty_write(b"\r")
                await asyncio.sleep(throttle)

    async def _await_marker(self, base_offset: int) -> int | None:
        deadline = time.monotonic() + _CONFIRM_WINDOW
        while time.monotonic() < deadline:
            marker_offset = self._marker_offset(base_offset)
            if marker_offset is not None:
                return marker_offset
            await asyncio.sleep(_CONFIRM_POLL_INTERVAL)
        return None

    def _marker_offset(self, base_offset: int) -> int | None:
        try:
            size = self._jsonl_path.stat().st_size
        except FileNotFoundError:
            return None
        if size <= base_offset:
            return None
        try:
            with self._jsonl_path.open("rb") as f:
                f.seek(base_offset)
                offset = base_offset
                for line in f:
                    if contains_user_submit_marker(line):
                        return offset
                    offset += len(line)
        except OSError:
            return None
        return None

    def _current_size(self) -> int:
        try:
            return self._jsonl_path.stat().st_size
        except FileNotFoundError:
            return 0

    def _fingerprint_scan(
        self,
        content: str,
        *,
        confirmation_text: str | None = None,
    ) -> _FingerprintMatch | None:
        fingerprint = fingerprint_prompt(confirmation_text or content)
        if not fingerprint:
            return None
        now = time.time()
        cutoff = now - _FINGERPRINT_MTIME_WINDOW

        # Only scan siblings under this project's hash. Cross-project fallback
        # adoption is worse than a visible submit-confirmation failure.
        project_root = project_dir(self._expected_cwd, self._data_dir)
        for candidate in _mtime_sorted_jsonls(project_root, cutoff):
            match = _scan_for_fingerprint(candidate, fingerprint)
            if match is not None:
                return match
        return None

    async def _recheck(
        self,
        content: str,
        confirmation_text: str,
        base_path: Path,
        base_offset: int,
    ) -> SubmitResult:
        if self._jsonl_path == base_path:
            marker_offset = self._marker_offset(base_offset)
            if marker_offset is not None:
                return SubmitResult(submitted=True, stream_offset=marker_offset)

        matched = self._fingerprint_scan(content, confirmation_text=confirmation_text)
        if matched is not None:
            return SubmitResult(
                submitted=True,
                session_id=matched.session_id,
                stream_offset=matched.stream_offset,
            )
        return SubmitResult(submitted=False)


def _mtime_sorted_jsonls(directory: Path, cutoff: float) -> list[Path]:
    if not directory.exists():
        return []
    try:
        entries = [
            (p.stat().st_mtime, p)
            for p in directory.glob("*.jsonl")
            if p.is_file()
        ]
    except OSError:
        return []
    fresh = [p for mtime, p in entries if mtime >= cutoff]
    fresh.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return fresh


def _scan_for_fingerprint(path: Path, fingerprint: str) -> _FingerprintMatch | None:
    match: _FingerprintMatch | None = None
    try:
        with path.open("rb") as f:
            offset = 0
            for raw_line in f:
                line_offset = offset
                offset += len(raw_line)
                line = raw_line.decode("utf-8", errors="ignore")
                if fingerprint not in line:
                    continue
                if line_matches_fingerprint(line, fingerprint):
                    match = _FingerprintMatch(
                        session_id=path.stem,
                        stream_offset=line_offset,
                    )
    except OSError:
        return None
    return match


def _looks_like_image_path(content: str) -> bool:
    lowered = content.lower()
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        if ext in lowered:
            return True
    return False


# Debug helper used by tests + logging: expose the actual throttle values.
def throttle_env() -> dict[str, float]:
    """Read optional environment overrides for the throttle constants."""
    def _f(name: str, default: float) -> float:
        raw = os.environ.get(name)
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    return {
        "first_write": _f("MINICLAW_CLAUDE_THROTTLE_FIRST", _FIRST_WRITE_THROTTLE),
        "steady_write": _f("MINICLAW_CLAUDE_THROTTLE_STEADY", _STEADY_WRITE_THROTTLE),
        "submit_delay": _f("MINICLAW_CLAUDE_SUBMIT_DELAY", _SUBMIT_DELAY),
    }


__all__ = ["InputWriter", "SubmitResult", "throttle_env"]

# Silence unused-imports warnings; ``Any`` is exported for typing use in callers.
_ = Any
