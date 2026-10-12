"""The only module that talks to ``lark_oapi``.

The bridge sees Feishu through the ``FeishuTransport`` protocol, so tests use
a fake and never import the SDK. ``LarkTransport`` follows what Phase 0
measured on 1.7.3:

- The SDK is imported lazily on the long-connection thread: the import costs
  about 2.3 s and 160 MB, and ``lark_oapi.ws.client`` binds a module-level
  ``loop`` at import time. Importing it on the server loop would hand that
  running loop to ``Client.start()``, which then fails with "This event loop
  is already running"; the thread therefore owns a fresh loop and installs
  it as ``wsmod.loop`` before starting.
- Card callbacks arrive synchronously on that thread. They are handed back
  to the server loop with ``run_coroutine_threadsafe`` and answered within
  2.5 s, the margin under Feishu's 3 s callback budget. A handler that has
  not started by then is abandoned, so the user retries an unchanged card;
  one that started finishes, and its response goes to ``on_late_response``.
- Every HTTP call goes through ``asyncio.to_thread``: even the SDK's async
  methods refresh the tenant token with blocking ``requests``.
- A clean stop needs two private SDK members, ``_auto_reconnect`` and
  ``_disconnect``; the version is pinned and a test asserts they exist.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

SDK_PACKAGE = "lark_oapi"
SDK_PIN = "lark-oapi==1.7.3"
#: Feishu answers a card callback within 3 s or shows the user an error.
CALLBACK_DEADLINE_SECONDS = 2.5
#: ``99991403``: the tenant's monthly API quota is exhausted.
QUOTA_EXHAUSTED_CODE = 99991403
#: ``mget`` reads at most this many messages per call.
MGET_LIMIT = 50

ConnectionState = Literal["off", "connecting", "connected", "error"]


class FeishuApiError(Exception):
    """A Feishu API call that did not succeed, with its numeric code."""

    def __init__(self, operation: str, code: int | None, message: str) -> None:
        super().__init__(f"{operation} failed: {code}: {message}")
        self.operation = operation
        self.code = code
        self.message = message

    @property
    def quota_exhausted(self) -> bool:
        return self.code == QUOTA_EXHAUSTED_CODE


@dataclass(frozen=True)
class CardAction:
    """The parts of a ``card.action.trigger`` callback the bridge reads."""

    operator_open_id: str | None
    value: dict[str, Any]
    form_value: dict[str, Any]
    message_id: str | None


@dataclass(frozen=True)
class IncomingMessage:
    """A message a user sent the bot (manual-mode pairing only)."""

    sender_open_id: str | None
    chat_type: str | None
    message_type: str | None
    text: str


@dataclass
class TransportHandlers:
    """Coroutines the transport runs on the server loop."""

    on_card_action: Callable[[CardAction], Awaitable[dict[str, Any]]]
    #: Called on the server loop with a response that missed the deadline.
    on_late_response: Callable[[CardAction, dict[str, Any]], None] | None = None
    on_message: Callable[[IncomingMessage], Awaitable[None]] | None = None
    #: Called on the server loop after the SDK re-establishes the connection.
    on_reconnected: Callable[[], None] | None = None
    on_state: Callable[[ConnectionState, str | None], None] | None = None


class FeishuTransport(Protocol):
    @property
    def state(self) -> ConnectionState: ...

    async def start(self, handlers: TransportHandlers) -> None: ...

    async def stop(self) -> None: ...

    async def send_card(self, open_id: str, card: dict[str, Any], *, uuid: str) -> str: ...

    async def patch_card(self, message_id: str, card: dict[str, Any]) -> None: ...

    async def get_cards(self, message_ids: list[str]) -> dict[str, dict[str, Any] | None]: ...

    async def online_connections(self) -> int | None: ...

    async def list_scopes(self) -> list[str]: ...


def sdk_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec(SDK_PACKAGE) is not None


def open_api_domain(brand: str) -> str:
    return "https://open.larksuite.com" if brand == "lark" else "https://open.feishu.cn"


@dataclass
class _Thread:
    thread: threading.Thread
    ready: threading.Event = field(default_factory=threading.Event)
    loop: asyncio.AbstractEventLoop | None = None
    client: Any = None
    error: BaseException | None = None


class LarkTransport:
    """``FeishuTransport`` over ``lark_oapi`` (WebSocket long connection)."""

    def __init__(self, *, app_id: str, app_secret: str, brand: str = "feishu") -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._brand = brand
        self._state: ConnectionState = "off"
        self._handlers: TransportHandlers | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._ws: _Thread | None = None
        self._api: Any = None
        self._api_lock = threading.Lock()

    @property
    def state(self) -> ConnectionState:
        return self._state

    # ---- lifecycle ----

    async def start(self, handlers: TransportHandlers) -> None:
        if self._ws is not None:
            return
        self._handlers = handlers
        self._main_loop = asyncio.get_running_loop()
        self._set_state("connecting", None)
        worker = _Thread(
            thread=threading.Thread(
                target=self._run_ws_thread, name="miniclaw2-feishu", daemon=True
            )
        )
        self._ws = worker
        worker.thread.start()
        # Wait for the import and client construction, never for the network.
        await asyncio.to_thread(worker.ready.wait, 30)
        if worker.error is not None:
            self._ws = None
            self._set_state("error", str(worker.error))
            raise worker.error

    async def stop(self) -> None:
        worker, self._ws = self._ws, None
        if worker is None:
            return
        loop, client = worker.loop, worker.client
        if loop is not None and client is not None and loop.is_running():

            async def shut() -> None:
                client._auto_reconnect = False
                await client._disconnect()

            try:
                # wrap_future, not .result(): the close handshake runs on the
                # ws loop and must not wait on this loop being blocked.
                await asyncio.wait_for(
                    asyncio.wrap_future(asyncio.run_coroutine_threadsafe(shut(), loop)),
                    timeout=5,
                )
            except Exception:  # noqa: BLE001
                logger.warning("feishu long connection did not close cleanly", exc_info=True)
            loop.call_soon_threadsafe(loop.stop)
        await asyncio.to_thread(worker.thread.join, 5)
        self._set_state("off", None)

    def _run_ws_thread(self) -> None:
        worker = self._ws
        assert worker is not None
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(self._ws_loop_exception)
        worker.loop = loop
        try:
            import lark_oapi as lark
            from lark_oapi.ws import client as wsmod

            # ``Client.start()`` drives the module-level loop bound at import.
            wsmod.loop = loop
            handler = (
                lark.EventDispatcherHandler.builder("", "")
                .register_p2_card_action_trigger(self._on_card_action_sync)
                .register_p2_im_message_receive_v1(self._on_message_sync)
                .build()
            )
            client = wsmod.Client(
                self._app_id,
                self._app_secret,
                event_handler=handler,
                domain=open_api_domain(self._brand),
                log_level=lark.LogLevel.WARNING,
                source="miniclaw2",
            )
            client.on_reconnecting = lambda: self._set_state_threadsafe("connecting", None)
            client.on_reconnected = self._on_reconnected_threadsafe
            worker.client = client
        except BaseException as exc:  # noqa: BLE001
            worker.error = exc
            worker.ready.set()
            loop.close()
            return
        worker.ready.set()
        loop.call_later(1.0, self._mark_connected, client)
        try:
            client.start()
        except RuntimeError:
            # ``loop.stop()`` from ``stop()`` unwinds ``run_until_complete``.
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("feishu long connection ended: %s", exc)
            self._set_state_threadsafe("error", str(exc))
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    def _ws_loop_exception(self, loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        # With reconnect off, the SDK's receive loop re-raises the close it
        # just completed; after ``stop()`` that is the expected ending.
        exception = context.get("exception")
        if self._ws is None and type(exception).__name__.startswith("ConnectionClosed"):
            return
        loop.default_exception_handler(context)

    def _mark_connected(self, client: Any) -> None:
        if getattr(client, "_conn", None) is not None:
            self._set_state_threadsafe("connected", None)
        elif self._ws is not None and self._ws.client is client:
            assert self._ws.loop is not None
            self._ws.loop.call_later(1.0, self._mark_connected, client)

    def _on_reconnected_threadsafe(self) -> None:
        self._set_state_threadsafe("connected", None)
        handlers, loop = self._handlers, self._main_loop
        if handlers is not None and handlers.on_reconnected is not None and loop is not None:
            loop.call_soon_threadsafe(handlers.on_reconnected)

    def _set_state(self, state: ConnectionState, detail: str | None) -> None:
        self._state = state
        handlers = self._handlers
        if handlers is not None and handlers.on_state is not None:
            try:
                handlers.on_state(state, detail)
            except Exception:  # noqa: BLE001
                logger.exception("feishu state observer failed")

    def _set_state_threadsafe(self, state: ConnectionState, detail: str | None) -> None:
        loop = self._main_loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._set_state, state, detail)

    # ---- callbacks (long-connection thread) ----

    def _on_card_action_sync(self, data: Any) -> Any:
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )

        event = getattr(data, "event", None)
        action = getattr(event, "action", None)
        operator = getattr(event, "operator", None)
        context = getattr(event, "context", None)
        card_action = CardAction(
            operator_open_id=getattr(operator, "open_id", None),
            value=dict(getattr(action, "value", None) or {}),
            form_value=dict(getattr(action, "form_value", None) or {}),
            message_id=getattr(context, "open_message_id", None),
        )
        return P2CardActionTriggerResponse(self._answer_card_action(card_action))

    def _answer_card_action(self, card_action: CardAction) -> dict[str, Any]:
        handlers, loop = self._handlers, self._main_loop
        if handlers is None or loop is None:
            return {"toast": {"type": "warning", "content": "MiniClaw2 正在启动，请稍后再点"}}
        claim = threading.Lock()
        started = abandoned = False

        async def handle() -> dict[str, Any] | None:
            nonlocal started
            with claim:
                if abandoned:
                    return None
                started = True
            return await handlers.on_card_action(card_action)

        future = asyncio.run_coroutine_threadsafe(handle(), loop)
        try:
            out = future.result(timeout=CALLBACK_DEADLINE_SECONDS)
        except TimeoutError:
            # Cancelling would not say whether the handler already ran, so the
            # flag decides: it either never runs or ran and is applied late.
            with claim:
                abandoned = not started
            if abandoned:
                return {"toast": {"type": "warning", "content": "MiniClaw2 正忙，这次提交未处理，请再提交一次"}}
            # Its effect will stand (a local answer resolves its gate); only
            # the card this response would carry has to be applied later.
            future.add_done_callback(lambda done: self._late_response(card_action, done))
            return {"toast": {"type": "info", "content": "已收到，卡片稍后更新"}}
        except Exception:  # noqa: BLE001
            # A raised callback shows the user an error and is not redelivered.
            logger.exception("feishu card callback failed")
            return {"toast": {"type": "error", "content": "处理失败，请回网页端作答"}}
        assert out is not None
        return out

    def _late_response(
        self, action: CardAction, future: concurrent.futures.Future[dict[str, Any] | None]
    ) -> None:
        if future.cancelled():
            return
        exception = future.exception()
        if exception is not None:
            logger.error("feishu card callback failed after its deadline", exc_info=exception)
            return
        handlers, loop, out = self._handlers, self._main_loop, future.result()
        if out is None or handlers is None or handlers.on_late_response is None:
            return
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(handlers.on_late_response, action, out)

    def _on_message_sync(self, data: Any) -> None:
        handlers, loop = self._handlers, self._main_loop
        if handlers is None or handlers.on_message is None or loop is None:
            return
        event = getattr(data, "event", None)
        message = getattr(event, "message", None)
        sender = getattr(event, "sender", None)
        sender_id = getattr(sender, "sender_id", None)
        text = ""
        content = getattr(message, "content", None)
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except ValueError:
                parsed = {}
            if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
                text = parsed["text"]
        incoming = IncomingMessage(
            sender_open_id=getattr(sender_id, "open_id", None),
            chat_type=getattr(message, "chat_type", None),
            message_type=getattr(message, "message_type", None),
            text=text,
        )
        asyncio.run_coroutine_threadsafe(handlers.on_message(incoming), loop)

    # ---- HTTP (thread pool) ----

    def _client(self) -> Any:
        with self._api_lock:
            if self._api is None:
                import lark_oapi as lark

                self._api = (
                    lark.Client.builder()
                    .app_id(self._app_id)
                    .app_secret(self._app_secret)
                    .domain(open_api_domain(self._brand))
                    .log_level(lark.LogLevel.ERROR)
                    .timeout(15)
                    .build()
                )
            return self._api

    async def send_card(self, open_id: str, card: dict[str, Any], *, uuid: str) -> str:
        def call() -> str:
            from lark_oapi.api.im.v1 import (
                CreateMessageRequest,
                CreateMessageRequestBody,
            )

            body = (
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .uuid(uuid)
                .build()
            )
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(body)
                .build()
            )
            response = self._client().im.v1.message.create(request)
            _raise_for(response, "send")
            return str(response.data.message_id)

        return await asyncio.to_thread(call)

    async def patch_card(self, message_id: str, card: dict[str, Any]) -> None:
        def call() -> None:
            from lark_oapi.api.im.v1 import (
                PatchMessageRequest,
                PatchMessageRequestBody,
            )

            request = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(json.dumps(card, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            _raise_for(self._client().im.v1.message.patch(request), "patch")

        await asyncio.to_thread(call)

    async def get_cards(self, message_ids: list[str]) -> dict[str, dict[str, Any] | None]:
        """Read cards back as sent JSON (``user_card_content``), in one ``mget``."""
        if not message_ids:
            return {}
        query = urlencode(
            [("message_ids", mid) for mid in message_ids[:MGET_LIMIT]]
            + [("card_msg_content_type", "user_card_content")]
        )
        payload = await self._raw_get(f"/open-apis/im/v1/messages/mget?{query}", "mget")
        cards: dict[str, dict[str, Any] | None] = {}
        for item in (payload.get("data") or {}).get("items") or []:
            if not isinstance(item, dict) or not isinstance(item.get("message_id"), str):
                continue
            content = (item.get("body") or {}).get("content")
            try:
                card = json.loads(content) if isinstance(content, str) else None
            except ValueError:
                card = None
            cards[item["message_id"]] = card if isinstance(card, dict) else None
        return cards

    async def online_connections(self) -> int | None:
        """``online_instance_cnt`` (undocumented; found in lark-cli)."""
        payload = await self._raw_get("/open-apis/event/v1/connection", "connections")
        count = (payload.get("data") or {}).get("online_instance_cnt")
        return count if isinstance(count, int) else None

    async def list_scopes(self) -> list[str]:
        payload = await self._raw_get("/open-apis/application/v6/scopes", "scopes")
        names: list[str] = []
        for scope in (payload.get("data") or {}).get("scopes") or []:
            if not isinstance(scope, dict):
                continue
            name = scope.get("scope_name")
            if isinstance(name, str) and scope.get("grant_status", 1) == 1 and name not in names:
                names.append(name)
        return names

    async def _raw_get(self, uri: str, operation: str) -> dict[str, Any]:
        def call() -> dict[str, Any]:
            from lark_oapi.core.enum import AccessTokenType, HttpMethod
            from lark_oapi.core.model import BaseRequest

            request = (
                BaseRequest.builder()
                .http_method(HttpMethod.GET)
                .uri(uri)
                .token_types({AccessTokenType.TENANT})
                .build()
            )
            response = self._client().request(request)
            raw = getattr(getattr(response, "raw", None), "content", None)
            try:
                payload = json.loads(raw) if raw else {}
            except ValueError:
                payload = {}
            code = payload.get("code") if isinstance(payload, dict) else None
            if code != 0:
                message = payload.get("msg") if isinstance(payload, dict) else None
                raise FeishuApiError(operation, code, str(message or response.msg or ""))
            return payload

        return await asyncio.to_thread(call)


def _raise_for(response: Any, operation: str) -> None:
    if response.success():
        return
    raise FeishuApiError(operation, response.code, str(response.msg or ""))
