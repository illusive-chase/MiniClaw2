"""Replay/live coordination for reconnecting WebSocket observers."""

from __future__ import annotations

from typing import Any

EVENT_SCHEMA_VERSION = 2


def upgrade_event_record(record: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a persisted event envelope before it reaches runtime code."""

    version = record.get("schema_version", 1)
    if not isinstance(version, int) or version < 1:
        raise ValueError("invalid event schema_version")
    if version > EVENT_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported event schema_version {version}; "
            f"this build supports {EVENT_SCHEMA_VERSION}"
        )
    upgraded = dict(record)
    event = record.get("event")
    if isinstance(event, dict):
        upgraded_event = dict(event)
        if (
            version < 2
            and upgraded_event.get("type") == "interaction_request"
            and upgraded_event.get("interaction_type") == "checkpoint_review"
        ):
            upgraded_event["interaction_type"] = "human_review_prose"
            upgraded_event["tool_name"] = "human_review_prose"
        upgraded["event"] = upgraded_event
    upgraded["schema_version"] = EVENT_SCHEMA_VERSION
    return upgraded


def upgrade_legacy_interaction_response(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a persisted legacy response fixture to the current carrier."""

    upgraded = dict(raw)
    response = raw.get("response")
    if isinstance(response, dict) and (
        isinstance(response.get("answers"), dict)
        or isinstance(response.get("prose"), str)
    ):
        return upgraded

    updated_input = raw.get("updated_input")
    legacy_answers = (
        updated_input.get("answers")
        if isinstance(updated_input, dict)
        else raw.get("answers")
    )
    if isinstance(legacy_answers, dict):
        answers: dict[str, dict[str, list[str]]] = {}
        for key, value in legacy_answers.items():
            if isinstance(value, dict) and isinstance(value.get("answers"), list):
                selected = value["answers"]
            elif isinstance(value, list):
                selected = value
            elif value is None:
                selected = []
            else:
                selected = [value]
            answers[str(key)] = {"answers": [str(item) for item in selected]}
        upgraded["response"] = {"answers": answers}
        upgraded.pop("answers", None)
        upgraded.pop("updated_input", None)
        return upgraded

    prose: str | None = None
    if isinstance(response, dict):
        for key in ("text", "message"):
            value = response.get(key)
            if isinstance(value, str):
                prose = value
                break
    if prose is None and isinstance(raw.get("message"), str):
        prose = raw["message"]
    if prose is not None:
        upgraded["response"] = {"prose": prose}
    return upgraded


class LiveReplayBuffer:
    """Buffer live events until replay has filled the reconnect gap."""

    def __init__(self) -> None:
        self.live_ready = False
        self._buffered: list[tuple[str | None, dict[str, Any]]] = []

    def push_live(self, event: dict[str, Any]) -> dict[str, Any] | None:
        raw_node_id = event.get("node_id")
        node_id = raw_node_id if isinstance(raw_node_id, str) else None
        if self.live_ready:
            return event
        self._buffered.append((node_id, event))
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
    if event_node_id != replay_node_id:
        return False
    seq = event.get("seq")
    if not isinstance(seq, int) or seq > replayed_through_seq:
        return False
    if event.get("type") == "node_started":
        return event.get("node_id") == replay_node_id
    return True
