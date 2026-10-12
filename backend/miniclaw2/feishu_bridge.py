"""Feishu remote answers for ``ask_user`` gates, across hosts sharing one app.

The bridge is a ``GateObserver``: when an ``ask_user`` gate stays unanswered
for ``delay_seconds`` it sends the bound user a card, and a submission on
that card resolves the gate exactly like the web dialog (``source=feishu``),
so whichever channel answers first wins and the other learns it lost.

Several hosts may share one Feishu app. Its long connection is a cluster:
each card click is delivered to one random connected host, and a host that
rejects or ignores a click only shows the user an error — Feishu never
redelivers. So every connected host answers every click:

- a click on this host's own card settles locally, and the callback response
  replaces the card with its final state;
- a click on another host's card is answered with a *receipt* card that
  carries the submission (the "card mailbox"); the owner later reads its
  pending cards back with ``mget``, verifies the receipt against its local
  ledger (gate id, nonce, operator) and settles. Receipts cost no API call,
  need no host-to-host channel and survive the relaying host going offline.

Only the store's runtime owner connects, so a second backend on the same
``MINICLAW_HOME`` cannot steal clicks or share the ledger. All bridge state
lives on the server event loop; the transport hands callbacks back to it.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import feishu_cards as cards
from .feishu_config import (
    FeishuConfig,
    current_month,
    delete_feishu_config,
    load_feishu_config,
    load_relay_ledger,
    load_usage,
    purge_relay_ledger,
    record_usage,
    save_feishu_config,
    save_relay_ledger,
)
from .feishu_transport import (
    MGET_LIMIT,
    SDK_PIN,
    CardAction,
    ConnectionState,
    FeishuApiError,
    FeishuTransport,
    IncomingMessage,
    LarkTransport,
    TransportHandlers,
    sdk_installed,
)
from .gate_observer import GateNotice, GateOutcome

logger = logging.getLogger(__name__)

#: What a scan-created app is granted: send cards (and PATCH them), read them
#: back for the mailbox. ``preset: False`` keeps Feishu from stacking its
#: default template on top, which otherwise grows a joined app to 36 scopes.
MINIMAL_SCOPES = ("im:message:send_as_bot", "im:message:readonly")
#: Manual mode pairs through a direct message, which needs one more scope.
MANUAL_SCOPES = ("im:message.p2p_msg:readonly",)
MINIMAL_ADDONS: dict[str, Any] = {
    "preset": False,
    "scopes": {"tenant": list(MINIMAL_SCOPES)},
    "callbacks": {"items": ["card.action.trigger"]},
}
APP_PRESET = {
    "name": "MiniClaw2 远程回答",
    "desc": "把 MiniClaw2 中 agent 的提问推送到飞书，在手机上作答。",
}

SEND_RETRY_DELAYS = (2.0, 5.0, 15.0)
PATCH_RETRY_DELAYS = (1.0, 3.0)
#: Feishu only lets a message be PATCHed within 14 days of sending.
PATCH_WINDOW_SECONDS = 14 * 24 * 3600
CONNECTION_CACHE_SECONDS = 300
#: A lone connection is rechecked sooner: a host that joins meanwhile may
#: already be relaying clicks that the owner reads only once it sees two.
SOLO_CONNECTION_CACHE_SECONDS = 60
STATUS_REFRESH_SECONDS = 60
SCOPES_REFRESH_SECONDS = 600
CLOCK_TICK_SECONDS = 10
#: Wall time running ahead of monotonic time by this much means the host slept.
CLOCK_JUMP_SECONDS = 60
PAIRING_TTL_SECONDS = 600
REGISTER_QR_TIMEOUT_SECONDS = 20

RelayState = Literal["open", "scheduled", "sending", "sent", "failed"]
#: ``rejected``: Feishu refused the PATCH. ``unreachable``: it never answered
#: (or the quota ran out), so the same PATCH may still succeed later.
PatchResult = Literal["ok", "rejected", "unreachable"]
#: The ``state`` of an unbind refused because some cards are still live.
UNRETIRED_CARDS_STATE = "feishu_cards_unretired"
Registrar = Callable[..., Awaitable[dict[str, Any]]]
TransportFactory = Callable[[FeishuConfig], FeishuTransport]


class FeishuBridgeError(Exception):
    """A request the bridge refuses, with the HTTP status that fits it."""

    def __init__(self, message: str, status: int = 400, state: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.state = state


@dataclass
class _Relay:
    notice: GateNotice
    state: RelayState = "open"
    timer: asyncio.TimerHandle | None = None
    nonce: str | None = None
    message_id: str | None = None
    sent_at: float | None = None
    polled_at: float = 0.0
    #: Another connection was online while this card was pending, so a
    #: receipt may exist even after that host has gone away again.
    shared: bool = False
    #: The callback response already replaced the card with its final state.
    final_in_callback: bool = False
    #: The gate closed while its card was still being sent.
    closed: GateOutcome | None = None
    event: dict[str, Any] | None = None


@dataclass
class _Registration:
    mode: Literal["create", "join"]
    app_id: str | None
    state: Literal["starting", "polling", "succeeded", "failed", "cancelled"] = "starting"
    url: str | None = None
    expires_at: float | None = None
    error: str | None = None
    detail: str | None = None
    started_at: float = field(default_factory=time.time)
    result_app_id: str | None = None
    qr_ready: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "state": self.state,
            "url": self.url if self.state == "polling" else None,
            "expires_at": self.expires_at,
            "error": self.error,
            "detail": self.detail,
            "started_at": self.started_at,
            "app_id": self.result_app_id,
        }


async def _sdk_registrar(**kwargs: Any) -> dict[str, Any]:
    # The first SDK import takes seconds; keep it off the server loop.
    module = await asyncio.to_thread(importlib.import_module, "lark_oapi.scene.registration")
    return await module.aregister_app(**kwargs)


def _default_transport(config: FeishuConfig) -> FeishuTransport:
    return LarkTransport(app_id=config.app_id, app_secret=config.app_secret, brand=config.brand)


class FeishuBridge:
    def __init__(
        self,
        registry: Any,
        *,
        transport_factory: TransportFactory | None = None,
        registrar: Registrar | None = None,
        sdk_available: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._registry = registry
        self._transport_factory = transport_factory or _default_transport
        self._registrar = registrar or _sdk_registrar
        self._sdk_available = sdk_available or sdk_installed
        self._clock = clock
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        self._owner = False
        #: Sending waits for the startup sweep, so a fresh card is never swept.
        self._booted = False
        self._config: FeishuConfig | None = None
        self._ledger: dict[str, dict[str, Any]] = {}
        self._open: dict[str, _Relay] = {}
        self._transport: FeishuTransport | None = None
        self._connection: ConnectionState = "off"
        self._connection_detail: str | None = None
        self._connection_since: float | None = None
        self._connections: tuple[int | None, float] | None = None
        self._connections_error: str | None = None
        self._scopes: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._last_poll_at: float | None = None
        self._last_poll_error: str | None = None
        self._quota_month: str | None = None
        self._force_poll = False
        self._poll_event = asyncio.Event()
        self._pairing: tuple[str, float] | None = None
        self._registration: _Registration | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._loops: list[asyncio.Task[None]] = []
        #: Bumped whenever this host leaves its Feishu app (unbind or switch).
        self._app_epoch = 0

    # ---- identity ----

    @property
    def _root(self) -> Path:
        return Path(self._registry.store.root)

    @property
    def _host_short(self) -> str:
        return cards.host_short_id(self._registry.store.machine.id)

    @property
    def _host_label(self) -> str:
        return self._registry.store.machine.label

    @property
    def _host_color(self) -> str:
        return cards.host_color(self._host_short)

    @property
    def config(self) -> FeishuConfig:
        if self._config is None:
            self._config = load_feishu_config(self._root)
        return self._config

    @property
    def _quota_exhausted(self) -> bool:
        return self._quota_month == current_month()

    # ---- lifecycle ----

    async def start(self) -> None:
        """Connect when this process owns the store and credentials exist.

        Returns without waiting for the network: the SDK import alone takes
        seconds and must not delay serving the web UI.
        """
        if self._started:
            return
        self._started = True
        self._loop = asyncio.get_running_loop()
        self._owner = bool(getattr(self._registry, "is_runtime_owner", False))
        if not self._owner:
            return
        self._config = load_feishu_config(self._root)
        self._ledger = purge_relay_ledger(load_relay_ledger(self._root), now=self._clock())
        self._loops = [
            asyncio.create_task(self._poll_loop(), name="feishu-poll"),
            asyncio.create_task(self._clock_loop(), name="feishu-clock"),
        ]
        self._spawn(self._boot())

    async def stop(self) -> None:
        for task in [*self._loops, *self._tasks]:
            task.cancel()
        registration = self._registration
        if registration is not None and registration.task is not None:
            registration.task.cancel()
        await asyncio.gather(*self._loops, *self._tasks, return_exceptions=True)
        self._loops = []
        for relay in self._open.values():
            if relay.timer is not None:
                relay.timer.cancel()
        await self._disconnect()
        self._started = False

    async def _boot(self) -> None:
        if not self.config.has_credentials:
            self._booted = True
            return
        await self._connect()
        if self._transport is not None:
            live = await self._sweep(
                list(self._ledger),
                title="提问已失效",
                line="主机已重启，此提问已失效。",
            )
            if live:
                self._last_error = f"{len(live)} 张旧卡片未能改为失效，下次启动时重试"
            self._spawn(self._refresh_scopes())
        self._booted = True
        self._schedule_open_gates()

    async def _connect(self) -> None:
        config = self.config
        if not config.has_credentials:
            return
        if not self._sdk_available():
            self._set_connection("error", f"未安装飞书 SDK，请执行 pip install '{SDK_PIN}'")
            return
        transport = self._transport_factory(config)
        self._transport = transport
        try:
            await transport.start(
                TransportHandlers(
                    on_card_action=self.on_card_action,
                    on_late_response=self.on_late_response,
                    on_message=self._on_message,
                    on_reconnected=lambda: self._wake("飞书长连接已重连"),
                    on_state=self._set_connection,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("feishu transport failed to start: %s", exc)
            self._transport = None
            self._set_connection("error", str(exc))
            return
        if transport.state == "connected":
            self._set_connection("connected", None)

    async def _disconnect(self) -> None:
        transport, self._transport = self._transport, None
        self._connections = None
        if transport is not None:
            try:
                await transport.stop()
            except Exception:  # noqa: BLE001
                logger.exception("feishu transport failed to stop")
        self._set_connection("off", None)

    def _set_connection(self, state: ConnectionState, detail: str | None) -> None:
        if state != self._connection:
            self._connection_since = self._clock()
        self._connection = state
        self._connection_detail = detail

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
        relay = _Relay(notice=notice)
        self._open[notice.gate_id] = relay
        self._schedule(relay)

    def closed(self, gate_id: str, outcome: GateOutcome) -> None:
        relay = self._open.pop(gate_id, None)
        if relay is None:
            return
        if relay.timer is not None:
            relay.timer.cancel()
            relay.timer = None
        if relay.state == "scheduled":
            self._publish(relay, "cancelled")
        elif relay.state == "sending":
            relay.closed = outcome
        elif relay.state == "sent":
            self._spawn(self._finish(relay, outcome))

    def _can_send(self) -> bool:
        config = self.config
        return (
            self._owner
            and self._booted
            and self._transport is not None
            and config.enabled
            and bool(config.bound_open_id)
            and not self._quota_exhausted
        )

    def _schedule(self, relay: _Relay) -> None:
        if relay.state != "open" or not self._can_send() or self._loop is None:
            return
        now = self._clock()
        created = relay.notice.created_at or now
        delay = max(0.0, self.config.delay_seconds - (now - created))
        relay.state = "scheduled"
        relay.timer = self._loop.call_later(delay, self._fire, relay.notice.gate_id)
        self._publish(relay, "scheduled", send_at=now + delay)

    def _schedule_open_gates(self) -> None:
        for relay in list(self._open.values()):
            self._schedule(relay)

    def _unschedule_open_gates(self) -> None:
        for relay in self._open.values():
            if relay.state == "scheduled":
                if relay.timer is not None:
                    relay.timer.cancel()
                relay.timer = None
                relay.state = "open"
                self._publish(relay, "cancelled")

    def _fire(self, gate_id: str) -> None:
        relay = self._open.get(gate_id)
        if relay is None or relay.state != "scheduled":
            return
        relay.timer = None
        if not self._can_send():
            relay.state = "open"
            self._publish(relay, "cancelled")
            return
        relay.state = "sending"
        self._spawn(self._send(relay))

    async def _send(self, relay: _Relay) -> None:
        notice = relay.notice
        epoch = self._app_epoch
        relay.nonce = secrets.token_hex(8)
        card = cards.ask_card(
            notice,
            host_short=self._host_short,
            host_label=self._host_label,
            color=self._host_color,
            nonce=relay.nonce,
            now=self._clock(),
        )
        # Retries share the nonce and so dedupe; a later send for the same
        # gate (after a reset) must not be handed back a card with an old one.
        # 46 characters, under Feishu's 50.
        uuid = f"mc2-{self._host_short}-{notice.gate_id}-{relay.nonce}"
        error: str | None = None
        for delay in (0.0, *SEND_RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            transport, open_id = self._transport, self.config.bound_open_id
            if (
                relay.closed is not None
                or epoch != self._app_epoch
                or transport is None
                or not open_id
            ):
                break
            try:
                message_id = await transport.send_card(open_id, card, uuid=uuid)
            except FeishuApiError as exc:
                error = str(exc)
                self._note_api_error(exc)
                if exc.quota_exhausted:
                    break
                continue
            except Exception as exc:  # noqa: BLE001
                error = str(exc) or type(exc).__name__
                continue
            self._count("send")
            if epoch != self._app_epoch:
                await self._retire_orphan(relay, message_id, transport)
                return
            relay.message_id = message_id
            relay.sent_at = self._clock()
            relay.state = "sent"
            self._ledger[notice.gate_id] = {
                "mid": message_id,
                "nonce": relay.nonce,
                "project_id": notice.project_id,
                "node_id": notice.node_id,
                "subtitle": cards.card_subtitle(notice),
                "sent_at": relay.sent_at,
                "state": "sent",
            }
            self._save_ledger()
            self._publish(relay, "sent")
            if relay.closed is not None:
                await self._finish(relay, relay.closed)
            return
        if epoch != self._app_epoch:
            self._reopen(relay)
        elif relay.closed is None and notice.gate_id in self._open:
            relay.state = "failed"
            if error:
                self._last_error = error
            self._publish(relay, "failed", detail=error)

    async def _retire_orphan(
        self, relay: _Relay, message_id: str, transport: FeishuTransport
    ) -> None:
        """Retire a card that landed after this host left the app sending it.

        Neither a later poll nor a sweep can reach it, so it is PATCHed now
        through the transport that sent it, which still holds the old app's
        credentials after its long connection closed.
        """
        notice = relay.notice
        card = (
            self._terminal_card(notice, relay.closed)
            if relay.closed is not None
            else self._final_card(
                cards.card_subtitle(notice),
                "提问已转回网页端",
                "grey",
                ["此主机已解绑或切换了飞书应用，此提问不再从这里推送。", "如仍需回答，请回网页端。"],
            )
        )
        if await self._patch(message_id, card, transport) != "ok":
            self._last_error = "1 张在解绑或切换应用时送达的卡片未能改为失效，请回网页端作答"
        self._reopen(relay)

    def _reopen(self, relay: _Relay) -> None:
        if self._open.get(relay.notice.gate_id) is not relay:
            return
        relay.state = "open"
        self._publish(relay, "cancelled")
        self._schedule(relay)

    async def _finish(self, relay: _Relay, outcome: GateOutcome) -> None:
        """Move a sent card to its final state once its gate has closed."""
        # Ledger first: a shutdown cutting the PATCH short must not leave the
        # record ``sent``, or the next startup sweep would overwrite an
        # answered card with "expired".
        record = self._ledger.get(relay.notice.gate_id)
        if record is not None:
            record["state"] = "settled" if outcome.kind == "answered" else "ended"
            record["closed_at"] = self._clock()
            self._save_ledger()
        if not relay.final_in_callback and relay.message_id:
            await self._patch(relay.message_id, self._terminal_card(relay.notice, outcome))

    def _terminal_card(self, notice: GateNotice, outcome: GateOutcome) -> dict[str, Any]:
        lines: list[str]
        if outcome.kind == "answered":
            answers = outcome.response.get("answers") if outcome.response else None
            lines = cards.answer_lines(notice.questions, answers)
            if outcome.source == "feishu":
                title = f"已回答 · 经 {outcome.via} 中转" if outcome.via else "已回答"
                template = "green"
            elif outcome.source == "web":
                title, template = "已在网页端回答", "grey"
            else:
                title, template = "已回答", "grey"
            lines.append(f"回答时间 {cards.clock(self._clock())}")
        else:
            title, template = "提问已结束", "grey"
            lines = ["节点已结束或被取消，此提问已失效。"]
        return self._final_card(cards.card_subtitle(notice), title, template, lines)

    def _final_card(
        self, subtitle: str, title: str, template: str, lines: list[str]
    ) -> dict[str, Any]:
        return cards.final_card(
            title=title,
            template=template,
            subtitle=subtitle,
            host_label=self._host_label,
            color=self._host_color,
            lines=lines,
        )

    async def _patch(
        self,
        message_id: str,
        card: dict[str, Any],
        transport: FeishuTransport | None = None,
    ) -> PatchResult:
        result: PatchResult = "unreachable"
        for delay in (0.0, *PATCH_RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            target = transport or self._transport
            if target is None:
                return "unreachable"
            try:
                await target.patch_card(message_id, card)
            except FeishuApiError as exc:
                self._note_api_error(exc)
                if exc.quota_exhausted:
                    return "unreachable"
                result = "rejected" if exc.code is not None else "unreachable"
                continue
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc) or type(exc).__name__
                result = "unreachable"
                continue
            self._count("patch")
            return "ok"
        return result

    # ---- card callbacks ----

    async def on_card_action(self, action: CardAction) -> dict[str, Any]:
        """Answer any click within the callback budget; never raise."""
        bound = self.config.bound_open_id
        if bound and action.operator_open_id != bound:
            return cards.toast("error", "无权回答：这张卡片只接受绑定账号的提交")
        value, form_value = action.value, action.form_value
        if value.get("v") != cards.CARD_VALUE_VERSION:
            return cards.toast("warning", "这张卡片来自不兼容的 MiniClaw2 版本，请回网页端作答")
        kind = value.get("a")
        if kind == cards.ACTION_TEST:
            return cards.toast(
                "success",
                f"{self._host_label} 收到了这次点击（{time.strftime('%H:%M:%S')}）",
            )
        if kind != cards.ACTION_SUBMIT:
            return cards.toast("warning", "未知操作，请回网页端作答")
        if value.get("h") == self._host_short:
            return await self._settle_local(action)
        missing = cards.first_unanswered(form_value, value.get("k"))
        if missing is not None:
            return cards.toast("warning", f"第 {missing} 题还没有回答")
        if cards.receipt_form_too_long(form_value):
            return cards.toast("warning", "回答内容过长，无法经其他主机转交，请回网页端作答")
        owner = str(value.get("hl") or value.get("h") or "属主主机")
        receipt = cards.receipt_card(
            value=value,
            form_value=form_value,
            operator_open_id=action.operator_open_id,
            via_short=self._host_short,
            via_label=self._host_label,
            at=self._clock(),
        )
        return cards.with_card(cards.toast("info", f"已收到，正在交给 {owner}"), receipt)

    def on_late_response(self, action: CardAction, response: dict[str, Any]) -> None:
        """Apply a callback's card that missed the deadline with a PATCH.

        The handler's effect already stands, so the card is all that is
        missing: for another host's card it is the receipt, the only place
        the answer lives; for this host's own card it is the final state,
        which ``final_in_callback`` keeps the gate's close from PATCHing.
        """
        card = (response.get("card") or {}).get("data")
        if isinstance(card, dict) and action.message_id:
            self._spawn(self._patch(action.message_id, card))

    async def _settle_local(self, action: CardAction) -> dict[str, Any]:
        value, form_value = action.value, action.form_value
        gate_id = value.get("g")
        relay = self._open.get(gate_id) if isinstance(gate_id, str) else None
        if relay is None:
            record = self._ledger.get(gate_id) if isinstance(gate_id, str) else None
            if record is not None and record.get("state") == "settled":
                return cards.toast("info", "这个提问已经回答过了")
            return cards.with_card(
                cards.toast("warning", "此提问已失效"),
                self._final_card(
                    str((record or {}).get("subtitle") or ""),
                    "提问已失效",
                    "grey",
                    ["主机已重启或节点已结束，此提问已失效。", "如仍需回答，请回网页端。"],
                ),
            )
        if relay.nonce is None or value.get("n") != relay.nonce:
            return cards.toast("error", "无法确认这张卡片，请回网页端作答")
        if relay.final_in_callback:
            # A repeated click before the gate's close reached the bridge.
            return cards.toast("info", "这个提问已经回答过了")
        answers, missing = cards.normalize_answers(relay.notice.questions, form_value)
        if answers is None:
            return cards.toast("warning", f"第 {missing} 题还没有回答")
        if relay.message_id is None and action.message_id:
            relay.message_id = action.message_id
        ok, error = self._resolve(relay, answers, via=None)
        if not ok:
            return cards.toast("warning" if error else "info", error or "此提问已在网页端回答")
        # Set before returning, with no await in between: the gate's close
        # runs later and must not PATCH over the card this response carries.
        relay.final_in_callback = True
        card = self._final_card(
            cards.card_subtitle(relay.notice),
            "已回答",
            "green",
            [
                *cards.answer_lines(relay.notice.questions, answers),
                f"回答时间 {cards.clock(self._clock())}",
            ],
        )
        return cards.with_card(cards.toast("success", "已提交"), card)

    def _resolve(
        self, relay: _Relay, answers: dict[str, Any], *, via: str | None
    ) -> tuple[bool, str | None]:
        from .registry import PlanspaceArchivedError, ProjectArchivedError

        notice = relay.notice
        try:
            ok = self._registry.resolve_gate(
                notice.project_id,
                notice.gate_id,
                node_id=notice.node_id,
                allow=True,
                response={"answers": answers},
                source="feishu",
                via=via,
            )
        except (ProjectArchivedError, PlanspaceArchivedError) as exc:
            return False, str(exc)
        return bool(ok), None

    # ---- mailbox ----

    async def poll_now(self, *, force: bool = False) -> None:
        await self._poll_once(force)

    async def _poll_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._poll_event.wait(), timeout=self.config.poll_seconds)
            except TimeoutError:
                pass
            self._poll_event.clear()
            force, self._force_poll = self._force_poll, False
            try:
                await self._poll_once(force)
            except Exception:  # noqa: BLE001
                logger.exception("feishu mailbox poll failed")

    async def _clock_loop(self) -> None:
        wall, mono = time.time(), time.monotonic()
        while True:
            await asyncio.sleep(CLOCK_TICK_SECONDS)
            now_wall, now_mono = time.time(), time.monotonic()
            if (now_wall - wall) - (now_mono - mono) > CLOCK_JUMP_SECONDS:
                self._wake("系统唤醒")
            wall, mono = now_wall, now_mono

    def _wake(self, reason: str) -> None:
        """Poll at once: a click may have been relayed while we were away."""
        logger.info("feishu mailbox poll forced: %s", reason)
        self._force_poll = True
        self._connections = None
        self._poll_event.set()

    async def _poll_once(self, force: bool) -> None:
        transport = self._transport
        if transport is None or self._quota_exhausted:
            return
        now = self._clock()
        pending: list[_Relay] = []
        for relay in self._open.values():
            if relay.state != "sent" or not relay.message_id or relay.sent_at is None:
                continue
            if now - relay.sent_at >= PATCH_WINDOW_SECONDS:
                relay.state = "failed"
                record = self._ledger.get(relay.notice.gate_id)
                if record is not None:
                    record["state"] = "expired"
                    self._save_ledger()
                self._publish(relay, "failed", detail="卡片已超过 14 天，无法再更新")
                continue
            pending.append(relay)
        if not pending:
            return
        if not force:
            count = await self._online_connections(CONNECTION_CACHE_SECONDS)
            # Unknown counts poll anyway: a stuck receipt is worse than a call.
            if count is None or count > 1:
                for relay in pending:
                    relay.shared = True
            pending = [relay for relay in pending if relay.shared]
            if not pending:
                return
        pending.sort(key=lambda relay: relay.polled_at)
        batch = {relay.message_id: relay for relay in pending[:MGET_LIMIT] if relay.message_id}
        try:
            found = await transport.get_cards(list(batch))
        except FeishuApiError as exc:
            self._note_api_error(exc)
            self._last_poll_error = str(exc)
            return
        except Exception as exc:  # noqa: BLE001
            self._last_poll_error = str(exc) or type(exc).__name__
            return
        self._count("mget")
        self._last_poll_at = now
        self._last_poll_error = None
        for message_id, relay in batch.items():
            relay.polled_at = now
            card = found.get(message_id)
            receipt = cards.find_receipt(card) if card is not None else None
            if receipt is None or self._open.get(relay.notice.gate_id) is not relay:
                continue
            await self._settle_receipt(relay, receipt)

    async def _settle_receipt(self, relay: _Relay, receipt: dict[str, Any]) -> None:
        notice = relay.notice
        value = receipt.get("value") if isinstance(receipt.get("value"), dict) else {}
        form_value = receipt.get("fv") if isinstance(receipt.get("fv"), dict) else {}
        bound = self.config.bound_open_id
        problems = [
            reason
            for failed, reason in (
                (value.get("h") != self._host_short, "主机不符"),
                (value.get("g") != notice.gate_id, "提问不符"),
                (relay.nonce is None or value.get("n") != relay.nonce, "校验码不符"),
                (not bound or receipt.get("op") != bound, "回答人不符"),
            )
            if failed
        ]
        answers = None
        if not problems:
            answers, missing = cards.normalize_answers(notice.questions, form_value)
            if answers is None:
                problems.append(f"第 {missing} 题缺少回答")
        if problems or answers is None:
            logger.warning(
                "rejected feishu receipt gate=%s via=%s: %s",
                notice.gate_id,
                receipt.get("via"),
                "、".join(problems),
            )
            relay.state = "failed"
            if relay.message_id:
                await self._patch(
                    relay.message_id,
                    self._final_card(
                        cards.card_subtitle(notice),
                        "无法确认这次回答",
                        "red",
                        ["其他主机代收的回答没有通过校验，未交给 agent。", "请回网页端作答。"],
                    ),
                )
            record = self._ledger.get(notice.gate_id)
            if record is not None:
                record["state"] = "unverified"
                record["closed_at"] = self._clock()
                self._save_ledger()
            self._publish(relay, "failed", detail="回执未通过校验")
            return
        via = str(receipt.get("vl") or receipt.get("via") or "其他主机")
        ok, error = self._resolve(relay, answers, via=via)
        if error:
            logger.warning("feishu receipt for gate=%s not applied: %s", notice.gate_id, error)
        # On success the gate's close PATCHes the card green (``_finish``);
        # when the web answered first that same close reports it instead.
        del ok

    async def _online_connections(self, max_age: float) -> int | None:
        cached = self._connections
        now = self._clock()
        if cached is not None and cached[0] == 1:
            max_age = min(max_age, SOLO_CONNECTION_CACHE_SECONDS)
        if cached is not None and now - cached[1] < max_age:
            return cached[0]
        transport = self._transport
        if transport is None:
            return None
        try:
            count = await transport.online_connections()
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, FeishuApiError):
                self._note_api_error(exc)
            self._connections_error = str(exc) or type(exc).__name__
            self._connections = (None, now)
            return None
        self._count("connections")
        self._connections_error = None
        self._connections = (count, now)
        return count

    async def _sweep(
        self,
        gate_ids: list[str],
        *,
        title: str,
        line: str,
    ) -> list[str]:
        """Retire cards whose gates this process can no longer settle.

        Returns the gates whose cards Feishu could not be reached to retire.
        Their records stay live, so the next sweep tries them again.
        """
        now = self._clock()
        live: list[str] = []
        changed = False
        for gate_id in gate_ids:
            record = self._ledger.get(gate_id)
            if record is None or record.get("state") not in {"sending", "sent"}:
                continue
            message_id = record.get("mid")
            sent_at = record.get("sent_at")
            fresh = isinstance(sent_at, (int, float)) and now - sent_at < PATCH_WINDOW_SECONDS
            if isinstance(message_id, str) and fresh:
                result = await self._patch(
                    message_id,
                    self._final_card(
                        str(record.get("subtitle") or ""),
                        title,
                        "grey",
                        [line, "如仍需回答，请回网页端。"],
                    ),
                )
                if result == "unreachable":
                    live.append(gate_id)
                    continue
                record["state"] = "stale"
            else:
                record["state"] = "expired"
            record["closed_at"] = now
            changed = True
        if changed:
            self._save_ledger()
        return live

    # ---- manual pairing ----

    def start_pairing(self) -> dict[str, Any]:
        self._require_owner()
        if not self.config.has_credentials:
            raise FeishuBridgeError("请先填写 App ID 和 App Secret")
        if self._transport is None:
            raise FeishuBridgeError("飞书长连接尚未建立，稍后再试", status=409)
        code = f"{secrets.randbelow(10**6):06d}"
        expires_at = self._clock() + PAIRING_TTL_SECONDS
        self._pairing = (code, expires_at)
        return {"code": code, "expires_at": expires_at}

    async def _on_message(self, message: IncomingMessage) -> None:
        pairing = self._pairing
        if pairing is None or self._clock() > pairing[1]:
            return
        if (
            message.chat_type != "p2p"
            or message.text.strip() != pairing[0]
            or not message.sender_open_id
        ):
            return
        self._pairing = None
        config = self.config.model_copy(
            update={
                "bound_open_id": message.sender_open_id,
                "bound_at": self._clock(),
                "binding_source": "manual",
            }
        )
        self._save_config(config)
        await self._send_welcome()
        self._schedule_open_gates()

    # ---- scan-to-register ----

    async def begin_registration(
        self, mode: Literal["create", "join"], app_id: str | None = None
    ) -> dict[str, Any]:
        self._require_owner()
        if not self._sdk_available():
            raise FeishuBridgeError(f"未安装飞书 SDK，请执行 pip install '{SDK_PIN}'", status=503)
        current = self._registration
        if current is not None and current.state in {"starting", "polling"}:
            raise FeishuBridgeError("已有一个扫码绑定正在进行", status=409)
        if mode == "join":
            app_id = (app_id or "").strip()
            if not app_id.startswith("cli_"):
                raise FeishuBridgeError("App ID 应以 cli_ 开头")
        registration = _Registration(mode=mode, app_id=app_id if mode == "join" else None)
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
        def on_qr_code(info: dict[str, Any]) -> None:
            # The URL carries the device code: whoever opens it can approve.
            # It goes to this host's own settings page and nowhere else.
            registration.url = str(info.get("url") or "")
            expire_in = info.get("expire_in")
            registration.expires_at = self._clock() + (
                float(expire_in) if isinstance(expire_in, (int, float)) else 600.0
            )
            registration.state = "polling"
            registration.qr_ready.set()

        def on_status(info: dict[str, Any]) -> None:
            status = info.get("status")
            if status == "slow_down":
                registration.detail = "飞书要求放慢轮询，稍等片刻"
            elif status == "domain_switched":
                registration.detail = "已切换到 Lark 国际版"

        kwargs: dict[str, Any] = {
            "on_qr_code": on_qr_code,
            "on_status_change": on_status,
            "source": "miniclaw2",
            "addons": MINIMAL_ADDONS,
        }
        if registration.mode == "create":
            kwargs.update(create_only=True, app_preset=APP_PRESET)
        else:
            kwargs["app_id"] = registration.app_id
        try:
            result = await self._registrar(**kwargs)
            await self._complete_registration(registration, result)
        except asyncio.CancelledError:
            registration.state = "cancelled"
            registration.qr_ready.set()
            raise
        except FeishuBridgeError as exc:
            registration.state = "failed"
            registration.error = str(exc)
        except Exception as exc:  # noqa: BLE001
            registration.state = "failed"
            registration.error = _registration_error(exc)
            logger.warning("feishu registration failed: %s", registration.error)
        finally:
            registration.qr_ready.set()

    async def _complete_registration(
        self, registration: _Registration, result: dict[str, Any]
    ) -> None:
        app_id = str(result.get("client_id") or "")
        app_secret = str(result.get("client_secret") or "")
        user_info = result.get("user_info") if isinstance(result.get("user_info"), dict) else {}
        open_id = user_info.get("open_id")
        if not app_id or not app_secret:
            raise FeishuBridgeError("飞书没有返回应用凭证")
        if not isinstance(open_id, str) or not open_id:
            raise FeishuBridgeError("飞书没有返回扫码人的 open_id")
        current = self.config
        if (
            registration.mode == "join"
            and current.app_id == app_id
            and current.bound_open_id
            and current.bound_open_id != open_id
        ):
            raise FeishuBridgeError("此应用已由另一个账号使用")
        brand = "lark" if user_info.get("tenant_brand") == "lark" else "feishu"
        config = FeishuConfig(
            enabled=True,
            brand=brand,
            app_id=app_id,
            app_secret=app_secret,
            bound_open_id=open_id,
            binding_source="created" if registration.mode == "create" else "joined",
            bound_at=self._clock(),
            delay_seconds=current.delay_seconds,
            poll_seconds=current.poll_seconds,
        )
        await self._apply_config(config, reconnect=True)
        registration.result_app_id = app_id
        registration.state = "succeeded"
        if self._transport is not None:
            await self._send_welcome()
            await self._refresh_scopes()

    # ---- settings ----

    async def update_settings(self, changes: dict[str, Any]) -> None:
        self._require_owner()
        current = self.config
        credentials = {
            key: changes[key]
            for key in ("brand", "app_id", "app_secret")
            if key in changes and changes[key] is not None
        }
        updates = {
            key: changes[key]
            for key in ("enabled", "delay_seconds", "poll_seconds")
            if key in changes and changes[key] is not None
        }
        if "app_id" in credentials:
            credentials["app_id"] = str(credentials["app_id"]).strip()
        reconnect = any(credentials.get(key) != getattr(current, key) for key in credentials)
        if reconnect:
            updates.update(credentials)
            updates["binding_source"] = "manual"
            if credentials.get("app_id", current.app_id) != current.app_id:
                # open_id is per app, so a new app needs a new binding.
                updates.update(bound_open_id=None, bound_at=None)
        try:
            config = FeishuConfig.model_validate({**current.model_dump(), **updates})
        except ValueError as exc:
            raise FeishuBridgeError(str(exc)) from exc
        await self._apply_config(config, reconnect=reconnect)
        if config.enabled and not current.enabled:
            self._schedule_open_gates()
        elif not config.enabled and current.enabled:
            self._unschedule_open_gates()

    async def _apply_config(self, config: FeishuConfig, *, reconnect: bool) -> None:
        previous = self.config
        switching = previous.app_id != config.app_id
        live: list[str] = []
        if switching:
            self._app_epoch += 1
            live = await self._retire_all("此主机已切换到其他飞书应用，此提问不再从这里推送。")
            # The old app's credentials are about to go: nothing can retry.
            for gate_id in live:
                self._ledger[gate_id].update(state="unretired", closed_at=self._clock())
            if live:
                self._save_ledger()
        if reconnect and self._transport is not None:
            await self._disconnect()
        self._save_config(config)
        if reconnect and config.has_credentials:
            self._scopes = None
            await self._connect()
            self._booted = True
            self._schedule_open_gates()
        if live:
            self._last_error = (
                f"切换前有 {len(live)} 张卡片未能改为失效。它们在飞书里仍可点击，"
                "但答案不会送达，请回网页端作答"
            )

    async def unbind(self, *, force: bool = False) -> None:
        """Disconnect, forget the credentials and retire this host's cards.

        A host that stays connected without handling clicks would make
        Feishu fail every click delivered to it, so unbinding always closes
        the long connection. A card that cannot be retired stays clickable
        after its credentials are gone, with no host left to read what is
        relayed onto it, so unbinding then stops unless ``force`` is set.
        """
        self._require_owner()
        self._app_epoch += 1
        connected = self._transport is not None
        live = await self._retire_all("此主机已解绑飞书，此提问不再从这里推送。")
        if live and not force:
            # Still bound: live cards stay served and unsent gates go back on
            # their timers; a gate whose card was retired stays web-only, as
            # that card now says.
            self._booted = True
            for relay in list(self._open.values()):
                if relay.state != "open":
                    continue
                if relay.message_id is None:
                    self._schedule(relay)
                elif relay.notice.gate_id in live:
                    relay.state = "sent"
                    self._publish(relay, "sent")
            reason = (
                "未能改为失效（飞书没有响应，或本月 API 额度已用完）"
                if connected
                else "因飞书未连接，无法改为失效"
            )
            raise FeishuBridgeError(
                f"有 {len(live)} 张卡片{reason}。解绑后它们在飞书里仍可点击，但答案不会送达。",
                status=409,
                state=UNRETIRED_CARDS_STATE,
            )
        await self._disconnect()
        delete_feishu_config(self._root)
        self._config = FeishuConfig()
        self._ledger = {}
        self._save_ledger()
        self._pairing = None
        self._scopes = None
        self._last_error = None

    def _live_records(self) -> list[str]:
        """Gates whose cards may still be clickable, judged from the ledger."""
        now = self._clock()
        return [
            gate_id
            for gate_id, record in self._ledger.items()
            if record.get("state") in {"sending", "sent"}
            and isinstance(record.get("mid"), str)
            and isinstance(record.get("sent_at"), (int, float))
            and now - record["sent_at"] < PATCH_WINDOW_SECONDS
        ]

    async def _retire_all(self, line: str) -> list[str]:
        """Retire every card before leaving the app; returns those still live."""
        # As at startup, nothing is sent while the cards are being swept.
        self._booted = False
        self._unschedule_open_gates()
        for relay in self._open.values():
            if relay.state in {"sent", "failed"}:
                relay.state = "open"
                self._publish(relay, "cancelled")
        if self._transport is None:
            return self._live_records()
        return await self._sweep(list(self._ledger), title="提问已转回网页端", line=line)

    async def send_test(self) -> dict[str, Any]:
        self._require_owner()
        transport, open_id = self._transport, self.config.bound_open_id
        if transport is None:
            raise FeishuBridgeError("飞书长连接尚未建立", status=409)
        if not open_id:
            raise FeishuBridgeError("此主机还没有绑定飞书账号", status=409)
        card = cards.test_card(
            host_short=self._host_short, host_label=self._host_label, color=self._host_color
        )
        try:
            message_id = await transport.send_card(
                open_id, card, uuid=f"mc2-test-{self._host_short}-{secrets.token_hex(4)}"
            )
        except FeishuApiError as exc:
            self._note_api_error(exc)
            raise FeishuBridgeError(str(exc), status=502) from exc
        self._count("send")
        return {"message_id": message_id}

    async def _send_welcome(self) -> None:
        transport, open_id = self._transport, self.config.bound_open_id
        if transport is None or not open_id:
            return
        card = cards.welcome_card(host_label=self._host_label, color=self._host_color)
        try:
            await transport.send_card(
                open_id, card, uuid=f"mc2-hello-{self._host_short}-{secrets.token_hex(4)}"
            )
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, FeishuApiError):
                self._note_api_error(exc)
            self._last_error = f"欢迎卡发送失败：{exc}"
            return
        self._count("send")

    async def _refresh_scopes(self) -> None:
        transport = self._transport
        if transport is None:
            return
        try:
            granted = await transport.list_scopes()
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, FeishuApiError):
                self._note_api_error(exc)
            self._scopes = {"error": str(exc), "checked_at": self._clock()}
            return
        self._count("scopes")
        expected = set(MINIMAL_SCOPES)
        if self.config.binding_source == "manual":
            expected |= set(MANUAL_SCOPES)
        self._scopes = {
            "granted": granted,
            "unexpected": sorted(set(granted) - expected),
            "missing": sorted(expected - set(granted)),
            "checked_at": self._clock(),
        }

    # ---- reporting ----

    async def status(self, *, refresh: bool = False) -> dict[str, Any]:
        config = self.config
        if refresh and self._transport is not None:
            await self._online_connections(STATUS_REFRESH_SECONDS)
            scopes_at = (self._scopes or {}).get("checked_at") or 0
            if self._clock() - scopes_at > SCOPES_REFRESH_SECONDS:
                await self._refresh_scopes()
        pairing = self._pairing
        if pairing is not None and self._clock() > pairing[1]:
            pairing = self._pairing = None
        connections = self._connections
        usage = load_usage(self._root)
        return {
            "installed": self._sdk_available(),
            "sdk": SDK_PIN,
            "runtime_owner": self._owner,
            "configured": config.has_credentials,
            "enabled": config.enabled,
            "brand": config.brand,
            "app_id": config.app_id or None,
            "binding_source": config.binding_source,
            "bound": (
                {"open_id": config.bound_open_id, "at": config.bound_at}
                if config.bound_open_id
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
            "online_connections": (
                {
                    "count": connections[0],
                    "checked_at": connections[1],
                    "error": self._connections_error,
                }
                if connections is not None
                else None
            ),
            "scopes": self._scopes,
            "relay": {
                "pending": sum(1 for relay in self._open.values() if relay.state == "sent"),
                "last_poll_at": self._last_poll_at,
                "last_poll_error": self._last_poll_error,
            },
            "usage_this_month": usage,
            "quota_exhausted": self._quota_exhausted,
            "last_error": self._last_error,
            "delay_seconds": config.delay_seconds,
            "poll_seconds": config.poll_seconds,
            "pairing": {"code": pairing[0], "expires_at": pairing[1]} if pairing else None,
            "registration": self.registration_status(),
        }

    def relays_for(self, project_id: str) -> dict[str, dict[str, Any]]:
        """The latest relay event per open gate, for a page that just loaded."""
        return {
            gate_id: relay.event
            for gate_id, relay in self._open.items()
            if relay.notice.project_id == project_id and relay.event is not None
        }

    # ---- internals ----

    def _publish(self, relay: _Relay, state: str, **extra: Any) -> None:
        notice = relay.notice
        event = {
            "type": "gate_relay_updated",
            "node_id": notice.node_id,
            "gate_id": notice.gate_id,
            "state": state,
            "at": self._clock(),
            # Ephemeral: never persisted or replayed; ``relays_for`` heals reloads.
            "seq": 0,
            **{key: value for key, value in extra.items() if value is not None},
        }
        relay.event = event if state != "cancelled" else None
        publish = getattr(self._registry, "publish_project_event", None)
        if publish is not None:
            try:
                publish(notice.project_id, event)
            except Exception:  # noqa: BLE001
                logger.debug("failed to publish feishu relay event", exc_info=True)

    def _count(self, kind: str) -> None:
        try:
            record_usage(self._root, kind)
        except OSError:
            logger.debug("failed to record feishu usage", exc_info=True)

    def _note_api_error(self, exc: FeishuApiError) -> None:
        self._last_error = str(exc)
        if exc.quota_exhausted:
            self._quota_month = current_month()
            self._unschedule_open_gates()

    def _save_config(self, config: FeishuConfig) -> None:
        save_feishu_config(self._root, config)
        self._config = config

    def _save_ledger(self) -> None:
        try:
            save_relay_ledger(self._root, self._ledger)
        except OSError:
            logger.exception("failed to save feishu relay ledger")

    def _require_owner(self) -> None:
        if not self._owner:
            raise FeishuBridgeError(
                "这个后端进程不是本机存储的运行时属主，不能连接飞书", status=409
            )


def _registration_error(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if code == "access_denied":
        return "你在飞书端取消了授权"
    if code == "expired_token":
        return "二维码已过期，请重新发起"
    if code == "abort":
        return "已取消"
    description = getattr(exc, "description", None)
    if code and description:
        return f"{code}：{description}"
    return str(exc) or type(exc).__name__


def _log_task_failure(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("feishu bridge task failed", exc_info=exc)
