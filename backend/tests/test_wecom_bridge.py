"""WeCom remote answers: one bot per host, one card per question, quoted text replies.

``FakeWecom`` stands in for the WeCom side: one bot, one live connection at a
time (a newer connection kicks the older with ``disconnected_event``), pushes
refused with 846607 until the user has written to the bot, and a card that
only changes when a click is answered within 5 s. ``Host`` is one MiniClaw2
backend with its own store and bridge. No test opens a socket.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from miniclaw2 import wecom_bridge
from miniclaw2 import wecom_cards as cards
from miniclaw2 import wecom_transport
from miniclaw2.feishu_config import FeishuConfig, save_feishu_config
from miniclaw2.gate_observer import GateNotice, GateOutcome
from miniclaw2.remote_answers import RemoteAnswers, RemoteAnswersError, load_channel, save_channel
from miniclaw2.store import Store
from miniclaw2.wecom_bridge import WecomBridge, WecomBridgeError
from miniclaw2.wecom_config import (
    WecomConfig,
    load_wecom_config,
    save_wecom_config,
    wecom_config_path,
)
from miniclaw2.wecom_transport import (
    FREQUENCY_LIMIT_CODE,
    CardEvent,
    IncomingMessage,
    TransportHandlers,
    WecomApiError,
    WecomNotConnected,
    WsTransport,
)

USER = "T57560052A"
BOT_ID = "aib_test"

DB = {"id": "db", "header": "数据库", "question": "用哪个数据库？", "options": [{"label": "Postgres"}, {"label": "SQLite"}]}
AUTH = {"id": "auth", "header": "鉴权", "question": "鉴权方式？", "options": [{"label": "JWT"}, {"label": "Session"}]}
EXTRAS = {"id": "extras", "header": "附加", "question": "还要哪些？", "multiSelect": True, "options": [{"label": "缓存"}, {"label": "队列"}, {"label": "搜索"}]}
NOTE = {"id": "note", "header": "备注", "question": "还有什么要说明？", "options": None}


class FakeWecom:
    def __init__(self) -> None:
        self.connections: list[FakeTransport] = []
        self.messages: list[dict[str, Any]] = []
        self.cards: dict[str, dict[str, Any]] = {}
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.talked: set[str] = set()
        self.fail_next: list[Exception] = []
        self.clock = 0.0
        self._req = 0

    def connect(self, transport: FakeTransport) -> None:
        for old in self.connections:
            old.kicked()
        self.connections = [transport]

    @property
    def live(self) -> FakeTransport | None:
        return self.connections[0] if self.connections else None

    def push(self, kind: str, chatid: str, payload: Any) -> None:
        if self.fail_next:
            raise self.fail_next.pop(0)
        if chatid not in self.talked:
            raise WecomApiError("aibot_send_msg", FREQUENCY_LIMIT_CODE, "aibot send msg frequency limit exceeded")
        self.messages.append({"kind": kind, "to": chatid, "payload": copy.deepcopy(payload)})
        if kind == "card":
            self.cards[payload["task_id"]] = copy.deepcopy(payload)

    def markdowns(self) -> list[str]:
        return [item["payload"] for item in self.messages if item["kind"] == "markdown"]

    def sent_cards(self) -> list[dict[str, Any]]:
        return [item["payload"] for item in self.messages if item["kind"] == "card"]

    async def say(self, text: str, *, userid: str = USER, quote: str | None = None) -> None:
        self.talked.add(userid)
        transport = self.live
        assert transport is not None and transport.handlers is not None
        assert transport.handlers.on_message is not None
        await transport.handlers.on_message(
            IncomingMessage(userid=userid, chattype="single", msgtype="text", text=text, quote_text=quote)
        )
        await settle()

    async def click(
        self,
        task_id: str,
        *,
        key: str = cards.SUBMIT_KEY,
        selections: dict[str, list[str]] | None = None,
        userid: str = USER,
    ) -> dict[str, Any] | None:
        transport = self.live
        assert transport is not None and transport.handlers is not None
        card = self.cards.get(task_id, {})
        self._req += 1
        event = CardEvent(
            req_id=f"req{self._req}",
            userid=userid,
            chattype="single",
            card_type=card.get("card_type", "vote_interaction"),
            task_id=task_id,
            event_key=key,
            selections=selections or {},
        )
        # The bridge answers at once, inside WeCom's 5 s window, so the card changes.
        replacement = await transport.handlers.on_card_event(event)
        if replacement is not None:
            self.updates.append((task_id, replacement))
            self.cards[task_id] = copy.deepcopy(replacement)
        await settle()
        return replacement


class FakeTransport:
    def __init__(self, wecom: FakeWecom) -> None:
        self.wecom = wecom
        self.handlers: TransportHandlers | None = None
        self._state = "off"

    @property
    def state(self) -> str:
        return self._state

    async def start(self, handlers: TransportHandlers) -> None:
        self.handlers = handlers
        self.wecom.connect(self)
        self._set("connected", None)

    async def stop(self) -> None:
        if self in self.wecom.connections:
            self.wecom.connections.remove(self)
        self._set("off", None)

    def kicked(self) -> None:
        self._set("kicked", "kicked by a newer connection")

    def _set(self, state: str, detail: str | None) -> None:
        self._state = state
        if self.handlers is not None and self.handlers.on_state is not None:
            self.handlers.on_state(state, detail)  # type: ignore[arg-type]

    async def send_markdown(self, chatid: str, content: str) -> None:
        if self.wecom.live is not self or self._state != "connected":
            raise WecomNotConnected("not connected")
        self.wecom.push("markdown", chatid, content)

    async def send_card(self, chatid: str, card: dict[str, Any]) -> None:
        if self.wecom.live is not self or self._state != "connected":
            raise WecomNotConnected("not connected")
        self.wecom.push("card", chatid, card)


class FakeQr:
    def __init__(self) -> None:
        self.result: dict[str, Any] = {"data": {"status": "init"}}
        self.generated = 0

    async def generate(self) -> dict[str, Any]:
        self.generated += 1
        return {"data": {"scode": "sc1", "auth_url": "https://work.weixin.qq.com/ai/qc/c?s=sc1"}}

    async def query(self, scode: str) -> dict[str, Any]:
        return self.result


class FakeRegistry:
    """The registry surface the bridge uses, backed by a real Store."""

    def __init__(self, root: Path, *, owner: bool = True) -> None:
        self.store = Store(root=root)
        self.is_runtime_owner = owner
        self.gates: dict[str, dict[str, Any] | None] = {}
        self.resolutions: list[dict[str, Any]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.observer: Any = None

    def open_gate(self, gate_id: str, questions: list[dict[str, Any]]) -> GateNotice:
        notice = GateNotice(
            gate_id=gate_id,
            project_id="p1",
            node_id=f"n-{gate_id}",
            subtype="ask_user",
            tool_name="AskUserQuestion",
            questions=questions,
            project_name="demo",
            lane_title="lane",
            node_label="build the thing",
            created_at=time.time(),
        )
        self.gates[gate_id] = None
        self.observer.opened(notice)
        return notice

    def resolve_gate(self, pid: str, gate_id: str, **kwargs: Any) -> bool:
        if gate_id not in self.gates or self.gates[gate_id] is not None:
            return False
        self.gates[gate_id] = kwargs
        self.resolutions.append({"gate_id": gate_id, **kwargs})
        loop = asyncio.get_running_loop()
        # The runner's ``finally`` notifies the observer on a later iteration.
        loop.call_soon(
            self.observer.closed,
            gate_id,
            GateOutcome(kind="answered", source=kwargs.get("source"), response=kwargs.get("response")),
        )
        return True

    def answer_on_web(self, gate_id: str) -> None:
        self.gates[gate_id] = {"source": "web"}
        self.observer.closed(gate_id, GateOutcome(kind="answered", source="web", response={"answers": {}}))

    def end_node(self, gate_id: str) -> None:
        self.gates[gate_id] = {"ended": True}
        self.observer.closed(gate_id, GateOutcome(kind="ended"))

    def publish_project_event(self, pid: str, event: dict[str, Any]) -> None:
        self.events.append((pid, event))


class Host:
    def __init__(self, wecom: FakeWecom, root: Path, *, label: str = "HOST-A", bound: bool = True) -> None:
        self.root = root
        self.wecom = wecom
        self.qr = FakeQr()
        self.registry = FakeRegistry(root)
        self.registry.store.machine = self.registry.store.machine.__class__(
            **{**self.registry.store.machine.payload(), "label": label}
        )
        save_wecom_config(
            root,
            WecomConfig(
                bot_id=BOT_ID,
                secret="secret",
                bound_userid=USER if bound else None,
                binding_source="created",
                delay_seconds=0,
            ),
        )
        if bound:
            wecom.talked.add(USER)
        self.bridge = self.new_bridge()

    def new_bridge(self) -> WecomBridge:
        bridge = WecomBridge(
            self.registry,
            transport_factory=lambda _config: FakeTransport(self.wecom),
            qr_api=self.qr,
            transport_available=lambda: True,
        )
        self.registry.observer = bridge
        return bridge

    async def start(self) -> None:
        await self.bridge.start()
        await settle()


async def settle(rounds: int = 30) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


def detail_of(wecom: FakeWecom, index: int = -1) -> str:
    return [text for text in wecom.markdowns() if text.startswith("**MiniClaw2 需要你的回答**")][index]


def prompt_for(wecom: FakeWecom, number: int) -> str:
    return [text for text in wecom.markdowns() if text.startswith(f"**第 {number} 题 · 请用文字回答**")][-1]


def quoted(card: dict[str, Any]) -> str:
    """What a quote of a card may carry; untested on a real client, so the
    bridge reads any of its strings (``parse_message`` joins them all)."""
    return "\n".join([card["main_title"]["title"], card["main_title"].get("desc", ""), card["sub_title_text"]])


class WecomBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        patcher = mock.patch.object(wecom_bridge, "SEND_RETRY_DELAYS", (0.0, 0.0))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.wecom = FakeWecom()
        self.host = Host(self.wecom, Path(self.tmp.name) / "a")
        await self.host.start()

    async def asyncTearDown(self) -> None:
        await self.host.bridge.stop()
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    async def ask(self, gate_id: str, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        before = len(self.wecom.sent_cards())
        self.host.registry.open_gate(gate_id, questions)
        await settle()
        return self.wecom.sent_cards()[before:]

    @property
    def resolutions(self) -> list[dict[str, Any]]:
        return self.host.registry.resolutions

    async def test_single_question_button_click_resolves_at_once(self) -> None:
        sent = await self.ask("g1", [DB])

        self.assertEqual([card["card_type"] for card in sent], ["button_interaction"])
        detail = detail_of(self.wecom)
        self.assertIn("用哪个数据库？", detail)
        self.assertIn("A. Postgres", detail)
        replacement = await self.wecom.click(sent[0]["task_id"], key="q0:1")

        self.assertEqual(len(self.resolutions), 1)
        resolution = self.resolutions[0]
        self.assertEqual(resolution["source"], "wecom")
        self.assertEqual(resolution["response"], {"answers": {"db": {"answers": ["SQLite"]}}})
        assert replacement is not None
        self.assertIn("已回答", replacement["main_title"]["desc"])
        self.assertTrue(any(button["text"].startswith("✔") for button in replacement["button_list"]))
        # Answered here: no "answered elsewhere" message follows.
        self.assertFalse(any("已在网页端" in text or "已结束" in text for text in self.wecom.markdowns()))
        event = self.host.registry.events[-1][1]
        self.assertEqual((event["channel"], event["state"]), ("wecom", "sent"))

    async def test_each_question_gets_its_own_card_answered_in_any_order(self) -> None:
        sent = await self.ask("g1", [DB, AUTH, EXTRAS])
        self.assertEqual(
            [card["card_type"] for card in sent],
            ["button_interaction", "button_interaction", "vote_interaction"],
        )
        db, auth, extras = sent

        unchosen = await self.wecom.click(extras["task_id"], selections={})
        assert unchosen is not None
        self.assertIn("还没有选择", unchosen["main_title"]["desc"])
        self.assertNotIn("disable", unchosen["checkbox"])

        recorded = await self.wecom.click(extras["task_id"], selections={"q2": ["2", "0"]})
        assert recorded is not None
        self.assertIn("还差 2 题", recorded["main_title"]["desc"])
        self.assertTrue(recorded["checkbox"]["disable"])
        await self.wecom.click(auth["task_id"], key="q1:0")
        self.assertEqual(self.resolutions, [])

        await self.wecom.click(db["task_id"], key="q0:1")
        self.assertEqual(
            self.resolutions[0]["response"]["answers"],
            {"db": {"answers": ["SQLite"]}, "auth": {"answers": ["JWT"]}, "extras": {"answers": ["缓存", "搜索"]}},
        )

    async def test_other_then_reply_quoting_the_card_answers(self) -> None:
        sent = await self.ask("g1", [DB])
        before = len(self.wecom.markdowns())
        await self.wecom.click(sent[0]["task_id"], key="q0:o")

        self.assertEqual(self.resolutions, [])
        # No separate prompt: the card itself is what to quote.
        self.assertEqual(len(self.wecom.markdowns()), before)
        card = self.wecom.cards[sent[0]["task_id"]]
        self.assertIn("引用", card["main_title"]["desc"])

        await self.wecom.say("用 k8s 自建", quote=quoted(card))

        self.assertEqual(self.resolutions[0]["response"]["answers"], {"db": {"answers": ["用 k8s 自建"]}})
        self.assertIn("全部题目已回答", self.wecom.markdowns()[-1])

    async def test_several_others_each_answered_by_quoting_their_card(self) -> None:
        """The case one card per question exists for: two 「其他」 at once."""
        sent = await self.ask("g1", [DB, AUTH, EXTRAS])
        db, auth, extras = sent
        await self.wecom.click(db["task_id"], key="q0:o")
        await self.wecom.click(extras["task_id"], selections={"q2": ["o"]})
        await self.wecom.click(auth["task_id"], key="q1:1")

        # Quoting either card names exactly one question, whatever else waits for text.
        await self.wecom.say("队列和对象存储", quote=quoted(extras))
        self.assertIn("第 3 题", self.wecom.markdowns()[-1])
        self.assertIn("还差第 1 题", self.wecom.markdowns()[-1])
        await self.wecom.say("用 TiDB", quote=quoted(self.wecom.cards[db["task_id"]]))

        self.assertEqual(
            self.resolutions[0]["response"]["answers"],
            {
                "db": {"answers": ["用 TiDB"]},
                "auth": {"answers": ["Session"]},
                "extras": {"answers": ["队列和对象存储"]},
            },
        )

    async def test_quoting_a_card_overrides_its_choice_and_a_tap_overrides_other(self) -> None:
        sent = await self.ask("g1", [DB, AUTH])
        db, auth = sent
        await self.wecom.click(db["task_id"], key="q0:o")
        # Changing one's mind: a tap on a real option replaces 「其他」.
        await self.wecom.click(db["task_id"], key="q0:0")
        # Text quoting a card answers its question even without 「其他」 first.
        await self.wecom.say("JWT + 刷新令牌", quote=quoted(auth))

        self.assertEqual(
            self.resolutions[0]["response"]["answers"],
            {"db": {"answers": ["Postgres"]}, "auth": {"answers": ["JWT + 刷新令牌"]}},
        )

    async def test_plain_message_is_never_an_answer(self) -> None:
        """Only replies that quote a question message count (user decision)."""
        sent = await self.ask("g1", [DB])
        await self.wecom.click(sent[0]["task_id"], key="q0:o")
        before = len(self.wecom.markdowns())

        await self.wecom.say("用k8s")

        self.assertEqual(self.resolutions, [])
        hints = self.wecom.markdowns()[before:]
        self.assertEqual(len(hints), 1)
        self.assertIn("引用", hints[0])
        # A second plain message within the window gets no second hint.
        await self.wecom.say("还是用k8s")
        self.assertEqual(len(self.wecom.markdowns()), before + 1)
        self.assertEqual(self.resolutions, [])

    async def test_plain_message_without_pending_text_is_ignored(self) -> None:
        await self.ask("g1", [DB])
        before = len(self.wecom.markdowns())
        await self.wecom.say("你好")
        self.assertEqual(len(self.wecom.markdowns()), before)

    async def test_text_only_gate_answered_by_quoting_detail(self) -> None:
        sent = await self.ask("g1", [NOTE])
        self.assertEqual(sent, [])
        detail = detail_of(self.wecom)

        await self.wecom.say("无", quote=detail)

        self.assertEqual(self.resolutions[0]["response"]["answers"], {"note": {"answers": ["无"]}})

    async def test_question_without_options_gets_its_prompt_in_place(self) -> None:
        self.host.registry.open_gate("g1", [DB, NOTE, AUTH])
        await settle()
        kinds = [
            item["payload"].get("card_type") if item["kind"] == "card" else item["payload"].split("\n")[0]
            for item in self.wecom.messages
        ]
        self.assertTrue(kinds[0].startswith("**MiniClaw2 需要你的回答**"))
        self.assertEqual(kinds[1], "button_interaction")
        self.assertTrue(kinds[2].startswith("**第 2 题 · 请用文字回答** #"))
        self.assertEqual(kinds[3], "button_interaction")
        db, auth = self.wecom.sent_cards()

        await self.wecom.say("改一下备注", quote=prompt_for(self.wecom, 2))
        await self.wecom.click(db["task_id"], key="q0:0")
        await self.wecom.click(auth["task_id"], key="q2:1")

        self.assertEqual(
            self.resolutions[0]["response"]["answers"],
            {"db": {"answers": ["Postgres"]}, "note": {"answers": ["改一下备注"]}, "auth": {"answers": ["Session"]}},
        )

    async def test_quoting_the_detail_of_several_questions_is_refused(self) -> None:
        """Even when only one question waits for text: a reply never depends on timing."""
        sent = await self.ask("g1", [DB, NOTE])
        await self.wecom.click(sent[0]["task_id"], key="q0:0")
        detail = detail_of(self.wecom)

        await self.wecom.say("先记一笔", quote=detail)

        self.assertEqual(self.resolutions, [])
        self.assertIn("无法确定", self.wecom.markdowns()[-1])
        self.assertIn("卡片", self.wecom.markdowns()[-1])
        await self.wecom.say("备注的答案", quote=prompt_for(self.wecom, 2))
        self.assertEqual(
            self.resolutions[0]["response"]["answers"],
            {"db": {"answers": ["Postgres"]}, "note": {"answers": ["备注的答案"]}},
        )

    async def test_quote_naming_several_questions_is_refused(self) -> None:
        sent = await self.ask("g1", [DB, AUTH])
        await self.wecom.say("两题一起答", quote=quoted(sent[0]) + "\n" + quoted(sent[1]))
        self.assertEqual(self.resolutions, [])
        self.assertIn("无法确定", self.wecom.markdowns()[-1])

    async def test_quote_from_another_user_or_unknown_gate_is_ignored(self) -> None:
        await self.ask("g1", [NOTE])
        detail = detail_of(self.wecom)

        await self.wecom.say("冒充", userid="someone-else", quote=detail)
        self.assertEqual(self.resolutions, [])

        await self.wecom.say("旧提问", quote="**MiniClaw2 需要你的回答** #ZZZZ")
        self.assertEqual(self.resolutions, [])
        self.assertIn("已结束或已失效", self.wecom.markdowns()[-1])

    async def test_web_answer_first_announces_and_click_gets_closed_card(self) -> None:
        sent = await self.ask("g1", [DB, EXTRAS])

        self.host.registry.answer_on_web("g1")
        await settle()

        self.assertIn("已在网页端回答", self.wecom.markdowns()[-1])
        replacement = await self.wecom.click(sent[1]["task_id"], selections={"q1": ["0"]})
        assert replacement is not None
        self.assertEqual(replacement["main_title"]["desc"], "已在网页端回答")
        self.assertTrue(replacement["checkbox"]["disable"])
        self.assertEqual(self.resolutions, [])
        await self.wecom.say("晚到的文字", quote=quoted(sent[0]))
        self.assertIn("已结束或已失效", self.wecom.markdowns()[-1])
        self.assertEqual(self.resolutions, [])

    async def test_node_end_and_unknown_task_get_expired_cards(self) -> None:
        sent = await self.ask("g1", [DB])
        self.host.registry.end_node("g1")
        await settle()
        self.assertIn("已结束", self.wecom.markdowns()[-1])
        replacement = await self.wecom.click(sent[0]["task_id"], key="q0:0")
        assert replacement is not None
        self.assertEqual([button["text"] for button in replacement["button_list"]], ["已失效"])

        # A card from before a restart: this process has never seen it.
        unknown = await self.wecom.click("mc2-old-1-abc", selections={"q0": ["0"]})
        assert unknown is not None
        self.assertEqual(unknown["main_title"]["title"], "提问已失效")
        self.assertEqual(unknown["task_id"], "mc2-old-1-abc")

    async def test_clicks_from_others_are_ignored(self) -> None:
        sent = await self.ask("g1", [DB])
        self.assertIsNone(await self.wecom.click(sent[0]["task_id"], key="q0:0", userid="intruder"))
        self.assertEqual(self.resolutions, [])

    async def test_repeat_click_after_answer_is_harmless(self) -> None:
        sent = await self.ask("g1", [DB])
        await self.wecom.click(sent[0]["task_id"], key="q0:0")
        again = await self.wecom.click(sent[0]["task_id"], key="q0:1")
        self.assertEqual(len(self.resolutions), 1)
        assert again is not None
        self.assertIn("已回答", again["main_title"]["desc"])

    async def test_secret_question_is_notice_only(self) -> None:
        sent = await self.ask("g1", [{**DB, "isSecret": True}])
        self.assertEqual(sent, [])
        self.assertIn("敏感输入", self.wecom.markdowns()[-1])
        await self.wecom.say("密码", quote=self.wecom.markdowns()[-1])
        self.assertEqual(self.resolutions, [])

    async def test_kicked_connection_stops_pushing_and_reconnect_restores(self) -> None:
        intruder = FakeTransport(self.wecom)
        await intruder.start(TransportHandlers(on_card_event=lambda _e: asyncio.sleep(0)))
        status = await self.host.bridge.status()
        self.assertEqual(status["connection"]["state"], "kicked")

        sent = await self.ask("g1", [DB])
        self.assertEqual(sent, [])
        self.assertNotIn("g1", [e[1]["gate_id"] for e in self.host.registry.events if e[1]["state"] == "sent"])

        await self.host.bridge.reconnect()
        await settle()
        self.assertEqual((await self.host.bridge.status())["connection"]["state"], "connected")
        self.assertEqual(len(self.wecom.sent_cards()), 1)

    async def test_frequency_limit_retries_then_fails_visibly(self) -> None:
        error = WecomApiError("aibot_send_msg", FREQUENCY_LIMIT_CODE, "limited")
        # Three attempts with the retry delays patched to (0, 0).
        self.wecom.fail_next = [error] * 2
        sent = await self.ask("g1", [DB])
        self.assertEqual(len(sent), 1)  # recovered on the third try
        self.wecom.fail_next = [error] * 3
        await self.ask("g2", [AUTH])
        failed = [e[1] for e in self.host.registry.events if e[1]["gate_id"] == "g2"]
        self.assertEqual(failed[-1]["state"], "failed")

    async def test_ack_timeout_is_not_resent(self) -> None:
        self.wecom.fail_next = [TimeoutError("ack lost")]
        await self.ask("g1", [DB])
        # The detail's ack was lost; it is assumed delivered, not repeated.
        self.assertEqual(len([t for t in self.wecom.markdowns() if t.startswith("**MiniClaw2")]), 0)
        self.assertEqual(len(self.wecom.sent_cards()), 1)

    async def test_disabled_or_unbound_never_pushes(self) -> None:
        await self.host.bridge.update_settings({"enabled": False})
        self.assertEqual(await self.ask("g1", [DB]), [])
        await self.host.bridge.update_settings({"enabled": True})
        await settle()
        self.assertEqual(len(self.wecom.sent_cards()), 1)


class PairingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.wecom = FakeWecom()
        self.host = Host(self.wecom, Path(self.tmp.name) / "a", bound=False)
        await self.host.start()

    async def asyncTearDown(self) -> None:
        await self.host.bridge.stop()
        self.tmp.cleanup()

    async def test_pairing_code_claims_bot_and_welcome_follows(self) -> None:
        self.host.registry.open_gate("g1", [DB])
        await settle()
        self.assertEqual(self.wecom.messages, [])  # unclaimed: nothing is pushed

        code = self.host.bridge.start_pairing()["code"]
        await self.wecom.say("000000" if code != "000000" else "111111", userid="someone")
        self.assertIsNone(load_wecom_config(self.host.root).bound_userid)

        await self.wecom.say(code)

        self.assertEqual(load_wecom_config(self.host.root).bound_userid, USER)
        self.assertTrue(self.wecom.markdowns()[0].startswith("**已连接 · 本机：HOST-A**"))
        # The gate that waited for the claim goes out now.
        self.assertEqual(len(self.wecom.sent_cards()), 1)
        status = await self.host.bridge.status()
        self.assertIsNone(status["pairing"])
        self.assertEqual(status["bound"]["userid"], USER)

    async def test_expired_code_is_refused(self) -> None:
        code = self.host.bridge.start_pairing()["code"]
        self.host.bridge._pairing = (code, time.time() - 1)
        await self.wecom.say(code)
        self.assertIsNone(load_wecom_config(self.host.root).bound_userid)


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        for name, value in (("QR_POLL_SECONDS", 0.0),):
            patcher = mock.patch.object(wecom_bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.wecom = FakeWecom()
        self.qr = FakeQr()
        self.registry = FakeRegistry(self.root)
        self.bridge = WecomBridge(
            self.registry,
            transport_factory=lambda _config: FakeTransport(self.wecom),
            qr_api=self.qr,
            transport_available=lambda: True,
        )
        self.registry.observer = self.bridge
        await self.bridge.start()

    async def asyncTearDown(self) -> None:
        await self.bridge.stop()
        self.tmp.cleanup()

    async def test_scan_creates_bot_connects_and_starts_pairing(self) -> None:
        started = await self.bridge.begin_registration()
        self.assertEqual(started["state"], "polling")
        self.assertIn("scode=sc1", started["page_url"])
        self.assertIn("source=wecom_cli_external", started["page_url"])

        self.qr.result = {"data": {"status": "success", "bot_info": {"botid": "aib_new", "secret": "s3cret"}}}
        for _ in range(50):
            await asyncio.sleep(0)
            if self.bridge.registration_status()["state"] != "polling":
                break

        status = await self.bridge.status()
        self.assertEqual(status["registration"]["state"], "succeeded")
        self.assertEqual(status["bot_id"], "aib_new")
        self.assertEqual(status["binding_source"], "created")
        self.assertEqual(status["connection"]["state"], "connected")
        self.assertIsNotNone(status["pairing"])
        self.assertNotIn("s3cret", json.dumps(status))
        self.assertEqual(wecom_config_path(self.root).stat().st_mode & 0o777, 0o600)

    async def test_changed_response_shape_fails_with_manual_hint(self) -> None:
        async def broken() -> dict[str, Any]:
            return {"errcode": 1}

        self.qr.generate = broken  # type: ignore[method-assign]
        result = await self.bridge.begin_registration()
        self.assertEqual(result["state"], "failed")
        self.assertIn("手动填写", result["error"])

    async def test_cancel_and_single_flight(self) -> None:
        await self.bridge.begin_registration()
        with self.assertRaises(WecomBridgeError):
            await self.bridge.begin_registration()
        cancelled = await self.bridge.cancel_registration()
        self.assertEqual(cancelled["state"], "cancelled")

    async def test_manual_credentials_connect_and_unbind_forgets(self) -> None:
        await self.bridge.update_settings({"bot_id": "aib_manual", "secret": "s"})
        status = await self.bridge.status()
        self.assertEqual(status["binding_source"], "manual")
        self.assertEqual(status["connection"]["state"], "connected")
        self.assertIsNotNone(status["pairing"])
        with self.assertRaises(WecomBridgeError):
            await self.bridge.begin_registration()  # already has a bot

        await self.bridge.unbind()
        self.assertFalse(wecom_config_path(self.root).exists())
        self.assertEqual(self.wecom.connections, [])


class RemoteAnswersTests(unittest.IsolatedAsyncioTestCase):
    """One channel per host; switching retires the old pushes and re-announces gates."""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.root = Path(self.tmp.name)
        self.wecom = FakeWecom()
        self.wecom.talked.add(USER)
        self.registry = FakeRegistry(self.root)
        save_wecom_config(self.root, WecomConfig(bot_id=BOT_ID, secret="s", bound_userid=USER, delay_seconds=0))
        self.bridge = WecomBridge(
            self.registry,
            transport_factory=lambda _config: FakeTransport(self.wecom),
            transport_available=lambda: True,
        )
        self.feishu = _RecordingBridge()

    async def asyncTearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def router(self) -> RemoteAnswers:
        router = RemoteAnswers(self.registry, feishu=self.feishu, wecom=self.bridge)
        self.registry.observer = router
        return router

    async def test_default_prefers_the_configured_channel(self) -> None:
        router = self.router()
        await router.start()
        self.assertEqual(router.channel, "wecom")  # only WeCom has credentials
        await router.stop()
        save_feishu_config(self.root, FeishuConfig(app_id="cli_x", app_secret="s"))
        other = self.router()
        await other.start()
        self.assertEqual(other.channel, "feishu")  # Feishu stays the default when both exist
        await other.stop()

    async def test_switch_retires_pushes_and_reannounces_open_gates(self) -> None:
        router = self.router()
        await router.start()
        self.registry.open_gate("g1", [DB])
        await settle()
        task_id = self.wecom.sent_cards()[0]["task_id"]
        self.assertEqual(self.feishu.opened_notices, [])

        await router.set_channel("feishu")
        await settle()

        self.assertEqual(load_channel(self.root), "feishu")
        self.assertIn("改用飞书", self.wecom.markdowns()[-1])
        self.assertEqual(self.wecom.connections, [])
        self.assertTrue(load_wecom_config(self.root).has_credentials)  # kept for switching back
        self.assertEqual([notice.gate_id for notice in self.feishu.opened_notices], ["g1"])
        self.assertTrue(self.feishu.started)

        # A gate closing now reaches Feishu only.
        self.registry.answer_on_web("g1")
        self.assertEqual(self.feishu.closed_ids, ["g1"])

        await router.set_channel("wecom")
        await settle()
        self.assertTrue(self.feishu.deactivated)
        # The card pushed before the first switch only closes on click.
        replacement = await self.wecom.click(task_id, key="q0:0")
        assert replacement is not None
        self.assertEqual([button["text"] for button in replacement["button_list"]], ["已失效"])
        self.assertEqual(self.registry.resolutions, [])
        await router.stop()

    async def test_none_channel_pushes_nowhere(self) -> None:
        router = self.router()
        await router.start()
        await router.set_channel("none")
        self.registry.open_gate("g1", [DB])
        await settle()
        self.assertEqual(self.wecom.sent_cards(), [])
        self.assertEqual(self.feishu.opened_notices, [])
        await router.stop()

    async def test_concurrent_switches_leave_only_the_last_channel_connected(self) -> None:
        save_channel(self.root, "feishu")
        router = self.router()
        await router.start()
        self.feishu.hold.clear()  # both switches would capture Feishu as the old bridge

        to_wecom = asyncio.create_task(router.set_channel("wecom"))
        to_none = asyncio.create_task(router.set_channel("none"))
        await settle()
        self.feishu.hold.set()
        await asyncio.gather(to_wecom, to_none)

        self.assertEqual((router.channel, load_channel(self.root)), ("none", "none"))
        self.assertEqual(self.wecom.connections, [])
        self.assertFalse(self.feishu.started)
        self.registry.open_gate("g1", [DB])
        await settle()
        self.assertEqual(self.wecom.sent_cards(), [])
        await router.stop()

    async def test_change_in_flight_finishes_before_a_switch_away(self) -> None:
        hold = asyncio.Event()
        hold.set()
        self.bridge = WecomBridge(
            self.registry,
            transport_factory=lambda _config: _HeldTransport(self.wecom, hold),
            transport_available=lambda: True,
        )
        router = self.router()
        await router.start()
        hold.clear()

        async def reconnect() -> None:
            async with router.while_active("wecom"):
                await self.bridge.reconnect()

        change = asyncio.create_task(reconnect())
        await settle()
        switch = asyncio.create_task(router.set_channel("feishu"))
        await settle()
        self.assertFalse(self.feishu.started)  # the switch waits for the change
        hold.set()
        await asyncio.gather(change, switch)

        self.assertEqual(router.channel, "feishu")
        self.assertEqual(self.wecom.connections, [])  # the reconnect did not outlive the switch
        self.assertTrue(self.feishu.started)
        await router.stop()

    async def test_change_queued_behind_a_switch_away_is_refused(self) -> None:
        router = self.router()
        await router.start()
        self.feishu.hold.clear()
        switch = asyncio.create_task(router.set_channel("feishu"))
        await settle()

        async def reconnect() -> None:
            async with router.while_active("wecom"):
                await self.bridge.reconnect()

        change = asyncio.create_task(reconnect())
        # A gate opening mid-switch reaches the new channel once, after it started.
        self.registry.open_gate("g1", [DB])
        await settle()
        self.assertFalse(change.done())
        self.assertEqual(self.feishu.opened_notices, [])
        self.feishu.hold.set()
        await switch
        with self.assertRaises(RemoteAnswersError) as refused:
            await change

        self.assertEqual(refused.exception.status, 409)
        self.assertEqual(self.wecom.connections, [])
        self.assertEqual(self.wecom.sent_cards(), [])
        self.assertEqual([notice.gate_id for notice in self.feishu.opened_notices], ["g1"])
        await router.stop()


class _HeldTransport(FakeTransport):
    """Connects only once ``hold`` is set, like a slow handshake."""

    def __init__(self, wecom: FakeWecom, hold: asyncio.Event) -> None:
        super().__init__(wecom)
        self.hold = hold

    async def start(self, handlers: TransportHandlers) -> None:
        await self.hold.wait()
        await super().start(handlers)


class _RecordingBridge:
    """The Feishu side of the router, recording what reaches it."""

    def __init__(self) -> None:
        self.opened_notices: list[GateNotice] = []
        self.closed_ids: list[str] = []
        self.started = False
        self.deactivated = False
        #: Cleared to keep ``start`` and ``deactivate`` waiting, like the network.
        self.hold = asyncio.Event()
        self.hold.set()

    def opened(self, notice: GateNotice) -> None:
        self.opened_notices.append(notice)

    def closed(self, gate_id: str, outcome: GateOutcome) -> None:
        self.closed_ids.append(gate_id)

    async def start(self) -> None:
        await self.hold.wait()
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def deactivate(self, line: str) -> None:
        await self.hold.wait()
        self.deactivated = True
        await self.stop()

    def relays_for(self, project_id: str) -> dict[str, dict[str, Any]]:
        return {}


class TransportFrameTests(unittest.IsolatedAsyncioTestCase):
    """``WsTransport`` against a scripted socket: no network."""

    async def asyncSetUp(self) -> None:
        self.events: list[CardEvent] = []
        self.messages: list[IncomingMessage] = []
        self.states: list[tuple[str, str | None]] = []
        self.transport = WsTransport(bot_id=BOT_ID, secret="s")
        self.sent: list[dict[str, Any]] = []

        async def on_card_event(event: CardEvent) -> dict[str, Any] | None:
            self.events.append(event)
            return {"card_type": "button_interaction", "task_id": event.task_id}

        async def on_message(message: IncomingMessage) -> None:
            self.messages.append(message)

        self.transport._handlers = TransportHandlers(
            on_card_event=on_card_event,
            on_message=on_message,
            on_state=lambda state, detail: self.states.append((state, detail)),
        )
        transport = self

        class Socket:
            async def send(self, raw: str) -> None:
                frame = json.loads(raw)
                transport.sent.append(frame)
                if frame["cmd"] == "aibot_send_msg":
                    transport.transport._dispatch(
                        json.dumps({"headers": frame["headers"], "errcode": transport.code, "errmsg": "x"})
                    )

            async def close(self) -> None:
                return None

        self.code = 0
        self.transport._ws = Socket()
        self.transport._authed = True

    async def test_card_event_is_answered_with_its_req_id(self) -> None:
        frame = {
            "cmd": "aibot_event_callback",
            "headers": {"req_id": "REQ1"},
            "body": {
                "chattype": "single",
                "from": {"userid": USER},
                "event": {
                    "eventtype": "template_card_event",
                    "template_card_event": {
                        "card_type": "multiple_interaction",
                        "event_key": "submit",
                        "task_id": "t1",
                        "selected_items": {"selected_item": [{"question_key": "q0", "option_ids": {"option_id": ["1"]}}]},
                    },
                },
            },
        }
        self.transport._dispatch(json.dumps(frame))
        await settle()
        self.assertEqual(self.events[0].selections, {"q0": ["1"]})
        reply = self.sent[-1]
        self.assertEqual(reply["cmd"], "aibot_respond_update_msg")
        self.assertEqual(reply["headers"]["req_id"], "REQ1")
        self.assertEqual(reply["body"]["response_type"], "update_template_card")
        self.assertEqual(reply["body"]["userids"], [USER])

    async def test_quoted_message_text_is_extracted(self) -> None:
        frame = {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": "R2"},
            "body": {
                "chattype": "single",
                "from": {"userid": USER},
                "msgtype": "text",
                "text": {"content": "引用回答"},
                "quote": {"msgtype": "text", "text": {"content": "**第 1 题** #ABCD-1"}},
            },
        }
        self.transport._dispatch(json.dumps(frame))
        await settle()
        self.assertEqual(self.messages[0].text, "引用回答")
        self.assertIn("#ABCD-1", self.messages[0].quote_text or "")
        plain = dict(frame, body={**frame["body"], "quote": None})
        self.transport._dispatch(json.dumps(plain))
        await settle()
        self.assertIsNone(self.messages[1].quote_text)

    async def test_disconnected_event_is_final(self) -> None:
        self.transport._dispatch(
            json.dumps({"cmd": "aibot_event_callback", "headers": {"req_id": "R3"}, "body": {"event": {"eventtype": "disconnected_event"}}})
        )
        self.assertEqual(self.transport.state, "kicked")
        self.transport._set_state("connecting", None)  # a reconnect attempt cannot revive it
        self.assertEqual(self.transport.state, "kicked")

    async def test_send_acks_and_errors(self) -> None:
        await self.transport.send_markdown(USER, "hi")
        self.assertEqual(self.sent[-1]["body"], {"chatid": USER, "msgtype": "markdown", "markdown": {"content": "hi"}})
        self.code = FREQUENCY_LIMIT_CODE
        with self.assertRaises(WecomApiError) as caught:
            await self.transport.send_card(USER, {"card_type": "text_notice"})
        self.assertTrue(caught.exception.frequency_limited)
        self.transport._authed = False
        with self.assertRaises(WecomNotConnected):
            await self.transport.send_markdown(USER, "hi")

    async def test_ack_timeout_reports_unknown_outcome(self) -> None:
        async def silent(raw: str) -> None:
            return None

        self.transport._ws.send = silent  # type: ignore[attr-defined]
        with mock.patch.object(wecom_transport, "ACK_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(TimeoutError):
                await self.transport.send_markdown(USER, "hi")


class WecomApiTests(unittest.TestCase):
    """HTTP surface: secrets stay write-only, channel switching guards endpoints."""

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from miniclaw2.app import create_app
        from miniclaw2.feishu_bridge import FeishuBridge
        from miniclaw2.registry import ProjectRegistry

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.wecom = FakeWecom()
        self.registry = ProjectRegistry(Store(self.root))
        self.bridge = WecomBridge(
            self.registry,
            transport_factory=lambda _config: FakeTransport(self.wecom),
            qr_api=FakeQr(),
            transport_available=lambda: True,
        )
        self.feishu = FeishuBridge(self.registry, sdk_available=lambda: False)
        self.client_context = TestClient(
            create_app(self.registry, feishu_bridge=self.feishu, wecom_bridge=self.bridge)
        )
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_channel_switch_guards_and_secret_stays_write_only(self) -> None:
        self.assertEqual(self.client.get("/remote-answers").json()["channel"], "feishu")
        refused = self.client.put("/wecom/settings", json={"bot_id": "aib_x", "secret": "s"})
        self.assertEqual(refused.status_code, 409)

        switched = self.client.put("/remote-answers", json={"channel": "wecom"})
        self.assertEqual(switched.json()["channel"], "wecom")
        self.assertEqual(self.client.put("/feishu/settings", json={"enabled": True}).status_code, 409)

        response = self.client.put(
            "/wecom/settings", json={"bot_id": "aib_x", "secret": "top-secret", "delay_seconds": 5}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertNotIn("top-secret", response.text)
        self.assertEqual((body["bot_id"], body["delay_seconds"]), ("aib_x", 5))
        self.assertEqual(body["connection"]["state"], "connected")
        self.assertEqual(len(body["pairing"]["code"]), 6)
        self.assertNotIn("top-secret", self.client.get("/wecom/status").text)
        self.assertEqual(self.client.post("/wecom/test").status_code, 409)  # not claimed yet
        self.assertEqual(self.client.put("/wecom/settings", json={"surprise": 1}).status_code, 422)
        self.assertEqual(self.client.put("/remote-answers", json={"channel": "slack"}).status_code, 422)

        unbound = self.client.delete("/wecom/binding").json()
        self.assertFalse(unbound["configured"])
        self.assertFalse(wecom_config_path(self.root).exists())

    def test_relays_endpoints(self) -> None:
        self.assertEqual(self.client.get("/sessions/missing/gate-relays").status_code, 404)
        self.assertEqual(self.client.get("/sessions/missing/feishu-relays").status_code, 404)
        self.assertIs(self.registry.gate_observer, self.client.app.state.remote_answers)  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
