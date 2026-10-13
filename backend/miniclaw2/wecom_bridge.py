"""WeCom (企业微信) remote answers for ``ask_user`` gates: one bot per host.

The bridge is a ``GateObserver`` like the Feishu one: when an ``ask_user``
gate stays unanswered for ``delay_seconds`` it pushes the gate to the bound
user, and once every question has an answer it resolves the gate exactly
like the web dialog (``source=wecom``); whichever channel answers first wins.

WeCom shapes the design differently from Feishu:

- A bot accepts one long connection at a time, so each host has its own bot
  and every click lands on the host that sent the card: no relaying.
- Scanning returns only the bot's credentials, never the scanner, and a bot
  cannot message anyone who has not written to it first, so the user claims
  the bot by sending it a pairing code; only that user's clicks and replies
  count afterwards.
- Cards hold at most three dropdowns or one checkbox question and no text
  input, so a gate becomes a markdown detail plus cards (``wecom_cards``),
  answers accumulate per question, and the gate resolves when the last one
  arrives. Free text counts only from a reply that quotes a question
  message; a plain message is never taken as an answer.
- A card can be changed only in the response to a click on it, so a gate
  that closes elsewhere is announced with one markdown message, and its
  cards turn into closed cards the next time they are clicked.

Only the store's runtime owner connects. All bridge state lives on the
server event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from . import wecom_cards as cards
from .feishu_cards import clock, host_short_id
from .gate_observer import GateNotice, GateOutcome
from .wecom_config import (
    WecomConfig,
    delete_wecom_config,
    load_wecom_config,
    save_wecom_config,
)
from .wecom_transport import (
    TRANSPORT_PIN,
    CardEvent,
    ConnectionState,
    IncomingMessage,
    TransportHandlers,
    WecomApiError,
    WecomNotConnected,
    WecomTransport,
    WsTransport,
    transport_installed,
)

logger = logging.getLogger(__name__)

#: Undocumented endpoints used by WeCom's own CLI (``wecom-cli auth init``).
QR_GENERATE_URL = "https://work.weixin.qq.com/ai/qc/generate"
QR_QUERY_URL = "https://work.weixin.qq.com/ai/qc/query_result"
QR_PAGE_URL = "https://work.weixin.qq.com/ai/qc/gen"
QR_SOURCE = "wecom_cli_external"
QR_POLL_SECONDS = 3.0
QR_TIMEOUT_SECONDS = 300.0
QR_MAX_POLL_ERRORS = 3
REGISTER_QR_TIMEOUT_SECONDS = 20.0

SEND_RETRY_DELAYS = (2.0, 5.0, 15.0)
PAIRING_TTL_SECONDS = 600
#: A plain (unquoted) message gets at most one how-to-answer hint per window.
HINT_INTERVAL_SECONDS = 60.0
#: Task ids and refs of closed gates kept for late clicks and quotes.
MEMORY_LIMIT = 1000
TEST_TASK_PREFIX = "mc2-test-"
FAREWELL_TIMEOUT_SECONDS = 3.0

PushState = Literal["open", "scheduled", "sending", "sent", "failed"]
TransportFactory = Callable[[WecomConfig], WecomTransport]
Resolution = Literal["resolved", "pending", "lost"]


class WecomBridgeError(Exception):
    """A request the bridge refuses, with the HTTP status that fits it."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class QrApi(Protocol):
    async def generate(self) -> dict[str, Any]: ...

    async def query(self, scode: str) -> dict[str, Any]: ...


class HttpQrApi:
    """The two GETs ``wecom-cli`` makes to create a bot by scanning."""

    async def generate(self) -> dict[str, Any]:
        query = urllib.parse.urlencode({"source": QR_SOURCE, "plat": _plat_code()})
        return await asyncio.to_thread(_get_json, f"{QR_GENERATE_URL}?{query}")

    async def query(self, scode: str) -> dict[str, Any]:
        query = urllib.parse.urlencode({"scode": scode})
        return await asyncio.to_thread(_get_json, f"{QR_QUERY_URL}?{query}")


def qr_page_url(scode: str) -> str:
    return f"{QR_PAGE_URL}?{urllib.parse.urlencode({'source': QR_SOURCE, 'scode': scode})}"


@dataclass(eq=False)
class _Push:
    notice: GateNotice
    state: PushState = "open"
    timer: asyncio.TimerHandle | None = None
    ref: str | None = None
    plan: cards.PushPlan | None = None
    #: Question index to its answer labels (or text).
    answers: dict[int, list[str]] = field(default_factory=dict)
    #: Questions waiting for a quoted text reply.
    awaiting: set[int] = field(default_factory=set)
    #: Questions whose text prompt has been sent (or is the detail itself).
    prompted: set[int] = field(default_factory=set)
    delivered: int = 0
    #: The gate was resolved from here; its close is still on the way.
    resolved: bool = False
    #: The gate closed while its messages were still being sent.
    closed: GateOutcome | None = None
    event: dict[str, Any] | None = None

    def reset(self) -> None:
        self.state = "open"
        self.ref = None
        self.plan = None
        self.answers = {}
        self.awaiting = set()
        self.prompted = set()
        self.delivered = 0


@dataclass(eq=False)
class _Card:
    gate_id: str
    spec: cards.CardSpec
    #: The card as last shown, which a closed card is built from.
    card: dict[str, Any]
    closed: str | None = None
    answered: bool = False


@dataclass
class _Registration:
    state: Literal["starting", "polling", "succeeded", "failed", "cancelled"] = "starting"
    url: str | None = None
    page_url: str | None = None
    expires_at: float | None = None
    error: str | None = None
    detail: str | None = None
    started_at: float = field(default_factory=time.time)
    bot_id: str | None = None
    qr_ready: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None

    def payload(self) -> dict[str, Any]:
        polling = self.state == "polling"
        return {
            "state": self.state,
            "url": self.url if polling else None,
            "page_url": self.page_url if polling else None,
            "expires_at": self.expires_at,
            "error": self.error,
            "detail": self.detail,
            "started_at": self.started_at,
            "bot_id": self.bot_id,
        }


def _default_transport(config: WecomConfig) -> WecomTransport:
    return WsTransport(bot_id=config.bot_id, secret=config.secret)


class WecomBridge:
    def __init__(
        self,
        registry: Any,
        *,
        transport_factory: TransportFactory | None = None,
        qr_api: QrApi | None = None,
        transport_available: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._registry = registry
        self._transport_factory = transport_factory or _default_transport
        self._qr_api = qr_api or HttpQrApi()
        self._transport_available = transport_available or transport_installed
        self._clock = clock
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        self._owner = False
        self._config: WecomConfig | None = None
        self._transport: WecomTransport | None = None
        self._connection: ConnectionState = "off"
        self._connection_detail: str | None = None
        self._connection_since: float | None = None
        self._last_error: str | None = None
        self._open: dict[str, _Push] = {}
        self._cards: dict[str, _Card] = {}
        self._refs: dict[str, str] = {}
        self._test_cards: dict[str, dict[str, Any]] = {}
        self._pairing: tuple[str, float] | None = None
        self._registration: _Registration | None = None
        self._last_hint_at = 0.0
        self._tasks: set[asyncio.Task[Any]] = set()
        #: Bumped whenever this host stops pushing from its bot (unbind, switch).
        self._epoch = 0

    # ---- identity ----

    @property
    def _root(self) -> Path:
        return Path(self._registry.store.root)

    @property
    def _host_short(self) -> str:
        return host_short_id(self._registry.store.machine.id)

    @property
    def _host_label(self) -> str:
        return self._registry.store.machine.label

    @property
    def _host_color(self) -> int:
        return cards.host_color(self._host_short)

    @property
    def config(self) -> WecomConfig:
        if self._config is None:
            self._config = load_wecom_config(self._root)
        return self._config

    # ---- lifecycle ----

    async def start(self) -> None:
        """Connect when this process owns the store and credentials exist."""
        if self._started:
            return
        self._started = True
        self._loop = asyncio.get_running_loop()
        self._owner = bool(getattr(self._registry, "is_runtime_owner", False))
        if not self._owner:
            return
        self._config = load_wecom_config(self._root)
        await self._connect()

    async def stop(self) -> None:
        tasks = list(self._tasks)
        registration = self._registration
        if registration is not None and registration.task is not None:
            # Awaited too, so a scan that was finishing cannot reconnect later.
            tasks.append(registration.task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for push in self._open.values():
            if push.timer is not None:
                push.timer.cancel()
                push.timer = None
            if push.state == "scheduled":
                push.state = "open"
        await self._farewell()
        await self._disconnect()
        self._pairing = None
        self._started = False

    async def deactivate(self, line: str) -> None:
        """Hand remote answers to another channel; the credentials stay.

        Gates still open are forgotten: the router announces them to
        whichever channel becomes active, including this one later.
        """
        if self._started and self._owner:
            await self._leave(line)
        await self.stop()
        self._open.clear()

    async def _farewell(self) -> None:
        """Tell the user once that pushed questions die with this process."""
        transport, userid = self._transport, self.config.bound_userid
        if transport is None or not userid or self._connection != "connected":
            return
        if not any(push.delivered and not push.resolved for push in self._open.values()):
            return
        try:
            await asyncio.wait_for(
                transport.send_markdown(
                    userid,
                    f"{cards.plain(self._host_label)} 上的 MiniClaw2 后端已停止，"
                    "之前推送的提问卡片已失效。重启后请回网页端查看。",
                ),
                timeout=FAREWELL_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001
            logger.debug("wecom farewell not delivered", exc_info=True)

    async def _connect(self) -> None:
        config = self.config
        if not config.has_credentials:
            return
        if not self._transport_available():
            self._set_connection("error", f"未安装 websockets，请执行 pip install '{TRANSPORT_PIN}'")
            return
        transport = self._transport_factory(config)
        self._transport = transport
        try:
            await transport.start(
                TransportHandlers(
                    on_card_event=self.on_card_event,
                    on_message=self._on_message,
                    on_state=self._set_connection,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("wecom transport failed to start: %s", exc)
            self._transport = None
            self._set_connection("error", str(exc))

    async def _disconnect(self) -> None:
        transport, self._transport = self._transport, None
        if transport is not None:
            try:
                await transport.stop()
            except Exception:  # noqa: BLE001
                logger.exception("wecom transport failed to stop")
        self._set_connection("off", None)

    def _set_connection(self, state: ConnectionState, detail: str | None) -> None:
        if state != self._connection:
            self._connection_since = self._clock()
        self._connection = state
        self._connection_detail = detail
        if state == "connected":
            self._schedule_open_gates()
        elif state == "kicked":
            self._unschedule_open_gates()

    def _spawn(self, coro: Awaitable[Any]) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(_log_task_failure)
        return task

    # ---- GateObserver ----

    def opened(self, notice: GateNotice) -> None:
        if notice.subtype != "ask_user":
            return
        push = _Push(notice=notice)
        self._open[notice.gate_id] = push
        self._schedule(push)

    def closed(self, gate_id: str, outcome: GateOutcome) -> None:
        push = self._open.pop(gate_id, None)
        if push is None:
            return
        if push.timer is not None:
            push.timer.cancel()
            push.timer = None
        # At once, so a click from now on gets the closed card.
        answered = outcome.kind == "answered"
        self._close_cards(gate_id, _closed_status(outcome), answered=answered)
        if push.state == "scheduled":
            self._publish(push, "cancelled")
        elif push.state == "sending":
            push.closed = outcome
        elif push.delivered:
            self._spawn(self._announce_closed(push, outcome))

    def _can_send(self) -> bool:
        config = self.config
        return (
            self._owner
            and self._started
            and self._transport is not None
            and self._connection == "connected"
            and config.enabled
            and bool(config.bound_userid)
        )

    def _schedule(self, push: _Push) -> None:
        if push.state != "open" or not self._can_send() or self._loop is None:
            return
        now = self._clock()
        created = push.notice.created_at or now
        delay = max(0.0, self.config.delay_seconds - (now - created))
        push.state = "scheduled"
        push.timer = self._loop.call_later(delay, self._fire, push.notice.gate_id)
        self._publish(push, "scheduled", send_at=now + delay)

    def _schedule_open_gates(self) -> None:
        for push in list(self._open.values()):
            self._schedule(push)

    def _unschedule_open_gates(self) -> None:
        for push in self._open.values():
            if push.state == "scheduled":
                if push.timer is not None:
                    push.timer.cancel()
                push.timer = None
                push.state = "open"
                self._publish(push, "cancelled")

    def _fire(self, gate_id: str) -> None:
        push = self._open.get(gate_id)
        if push is None or push.state != "scheduled":
            return
        push.timer = None
        if not self._can_send():
            push.state = "open"
            self._publish(push, "cancelled")
            return
        push.state = "sending"
        self._spawn(self._send(push))

    async def _send(self, push: _Push) -> None:
        notice = push.notice
        epoch = self._epoch
        ref = cards.new_ref(set(self._refs))
        plan = cards.plan_push(
            notice,
            ref=ref,
            host_label=self._host_label,
            color=self._host_color,
            nonce=secrets.token_hex(3),
            now=self._clock(),
        )
        push.ref, push.plan = ref, plan
        push.awaiting = set(plan.text_questions)
        self._remember(self._refs, ref, notice.gate_id)
        messages: list[str | cards.CardSpec] = [plan.detail, *plan.cards]
        if plan.detail_is_prompt:
            push.prompted.update(plan.text_questions)
        else:
            for index in plan.text_questions:
                messages.append(cards.text_prompt_markdown(notice, ref=ref, index=index))
                push.prompted.add(index)
        error: str | None = None
        for message in messages:
            if push.closed is not None or epoch != self._epoch:
                break
            if isinstance(message, cards.CardSpec):
                self._remember(
                    self._cards,
                    message.task_id,
                    _Card(gate_id=notice.gate_id, spec=message, card=message.card),
                )
                error = await self._deliver(message.card, epoch=epoch)
            else:
                error = await self._deliver(message, epoch=epoch)
            if epoch != self._epoch:
                return
            if error is not None:
                break
            push.delivered += 1
        if epoch != self._epoch:
            return
        if push.closed is not None:
            push.state = "sent"
            self._close_cards(
                notice.gate_id, _closed_status(push.closed), answered=push.closed.kind == "answered"
            )
            if push.delivered:
                await self._announce_closed(push, push.closed)
            return
        if error is not None:
            push.state = "failed"
            self._last_error = error
            self._publish(push, "failed", detail=error)
            return
        push.state = "sent"
        self._publish(push, "sent")

    async def _deliver(self, message: str | dict[str, Any], *, epoch: int | None = None) -> str | None:
        """Send one message to the bound user; returns why it failed, if it did.

        A request whose ack never came may have been delivered, so it is not
        repeated: WeCom has no idempotency key and a resend could duplicate
        a card. Only requests that were refused or never sent are retried.
        """
        error: str | None = None
        for delay in (0.0, *SEND_RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            transport, userid = self._transport, self.config.bound_userid
            if transport is None or not userid or (epoch is not None and epoch != self._epoch):
                return error or "企业微信未连接"
            try:
                if isinstance(message, str):
                    await transport.send_markdown(userid, message)
                else:
                    await transport.send_card(userid, message)
            except WecomNotConnected as exc:
                error = str(exc) or "企业微信未连接"
                continue
            except WecomApiError as exc:
                error = str(exc)
                if exc.frequency_limited:
                    continue
                return error
            except TimeoutError:
                logger.warning("wecom send ack timed out; assuming delivered")
                return None
            except Exception as exc:  # noqa: BLE001
                return str(exc) or type(exc).__name__
            return None
        return error

    async def _announce_closed(self, push: _Push, outcome: GateOutcome) -> None:
        """One message when a pushed gate closed anywhere but here.

        Its cards cannot be changed until clicked, so this is what tells the
        user they are stale.
        """
        if outcome.kind == "answered" and outcome.source == "wecom":
            return
        await self._deliver(
            cards.closed_markdown(
                push.notice,
                answered_on_web=outcome.kind == "answered" and outcome.source == "web",
            )
        )

    def _close_cards(self, gate_id: str, status: str, *, answered: bool) -> None:
        for entry in self._cards.values():
            if entry.gate_id == gate_id and entry.closed is None:
                entry.closed = status
                entry.answered = answered

    # ---- card clicks ----

    async def on_card_event(self, event: CardEvent) -> dict[str, Any] | None:
        """The card that replaces the clicked one; answered within WeCom's 5 s."""
        bound = self.config.bound_userid
        if not bound or event.userid != bound:
            return None
        if event.task_id.startswith(TEST_TASK_PREFIX):
            card = self._test_cards.get(event.task_id)
            if card is None:
                return None
            return cards.test_clicked_card(card, host_label=self._host_label, at=self._clock())
        entry = self._cards.get(event.task_id)
        if entry is None:
            keys = list(event.selections) or [event.event_key.partition(":")[0]]
            return cards.expired_card(
                card_type=event.card_type,
                question_keys=[key for key in keys if key],
                host_label=self._host_label,
                color=self._host_color,
                task_id=event.task_id,
            )
        push = self._open.get(entry.gate_id)
        if entry.closed is None and (push is None or push.resolved):
            entry.closed = "已回答" if push is not None else "提问已结束"
            entry.answered = push is not None
        if entry.closed is not None:
            return cards.closed_card(
                entry.card, kind=entry.spec.kind, status=entry.closed, answered=entry.answered
            )
        assert push is not None
        questions = push.notice.questions
        ids = cards.card_ids(entry.spec, event.event_key, event.selections)
        choices = {
            index: cards.interpret(questions[index], ids.get(index, []))
            for index in entry.spec.questions
        }
        missing = [index for index, (choice, _) in choices.items() if choice == "none"]
        if missing:
            if entry.spec.kind == "button":
                return None
            entry.card = cards.card_after_click(
                entry.spec,
                ids,
                done=False,
                status=f"第 {'、'.join(str(index + 1) for index in missing)} 题还没选",
            )
            return entry.card
        others: list[int] = []
        for index, (choice, labels) in choices.items():
            if choice == "other":
                push.answers.pop(index, None)
                push.awaiting.add(index)
                others.append(index)
            else:
                push.answers[index] = labels
                push.awaiting.discard(index)
        result, error = self._try_resolve(push)
        if error is not None:
            entry.card = cards.card_after_click(entry.spec, ids, done=False, status=error)
            return entry.card
        if result == "lost":
            self._close_cards(entry.gate_id, "已在网页端回答", answered=True)
            return cards.closed_card(
                entry.card, kind=entry.spec.kind, status="已在网页端回答", answered=True
            )
        if result == "resolved":
            status = f"已回答 · {clock(self._clock())}"
        elif others:
            status = "已选「其他」· 请引用提示消息回复"
        else:
            status = f"已记录 · 还差 {len(self._remaining(push))} 题"
        for index in others:
            if index not in push.prompted:
                push.prompted.add(index)
                assert push.ref is not None
                self._spawn(
                    self._deliver(cards.text_prompt_markdown(push.notice, ref=push.ref, index=index))
                )
        entry.card = cards.card_after_click(entry.spec, ids, done=True, status=status)
        return entry.card

    def _remaining(self, push: _Push) -> list[int]:
        return [index for index in range(len(push.notice.questions)) if index not in push.answers]

    def _try_resolve(self, push: _Push) -> tuple[Resolution, str | None]:
        """Resolve the gate once every question has an answer."""
        from .registry import PlanspaceArchivedError, ProjectArchivedError

        if push.awaiting or self._remaining(push):
            return "pending", None
        notice = push.notice
        try:
            ok = self._registry.resolve_gate(
                notice.project_id,
                notice.gate_id,
                node_id=notice.node_id,
                allow=True,
                response={"answers": cards.answer_mapping(notice.questions, push.answers)},
                source="wecom",
            )
        except (ProjectArchivedError, PlanspaceArchivedError) as exc:
            return "pending", str(exc)
        if not ok:
            return "lost", None
        push.resolved = True
        return "resolved", None

    # ---- messages ----

    async def _on_message(self, message: IncomingMessage) -> None:
        if await self._try_pair(message):
            return
        bound = self.config.bound_userid
        if not bound or message.userid != bound or message.chattype != "single":
            return
        if message.msgtype != "text":
            return
        text = message.text.strip()
        if not text:
            return
        markers = cards.find_markers(message.quote_text or "")
        if not markers:
            # Only a reply quoting a question message answers anything.
            await self._maybe_hint()
            return
        target, reply = self._match_quote(markers)
        if target is None:
            await self._deliver(reply or cards.hint_markdown())
            return
        push, index = target
        push.answers[index] = [text]
        push.awaiting.discard(index)
        result, error = self._try_resolve(push)
        if error is not None:
            await self._deliver(f"未能记录这条回复：{error}")
            return
        if result == "lost":
            self._close_cards(push.notice.gate_id, "已在网页端回答", answered=True)
            await self._deliver("这个提问已在网页端回答，这条回复没有记录。")
            return
        await self._deliver(
            cards.recorded_markdown(
                push.notice,
                index=index,
                text=text,
                remaining=self._remaining(push),
                resolved=result == "resolved",
            )
        )

    def _match_quote(
        self, markers: list[tuple[str, int | None]]
    ) -> tuple[tuple[_Push, int] | None, str | None]:
        """The question a quoted reply answers, or what to tell the user instead.

        A quoted prompt names one question; the quoted detail names its whole
        gate, which settles a reply only when exactly one of its questions
        is the answer's obvious target.
        """
        candidates: list[tuple[_Push, int]] = []
        for ref, index in markers:
            gate_id = self._refs.get(ref)
            push = self._open.get(gate_id) if gate_id else None
            if (
                push is None
                or push.resolved
                or push.ref != ref
                or push.plan is None
                or push.plan.notice_only
            ):
                continue
            count = len(push.notice.questions)
            indices = range(count) if index is None else [index] if index < count else []
            for item in indices:
                if (push, item) not in candidates:
                    candidates.append((push, item))
        awaiting = [(push, index) for push, index in candidates if index in push.awaiting]
        if len(awaiting) == 1:
            return awaiting[0], None
        if not awaiting and len(candidates) == 1:
            return candidates[0], None
        if candidates:
            return None, (
                "你引用的消息包含多道题，无法确定回答的是哪一题，这条回复没有记录。"
                "请引用该题单独的提示消息回复；还没有提示消息的题，先在卡片中选「其他（文字回答）」。"
            )
        return None, "你引用的提问已结束或已失效，这条回复没有记录。"

    async def _maybe_hint(self) -> None:
        if not any(push.awaiting and not push.resolved for push in self._open.values()):
            return
        now = self._clock()
        if now - self._last_hint_at < HINT_INTERVAL_SECONDS:
            return
        self._last_hint_at = now
        await self._deliver(cards.hint_markdown())

    # ---- pairing ----

    def start_pairing(self) -> dict[str, Any]:
        self._require_owner()
        if not self.config.has_credentials:
            raise WecomBridgeError("请先扫码创建机器人，或填写 Bot ID 和 Secret")
        if self._transport is None or self._connection == "kicked":
            raise WecomBridgeError("企业微信长连接尚未建立，稍后再试", status=409)
        code = f"{secrets.randbelow(10**6):06d}"
        expires_at = self._clock() + PAIRING_TTL_SECONDS
        self._pairing = (code, expires_at)
        return {"code": code, "expires_at": expires_at}

    async def _try_pair(self, message: IncomingMessage) -> bool:
        pairing = self._pairing
        if pairing is None or self._clock() > pairing[1]:
            return False
        if (
            message.chattype != "single"
            or message.text.strip() != pairing[0]
            or not message.userid
        ):
            return False
        self._pairing = None
        self._save_config(
            self.config.model_copy(
                update={"bound_userid": message.userid, "bound_at": self._clock()}
            )
        )
        await self._deliver(cards.welcome_markdown(host_label=self._host_label))
        self._schedule_open_gates()
        return True

    # ---- scan-to-create ----

    async def begin_registration(self) -> dict[str, Any]:
        self._require_owner()
        if not self._transport_available():
            raise WecomBridgeError(
                f"未安装 websockets，请执行 pip install '{TRANSPORT_PIN}'", status=503
            )
        if self.config.has_credentials:
            raise WecomBridgeError("本机已绑定一个机器人，请先解绑", status=409)
        current = self._registration
        if current is not None and current.state in {"starting", "polling"}:
            raise WecomBridgeError("已有一个扫码创建正在进行", status=409)
        registration = _Registration(started_at=self._clock())
        self._registration = registration
        registration.task = asyncio.create_task(self._run_registration(registration))
        try:
            await asyncio.wait_for(
                asyncio.shield(registration.qr_ready.wait()),
                timeout=REGISTER_QR_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            pass
        return registration.payload()

    def registration_status(self) -> dict[str, Any] | None:
        registration = self._registration
        return registration.payload() if registration is not None else None

    async def cancel_registration(self) -> dict[str, Any] | None:
        registration = self._registration
        if registration is None:
            return None
        if registration.task is not None and not registration.task.done():
            registration.task.cancel()
            await asyncio.gather(registration.task, return_exceptions=True)
        return registration.payload()

    async def _run_registration(self, registration: _Registration) -> None:
        try:
            generated = _qr_data(await self._qr_api.generate())
            scode, auth_url = generated.get("scode"), generated.get("auth_url")
            if not isinstance(scode, str) or not scode or not isinstance(auth_url, str) or not auth_url:
                raise WecomBridgeError("扫码接口不可用（返回格式已变化），请改用手动填写")
            registration.url = auth_url
            registration.page_url = qr_page_url(scode)
            registration.expires_at = self._clock() + QR_TIMEOUT_SECONDS
            registration.state = "polling"
            registration.qr_ready.set()
            errors = 0
            while True:
                await asyncio.sleep(QR_POLL_SECONDS)
                if self._clock() > registration.expires_at:
                    raise WecomBridgeError("二维码已过期，请重新发起；如果反复失败，请改用手动填写")
                try:
                    result = await self._qr_api.query(scode)
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    if errors >= QR_MAX_POLL_ERRORS:
                        raise WecomBridgeError(
                            f"扫码接口不可用（{exc}），请改用手动填写"
                        ) from exc
                    continue
                errors = 0
                data = _qr_data(result)
                if data.get("status") != "success":
                    continue
                bot = data.get("bot_info") if isinstance(data.get("bot_info"), dict) else {}
                bot_id, secret = bot.get("botid"), bot.get("secret")
                if not isinstance(bot_id, str) or not bot_id or not isinstance(secret, str) or not secret:
                    raise WecomBridgeError("扫码成功，但企业微信没有返回机器人凭证，请改用手动填写")
                await self._complete_registration(registration, bot_id, secret)
                return
        except asyncio.CancelledError:
            registration.state = "cancelled"
            raise
        except WecomBridgeError as exc:
            registration.state = "failed"
            registration.error = str(exc)
        except Exception as exc:  # noqa: BLE001
            registration.state = "failed"
            registration.error = f"扫码接口不可用（{exc or type(exc).__name__}），请改用手动填写"
            logger.warning("wecom registration failed: %s", registration.error)
        finally:
            registration.qr_ready.set()

    async def _complete_registration(
        self, registration: _Registration, bot_id: str, secret: str
    ) -> None:
        current = self.config
        config = WecomConfig(
            enabled=True,
            bot_id=bot_id,
            secret=secret,
            binding_source="created",
            delay_seconds=current.delay_seconds,
        )
        await self._apply_config(config, reconnect=True)
        registration.bot_id = bot_id
        registration.state = "succeeded"

    # ---- settings ----

    async def update_settings(self, changes: dict[str, Any]) -> None:
        self._require_owner()
        current = self.config
        credentials = {
            key: str(changes[key]).strip()
            for key in ("bot_id", "secret")
            if key in changes and changes[key] is not None
        }
        updates = {
            key: changes[key]
            for key in ("enabled", "delay_seconds")
            if key in changes and changes[key] is not None
        }
        if any(not value for value in credentials.values()):
            raise WecomBridgeError("Bot ID 和 Secret 不能为空")
        reconnect = any(credentials[key] != getattr(current, key) for key in credentials)
        if reconnect:
            updates.update(credentials)
            if credentials.get("bot_id", current.bot_id) != current.bot_id:
                # A new bot cannot push until its user has written to it.
                updates.update(bound_userid=None, bound_at=None, binding_source="manual")
            elif not current.binding_source:
                updates["binding_source"] = "manual"
        try:
            config = WecomConfig.model_validate({**current.model_dump(), **updates})
        except ValueError as exc:
            raise WecomBridgeError(str(exc)) from exc
        await self._apply_config(config, reconnect=reconnect)
        if config.enabled and not current.enabled:
            self._schedule_open_gates()
        elif not config.enabled and current.enabled:
            self._unschedule_open_gates()

    async def _apply_config(self, config: WecomConfig, *, reconnect: bool) -> None:
        previous = self.config
        if previous.bot_id and previous.bot_id != config.bot_id:
            await self._leave("此主机已改用另一个企业微信机器人，之前推送的提问卡片已失效，请回网页端作答。")
        if reconnect and self._transport is not None:
            await self._disconnect()
        self._save_config(config)
        if reconnect and config.has_credentials:
            await self._connect()
            if not config.bound_userid and self._transport is not None:
                self.start_pairing()

    async def reconnect(self) -> None:
        """Connect again after this bot's connection was taken elsewhere."""
        self._require_owner()
        if not self.config.has_credentials:
            raise WecomBridgeError("本机还没有绑定企业微信机器人", status=409)
        await self._disconnect()
        await self._connect()

    async def unbind(self) -> None:
        """Say goodbye, disconnect and forget the credentials."""
        self._require_owner()
        await self._leave(
            "本机已解绑企业微信远程回答，之前推送的提问卡片已失效，请回网页端作答。"
            "不再需要这个机器人时，可以在企业微信里删除它。"
        )
        await self._disconnect()
        delete_wecom_config(self._root)
        self._config = WecomConfig()
        self._pairing = None
        self._last_error = None

    async def _leave(self, line: str) -> None:
        """Stop pushing from this bot: retire every pushed gate, telling the user once."""
        self._epoch += 1
        pushed = False
        for push in self._open.values():
            if push.timer is not None:
                push.timer.cancel()
                push.timer = None
            pushed = pushed or bool(push.delivered)
            self._close_cards(push.notice.gate_id, "已失效", answered=False)
            if push.state != "open":
                self._publish(push, "cancelled")
            push.reset()
        if pushed and self._connection == "connected":
            await self._deliver(line)

    async def send_test(self) -> dict[str, Any]:
        self._require_owner()
        if self._transport is None or self._connection != "connected":
            raise WecomBridgeError("企业微信长连接尚未建立", status=409)
        if not self.config.bound_userid:
            raise WecomBridgeError("此主机还没有认领机器人，请先发送配对码", status=409)
        task_id = f"{TEST_TASK_PREFIX}{secrets.token_hex(6)}"
        card = cards.test_card(task_id=task_id, host_label=self._host_label, color=self._host_color)
        self._remember(self._test_cards, task_id, card, limit=20)
        error = await self._deliver(card)
        if error is not None:
            raise WecomBridgeError(error, status=502)
        return {"task_id": task_id}

    # ---- reporting ----

    async def status(self) -> dict[str, Any]:
        config = self.config
        pairing = self._pairing
        if pairing is not None and self._clock() > pairing[1]:
            pairing = self._pairing = None
        return {
            "installed": self._transport_available(),
            "package": TRANSPORT_PIN,
            "runtime_owner": self._owner,
            "configured": config.has_credentials,
            "enabled": config.enabled,
            "bot_id": config.bot_id or None,
            "binding_source": config.binding_source,
            "bound": (
                {"userid": config.bound_userid, "at": config.bound_at}
                if config.bound_userid
                else None
            ),
            "host": {
                "id": self._host_short,
                "label": self._host_label,
                "color": self._host_color,
            },
            "connection": {
                "state": self._connection,
                "detail": self._connection_detail,
                "since": self._connection_since,
            },
            "pending": sum(
                1 for push in self._open.values() if push.state == "sent" and not push.resolved
            ),
            "last_error": self._last_error,
            "delay_seconds": config.delay_seconds,
            "pairing": {"code": pairing[0], "expires_at": pairing[1]} if pairing else None,
            "registration": self.registration_status(),
        }

    def relays_for(self, project_id: str) -> dict[str, dict[str, Any]]:
        """The latest push event per open gate, for a page that just loaded."""
        return {
            gate_id: push.event
            for gate_id, push in self._open.items()
            if push.notice.project_id == project_id and push.event is not None
        }

    # ---- internals ----

    def _publish(self, push: _Push, state: str, **extra: Any) -> None:
        notice = push.notice
        event = {
            "type": "gate_relay_updated",
            "channel": "wecom",
            "node_id": notice.node_id,
            "gate_id": notice.gate_id,
            "state": state,
            "at": self._clock(),
            # Ephemeral: never persisted or replayed; ``relays_for`` heals reloads.
            "seq": 0,
            **{key: value for key, value in extra.items() if value is not None},
        }
        push.event = event if state != "cancelled" else None
        publish = getattr(self._registry, "publish_project_event", None)
        if publish is not None:
            try:
                publish(notice.project_id, event)
            except Exception:  # noqa: BLE001
                logger.debug("failed to publish wecom relay event", exc_info=True)

    @staticmethod
    def _remember(store: dict[str, Any], key: str, value: Any, *, limit: int = MEMORY_LIMIT) -> None:
        store[key] = value
        while len(store) > limit:
            store.pop(next(iter(store)))

    def _save_config(self, config: WecomConfig) -> None:
        save_wecom_config(self._root, config)
        self._config = config

    def _require_owner(self) -> None:
        if not self._owner:
            raise WecomBridgeError(
                "这个后端进程不是本机存储的运行时属主，或企业微信不是本机当前的远程回答通道",
                status=409,
            )


def _closed_status(outcome: GateOutcome) -> str:
    if outcome.kind != "answered":
        return "提问已结束"
    if outcome.source == "web":
        return "已在网页端回答"
    return "已回答"


def _qr_data(payload: Any) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise WecomBridgeError("扫码接口不可用（返回格式已变化），请改用手动填写")
    return data


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310 - fixed https URLs
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _plat_code() -> int:
    if sys.platform == "darwin":
        return 1
    if sys.platform.startswith("win"):
        return 2
    if sys.platform.startswith("linux"):
        return 3
    return 0


def _log_task_failure(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("wecom bridge task failed", exc_info=exc)
