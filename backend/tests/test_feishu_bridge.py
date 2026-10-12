"""Feishu remote answers: cluster delivery, card mailbox and bridge state.

``FakeFeishu`` stands in for the Feishu side of one shared app: it stores
cards by message id, applies callback responses and PATCHes to them, and
reports how many connections are online. Each ``Host`` is one MiniClaw2
backend (its own store, machine id and bridge) connected to that app, so a
click can be delivered to any host exactly as the long connection does.
No test imports ``lark_oapi``.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import itertools
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from miniclaw2 import feishu_bridge
from miniclaw2 import feishu_cards as cards
from miniclaw2 import feishu_transport
from miniclaw2.feishu_bridge import (
    MINIMAL_ADDONS,
    SOLO_CONNECTION_CACHE_SECONDS,
    UNRETIRED_CARDS_STATE,
    FeishuBridge,
    FeishuBridgeError,
)
from miniclaw2.feishu_config import (
    FeishuConfig,
    feishu_config_path,
    load_feishu_config,
    load_relay_ledger,
    load_usage,
    save_feishu_config,
    save_relay_ledger,
)
from miniclaw2.feishu_transport import (
    QUOTA_EXHAUSTED_CODE,
    CardAction,
    FeishuApiError,
    LarkTransport,
    TransportHandlers,
)
from miniclaw2.gate_observer import GateNotice, GateOutcome
from miniclaw2.store import Store

OPEN_ID = "ou_owner"
APP_ID = "cli_test"

QUESTIONS = [
    {
        "id": "db",
        "header": "数据库",
        "question": "用哪个数据库？",
        "options": [{"label": "Postgres"}, {"label": "SQLite"}],
    },
    {
        "id": "extras",
        "header": "附加",
        "question": "还要哪些？",
        "multiSelect": True,
        "options": [{"label": "缓存"}, {"label": "队列"}, {"label": "搜索"}],
    },
]
FORM = {"q0": "1", "q1_0": True, "q1_1": False, "q1_2": True, "o0": "", "o1": ""}
ANSWERS = {"db": {"answers": ["SQLite"]}, "extras": {"answers": ["缓存", "搜索"]}}


class FakeFeishu:
    def __init__(self) -> None:
        self.messages: dict[str, dict[str, Any]] = {}
        self.sent: list[dict[str, Any]] = []
        self.patches: list[tuple[str, dict[str, Any]]] = []
        self.calls: dict[str, int] = {}
        self.uuids: dict[str, str] = {}
        self.connected: list[FakeTransport] = []
        self.extra_connections = 0
        self.fail: dict[str, FeishuApiError] = {}
        self.scopes = ["im:message:send_as_bot", "im:message:readonly"]
        self._ids = itertools.count(1)

    def _call(self, kind: str) -> None:
        self.calls[kind] = self.calls.get(kind, 0) + 1
        error = self.fail.get(kind)
        if error is not None:
            raise error

    def card(self, message_id: str) -> dict[str, Any]:
        return self.messages[message_id]

    def title(self, message_id: str) -> str:
        return self.messages[message_id]["header"]["title"]["content"]


class FakeTransport:
    def __init__(self, feishu: FakeFeishu) -> None:
        self.feishu = feishu
        self.unreachable: set[str] = set()
        self.handlers: TransportHandlers | None = None
        self._state = "off"

    @property
    def state(self) -> str:
        return self._state

    async def start(self, handlers: TransportHandlers) -> None:
        self.handlers = handlers
        self._state = "connected"
        self.feishu.connected.append(self)

    async def stop(self) -> None:
        self._state = "off"
        if self in self.feishu.connected:
            self.feishu.connected.remove(self)

    async def send_card(self, open_id: str, card: dict[str, Any], *, uuid: str) -> str:
        self.feishu._call("send")
        if uuid in self.feishu.uuids:
            return self.feishu.uuids[uuid]
        message_id = f"om_{next(self.feishu._ids)}"
        self.feishu.uuids[uuid] = message_id
        self.feishu.messages[message_id] = copy.deepcopy(card)
        self.feishu.sent.append({"to": open_id, "mid": message_id, "card": card})
        return message_id

    async def patch_card(self, message_id: str, card: dict[str, Any]) -> None:
        if "patch" in self.unreachable:
            raise TimeoutError("read timed out")
        self.feishu._call("patch")
        self.feishu.messages[message_id] = copy.deepcopy(card)
        self.feishu.patches.append((message_id, card))

    async def get_cards(self, message_ids: list[str]) -> dict[str, dict[str, Any] | None]:
        self.feishu._call("mget")
        return {
            mid: copy.deepcopy(self.feishu.messages[mid])
            for mid in message_ids
            if mid in self.feishu.messages
        }

    async def online_connections(self) -> int | None:
        self.feishu._call("connections")
        return len(self.feishu.connected) + self.feishu.extra_connections

    async def list_scopes(self) -> list[str]:
        self.feishu._call("scopes")
        return list(self.feishu.scopes)


class FakeRegistry:
    """The registry surface the bridge uses, backed by a real Store."""

    def __init__(self, root: Path, *, owner: bool = True) -> None:
        self.store = Store(root=root)
        self.is_runtime_owner = owner
        self.gates: dict[str, dict[str, Any] | None] = {}
        self.resolutions: list[dict[str, Any]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.observer: FeishuBridge | None = None

    def open_gate(self, gate_id: str, *, questions: list[dict[str, Any]] | None = None) -> GateNotice:
        notice = GateNotice(
            gate_id=gate_id,
            project_id="p1",
            node_id=f"n-{gate_id}",
            subtype="ask_user",
            tool_name="AskUserQuestion",
            questions=QUESTIONS if questions is None else questions,
            project_name="demo",
            lane_title="lane",
            node_label="build the thing",
            created_at=time.time(),
        )
        self.gates[gate_id] = None
        assert self.observer is not None
        self.observer.opened(notice)
        return notice

    def resolve_gate(self, pid: str, gate_id: str, **kwargs: Any) -> bool:
        if gate_id not in self.gates or self.gates[gate_id] is not None:
            return False
        self.gates[gate_id] = kwargs
        self.resolutions.append({"gate_id": gate_id, **kwargs})
        assert self.observer is not None
        loop = asyncio.get_running_loop()
        # The runner's ``finally`` notifies the observer on a later iteration.
        loop.call_soon(
            self.observer.closed,
            gate_id,
            GateOutcome(
                kind="answered",
                source=kwargs.get("source"),
                via=kwargs.get("via"),
                response=kwargs.get("response"),
            ),
        )
        return True

    def answer_on_web(self, gate_id: str) -> None:
        self.gates[gate_id] = {"source": "web"}
        assert self.observer is not None
        self.observer.closed(
            gate_id,
            GateOutcome(kind="answered", source="web", response={"answers": ANSWERS}),
        )

    def end_node(self, gate_id: str) -> None:
        self.gates[gate_id] = {"ended": True}
        assert self.observer is not None
        self.observer.closed(gate_id, GateOutcome(kind="ended"))

    def publish_project_event(self, pid: str, event: dict[str, Any]) -> None:
        self.events.append((pid, event))


class Host:
    def __init__(self, feishu: FakeFeishu, root: Path, *, label: str, delay: int = 0) -> None:
        self.root = root
        self.now: float | None = None
        self.registry = FakeRegistry(root)
        self.registry.store.machine = self.registry.store.machine.__class__(
            **{**self.registry.store.machine.payload(), "label": label}
        )
        self.transport = FakeTransport(feishu)
        self.feishu = feishu
        save_feishu_config(
            root,
            FeishuConfig(
                app_id=APP_ID,
                app_secret="secret",
                bound_open_id=OPEN_ID,
                binding_source="created",
                delay_seconds=delay,
            ),
        )
        self.bridge = self._new_bridge()

    def _new_bridge(self) -> FeishuBridge:
        bridge = FeishuBridge(
            self.registry,
            transport_factory=lambda _config: self.transport,
            sdk_available=lambda: True,
            clock=lambda: time.time() if self.now is None else self.now,
        )
        self.registry.observer = bridge
        return bridge

    @property
    def short(self) -> str:
        return cards.host_short_id(self.registry.store.machine.id)

    async def start(self) -> None:
        await self.bridge.start()
        await settle()

    async def restart(self) -> None:
        await self.bridge.stop()
        self.transport = FakeTransport(self.feishu)
        self.registry.gates.clear()
        self.bridge = self._new_bridge()
        await self.start()


async def settle(rounds: int = 20) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


def submit_value(card: dict[str, Any]) -> dict[str, Any]:
    for element in cards._walk(card):
        if element.get("tag") == "button" and element.get("form_action_type") == "submit":
            return element["behaviors"][0]["value"]
    raise AssertionError("card has no submit button")


class FeishuClusterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # Retries run back to back, so a failing PATCH settles within settle().
        for name in ("PATCH_RETRY_DELAYS", "SEND_RETRY_DELAYS"):
            patcher = mock.patch.object(feishu_bridge, name, (0.0, 0.0))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.feishu = FakeFeishu()
        self.a = Host(self.feishu, Path(self.tmp.name) / "a", label="HOST-A")
        self.b = Host(self.feishu, Path(self.tmp.name) / "b", label="HOST-B")
        await self.a.start()
        await self.b.start()

    async def asyncTearDown(self) -> None:
        await self.a.bridge.stop()
        await self.b.bridge.stop()
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    async def click(self, host: Host, message_id: str, form: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        card = self.feishu.messages[message_id]
        response = await host.transport.handlers.on_card_action(
            CardAction(
                operator_open_id=kw.get("operator", OPEN_ID),
                value=copy.deepcopy(kw.get("value") or submit_value(card)),
                form_value=copy.deepcopy(FORM if form is None else form),
                message_id=message_id,
            )
        )
        replacement = response.get("card")
        if replacement:
            self.feishu.messages[message_id] = copy.deepcopy(replacement["data"])
        await settle()
        return response

    async def ask(self, host: Host, gate_id: str, **kw: Any) -> str:
        host.registry.open_gate(gate_id, **kw)
        await settle()
        return self.feishu.sent[-1]["mid"]

    async def test_click_on_owner_settles_locally_without_receipt_or_poll(self) -> None:
        mid = await self.ask(self.a, "g1")

        response = await self.click(self.a, mid)

        self.assertEqual(response["toast"]["type"], "success")
        self.assertEqual(self.feishu.title(mid), "已回答")
        resolution = self.a.registry.resolutions[0]
        self.assertEqual(resolution["source"], "feishu")
        self.assertIsNone(resolution["via"])
        self.assertEqual(resolution["response"], {"answers": ANSWERS})
        self.assertIsNone(cards.find_receipt(self.feishu.card(mid)))
        # The callback already showed the final card; closing adds no PATCH.
        self.assertEqual(self.feishu.patches, [])
        await self.a.bridge.poll_now()
        self.assertEqual(self.feishu.calls.get("mget", 0), 0)
        self.assertEqual(load_relay_ledger(self.a.root)["g1"]["state"], "settled")

    async def test_click_on_other_host_writes_receipt_and_owner_settles_once(self) -> None:
        mid = await self.ask(self.a, "g1")

        response = await self.click(self.b, mid)

        self.assertEqual(response["toast"]["type"], "info")
        self.assertIn("HOST-A", response["toast"]["content"])
        receipt = cards.find_receipt(self.feishu.card(mid))
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["vl"], "HOST-B")
        self.assertEqual(self.b.registry.resolutions, [])
        self.assertEqual(self.a.registry.resolutions, [])

        await self.a.bridge.poll_now()
        await settle()

        self.assertEqual(len(self.a.registry.resolutions), 1)
        resolution = self.a.registry.resolutions[0]
        self.assertEqual(resolution["via"], "HOST-B")
        self.assertEqual(resolution["response"], {"answers": ANSWERS})
        self.assertEqual(self.feishu.title(mid), "已回答 · 经 HOST-B 中转")

        # A second read of the same receipt (e.g. a stale copy) settles nothing.
        self.feishu.messages[mid] = cards.receipt_card(
            value=receipt["value"], form_value=receipt["fv"], operator_open_id=OPEN_ID,
            via_short=self.b.short, via_label="HOST-B", at=0,
        )
        await self.a.bridge.poll_now()
        await settle()
        self.assertEqual(len(self.a.registry.resolutions), 1)

    async def test_owner_offline_then_wakes_and_settles_relayed_click(self) -> None:
        mid = await self.ask(self.a, "g1")
        await self.a.bridge.poll_now()  # sees two connections, marks the card shared
        # A sleeps: its connection drops, the click lands on B.
        await self.a.transport.stop()
        await self.click(self.b, mid)
        self.assertEqual(self.feishu.title(mid), "已收到，正在交给 HOST-A")
        await self.b.bridge.stop()
        self.feishu.connected.append(self.a.transport)

        # Waking forces a poll even though only one connection is online now.
        await self.a.bridge.poll_now(force=True)
        await settle()

        self.assertEqual(self.a.registry.resolutions[0]["via"], "HOST-B")
        self.assertEqual(self.feishu.title(mid), "已回答 · 经 HOST-B 中转")

    async def test_restart_sweeps_old_cards_and_ignores_their_receipts(self) -> None:
        mid = await self.ask(self.a, "g1")
        await self.click(self.b, mid)

        await self.a.restart()

        self.assertEqual(self.feishu.title(mid), "提问已失效")
        self.assertEqual(load_relay_ledger(self.a.root)["g1"]["state"], "stale")
        await self.a.bridge.poll_now(force=True)
        self.assertEqual(self.a.registry.resolutions, [])

    async def test_forged_receipts_never_settle(self) -> None:
        forgeries = {
            "nonce": lambda v: {**v, "n": "0" * 16},
            "host": lambda v: {**v, "h": self.b.short},
            "gate": lambda v: {**v, "g": "other"},
        }
        for index, (name, forge) in enumerate(forgeries.items()):
            with self.subTest(name):
                gate_id = f"g{index}"
                mid = await self.ask(self.a, gate_id)
                value = forge(submit_value(self.feishu.card(mid)))
                self.feishu.messages[mid] = cards.receipt_card(
                    value=value, form_value=FORM, operator_open_id=OPEN_ID,
                    via_short=self.b.short, via_label="HOST-B", at=0,
                )
                await self.a.bridge.poll_now(force=True)
                await settle()
                self.assertNotIn(gate_id, [r["gate_id"] for r in self.a.registry.resolutions])
                self.assertEqual(self.feishu.title(mid), "无法确认这次回答")
        with self.subTest("operator"):
            mid = await self.ask(self.a, "g-op")
            self.feishu.messages[mid] = cards.receipt_card(
                value=submit_value(self.feishu.card(mid)), form_value=FORM,
                operator_open_id="ou_intruder", via_short=self.b.short, via_label="HOST-B", at=0,
            )
            await self.a.bridge.poll_now(force=True)
            await settle()
            self.assertEqual(self.a.registry.resolutions, [])
        with self.subTest("message id"):
            # A genuine receipt for g-x copied onto another card is not g-y's.
            mid_x = await self.ask(self.a, "g-x")
            mid_y = await self.ask(self.a, "g-y")
            self.feishu.messages[mid_y] = cards.receipt_card(
                value=submit_value(self.feishu.card(mid_x)), form_value=FORM,
                operator_open_id=OPEN_ID, via_short=self.b.short, via_label="HOST-B", at=0,
            )
            await self.a.bridge.poll_now(force=True)
            await settle()
            self.assertEqual(self.a.registry.resolutions, [])

    async def test_web_answer_first_then_receipt_reports_web(self) -> None:
        mid = await self.ask(self.a, "g1")
        await self.click(self.b, mid)

        self.a.registry.answer_on_web("g1")
        await settle()

        self.assertEqual(self.feishu.title(mid), "已在网页端回答")
        await self.a.bridge.poll_now(force=True)
        self.assertEqual(self.a.registry.resolutions, [])

    async def test_non_owner_blocks_incomplete_answers_without_receipt(self) -> None:
        mid = await self.ask(self.a, "g1")

        response = await self.click(self.b, mid, {"q0": "0", "q1_0": False, "q1_1": False, "q1_2": False})

        self.assertEqual(response["toast"]["content"], "第 2 题还没有回答")
        self.assertNotIn("card", response)
        self.assertIsNone(cards.find_receipt(self.feishu.card(mid)))

    async def test_owner_blocks_incomplete_answers(self) -> None:
        mid = await self.ask(self.a, "g1")

        response = await self.click(self.a, mid, {"o0": "  "})

        self.assertEqual(response["toast"]["content"], "第 1 题还没有回答")
        self.assertEqual(self.a.registry.resolutions, [])

    async def test_single_connection_never_polls(self) -> None:
        await self.b.bridge.stop()
        await self.ask(self.a, "g1")

        for _ in range(3):
            await self.a.bridge.poll_now()

        self.assertEqual(self.feishu.calls.get("mget", 0), 0)
        self.assertGreaterEqual(self.feishu.calls.get("connections", 0), 1)

    async def test_quota_exhaustion_stops_polling_and_reports(self) -> None:
        await self.ask(self.a, "g1")
        self.feishu.fail["mget"] = FeishuApiError("mget", QUOTA_EXHAUSTED_CODE, "quota")

        await self.a.bridge.poll_now(force=True)
        await self.a.bridge.poll_now(force=True)

        self.assertEqual(self.feishu.calls["mget"], 1)
        status = await self.a.bridge.status()
        self.assertTrue(status["quota_exhausted"])
        # New gates are not pushed either; the web dialog still works.
        sends = self.feishu.calls["send"]
        self.a.registry.open_gate("g2")
        await settle()
        self.assertEqual(self.feishu.calls["send"], sends)

    async def test_unknown_value_version_returns_toast(self) -> None:
        mid = await self.ask(self.a, "g1")
        value = {**submit_value(self.feishu.card(mid)), "v": 99}

        for host in (self.a, self.b):
            response = await self.click(host, mid, value=value)
            self.assertEqual(response["toast"]["type"], "warning")
            self.assertNotIn("card", response)

    async def test_unbound_operator_is_refused_everywhere(self) -> None:
        mid = await self.ask(self.a, "g1")

        for host in (self.a, self.b):
            response = await self.click(host, mid, operator="ou_intruder")
            self.assertEqual(response["toast"]["type"], "error")
        self.assertEqual(self.a.registry.resolutions, [])
        self.assertIsNone(cards.find_receipt(self.feishu.card(mid)))

    async def test_duplicate_click_settles_once(self) -> None:
        mid = await self.ask(self.a, "g1")
        card = copy.deepcopy(self.feishu.card(mid))

        await self.click(self.a, mid)
        self.feishu.messages[mid] = card  # the user taps the stale copy again
        response = await self.click(self.a, mid)

        self.assertEqual(len(self.a.registry.resolutions), 1)
        self.assertNotEqual(response["toast"]["type"], "success")

    async def test_web_answer_during_delay_sends_nothing(self) -> None:
        self.a.bridge._save_config(self.a.bridge.config.model_copy(update={"delay_seconds": 60}))
        self.a.registry.open_gate("g1")
        await settle()
        self.assertEqual(self.feishu.sent, [])

        self.a.registry.answer_on_web("g1")
        await settle()

        self.assertEqual(self.feishu.sent, [])
        kinds = [event["state"] for _, event in self.a.registry.events]
        self.assertEqual(kinds, ["scheduled", "cancelled"])

    async def test_node_end_after_send_retires_card(self) -> None:
        mid = await self.ask(self.a, "g1")

        self.a.registry.end_node("g1")
        await settle()

        self.assertEqual(self.feishu.title(mid), "提问已结束")
        self.assertEqual(load_relay_ledger(self.a.root)["g1"]["state"], "ended")

    async def test_gate_closing_while_sending_is_patched_after_send(self) -> None:
        gate = asyncio.Event()
        original = self.a.transport.send_card

        async def slow_send(*args: Any, **kwargs: Any) -> str:
            await gate.wait()
            return await original(*args, **kwargs)

        self.a.transport.send_card = slow_send  # type: ignore[method-assign]
        self.a.registry.open_gate("g1")
        await settle()
        self.a.registry.answer_on_web("g1")
        gate.set()
        await settle()

        mid = self.feishu.sent[-1]["mid"]
        self.assertEqual(self.feishu.title(mid), "已在网页端回答")

    async def test_secret_question_is_not_answerable_on_feishu(self) -> None:
        mid = await self.ask(
            self.a,
            "g1",
            questions=[{"id": "token", "question": "API token?", "isSecret": True, "options": None}],
        )
        card = self.feishu.card(mid)
        self.assertFalse(any(element.get("tag") == "form" for element in cards._walk(card)))
        self.assertIn("敏感输入", json.dumps(card, ensure_ascii=False))

    async def test_relay_events_and_usage_are_tracked(self) -> None:
        await self.ask(self.a, "g1")

        states = [event["state"] for _, event in self.a.registry.events]
        self.assertEqual(states, ["scheduled", "sent"])
        self.assertTrue(all(event["seq"] == 0 for _, event in self.a.registry.events))
        self.assertEqual(set(self.a.bridge.relays_for("p1")), {"g1"})
        usage = load_usage(self.a.root)
        self.assertEqual(usage["by_kind"]["send"], 1)

    async def test_disabled_host_stays_connected_and_relays(self) -> None:
        await self.b.bridge.update_settings({"enabled": False})
        sends = self.feishu.calls.get("send", 0)
        self.b.registry.open_gate("gb")
        await settle()
        self.assertEqual(self.feishu.calls.get("send", 0), sends)

        mid = await self.ask(self.a, "g1")
        await self.click(self.b, mid)
        self.assertIsNotNone(cards.find_receipt(self.feishu.card(mid)))

    async def test_late_receipt_is_patched_and_owner_settles(self) -> None:
        mid = await self.ask(self.a, "g1")
        action = CardAction(
            operator_open_id=OPEN_ID,
            value=submit_value(self.feishu.card(mid)),
            form_value=copy.deepcopy(FORM),
            message_id=mid,
        )
        # The response missed the deadline, so Feishu never applied its card.
        response = await self.b.transport.handlers.on_card_action(action)
        self.assertIsNone(cards.find_receipt(self.feishu.card(mid)))

        self.b.transport.handlers.on_late_response(action, response)
        await settle()

        self.assertIsNotNone(cards.find_receipt(self.feishu.card(mid)))
        await self.a.bridge.poll_now(force=True)
        await settle()
        self.assertEqual(self.a.registry.resolutions[0]["via"], "HOST-B")
        self.assertEqual(self.feishu.title(mid), "已回答 · 经 HOST-B 中转")

    async def test_late_local_answer_still_shows_final_card(self) -> None:
        mid = await self.ask(self.a, "g1")
        action = CardAction(
            operator_open_id=OPEN_ID,
            value=submit_value(self.feishu.card(mid)),
            form_value=copy.deepcopy(FORM),
            message_id=mid,
        )
        response = await self.a.transport.handlers.on_card_action(action)
        await settle()
        self.assertEqual(len(self.a.registry.resolutions), 1)
        # The gate's close relied on the callback card and PATCHed nothing.
        self.assertEqual(self.feishu.patches, [])

        self.a.transport.handlers.on_late_response(action, response)
        await settle()

        self.assertEqual(self.feishu.title(mid), "已回答")

    async def test_unbind_while_sending_retires_the_late_card(self) -> None:
        release = asyncio.Event()
        original = self.b.transport.send_card

        async def slow_send(*args: Any, **kwargs: Any) -> str:
            await release.wait()
            return await original(*args, **kwargs)

        self.b.transport.send_card = slow_send  # type: ignore[method-assign]
        self.b.registry.open_gate("gb")
        await settle()

        await self.b.bridge.unbind()
        release.set()
        await settle()

        mid = self.feishu.sent[-1]["mid"]
        self.assertEqual(self.feishu.title(mid), "提问已转回网页端")
        self.assertNotIn("gb", load_relay_ledger(self.b.root))
        self.assertEqual(self.b.registry.events[-1][1]["state"], "cancelled")
        self.assertEqual(self.b.bridge.relays_for("p1"), {})
        await self.b.bridge.poll_now(force=True)
        self.assertEqual(self.b.registry.resolutions, [])

    async def test_switching_app_while_sending_resends_on_the_new_app(self) -> None:
        release = asyncio.Event()
        old_transport = self.b.transport
        original = old_transport.send_card

        async def slow_send(*args: Any, **kwargs: Any) -> str:
            await release.wait()
            return await original(*args, **kwargs)

        old_transport.send_card = slow_send  # type: ignore[method-assign]
        self.b.registry.open_gate("gb")
        await settle()

        self.b.transport = FakeTransport(self.feishu)
        await self.b.bridge.update_settings({"app_id": "cli_other", "app_secret": "s2"})
        # Pairing on the new app, condensed.
        self.b.bridge._save_config(self.b.bridge.config.model_copy(update={"bound_open_id": OPEN_ID}))
        release.set()
        await settle()

        late = self.feishu.sent[0]["mid"]
        self.assertEqual(self.feishu.title(late), "提问已转回网页端")
        fresh = self.feishu.sent[-1]["mid"]
        self.assertNotEqual(fresh, late)
        self.assertEqual(load_relay_ledger(self.b.root)["gb"]["mid"], fresh)

    async def test_peer_joining_during_solo_wait_is_seen_within_a_minute(self) -> None:
        await self.b.bridge.stop()
        self.a.now = time.time()
        mid = await self.ask(self.a, "g1")
        await self.a.bridge.poll_now()  # alone: the card is not shared yet
        self.assertEqual(self.feishu.calls.get("mget", 0), 0)

        # B joins and relays a click; A's last count still says one connection.
        self.feishu.connected.append(self.b.transport)
        await self.click(self.b, mid)
        self.a.now += SOLO_CONNECTION_CACHE_SECONDS
        await self.a.bridge.poll_now()
        await settle()

        self.assertEqual(self.a.registry.resolutions[0]["via"], "HOST-B")

    async def test_unreachable_cards_block_unbind_until_forced(self) -> None:
        mid = await self.ask(self.b, "gb")
        self.b.transport.unreachable.add("patch")

        with self.assertRaises(FeishuBridgeError) as raised:
            await self.b.bridge.unbind()

        self.assertEqual(raised.exception.state, UNRETIRED_CARDS_STATE)
        self.assertTrue(feishu_config_path(self.b.root).exists())
        self.assertIn(self.b.transport, self.feishu.connected)
        self.assertEqual(load_relay_ledger(self.b.root)["gb"]["state"], "sent")
        # Still bound, the card still works.
        await self.click(self.b, mid)
        self.assertEqual(self.b.registry.resolutions[0]["gate_id"], "gb")

        self.b.registry.open_gate("gb2")
        await settle()
        await self.b.bridge.unbind(force=True)
        self.assertFalse(feishu_config_path(self.b.root).exists())

    async def test_unbind_once_feishu_answers_again_retires_the_card(self) -> None:
        mid = await self.ask(self.b, "gb")
        self.b.transport.unreachable.add("patch")
        with self.assertRaises(FeishuBridgeError):
            await self.b.bridge.unbind()

        self.b.transport.unreachable.clear()
        await self.b.bridge.unbind()

        self.assertEqual(self.feishu.title(mid), "提问已转回网页端")
        self.assertFalse(feishu_config_path(self.b.root).exists())

    async def test_rejected_patch_does_not_block_unbind(self) -> None:
        await self.ask(self.b, "gb")
        self.feishu.fail["patch"] = FeishuApiError("patch", 230011, "message recalled")

        await self.b.bridge.unbind()

        self.assertFalse(feishu_config_path(self.b.root).exists())

    async def test_unreachable_cards_survive_a_restart_sweep(self) -> None:
        mid = await self.ask(self.a, "g1")
        await self.a.bridge.stop()
        self.a.transport = FakeTransport(self.feishu)
        self.a.transport.unreachable.add("patch")
        self.a.registry.gates.clear()
        self.a.bridge = self.a._new_bridge()
        await self.a.start()

        self.assertEqual(load_relay_ledger(self.a.root)["g1"]["state"], "sent")
        self.assertIn("未能改为失效", (await self.a.bridge.status())["last_error"])

        await self.a.restart()
        self.assertEqual(self.feishu.title(mid), "提问已失效")
        self.assertEqual(load_relay_ledger(self.a.root)["g1"]["state"], "stale")

    async def test_switching_app_with_unreachable_cards_completes_and_reports(self) -> None:
        await self.ask(self.b, "gb")
        self.b.transport.unreachable.add("patch")
        self.b.transport = FakeTransport(self.feishu)

        await self.b.bridge.update_settings({"app_id": "cli_other", "app_secret": "s2"})

        self.assertEqual(self.b.bridge.config.app_id, "cli_other")
        self.assertEqual(load_relay_ledger(self.b.root)["gb"]["state"], "unretired")
        self.assertIn("1 张卡片未能改为失效", (await self.b.bridge.status())["last_error"])

    async def test_manual_binding_reports_missing_pairing_scope(self) -> None:
        self.a.bridge._save_config(
            self.a.bridge.config.model_copy(update={"binding_source": "manual"})
        )

        await self.a.bridge._refresh_scopes()

        status = await self.a.bridge.status()
        self.assertEqual(status["scopes"]["missing"], ["im:message.p2p_msg:readonly"])
        self.assertEqual(status["scopes"]["unexpected"], [])

    async def test_unbind_disconnects_and_retires_cards(self) -> None:
        mid = await self.ask(self.b, "gb")

        await self.b.bridge.unbind()

        self.assertNotIn(self.b.transport, self.feishu.connected)
        self.assertEqual(self.feishu.title(mid), "提问已转回网页端")
        self.assertFalse(feishu_config_path(self.b.root).exists())
        status = await self.b.bridge.status()
        self.assertFalse(status["configured"])
        self.assertEqual(status["connection"]["state"], "off")


class FeishuRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.root = Path(self.tmp.name) / "home"
        self.feishu = FakeFeishu()
        self.registry = FakeRegistry(self.root)
        self.calls: list[dict[str, Any]] = []
        self.release = asyncio.Event()
        self.result: dict[str, Any] = {
            "client_id": "cli_new",
            "client_secret": "s3cret",
            "user_info": {"open_id": OPEN_ID, "tenant_brand": "feishu"},
        }

        async def registrar(**kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            kwargs["on_qr_code"]({"url": "https://accounts.feishu.cn/x?user_code=ABCD", "expire_in": 600})
            await self.release.wait()
            return self.result

        self.transport = FakeTransport(self.feishu)
        self.bridge = FeishuBridge(
            self.registry,
            transport_factory=lambda _config: self.transport,
            registrar=registrar,
            sdk_available=lambda: True,
        )
        self.registry.observer = self.bridge
        await self.bridge.start()
        await settle()

    async def asyncTearDown(self) -> None:
        await self.bridge.stop()
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    async def test_create_binds_scanner_and_connects(self) -> None:
        begun = await self.bridge.begin_registration("create")
        self.assertEqual(begun["state"], "polling")
        self.assertTrue(begun["url"].startswith("https://accounts.feishu.cn/"))
        self.assertTrue(self.calls[0]["create_only"])
        self.assertEqual(self.calls[0]["addons"], MINIMAL_ADDONS)
        self.assertIs(self.calls[0]["addons"]["preset"], False)

        self.release.set()
        await settle(40)

        config = load_feishu_config(self.root)
        self.assertEqual(config.app_id, "cli_new")
        self.assertEqual(config.bound_open_id, OPEN_ID)
        self.assertEqual(config.binding_source, "created")
        self.assertEqual(oct(feishu_config_path(self.root).stat().st_mode & 0o777), "0o600")
        status = await self.bridge.status()
        self.assertEqual(status["registration"]["state"], "succeeded")
        self.assertIsNone(status["registration"]["url"])
        self.assertEqual(status["connection"]["state"], "connected")
        self.assertNotIn("s3cret", json.dumps(status))
        self.assertEqual(self.feishu.sent[0]["card"]["header"]["template"], "green")
        self.assertEqual(status["scopes"]["unexpected"], [])

    async def test_join_passes_app_id_with_minimal_addons(self) -> None:
        await self.bridge.begin_registration("join", "cli_existing")
        self.assertEqual(self.calls[0]["app_id"], "cli_existing")
        self.assertEqual(self.calls[0]["addons"], MINIMAL_ADDONS)
        self.assertNotIn("create_only", self.calls[0])
        self.result["client_id"] = "cli_existing"
        self.feishu.scopes = [*self.feishu.scopes, "docx:document:write_only"]
        self.release.set()
        await settle(40)
        status = await self.bridge.status()
        self.assertEqual(status["binding_source"], "joined")
        self.assertEqual(status["scopes"]["unexpected"], ["docx:document:write_only"])

    async def test_join_with_different_account_is_refused(self) -> None:
        save_feishu_config(
            self.root,
            FeishuConfig(app_id="cli_existing", app_secret="s", bound_open_id="ou_someone"),
        )
        self.bridge._config = None
        self.result.update(client_id="cli_existing")
        await self.bridge.begin_registration("join", "cli_existing")
        self.release.set()
        await settle(40)
        status = self.bridge.registration_status()
        self.assertEqual(status["state"], "failed")
        self.assertIn("另一个账号", status["error"])
        self.assertEqual(load_feishu_config(self.root).bound_open_id, "ou_someone")

    async def test_only_one_registration_at_a_time_and_cancel(self) -> None:
        await self.bridge.begin_registration("create")
        with self.assertRaises(FeishuBridgeError):
            await self.bridge.begin_registration("create")
        cancelled = await self.bridge.cancel_registration()
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertFalse(load_feishu_config(self.root).has_credentials)

    async def test_join_requires_an_app_id(self) -> None:
        with self.assertRaises(FeishuBridgeError):
            await self.bridge.begin_registration("join", "nope")

    async def test_non_owner_refuses_to_bind(self) -> None:
        other = FeishuBridge(FakeRegistry(Path(self.tmp.name) / "other", owner=False), sdk_available=lambda: True)
        await other.start()
        with self.assertRaises(FeishuBridgeError):
            await other.begin_registration("create")
        status = await other.status()
        self.assertFalse(status["runtime_owner"])
        await other.stop()


class FeishuStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_owner_never_connects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_feishu_config(root, FeishuConfig(app_id=APP_ID, app_secret="s", bound_open_id=OPEN_ID))
            feishu = FakeFeishu()
            registry = FakeRegistry(root, owner=False)
            bridge = FeishuBridge(
                registry,
                transport_factory=lambda _config: FakeTransport(feishu),
                sdk_available=lambda: True,
            )
            registry.observer = bridge
            await bridge.start()
            await settle()
            registry.open_gate("g1")
            await settle()
            self.assertEqual(feishu.connected, [])
            self.assertEqual(feishu.sent, [])
            await bridge.stop()

    async def test_missing_sdk_reports_install_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_feishu_config(root, FeishuConfig(app_id=APP_ID, app_secret="s", bound_open_id=OPEN_ID))
            bridge = FeishuBridge(FakeRegistry(root), sdk_available=lambda: False)
            await bridge.start()
            await settle()
            status = await bridge.status()
            self.assertEqual(status["connection"]["state"], "error")
            self.assertIn("lark-oapi==1.7.3", status["connection"]["detail"])
            await bridge.stop()

    async def test_answered_record_is_not_swept_as_expired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_feishu_config(root, FeishuConfig(app_id=APP_ID, app_secret="s", bound_open_id=OPEN_ID))
            save_relay_ledger(root, {
                "done": {"mid": "om_done", "state": "settled", "sent_at": 1.0, "closed_at": time.time()},
                "old": {"mid": "om_old", "state": "sent", "sent_at": 1.0},
            })
            feishu = FakeFeishu()
            feishu.messages["om_old"] = {"header": {"title": {"content": "pending"}}}
            bridge = FeishuBridge(
                FakeRegistry(root),
                transport_factory=lambda _config: FakeTransport(feishu),
                sdk_available=lambda: True,
            )
            await bridge.start()
            await settle()
            ledger = load_relay_ledger(root)
            self.assertEqual(ledger["done"]["state"], "settled")
            # Older than PATCH's 14-day window: marked, not PATCHed.
            self.assertEqual(ledger["old"]["state"], "expired")
            self.assertEqual(feishu.patches, [])
            await bridge.stop()


class FeishuApiTests(unittest.TestCase):
    """HTTP surface: secrets stay write-only and lifespan wires the observer."""

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from miniclaw2.app import create_app
        from miniclaw2.registry import ProjectRegistry

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.feishu = FakeFeishu()
        self.registry = ProjectRegistry(Store(self.root))
        self.bridge = FeishuBridge(
            self.registry,
            transport_factory=lambda _config: FakeTransport(self.feishu),
            sdk_available=lambda: True,
        )
        self.client_context = TestClient(create_app(self.registry, feishu_bridge=self.bridge))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_lifespan_attaches_bridge_and_stops_it(self) -> None:
        self.assertIs(self.registry.gate_observer, self.bridge)
        body = self.client.get("/feishu/status").json()
        self.assertTrue(body["runtime_owner"])
        self.assertFalse(body["configured"])
        self.assertEqual(body["host"]["label"], self.registry.store.machine.label)

    def test_settings_keep_secret_write_only_and_connect(self) -> None:
        response = self.client.put(
            "/feishu/settings",
            json={"app_id": "cli_manual", "app_secret": "top-secret", "delay_seconds": 5},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertNotIn("top-secret", response.text)
        self.assertEqual(body["app_id"], "cli_manual")
        self.assertEqual(body["binding_source"], "manual")
        self.assertEqual(body["delay_seconds"], 5)
        self.assertEqual(body["connection"]["state"], "connected")
        self.assertNotIn("top-secret", self.client.get("/feishu/status").text)
        pairing = self.client.post("/feishu/pairing").json()
        self.assertEqual(len(pairing["code"]), 6)
        self.assertEqual(self.client.post("/feishu/test").status_code, 409)  # not bound yet
        self.assertEqual(
            self.client.put("/feishu/settings", json={"poll_seconds": 1}).status_code, 422
        )
        self.assertEqual(
            self.client.put("/feishu/settings", json={"surprise": 1}).status_code, 422
        )

    def test_unbind_removes_credentials(self) -> None:
        self.client.put("/feishu/settings", json={"app_id": "cli_manual", "app_secret": "s"})
        body = self.client.delete("/feishu/binding").json()
        self.assertFalse(body["configured"])
        self.assertFalse(feishu_config_path(self.root).exists())
        self.assertEqual(self.feishu.connected, [])

    def test_unbind_with_live_cards_reports_state_and_force_proceeds(self) -> None:
        save_feishu_config(self.root, FeishuConfig(app_id="cli_manual", app_secret="s"))
        save_relay_ledger(
            self.root, {"g1": {"mid": "om_1", "state": "sent", "sent_at": time.time()}}
        )
        self.bridge._config = None
        self.bridge._ledger = load_relay_ledger(self.root)

        refused = self.client.delete("/feishu/binding")
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()["state"], UNRETIRED_CARDS_STATE)
        self.assertTrue(feishu_config_path(self.root).exists())

        body = self.client.delete("/feishu/binding?force=true").json()
        self.assertFalse(body["configured"])

    def test_register_validation_and_relays_endpoint(self) -> None:
        self.assertEqual(
            self.client.post("/feishu/register", json={"mode": "join", "app_id": "x"}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/feishu/register", json={"mode": "elsewhere"}).status_code, 422
        )
        self.assertIsNone(self.client.get("/feishu/register").json())
        self.assertEqual(self.client.get("/sessions/missing/feishu-relays").status_code, 404)


class CallbackDeadlineTests(unittest.TestCase):
    """``LarkTransport`` hands callbacks to a server loop that may be busy."""

    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.handled: list[CardAction] = []
        self.late: list[tuple[CardAction, dict[str, Any]]] = []
        self.release = threading.Event()
        self.transport = LarkTransport(app_id=APP_ID, app_secret="s")
        self.transport._main_loop = self.loop
        self.transport._handlers = TransportHandlers(
            on_card_action=self._handle,
            on_late_response=lambda action, out: self.late.append((action, out)),
        )
        self.action = CardAction(OPEN_ID, {"a": "submit"}, {}, "om_1")
        self.deadline = feishu_transport.CALLBACK_DEADLINE_SECONDS
        feishu_transport.CALLBACK_DEADLINE_SECONDS = 0.05

    def tearDown(self) -> None:
        feishu_transport.CALLBACK_DEADLINE_SECONDS = self.deadline
        self.release.set()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()

    async def _handle(self, action: CardAction) -> dict[str, Any]:
        self.handled.append(action)
        await asyncio.to_thread(self.release.wait, 5)
        return cards.with_card(cards.toast("success", "已提交"), {"header": {}})

    def _drain(self) -> None:
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0.05), self.loop).result(5)

    def test_handler_that_never_started_is_abandoned(self) -> None:
        self.loop.call_soon_threadsafe(time.sleep, 0.3)  # the server loop is busy

        out = self.transport._answer_card_action(self.action)

        self.assertEqual(out["toast"]["type"], "warning")
        self.assertNotIn("card", out)
        self.release.set()
        self._drain()
        self.assertEqual(self.handled, [])
        self.assertEqual(self.late, [])

    def test_started_handler_hands_its_card_back_late(self) -> None:
        out = self.transport._answer_card_action(self.action)

        self.assertEqual(out["toast"]["type"], "info")
        self.assertEqual(self.handled, [self.action])
        self.release.set()
        self._drain()
        self._drain()
        self.assertEqual(len(self.late), 1)
        self.assertIn("card", self.late[0][1])


@unittest.skipUnless(importlib.util.find_spec("lark_oapi"), "lark-oapi not installed")
class LarkSdkContractTests(unittest.TestCase):
    """The pinned SDK still has the private members a clean stop relies on."""

    def test_ws_client_private_members_exist(self) -> None:
        from lark_oapi.ws import client as wsmod

        client = wsmod.Client("cli_x", "secret")
        self.assertTrue(hasattr(client, "_auto_reconnect"))
        self.assertTrue(asyncio.iscoroutinefunction(client._disconnect))
        self.assertTrue(hasattr(client, "on_reconnected"))
        self.assertTrue(hasattr(wsmod, "loop"))

    def test_registration_accepts_addons_and_app_id(self) -> None:
        import inspect

        from lark_oapi.scene.registration import aregister_app

        parameters = inspect.signature(aregister_app).parameters
        for name in ("addons", "app_id", "create_only", "app_preset", "on_qr_code"):
            self.assertIn(name, parameters)


if __name__ == "__main__":
    unittest.main()
