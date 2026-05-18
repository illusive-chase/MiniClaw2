"""In-memory session registry. One CCAgent per session id."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from uuid import uuid4

from .agent import CCAgent


@dataclass
class Session:
    id: str
    agent: CCAgent
    created_at: float = field(default_factory=time.time)
    turns: int = 0


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, cwd: str | None = None, model: str | None = None) -> Session:
        sid = uuid4().hex[:12]
        session = Session(id=sid, agent=CCAgent(cwd=cwd, model=model))
        self._sessions[sid] = session
        return session

    def get(self, sid: str) -> Session | None:
        return self._sessions.get(sid)

    def delete(self, sid: str) -> bool:
        return self._sessions.pop(sid, None) is not None

    def list(self) -> list[Session]:
        return list(self._sessions.values())
