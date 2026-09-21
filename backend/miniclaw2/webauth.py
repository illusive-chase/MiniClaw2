"""Optional process-local passcode protection for the web interface."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from threading import RLock

PASSCODE_ENV = "MINICLAW_PASSCODE"
SESSION_COOKIE = "miniclaw_session"
MAX_FAILURES = 10


class PasscodeLockedError(RuntimeError):
    """Raised when a new login is attempted after the guard locks."""


@dataclass
class PasscodeGuard:
    passcode: str
    _sessions: set[str] = field(default_factory=set, init=False, repr=False)
    _failures: int = field(default=0, init=False, repr=False)
    _locked: bool = field(default=False, init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if re.fullmatch(r"\d{4}", self.passcode) is None:
            raise ValueError(f"{PASSCODE_ENV} must contain exactly four digits")

    def login(self, candidate: str) -> tuple[str | None, int]:
        """Return a new session and remaining attempts, or no session on failure."""
        with self._lock:
            if self._locked:
                raise PasscodeLockedError
            if secrets.compare_digest(candidate, self.passcode):
                self._failures = 0
                token = secrets.token_urlsafe(32)
                self._sessions.add(token)
                return token, MAX_FAILURES
            self._failures += 1
            if self._failures >= MAX_FAILURES:
                self._locked = True
            return None, max(0, MAX_FAILURES - self._failures)

    def has_session(self, candidate: str | None) -> bool:
        if not candidate:
            return False
        with self._lock:
            return any(
                secrets.compare_digest(candidate, token)
                for token in self._sessions
            )

    def logout(self, candidate: str | None) -> None:
        if not candidate:
            return
        with self._lock:
            matched = next(
                (
                    token
                    for token in self._sessions
                    if secrets.compare_digest(candidate, token)
                ),
                None,
            )
            if matched is not None:
                self._sessions.remove(matched)

    @property
    def locked(self) -> bool:
        with self._lock:
            return self._locked


def is_passcode_exempt(path: str) -> bool:
    """Return whether an HTTP route remains reachable before web login."""
    return (
        path
        in {
            "/",
            "/favicon.svg",
            "/health",
            "/auth/state",
            "/auth/login",
            "/auth/logout",
        }
        or path.startswith("/assets/")
        or path.startswith("/hook/")
    )
