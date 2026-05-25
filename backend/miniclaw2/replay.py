"""Replay/live coordination for reconnecting WebSocket observers."""

from __future__ import annotations

from typing import Any


class LiveReplayBuffer:
    """Buffer live events until replay has filled the reconnect gap."""

    def __init__(self) -> None:
        self.live_ready = False
        self._buffered: list[tuple[str | None, dict[str, Any]]] = []
        self._node_id: str | None = None

    def push_live(self, event: dict[str, Any]) -> dict[str, Any] | None:
        if event.get("type") == "node_started":
            node_id = event.get("node_id")
            self._node_id = node_id if isinstance(node_id, str) else None
        if self.live_ready:
            return event
        self._buffered.append((self._node_id, event))
        return None

    def mark_live_ready(
        self,
        *,
        replay_node_id: str | None = None,
        replayed_through_seq: int | None = None,
    ) -> list[dict[str, Any]]:
        if self.live_ready:
            return []
        self.live_ready = True
        pending = self._buffered
        self._buffered = []
        return [
            event
            for event_node_id, event in pending
            if not covered_by_replay(
                event_node_id,
                event,
                replay_node_id,
                replayed_through_seq,
            )
        ]


def covered_by_replay(
    event_node_id: str | None,
    event: dict[str, Any],
    replay_node_id: str | None,
    replayed_through_seq: int | None,
) -> bool:
    if replay_node_id is None or replayed_through_seq is None:
        return False
    if event_node_id is not None and event_node_id != replay_node_id:
        return False
    seq = event.get("seq")
    if not isinstance(seq, int) or seq > replayed_through_seq:
        return False
    if event.get("type") == "node_started":
        return event.get("node_id") == replay_node_id
    return True
