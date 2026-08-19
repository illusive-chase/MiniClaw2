"""Codex app-server provider adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from ..domain import GateSubtype
from ..events import Activity, TextDelta, Thinking, Usage
from ..model_catalog import get_model_preset
from .base import (
    AgentProviderContext,
    AgentProviderEvent,
    GateRequest,
    ReviewFinding,
    ReviewReport,
    ReviewSpec,
    compose_turn_text,
)

logger = logging.getLogger(__name__)

_CODEX_REQUEST_TIMEOUT_SECONDS = 60.0
_CODEX_STDERR_TAIL_LINES = 20
_CODEX_STDIO_BUFFER_LIMIT_BYTES = 16 * 1024 * 1024
_MIN_REVIEW_VERSION = (0, 144, 1)
_MIN_DYNAMIC_TOOLS_VERSION = (0, 146, 0)


class CodexRpcError(RuntimeError):
    """A structured JSON-RPC error returned by Codex app-server."""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(message)
        self.code = code


class CodexProvider:
    name = "codex"

    def __init__(self) -> None:
        self._client: _CodexJsonRpcClient | None = None
        self._thread_id: str | None = None
        self._turn_id: str | None = None
        self._stop = False

    async def run(self, context: AgentProviderContext) -> AsyncIterator[AgentProviderEvent]:
        self._stop = False
        async with _CodexJsonRpcClient(
            cwd=context.project.root_path,
            env_overrides=getattr(
                getattr(context, "skill_materialization", None),
                "env_overrides",
                None,
            ),
        ) as client:
            self._client = client
            try:
                initialized = await client.initialize()
                await _configure_skill_roots(client, context)
                thread_id = context.node.provider_session_id
                fresh_thread = not thread_id
                if (
                    fresh_thread
                    and not getattr(context, "minimal_mode", False)
                    and not _codex_dynamic_tools_capable(initialized)
                ):
                    raise RuntimeError(
                        "inline ask-user requires codex-cli 0.146.0 or newer"
                    )
                if not thread_id:
                    start = await client.request(
                        "thread/start",
                        _thread_params(context, {"cwd": context.project.root_path}),
                    )
                    thread_id = start.get("thread", {}).get("id")
                    if not thread_id:
                        raise RuntimeError(f"Codex thread/start returned no thread id: {start}")
                    yield AgentProviderEvent(kind="session", session_id=thread_id)
                else:
                    resumed = await client.request(
                        "thread/resume",
                        _thread_params(
                            context,
                            {
                                "threadId": thread_id,
                                "cwd": context.project.root_path,
                            },
                        ),
                    )
                    resumed_thread_id = resumed.get("thread", {}).get("id")
                    if resumed_thread_id and resumed_thread_id != thread_id:
                        thread_id = resumed_thread_id
                        yield AgentProviderEvent(kind="session", session_id=thread_id)

                self._thread_id = thread_id
                turn_text = compose_turn_text(
                    context.node.prompt,
                    getattr(context, "launch_instructions", ""),
                )
                # Minimal mode (out-of-band framework agent) deliberately
                # does not inject the project's own CONTEXT.md — the agent
                # reads it as a tool when needed.
                minimal_mode = getattr(context, "minimal_mode", False)
                if fresh_thread and context.system_context and not minimal_mode:
                    turn_text = f"{context.system_context}\n\n{turn_text}"
                turn = await client.request(
                    "turn/start",
                    _turn_params(context, thread_id, turn_text),
                )
                turn_id = turn.get("turn", {}).get("id")
                if not turn_id:
                    raise RuntimeError(f"Codex turn/start returned no turn id: {turn}")
                self._turn_id = turn_id
                yield AgentProviderEvent(kind="turn", turn_id=turn_id)

                while not self._stop:
                    message = await client.receive()
                    async for ev in self._handle_message(message, context, client):
                        yield ev
                        if ev.kind in {"done", "error"}:
                            self._stop = True
                    if self._stop:
                        break
            except asyncio.CancelledError:
                await self.interrupt()
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("Codex provider failed")
                text = f"Codex provider error: {exc}"
                yield AgentProviderEvent(kind="error", error=text)
            finally:
                self._client = None

    async def run_review(
        self, context: AgentProviderContext, spec: ReviewSpec
    ) -> AsyncIterator[AgentProviderEvent]:
        self._stop = False
        if spec.target.type != "uncommitted":
            yield AgentProviderEvent(
                kind="error", error=f"unsupported Codex review target: {spec.target.type}"
            )
            return
        async with _CodexJsonRpcClient(
            cwd=context.project.root_path,
            env_overrides=getattr(
                getattr(context, "skill_materialization", None),
                "env_overrides",
                None,
            ),
        ) as client:
            self._client = client
            try:
                initialized = await client.initialize()
                if not _codex_review_capable(initialized):
                    yield AgentProviderEvent(
                        kind="error",
                        error="native code review requires codex-cli 0.144.1 or newer",
                    )
                    return
                await _configure_skill_roots(client, context)
                thread_id = context.node.provider_session_id
                if not thread_id and not _codex_dynamic_tools_capable(initialized):
                    yield AgentProviderEvent(
                        kind="error",
                        error="inline ask-user requires codex-cli 0.146.0 or newer",
                    )
                    return
                if not thread_id:
                    thread_base: dict[str, Any] = {
                        "cwd": context.project.root_path,
                    }
                    if context.system_context:
                        thread_base["developerInstructions"] = context.system_context
                    started = await client.request(
                        "thread/start",
                        _thread_params(context, thread_base),
                    )
                    thread_id = started.get("thread", {}).get("id")
                    if not thread_id:
                        raise RuntimeError(
                            f"Codex thread/start returned no thread id: {started}"
                        )
                    yield AgentProviderEvent(kind="session", session_id=thread_id)
                else:
                    resumed = await client.request(
                        "thread/resume",
                        _thread_params(
                            context,
                            {"threadId": thread_id, "cwd": context.project.root_path},
                        ),
                    )
                    resumed_id = resumed.get("thread", {}).get("id")
                    if resumed_id and resumed_id != thread_id:
                        thread_id = resumed_id
                        yield AgentProviderEvent(kind="session", session_id=thread_id)
                self._thread_id = thread_id
                try:
                    response = await client.request(
                        "review/start",
                        {
                            "threadId": thread_id,
                            "target": {"type": "uncommittedChanges"},
                            "delivery": "inline",
                        },
                    )
                except CodexRpcError as exc:
                    if exc.code == -32601:
                        yield AgentProviderEvent(
                            kind="error",
                            error="native code review requires codex-cli 0.144.1 or newer",
                        )
                        return
                    raise
                turn_id = response.get("turn", {}).get("id")
                if not turn_id:
                    raise RuntimeError(f"Codex review/start returned no turn id: {response}")
                self._turn_id = turn_id
                yield AgentProviderEvent(kind="turn", turn_id=turn_id)
                while not self._stop:
                    message = await client.receive()
                    async for event in self._handle_message(message, context, client):
                        yield event
                        if event.kind in {"done", "error"}:
                            self._stop = True
                    if self._stop:
                        break
            except asyncio.CancelledError:
                await self.interrupt()
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("Codex native review failed")
                yield AgentProviderEvent(
                    kind="error", error=f"Codex native review error: {exc}"
                )
            finally:
                self._client = None

    async def interrupt(self) -> None:
        client = self._client
        if client is None or self._thread_id is None or self._turn_id is None:
            return
        try:
            await client.request(
                "turn/interrupt",
                {"threadId": self._thread_id, "turnId": self._turn_id},
                timeout=2.0,
            )
        except Exception:  # noqa: BLE001
            logger.debug("Codex turn/interrupt failed", exc_info=True)

    async def _handle_message(
        self,
        message: dict[str, Any],
        context: AgentProviderContext,
        client: "_CodexJsonRpcClient",
    ) -> AsyncIterator[AgentProviderEvent]:
        if "method" not in message:
            return

        method = message.get("method")
        params = message.get("params") or {}

        if "id" in message:
            response = await self._handle_server_request(message, context)
            await client.respond(message["id"], response)
            return

        if method == "item/agentMessage/delta":
            yield AgentProviderEvent(
                kind="event",
                event=TextDelta(text=str(params.get("delta") or "")),
            )
            return

        if method in {"item/reasoning/textDelta", "item/reasoning/summaryTextDelta"}:
            yield AgentProviderEvent(
                kind="event",
                event=Thinking(text=str(params.get("delta") or "")),
            )
            return

        if method == "item/plan/delta":
            yield AgentProviderEvent(
                kind="event",
                event=Thinking(text=str(params.get("delta") or "")),
            )
            return

        if method == "thread/tokenUsage/updated":
            usage = _usage_from_token_usage(params.get("tokenUsage") or {})
            yield AgentProviderEvent(
                kind="event",
                event=usage,
            )
            return

        if method == "turn/started":
            turn_id = (params.get("turn") or {}).get("id")
            if turn_id:
                self._turn_id = turn_id
                yield AgentProviderEvent(kind="turn", turn_id=turn_id)
            return

        if method == "item/started":
            activity = _activity_from_item(params.get("item") or {}, "start")
            if activity is not None:
                yield AgentProviderEvent(kind="event", event=activity)
            return

        if method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "exitedReviewMode":
                yield AgentProviderEvent(
                    kind="review", report=_review_report_from_codex(item.get("review"))
                )
                return
            activity = _activity_from_item(item, "finish")
            if activity is not None:
                yield AgentProviderEvent(kind="event", event=activity)
            return

        if method == "item/commandExecution/outputDelta":
            item_id = str(params.get("itemId") or "command")
            delta = str(params.get("delta") or "")
            yield AgentProviderEvent(
                kind="event",
                event=Activity(
                    kind="tool",
                    status="progress",
                    id=item_id,
                    name="command",
                    summary=_truncate(delta),
                    result=delta,
                    result_kind="stdout",
                ),
            )
            return

        if method == "item/fileChange/patchUpdated":
            item_id = str(params.get("itemId") or "fileChange")
            yield AgentProviderEvent(
                kind="event",
                event=Activity(
                    kind="tool",
                    status="progress",
                    id=item_id,
                    name="fileChange",
                    summary=_truncate(json.dumps(params.get("changes") or [], ensure_ascii=False)),
                ),
            )
            return

        if method == "turn/completed":
            turn = params.get("turn") or {}
            if turn.get("status") == "interrupted":
                yield AgentProviderEvent(kind="done", final_state="cancelled")
            elif turn.get("status") == "failed":
                error = turn.get("error") or {}
                text = error.get("message") or json.dumps(error, ensure_ascii=False)
                yield AgentProviderEvent(kind="error", error=text)
            else:
                yield AgentProviderEvent(kind="done")
            return

        if method == "error":
            error = params.get("error") or {}
            text = error.get("message") or json.dumps(error, ensure_ascii=False)
            if params.get("willRetry") is True:
                logger.warning("Codex turn error is being retried: %s", text)
                return
            yield AgentProviderEvent(kind="error", error=text)
            return

    async def _handle_server_request(
        self,
        message: dict[str, Any],
        context: AgentProviderContext,
    ) -> dict[str, Any]:
        method = message.get("method")
        params = message.get("params") or {}
        request_id = str(message.get("id"))

        if method == "item/tool/requestUserInput":
            response = await context.request_gate(
                GateRequest(
                    subtype=GateSubtype.ASK_USER,
                    tool_name="request_user_input",
                    tool_input={"questions": params.get("questions") or []},
                    provider_request_id=request_id,
                    response_hint={"codex_method": method},
                )
            )
            return _codex_user_input_response(response)

        if method == "item/commandExecution/requestApproval":
            response = await context.request_gate(
                GateRequest(
                    subtype=GateSubtype.PERMISSION,
                    tool_name="commandExecution",
                    tool_input=params,
                    provider_request_id=request_id,
                    response_hint={"codex_method": method, "decision_kind": "command"},
                )
            )
            return {"decision": _codex_decision(response, command=True)}

        if method == "execCommandApproval":
            response = await context.request_gate(
                GateRequest(
                    subtype=GateSubtype.PERMISSION,
                    tool_name="commandExecution",
                    tool_input=params,
                    provider_request_id=request_id,
                    response_hint={"codex_method": method, "decision_kind": "command"},
                )
            )
            return {"decision": _codex_legacy_decision(response)}

        if method == "item/fileChange/requestApproval":
            response = await context.request_gate(
                GateRequest(
                    subtype=GateSubtype.PERMISSION,
                    tool_name="fileChange",
                    tool_input=params,
                    provider_request_id=request_id,
                    response_hint={"codex_method": method, "decision_kind": "file"},
                )
            )
            return {"decision": _codex_decision(response, command=False)}

        if method == "applyPatchApproval":
            response = await context.request_gate(
                GateRequest(
                    subtype=GateSubtype.PERMISSION,
                    tool_name="fileChange",
                    tool_input=params,
                    provider_request_id=request_id,
                    response_hint={"codex_method": method, "decision_kind": "file"},
                )
            )
            return {"decision": _codex_legacy_decision(response)}

        if method == "item/permissions/requestApproval":
            response = await context.request_gate(
                GateRequest(
                    subtype=GateSubtype.PERMISSION,
                    tool_name="permissions",
                    tool_input=params,
                    provider_request_id=request_id,
                    response_hint={"codex_method": method, "decision_kind": "permissions"},
                )
            )
            if response.get("response"):
                return response["response"]
            if response.get("allow", True):
                return {
                    "permissions": params.get("permissions") or {},
                    "scope": response.get("scope") or "turn",
                }
            return {
                "permissions": {},
                "scope": "turn",
                "strictAutoReview": True,
            }

        if method == "mcpServer/elicitation/request":
            return {
                "action": "decline",
                "content": None,
                "_meta": {
                    "reason": "MiniClaw2 does not support MCP elicitation yet.",
                },
            }

        if method == "item/tool/call":
            if params.get("namespace") is None and params.get("tool") == "ask_user":
                return await _handle_dynamic_ask_user(params, request_id, context)
            return _dynamic_tool_error(
                f"不支持的动态工具：{params.get('tool') or '<unknown>'}"
            )

        response = await context.request_gate(
            GateRequest(
                subtype=GateSubtype.PERMISSION,
                tool_name=str(method),
                tool_input=params,
                provider_request_id=request_id,
                response_hint={"codex_method": method},
            )
        )
        return response.get("response") or {"decision": _codex_decision(response)}


class _CodexJsonRpcClient:
    def __init__(
        self,
        *,
        cwd: str | None = None,
        env_overrides: dict[str, str] | None = None,
    ) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._cwd = str(Path(cwd).resolve(strict=False)) if cwd else None
        self._env_overrides = dict(env_overrides or {})
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._queue: asyncio.Queue[dict[str, Any] | BaseException] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail: deque[str] = deque(maxlen=_CODEX_STDERR_TAIL_LINES)
        self._closed_error: RuntimeError | None = None
        self._closing = False

    async def __aenter__(self) -> "_CodexJsonRpcClient":
        env = os.environ.copy()
        env.update(self._env_overrides)
        self._proc = await asyncio.create_subprocess_exec(
            "codex",
            "app-server",
            "--listen",
            "stdio://",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=self._cwd,
            limit=_CODEX_STDIO_BUFFER_LIMIT_BYTES,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self._closing = True
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
        tasks = [
            task
            for task in (self._reader_task, self._stderr_task)
            if task is not None
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def initialize(self) -> dict[str, Any]:
        return await self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "miniclaw2",
                    "title": "MiniClaw2",
                    "version": "0.1.0",
                },
                "capabilities": {
                    "experimentalApi": True,
                    "requestAttestation": False,
                },
            },
        )

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = fut
        try:
            await self._send({"id": req_id, "method": method, "params": params})
            effective_timeout = (
                _CODEX_REQUEST_TIMEOUT_SECONDS if timeout is None else timeout
            )
            return await asyncio.wait_for(fut, timeout=effective_timeout)
        except TimeoutError as exc:
            fut.cancel()
            raise TimeoutError(
                f"Codex request {method} timed out after {effective_timeout:.1f}s"
            ) from exc
        finally:
            self._pending.pop(req_id, None)

    async def respond(self, req_id: str | int, result: dict[str, Any]) -> None:
        await self._send({"id": req_id, "result": result})

    async def receive(self) -> dict[str, Any]:
        if self._closed_error is not None and self._queue.empty():
            raise self._closed_error
        item = await self._queue.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("Codex app-server is not running")
        if self._proc.returncode is not None:
            raise self._fail("exited before request could be sent")
        data = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            self._proc.stdin.write(data.encode("utf-8"))
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise self._fail("closed stdin before request could be sent") from exc

    async def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    payload = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    logger.debug("non-json Codex stdout: %r", line)
                    continue
                msg_id = payload.get("id")
                if msg_id in self._pending and (
                    "result" in payload or "error" in payload
                ):
                    fut = self._pending.get(msg_id)
                    if fut is not None and not fut.done():
                        if "error" in payload:
                            error = payload["error"]
                            if not isinstance(error, dict):
                                error = {}
                            code = error.get("code")
                            fut.set_exception(
                                CodexRpcError(
                                    code if isinstance(code, int) else None,
                                    str(error.get("message") or "Codex error"),
                                )
                            )
                        else:
                            fut.set_result(payload.get("result") or {})
                else:
                    await self._queue.put(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not self._closing:
                self._fail("stdout reader failed", exc)
            return
        if not self._closing:
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=0.2)
            except TimeoutError:
                pass
            await asyncio.sleep(0)
            self._fail("closed stdout before completing the Codex request")

    async def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    self._stderr_tail.append(text)
                    logger.debug("codex app-server stderr: %s", text)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.debug("Codex stderr reader failed", exc_info=True)

    def _fail(self, reason: str, cause: BaseException | None = None) -> RuntimeError:
        if self._closed_error is None:
            self._closed_error = RuntimeError(
                self._format_process_error(reason, cause)
            )
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(self._closed_error)
            self._pending.clear()
            self._queue.put_nowait(self._closed_error)
        return self._closed_error

    def _format_process_error(
        self,
        reason: str,
        cause: BaseException | None = None,
    ) -> str:
        parts = [f"Codex app-server {reason}"]
        returncode = self._proc.returncode if self._proc is not None else None
        if returncode is not None:
            parts.append(f"exit code {returncode}")
        if cause is not None:
            parts.append(str(cause))
        message = "; ".join(parts)
        if self._stderr_tail:
            stderr = "\n".join(self._stderr_tail)
            message = f"{message}\nstderr tail:\n{stderr}"
        return message


def _codex_legacy_decision(response: dict[str, Any]) -> Any:
    if response.get("allow", True):
        if response.get("scope") == "session":
            return "approved_for_session"
        return "approved"
    if response.get("interrupt", False):
        return "abort"
    return "denied"


def _thread_params(
    context: AgentProviderContext,
    base: dict[str, Any],
) -> dict[str, Any]:
    settings = context.project.settings_override
    preset = get_model_preset(
        context.node.model_preset_id,
        store_root=getattr(context, "store_root", None),
    )
    params = dict(base)
    _set_if_present(params, "model", preset.model)
    # Generic presets leave this unset so app-server inherits the selected
    # model provider and base URL from the user's Codex configuration.
    _set_if_present(params, "modelProvider", preset.model_provider)
    _set_if_present(params, "serviceTier", preset.service_tier)
    _set_if_present(params, "reasoningEffort", preset.reasoning_effort)
    if getattr(context, "minimal_mode", False):
        # Out-of-band framework agent: no UI to answer approvals on, so
        # disable interactive approvals and avoid inheriting a project
        # sandbox override that could be read-only or danger-full-access.
        params["approvalPolicy"] = "never"
        params["sandbox"] = "workspace-write"
    else:
        _set_if_present(params, "approvalPolicy", settings.get("approval_policy"))
        params["sandbox"] = settings.get("sandbox") or "workspace-write"
    _set_if_present(params, "config", settings.get("codex_config"))
    if "threadId" not in params:
        params["serviceName"] = "MiniClaw2"
        if not getattr(context, "minimal_mode", False):
            params["dynamicTools"] = [_ask_user_dynamic_tool()]
    return params


def _ask_user_dynamic_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "ask_user",
        "description": (
            "Ask the user one to three short blocking questions and wait for "
            "the answers. Use this whenever user input is required before continuing."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["questions"],
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "header", "question", "options"],
                        "properties": {
                            "id": {"type": "string"},
                            "header": {"type": "string"},
                            "question": {"type": "string"},
                            "multiSelect": {"type": "boolean"},
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["label", "description"],
                                    "properties": {
                                        "label": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                }
            },
        },
    }


def _turn_params(
    context: AgentProviderContext,
    thread_id: str,
    turn_text: str,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "threadId": thread_id,
        "input": [
            {
                "type": "text",
                "text": turn_text,
                "text_elements": [],
            }
        ],
    }
    if getattr(context, "minimal_mode", False):
        params["approvalPolicy"] = "never"
        params["sandboxPolicy"] = _workspace_write_sandbox_policy(
            context,
            exclude_tmp=True,
        )
    elif (
        context.project.settings_override.get("sandbox") or "workspace-write"
    ) == "workspace-write":
        params["sandboxPolicy"] = _workspace_write_sandbox_policy(context)
    return params


def _workspace_write_sandbox_policy(
    context: AgentProviderContext,
    *,
    exclude_tmp: bool = False,
) -> dict[str, Any]:
    project_root = Path(context.project.root_path).resolve(strict=False)
    policy: dict[str, Any] = {
        "type": "workspaceWrite",
        "writableRoots": [str(project_root)],
        "networkAccess": False,
    }
    if exclude_tmp:
        policy["excludeTmpdirEnvVar"] = True
        policy["excludeSlashTmp"] = True
    return policy


def _set_if_present(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _codex_user_input_response(response: dict[str, Any]) -> dict[str, Any]:
    raw = response.get("response")
    answers = raw.get("answers") if isinstance(raw, dict) else None
    normalized: dict[str, dict[str, list[str]]] = {}
    if isinstance(answers, dict):
        for key, value in answers.items():
            if isinstance(value, dict) and isinstance(value.get("answers"), list):
                normalized[str(key)] = {"answers": [str(v) for v in value["answers"]]}
    return {"answers": normalized}


async def _handle_dynamic_ask_user(
    params: dict[str, Any],
    request_id: str,
    context: AgentProviderContext,
) -> dict[str, Any]:
    questions, error = _normalize_dynamic_questions(params.get("arguments"))
    if error is not None:
        return _dynamic_tool_error(error)

    response = await context.request_gate(
        GateRequest(
            subtype=GateSubtype.ASK_USER,
            tool_name="ask_user",
            tool_input={"questions": questions},
            provider_request_id=request_id,
            response_hint={
                "codex_method": "item/tool/call",
                "codex_call_id": params.get("callId"),
                "dynamic_tool": "ask_user",
            },
        )
    )
    answers = _normalize_dynamic_answers(response, questions)
    if answers is None:
        message = response.get("message")
        detail = f"：{message}" if isinstance(message, str) and message else ""
        return _dynamic_tool_error(f"用户未返回有效回答{detail}")
    return {
        "contentItems": [
            {
                "type": "inputText",
                "text": json.dumps(
                    {"answers": answers},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        ],
        "success": True,
    }


def _normalize_dynamic_questions(
    arguments: Any,
) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(arguments, dict):
        return [], "ask_user 参数必须是对象"
    if set(arguments) != {"questions"}:
        return [], "ask_user 参数只能包含 questions"
    raw_questions = arguments.get("questions")
    if not isinstance(raw_questions, list) or not 1 <= len(raw_questions) <= 3:
        return [], "ask_user 必须包含 1 到 3 个问题"

    questions: list[dict[str, Any]] = []
    question_ids: set[str] = set()
    allowed_question_keys = {"id", "header", "question", "options", "multiSelect"}
    for index, raw_question in enumerate(raw_questions, start=1):
        if not isinstance(raw_question, dict):
            return [], f"ask_user 的第 {index} 个问题必须是对象"
        if not {"id", "header", "question", "options"}.issubset(raw_question):
            return [], f"ask_user 的第 {index} 个问题缺少必填字段"
        if not set(raw_question).issubset(allowed_question_keys):
            return [], f"ask_user 的第 {index} 个问题包含未知字段"

        question_id = raw_question.get("id")
        header = raw_question.get("header")
        question = raw_question.get("question")
        if not isinstance(question_id, str) or not question_id.strip():
            return [], f"ask_user 的第 {index} 个问题 id 无效"
        question_id = question_id.strip()
        if question_id in question_ids:
            return [], f"ask_user 的问题 id 重复：{question_id}"
        if not isinstance(header, str) or not header.strip():
            return [], f"ask_user 的第 {index} 个问题 header 无效"
        if not isinstance(question, str) or not question.strip():
            return [], f"ask_user 的第 {index} 个问题正文无效"

        raw_options = raw_question.get("options")
        if not isinstance(raw_options, list):
            return [], f"ask_user 的第 {index} 个问题 options 必须是数组"
        options: list[dict[str, str]] = []
        for option_index, raw_option in enumerate(raw_options, start=1):
            if not isinstance(raw_option, dict) or set(raw_option) != {
                "label",
                "description",
            }:
                return [], (
                    f"ask_user 的第 {index} 个问题中第 {option_index} 个选项无效"
                )
            label = raw_option.get("label")
            description = raw_option.get("description")
            if not isinstance(label, str) or not label.strip():
                return [], (
                    f"ask_user 的第 {index} 个问题中第 {option_index} 个标签无效"
                )
            if not isinstance(description, str):
                return [], (
                    f"ask_user 的第 {index} 个问题中第 {option_index} 个说明无效"
                )
            options.append(
                {"label": label.strip(), "description": description.strip()}
            )

        normalized: dict[str, Any] = {
            "id": question_id,
            "header": header.strip(),
            "question": question.strip(),
            "options": options,
        }
        if "multiSelect" in raw_question:
            multi_select = raw_question.get("multiSelect")
            if not isinstance(multi_select, bool):
                return [], f"ask_user 的第 {index} 个问题 multiSelect 无效"
            normalized["multiSelect"] = multi_select
        question_ids.add(question_id)
        questions.append(normalized)
    return questions, None


def _normalize_dynamic_answers(
    response: dict[str, Any],
    questions: list[dict[str, Any]],
) -> dict[str, dict[str, list[str]]] | None:
    raw_response = response.get("response")
    raw_answers = raw_response.get("answers") if isinstance(raw_response, dict) else None
    expected_ids = [str(question["id"]) for question in questions]
    if not isinstance(raw_answers, dict) or set(raw_answers) != set(expected_ids):
        return None

    normalized: dict[str, dict[str, list[str]]] = {}
    for question_id in expected_ids:
        raw_answer = raw_answers.get(question_id)
        values = raw_answer.get("answers") if isinstance(raw_answer, dict) else None
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(value, str) and value.strip() for value in values)
        ):
            return None
        normalized[question_id] = {
            "answers": [value.strip() for value in values],
        }
    return normalized


def _dynamic_tool_error(message: str) -> dict[str, Any]:
    return {
        "contentItems": [{"type": "inputText", "text": message}],
        "success": False,
    }


def _codex_decision(
    response: dict[str, Any],
    *,
    command: bool = False,
) -> Any:
    if response.get("allow", True):
        if response.get("scope") == "session":
            return "acceptForSession"
        return "accept"
    if command and response.get("interrupt", False):
        return "cancel"
    return "decline"


def _activity_from_item(item: dict[str, Any], status: str) -> Activity | None:
    item_type = item.get("type")
    item_id = str(item.get("id") or item_type or "item")
    finishing = status in {"finish", "failed"}
    if item_type == "commandExecution":
        summary = item.get("command") or ""
        final_status = "failed" if item.get("exitCode") not in (None, 0) and status == "finish" else status
        is_failure = final_status == "failed"
        result_text: str | None = None
        result_kind: str | None = None
        if finishing:
            aggregated = item.get("aggregatedOutput")
            if aggregated:
                result_text = _truncate(str(aggregated), 4096)
                result_kind = "text" if is_failure else "stdout"
        return Activity(
            kind="tool",
            status=final_status,  # type: ignore[arg-type]
            id=item_id,
            name="command",
            summary=_truncate(str(summary)),
            parameters=str(summary),
            command=str(summary),
            result=result_text,
            result_kind=result_kind,  # type: ignore[arg-type]
        )
    if item_type == "fileChange":
        changes = item.get("changes") or []
        result_text = None
        result_kind = None
        if finishing and changes:
            rendered, rendered_kind = _render_changes(changes)
            if rendered:
                result_text = _truncate(rendered, 4096)
                result_kind = "text" if status == "failed" else rendered_kind
        return Activity(
            kind="tool",
            status=status,  # type: ignore[arg-type]
            id=item_id,
            name="fileChange",
            summary=_truncate(json.dumps(changes, ensure_ascii=False)),
            parameters=json.dumps(changes, ensure_ascii=False),
            result=result_text,
            result_kind=result_kind,  # type: ignore[arg-type]
        )
    if item_type == "mcpToolCall":
        parameters = json.dumps(item.get("arguments"), ensure_ascii=False)
        return Activity(
            kind="tool",
            status=status,  # type: ignore[arg-type]
            id=item_id,
            name=f"{item.get('server') or 'mcp'}:{item.get('tool') or ''}",
            summary=_truncate(parameters),
            parameters=parameters,
        )
    if item_type == "dynamicToolCall":
        parameters = json.dumps(item.get("arguments"), ensure_ascii=False)
        return Activity(
            kind="tool",
            status=status,  # type: ignore[arg-type]
            id=item_id,
            name=str(item.get("tool") or "tool"),
            summary=_truncate(parameters),
            parameters=parameters,
        )
    if item_type in {"webSearch", "imageGeneration", "collabAgentToolCall"}:
        parameters = json.dumps(item, ensure_ascii=False)
        return Activity(
            kind="agent",
            status=status,  # type: ignore[arg-type]
            id=item_id,
            name=str(item_type),
            summary=_truncate(parameters),
            parameters=parameters,
        )
    return None


def _usage_from_token_usage(token_usage: Any) -> Usage:
    root = token_usage if isinstance(token_usage, dict) else {}
    last = root.get("last") if isinstance(root.get("last"), dict) else {}
    total = root.get("total") if isinstance(root.get("total"), dict) else {}

    return Usage(
        input_tokens=_usage_int(
            last,
            "inputTokens",
            "input_tokens",
            "requestTokens",
            "promptTokens",
        ),
        output_tokens=_usage_int(
            last,
            "outputTokens",
            "output_tokens",
            "completionTokens",
        ),
        cache_read_tokens=_usage_int(
            last,
            "cachedInputTokens",
            "cacheReadInputTokens",
            "cacheReadTokens",
            "cache_read_input_tokens",
        ),
        cache_creation_tokens=_usage_int(
            last,
            "cacheCreationInputTokens",
            "cacheCreationTokens",
            "cache_creation_input_tokens",
        ),
        cumulative_output_tokens=_usage_optional_int(
            (total, root),
            "outputTokens",
            "cumulativeOutputTokens",
            "output_tokens",
            "completionTokens",
        ),
        cumulative_cache_creation_tokens=_usage_optional_int(
            (total, root),
            "cacheCreationInputTokens",
            "cacheCreationTokens",
            "cumulativeCacheCreationInputTokens",
            "cache_creation_input_tokens",
        ),
        final=False,
    )


def _codex_review_capable(initialized: dict[str, Any]) -> bool:
    return _codex_version_at_least(initialized, _MIN_REVIEW_VERSION)


def _codex_dynamic_tools_capable(initialized: dict[str, Any]) -> bool:
    return _codex_version_at_least(initialized, _MIN_DYNAMIC_TOOLS_VERSION)


def _codex_version_at_least(
    initialized: dict[str, Any], minimum: tuple[int, int, int]
) -> bool:
    server = initialized.get("serverInfo") or initialized.get("server_info") or {}
    raw = server.get("version") if isinstance(server, dict) else None
    if not isinstance(raw, str) or not raw:
        return True
    numbers: list[int] = []
    for part in raw.split(".")[:3]:
        digits = "".join(character for character in part if character.isdigit())
        if not digits:
            return True
        numbers.append(int(digits))
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers) >= minimum


async def _configure_skill_roots(
    client: _CodexJsonRpcClient, context: AgentProviderContext
) -> None:
    materialization = getattr(context, "skill_materialization", None)
    extra_roots = list(getattr(materialization, "extra_roots", []) or [])
    if not extra_roots:
        return
    try:
        await client.request(
            "skills/extraRoots/set",
            {"extraRoots": extra_roots},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Codex skill roots unavailable; launching without skills: %s", exc
        )
        for audit in getattr(materialization, "audit", []):
            if audit.get("materialized_path") not in extra_roots:
                continue
            audit["failed"] = True
            audit["error"] = f"Codex skills/extraRoots/set failed: {exc}"
        _strip_failed_skill_suggestions(context)


def _strip_failed_skill_suggestions(context: AgentProviderContext) -> None:
    materialization = getattr(context, "skill_materialization", None)
    failed = set(getattr(materialization, "failed_suggestions", []) or [])
    if not failed:
        return
    for attribute in ("launch_instructions", "system_context"):
        value = getattr(context, attribute, "")
        if not value:
            continue
        setattr(
            context,
            attribute,
            "\n".join(line for line in value.splitlines() if line not in failed).strip(),
        )


def _review_report_from_codex(value: Any) -> ReviewReport:
    raw = str(value or "").strip()
    payload: dict[str, Any] | None = None
    try:
        decoded = json.loads(raw)
        if isinstance(decoded, dict):
            payload = decoded
    except json.JSONDecodeError:
        payload = None
    if payload is None:
        return ReviewReport(raw_markdown=raw or "Codex review completed.")
    findings: list[ReviewFinding] = []
    for entry in payload.get("findings") or []:
        if not isinstance(entry, dict):
            continue
        location = entry.get("code_location") or entry.get("codeLocation") or {}
        line_range = location.get("line_range") or location.get("lineRange") or {}
        findings.append(
            ReviewFinding(
                title=str(entry.get("title") or "Finding"),
                body=str(entry.get("body") or ""),
                file=(
                    str(location.get("absolute_file_path") or location.get("absoluteFilePath"))
                    if location.get("absolute_file_path") or location.get("absoluteFilePath")
                    else None
                ),
                line_start=_optional_int(line_range.get("start")),
                line_end=_optional_int(line_range.get("end")),
                priority=(str(entry["priority"]) if entry.get("priority") is not None else None),
                confidence=_optional_float(
                    entry.get("confidence_score", entry.get("confidenceScore"))
                ),
            )
        )
    verdict = payload.get("overall_correctness", payload.get("overallCorrectness"))
    explanation = payload.get(
        "overall_explanation", payload.get("overallExplanation")
    )
    markdown_parts: list[str] = []
    if verdict is not None:
        markdown_parts.append(f"# Verdict\n\n**{verdict}**")
    if explanation:
        markdown_parts.append(str(explanation))
    if findings:
        markdown_parts.append("# Findings")
        for finding in findings:
            location = finding.file or ""
            if location and finding.line_start is not None:
                location += f":{finding.line_start}"
            heading = f"## {finding.title}"
            if location:
                heading += f"\n\n`{location}`"
            markdown_parts.append(f"{heading}\n\n{finding.body}".strip())
    return ReviewReport(
        raw_markdown="\n\n".join(markdown_parts) or raw,
        findings=findings,
        verdict=str(verdict) if verdict is not None else None,
        explanation=str(explanation) if explanation is not None else None,
    )


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _usage_optional_int(sources: tuple[dict[str, Any], ...], *keys: str) -> int | None:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
    return None


def _usage_int(source: dict[str, Any], *keys: str) -> int:
    value = _usage_optional_int((source,), *keys)
    return value or 0


def _render_changes(changes: Any) -> tuple[str, str]:
    """Render Codex fileChange details without inventing a patch."""
    if not isinstance(changes, list):
        return "", "text"
    patches: list[str] = []
    for entry in changes:
        if not isinstance(entry, dict):
            continue
        patch = entry.get("patch") or entry.get("diff") or entry.get("unifiedDiff")
        if isinstance(patch, str) and patch.strip():
            patches.append(patch.strip())
    if patches:
        return "\n\n".join(patches), "diff"
    return json.dumps(changes, ensure_ascii=False, indent=2), "json"


def _truncate(value: str, limit: int = 200) -> str:
    return value if len(value) <= limit else value[:limit] + "..."
