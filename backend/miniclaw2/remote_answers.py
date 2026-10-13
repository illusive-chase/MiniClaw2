"""Which remote-answer channel this host uses: Feishu, WeCom, or none.

Each host picks one channel (``remote_answers.local.json``, never synced).
The router is the registry's ``GateObserver``: it remembers every open
``ask_user`` gate and forwards it to the active bridge only, so switching
channel retires what the old bridge pushed and announces the gates still
open to the new one. The inactive bridge keeps its credentials but stays
disconnected, so switching back needs no new scan.

A switch spans awaits, so it and every change made through a bridge hold one
lock: two switches, or a switch and a settings update that reconnects, can
otherwise interleave and leave an inactive bridge connected.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Protocol

from .gate_observer import GateNotice, GateOutcome

logger = logging.getLogger(__name__)

REMOTE_ANSWERS_FILENAME = "remote_answers.local.json"

Channel = Literal["none", "feishu", "wecom"]
CHANNELS: tuple[Channel, ...] = ("none", "feishu", "wecom")
CHANNEL_NAMES = {"feishu": "飞书", "wecom": "企业微信"}


class RemoteAnswersError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class ChannelBridge(Protocol):
    def opened(self, notice: GateNotice) -> None: ...

    def closed(self, gate_id: str, outcome: GateOutcome) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def deactivate(self, line: str) -> None: ...

    def relays_for(self, project_id: str) -> dict[str, dict[str, Any]]: ...


def remote_answers_path(root: Path) -> Path:
    return root / REMOTE_ANSWERS_FILENAME


def load_channel(root: Path) -> Channel | None:
    try:
        payload = json.loads(remote_answers_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    channel = payload.get("channel") if isinstance(payload, dict) else None
    return channel if channel in CHANNELS else None


def save_channel(root: Path, channel: Channel) -> None:
    path = remote_answers_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps({"channel": channel}) + "\n")
    temporary.replace(path)


def default_channel(root: Path) -> Channel:
    """Before any choice: Feishu, as before WeCom existed, unless only WeCom is set up."""
    from .feishu_config import load_feishu_config
    from .wecom_config import load_wecom_config

    if load_wecom_config(root).has_credentials and not load_feishu_config(root).has_credentials:
        return "wecom"
    return "feishu"


class RemoteAnswers:
    def __init__(self, registry: Any, *, feishu: ChannelBridge, wecom: ChannelBridge) -> None:
        self._registry = registry
        self._bridges: dict[str, ChannelBridge] = {"feishu": feishu, "wecom": wecom}
        self._channel: Channel | None = None
        self._owner = False
        self._started = False
        self._open: dict[str, GateNotice] = {}
        self._lock = asyncio.Lock()
        #: While a switch is under way gates reach no bridge: the old one is
        #: retiring what it pushed, and the new one hears of every gate still
        #: open once it has started.
        self._switching = False

    @property
    def _root(self) -> Path:
        return Path(self._registry.store.root)

    @property
    def channel(self) -> Channel:
        if self._channel is None:
            self._channel = load_channel(self._root) or default_channel(self._root)
        return self._channel

    def _active(self) -> ChannelBridge | None:
        if self._switching:
            return None
        return self._bridges.get(self.channel)

    # ---- lifecycle ----

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._owner = bool(getattr(self._registry, "is_runtime_owner", False))
        self._channel = load_channel(self._root) or default_channel(self._root)
        bridge = self._active()
        if bridge is not None:
            await bridge.start()

    async def stop(self) -> None:
        async with self._lock:
            bridge = self._active()
            if bridge is not None:
                await bridge.stop()
            self._started = False

    async def set_channel(self, channel: Channel) -> None:
        async with self._lock:
            if not self._owner:
                raise RemoteAnswersError(
                    "这个后端进程不是本机存储的运行时属主，不能切换远程回答通道", status=409
                )
            if channel == self.channel:
                save_channel(self._root, channel)
                return
            old, new = self._bridges.get(self.channel), self._bridges.get(channel)
            self._switching = True
            try:
                if old is not None:
                    target = CHANNEL_NAMES.get(channel)
                    line = (
                        f"此主机已改用{target}远程回答，此提问不再从这里推送，请回网页端作答。"
                        if target
                        else "此主机已停用远程回答，此提问不再从这里推送，请回网页端作答。"
                    )
                    await old.deactivate(line)
                save_channel(self._root, channel)
                self._channel = channel
                if new is not None:
                    await new.start()
            finally:
                self._switching = False
            if new is not None:
                for notice in list(self._open.values()):
                    new.opened(notice)

    @asynccontextmanager
    async def while_active(self, channel: Channel) -> AsyncIterator[None]:
        """Change ``channel``'s bridge with no channel switch interleaved.

        Refused unless ``channel`` is this host's channel once the lock is
        held, so a change waiting behind a switch away never reconnects a
        bridge the host no longer answers through.
        """
        async with self._lock:
            self._require_active(channel)
            yield

    def _require_active(self, channel: Channel) -> None:
        if not self._owner:
            return  # the bridge itself explains that this process is not the owner
        if self.channel != channel:
            name = CHANNEL_NAMES.get(channel, channel)
            raise RemoteAnswersError(
                f"本机当前的远程回答通道不是{name}，请先在设置中切换", status=409
            )

    # ---- GateObserver ----

    def opened(self, notice: GateNotice) -> None:
        if notice.subtype != "ask_user":
            return
        self._open[notice.gate_id] = notice
        bridge = self._active()
        if bridge is not None:
            bridge.opened(notice)

    def closed(self, gate_id: str, outcome: GateOutcome) -> None:
        if self._open.pop(gate_id, None) is None:
            return
        bridge = self._active()
        if bridge is not None:
            bridge.closed(gate_id, outcome)

    # ---- reporting ----

    def status(self) -> dict[str, Any]:
        return {"channel": self.channel, "runtime_owner": self._owner}

    def relays_for(self, project_id: str) -> dict[str, dict[str, Any]]:
        bridge = self._active()
        return bridge.relays_for(project_id) if bridge is not None else {}
