"""The only module that talks to WeCom's intelligent-bot long connection.

The bridge sees WeCom through the ``WecomTransport`` protocol, so tests use a
fake. ``WsTransport`` is a small client over ``websockets`` that follows the
frames of WeCom's own SDKs, as measured against the live service:

- ``aibot_subscribe`` authenticates the socket; ``ping`` every 30 s keeps it,
  and two pings in a row without an ack mean the socket is dead.
- Requests (``aibot_send_msg``, ``aibot_respond_update_msg``) are acked by a
  frame carrying the same ``req_id`` and an ``errcode``.
- A bot has one connection at a time: a newer one makes the server send
  ``disconnected_event`` to the older. That is final, not a network fault,
  so this client stops instead of reconnecting; reconnecting would only kick
  the newer connection in turn (the official Python SDK 1.0.2 does exactly
  that, which is why it is not used).
- A card click can only be answered by ``aibot_respond_update_msg`` with the
  click's own ``req_id`` within 5 s, so the handler's card is sent back at
  once; there is no way to change a card later.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)

WS_URL = "wss://openws.work.weixin.qq.com"
TRANSPORT_PACKAGE = "websockets"
TRANSPORT_PIN = "websockets>=13.0"
HEARTBEAT_SECONDS = 30.0
MAX_MISSED_PONGS = 2
ACK_TIMEOUT_SECONDS = 5.0
SUBSCRIBE_TIMEOUT_SECONDS = 10.0
RECONNECT_BASE_SECONDS = 1.0
RECONNECT_MAX_SECONDS = 30.0
MAX_AUTH_FAILURES = 5

#: The response to a click came later than 5 s after it; the card is unchanged.
EXPIRED_RESPONSE_CODE = 846604
#: Rate limited, and also what a bot gets pushing to someone who never wrote to it.
FREQUENCY_LIMIT_CODE = 846607

ConnectionState = Literal["off", "connecting", "connected", "error", "kicked"]


class WecomApiError(Exception):
    """WeCom acked a request with a non-zero ``errcode``: it was not applied."""

    def __init__(self, operation: str, code: int | None, message: str) -> None:
        super().__init__(f"{operation} failed: {code}: {message}")
        self.operation = operation
        self.code = code
        self.message = message

    @property
    def frequency_limited(self) -> bool:
        return self.code == FREQUENCY_LIMIT_CODE


class WecomNotConnected(Exception):
    """No authenticated socket: the request was never sent."""


@dataclass(frozen=True)
class CardEvent:
    """The parts of a ``template_card_event`` the bridge reads."""

    req_id: str
    userid: str | None
    chattype: str | None
    card_type: str
    task_id: str
    event_key: str
    #: ``question_key`` to the chosen option ids.
    selections: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class IncomingMessage:
    """A message a user sent the bot."""

    userid: str | None
    chattype: str | None
    msgtype: str | None
    text: str
    #: Every string in the quoted message, when the user replied by quoting.
    quote_text: str | None = None


@dataclass
class TransportHandlers:
    """Coroutines the transport runs on the server loop."""

    #: Returns the card that replaces the clicked one, or ``None`` to leave it.
    on_card_event: Callable[[CardEvent], Awaitable[dict[str, Any] | None]]
    on_message: Callable[[IncomingMessage], Awaitable[None]] | None = None
    on_state: Callable[[ConnectionState, str | None], None] | None = None


class WecomTransport(Protocol):
    @property
    def state(self) -> ConnectionState: ...

    async def start(self, handlers: TransportHandlers) -> None: ...

    async def stop(self) -> None: ...

    async def send_markdown(self, chatid: str, content: str) -> None: ...

    async def send_card(self, chatid: str, card: dict[str, Any]) -> None: ...


def transport_installed() -> bool:
    """``websockets`` with its asyncio client (13.0 and later)."""
    import importlib.util

    try:
        return importlib.util.find_spec(f"{TRANSPORT_PACKAGE}.asyncio.client") is not None
    except ModuleNotFoundError:
        return False


def parse_card_event(req_id: str, body: dict[str, Any]) -> CardEvent | None:
    event = body.get("event") if isinstance(body.get("event"), dict) else {}
    payload = event.get("template_card_event")
    if not isinstance(payload, dict):
        return None
    selections: dict[str, list[str]] = {}
    items = (payload.get("selected_items") or {}).get("selected_item") or []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("question_key"), str):
            continue
        ids = (item.get("option_ids") or {}).get("option_id") or []
        selections[item["question_key"]] = [str(value) for value in ids if value not in (None, "")]
    sender = body.get("from") if isinstance(body.get("from"), dict) else {}
    return CardEvent(
        req_id=req_id,
        userid=sender.get("userid"),
        chattype=body.get("chattype"),
        card_type=str(payload.get("card_type") or ""),
        task_id=str(payload.get("task_id") or ""),
        event_key=str(payload.get("event_key") or ""),
        selections=selections,
    )


def parse_message(body: dict[str, Any]) -> IncomingMessage:
    sender = body.get("from") if isinstance(body.get("from"), dict) else {}
    text = body.get("text") if isinstance(body.get("text"), dict) else {}
    quote = body.get("quote")
    quote_text = "\n".join(_strings(quote)) if isinstance(quote, dict) else None
    return IncomingMessage(
        userid=sender.get("userid"),
        chattype=body.get("chattype"),
        msgtype=body.get("msgtype"),
        text=str(text.get("content") or ""),
        quote_text=quote_text,
    )


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [text for key, value in node.items() if key != "msgtype" for text in _strings(value)]
    if isinstance(node, list):
        return [text for item in node for text in _strings(item)]
    return []


def _req_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{secrets.token_hex(4)}"


class WsTransport:
    """``WecomTransport`` over one WebSocket long connection."""

    def __init__(self, *, bot_id: str, secret: str, url: str = WS_URL) -> None:
        self._bot_id = bot_id
        self._secret = secret
        self._url = url
        self._state: ConnectionState = "off"
        self._handlers: TransportHandlers | None = None
        self._task: asyncio.Task[None] | None = None
        self._ws: Any = None
        self._authed = False
        self._stopping = False
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._subscribe_ack: asyncio.Future[dict[str, Any]] | None = None
        self._missed_pongs = 0
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def state(self) -> ConnectionState:
        return self._state

    # ---- lifecycle ----

    async def start(self, handlers: TransportHandlers) -> None:
        if self._task is not None:
            return
        self._handlers = handlers
        self._stopping = False
        self._set_state("connecting", None)
        self._task = asyncio.create_task(self._run(), name="wecom-long-connection")

    async def stop(self) -> None:
        self._stopping = True
        task, self._task = self._task, None
        ws = self._ws
        if ws is not None:
            try:
                await asyncio.wait_for(ws.close(), timeout=5)
            except Exception:  # noqa: BLE001
                logger.debug("wecom socket did not close cleanly", exc_info=True)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for pending in list(self._tasks):
            pending.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._set_state("off", None)

    async def _run(self) -> None:
        from websockets.asyncio.client import connect

        attempts = auth_failures = 0
        while not self._stopping:
            error: str | None = None
            try:
                async with connect(
                    self._url, open_timeout=15, ping_interval=None, close_timeout=5
                ) as ws:
                    self._ws = ws
                    ack = await self._subscribe(ws)
                    if ack.get("errcode") not in (0, None):
                        auth_failures += 1
                        error = f"认证失败：{ack.get('errcode')} {ack.get('errmsg') or ''}".strip()
                        if auth_failures >= MAX_AUTH_FAILURES:
                            self._set_state("error", f"{error}。请检查 Bot ID 和 Secret")
                            return
                        raise _Retry(error)
                    attempts = auth_failures = 0
                    self._authed = True
                    self._set_state("connected", None)
                    heartbeat = asyncio.create_task(self._heartbeat(ws))
                    try:
                        async for raw in ws:
                            self._dispatch(raw)
                            if self._state == "kicked":
                                await ws.close()
                                return
                    finally:
                        heartbeat.cancel()
                        await asyncio.gather(heartbeat, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except _Retry as exc:
                error = str(exc)
            except Exception as exc:  # noqa: BLE001
                error = str(exc) or type(exc).__name__
                logger.info("wecom long connection dropped: %s", error)
            finally:
                self._ws = None
                self._authed = False
                self._fail_pending()
            if self._stopping or self._state == "kicked":
                return
            attempts += 1
            delay = min(RECONNECT_BASE_SECONDS * 2 ** (attempts - 1), RECONNECT_MAX_SECONDS)
            self._set_state("connecting", error)
            await asyncio.sleep(delay)

    async def _subscribe(self, ws: Any) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        req_id = _req_id("aibot_subscribe")
        self._subscribe_ack = loop.create_future()
        await ws.send(
            json.dumps(
                {
                    "cmd": "aibot_subscribe",
                    "headers": {"req_id": req_id},
                    "body": {"bot_id": self._bot_id, "secret": self._secret},
                }
            )
        )
        # Frames before the ack (none are expected) are dispatched as usual.
        async def read() -> dict[str, Any]:
            assert self._subscribe_ack is not None
            while not self._subscribe_ack.done():
                self._dispatch(await ws.recv())
            return self._subscribe_ack.result()

        try:
            return await asyncio.wait_for(read(), timeout=SUBSCRIBE_TIMEOUT_SECONDS)
        finally:
            self._subscribe_ack = None

    async def _heartbeat(self, ws: Any) -> None:
        self._missed_pongs = 0
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if self._missed_pongs >= MAX_MISSED_PONGS:
                logger.warning("wecom heartbeat lost, reconnecting")
                await ws.close()
                return
            self._missed_pongs += 1
            await ws.send(json.dumps({"cmd": "ping", "headers": {"req_id": _req_id("ping")}}))

    def _dispatch(self, raw: Any) -> None:
        try:
            frame = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(frame, dict):
            return
        cmd = frame.get("cmd") or ""
        req_id = str((frame.get("headers") or {}).get("req_id") or "")
        body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
        if cmd == "aibot_msg_callback":
            handlers = self._handlers
            if handlers is not None and handlers.on_message is not None:
                self._spawn(handlers.on_message(parse_message(body)))
            return
        if cmd == "aibot_event_callback":
            eventtype = (body.get("event") or {}).get("eventtype")
            if eventtype == "disconnected_event":
                self._set_state(
                    "kicked",
                    "这个机器人在别处建立了连接（另一台主机，或其他程序用了同一组凭证），本机已断开",
                )
                return
            if eventtype == "template_card_event":
                event = parse_card_event(req_id, body)
                if event is not None:
                    self._spawn(self._answer_card_event(event))
            return
        if req_id.startswith("aibot_subscribe"):
            ack = self._subscribe_ack
            if ack is not None and not ack.done():
                ack.set_result(frame)
            return
        if req_id.startswith("ping"):
            if frame.get("errcode") in (0, None):
                self._missed_pongs = 0
            return
        pending = self._pending.get(req_id)
        if pending is not None and not pending.done():
            pending.set_result(frame)

    async def _answer_card_event(self, event: CardEvent) -> None:
        handlers = self._handlers
        if handlers is None:
            return
        card = await handlers.on_card_event(event)
        if card is None:
            return
        body: dict[str, Any] = {"response_type": "update_template_card", "template_card": card}
        if event.userid:
            body["userids"] = [event.userid]
        try:
            await self._request("aibot_respond_update_msg", body, req_id=event.req_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wecom card update for task=%s failed: %s", event.task_id, exc)

    # ---- requests ----

    async def send_markdown(self, chatid: str, content: str) -> None:
        await self._request(
            "aibot_send_msg",
            {"chatid": chatid, "msgtype": "markdown", "markdown": {"content": content}},
        )

    async def send_card(self, chatid: str, card: dict[str, Any]) -> None:
        await self._request(
            "aibot_send_msg",
            {"chatid": chatid, "msgtype": "template_card", "template_card": card},
        )

    async def _request(
        self, cmd: str, body: dict[str, Any], *, req_id: str | None = None
    ) -> dict[str, Any]:
        """Send one frame and wait for its ack.

        ``WecomNotConnected`` means it was never sent; ``TimeoutError`` means
        the outcome is unknown (it may have been delivered).
        """
        ws = self._ws
        if ws is None or not self._authed:
            raise WecomNotConnected("企业微信长连接未建立")
        req_id = req_id or _req_id(cmd)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        try:
            try:
                await ws.send(
                    json.dumps(
                        {"cmd": cmd, "headers": {"req_id": req_id}, "body": body},
                        ensure_ascii=False,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                raise WecomNotConnected(str(exc) or "企业微信长连接已断开") from exc
            frame = await asyncio.wait_for(future, timeout=ACK_TIMEOUT_SECONDS)
        finally:
            self._pending.pop(req_id, None)
        code = frame.get("errcode")
        if code not in (0, None):
            raise WecomApiError(cmd, code, str(frame.get("errmsg") or ""))
        return frame

    def _fail_pending(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(TimeoutError("企业微信长连接已断开，结果未知"))
        self._pending.clear()

    # ---- internals ----

    def _set_state(self, state: ConnectionState, detail: str | None) -> None:
        if self._state == "kicked" and state != "off":
            return
        self._state = state
        handlers = self._handlers
        if handlers is not None and handlers.on_state is not None:
            try:
                handlers.on_state(state, detail)
            except Exception:  # noqa: BLE001
                logger.exception("wecom state observer failed")

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(_log_task_failure)


class _Retry(Exception):
    pass


def _log_task_failure(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("wecom transport task failed", exc_info=exc)
