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
from .base import AgentProviderContext, AgentProviderEvent, GateRequest, compose_turn_text

logger = logging.getLogger(__name__)

_CODEX_REQUEST_TIMEOUT_SECONDS = 60.0
_CODEX_STDERR_TAIL_LINES = 20


class CodexProvider:
    name = "codex"

    def __init__(self) -> None:
        self._client: _CodexJsonRpcClient | None = None
        self._thread_id: str | None = None
        self._turn_id: str | None = None
        self._stop = False

    async def run(self, context: AgentProviderContext) -> AsyncIterator[AgentProviderEvent]:
        self._stop = False
        async with _CodexJsonRpcClient() as client:
            self._client = client
            try:
                await client.initialize()
                thread_id = context.node.provider_session_id or context.node.sdk_session_id
                fresh_thread = not thread_id
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
            activity = _activity_from_item(params.get("item") or {}, "finish")
            if activity is not None:
                yield AgentProviderEvent(kind="event", event=activity)
            return

        if method == "item/commandExecution/outputDelta":
            item_id = str(params.get("itemId") or "command")
            yield AgentProviderEvent(
                kind="event",
                event=Activity(
                    kind="tool",
                    status="progress",
                    id=item_id,
                    name="command",
                    summary=_truncate(str(params.get("delta") or "")),
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
            return {
                "contentItems": [
                    {
                        "type": "inputText",
                        "text": "MiniClaw2 does not support dynamic client tools yet.",
                    }
                ],
                "success": False,
            }

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
    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
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
        self._proc = await asyncio.create_subprocess_exec(
            "codex",
            "app-server",
            "--listen",
            "stdio://",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
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
                            fut.set_exception(
                                RuntimeError(
                                    payload["error"].get("message", "Codex error")
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
    decision = response.get("decision")
    if isinstance(decision, dict):
        return decision
    if isinstance(decision, str):
        legacy_map = {
            "approved": "approved",
            "approved_for_session": "approved_for_session",
            "denied": "denied",
            "abort": "abort",
            "accept": "approved",
            "acceptForSession": "approved_for_session",
            "decline": "denied",
            "cancel": "abort",
        }
        mapped = legacy_map.get(decision)
        if mapped is not None:
            return mapped
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
    params = dict(base)
    _set_if_present(params, "model", settings.get("model"))
    _set_if_present(params, "modelProvider", settings.get("model_provider"))
    _set_if_present(params, "serviceTier", settings.get("service_tier"))
    if getattr(context, "minimal_mode", False):
        # Out-of-band framework agent: no UI to answer approvals on, so
        # disable interactive approvals and avoid inheriting a project
        # sandbox override that could be read-only or danger-full-access.
        params["approvalPolicy"] = "never"
        params["sandbox"] = "workspace-write"
    else:
        _set_if_present(params, "approvalPolicy", settings.get("approval_policy"))
        _set_if_present(params, "sandbox", settings.get("sandbox"))
    _set_if_present(params, "config", settings.get("codex_config"))
    if "threadId" not in params:
        params["serviceName"] = "MiniClaw2"
    return params


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
        params["sandboxPolicy"] = _minimal_context_sandbox_policy(context)
    return params


def _minimal_context_sandbox_policy(context: AgentProviderContext) -> dict[str, Any]:
    project_root = Path(context.project.root_path).resolve(strict=False)
    return {
        "type": "workspaceWrite",
        "writableRoots": [str(project_root)],
        "networkAccess": False,
        "excludeTmpdirEnvVar": True,
        "excludeSlashTmp": True,
    }


def _set_if_present(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _codex_user_input_response(response: dict[str, Any]) -> dict[str, Any]:
    raw = response.get("response")
    if isinstance(raw, dict) and "answers" in raw:
        return raw
    updated = response.get("updated_input") or {}
    answers = updated.get("answers") or response.get("answers") or {}
    normalized: dict[str, dict[str, list[str]]] = {}
    if isinstance(answers, dict):
        for key, value in answers.items():
            if isinstance(value, dict) and isinstance(value.get("answers"), list):
                normalized[str(key)] = {"answers": [str(v) for v in value["answers"]]}
            elif isinstance(value, list):
                normalized[str(key)] = {"answers": [str(v) for v in value]}
            elif value is not None:
                normalized[str(key)] = {"answers": [str(value)]}
    return {"answers": normalized}


def _codex_decision(
    response: dict[str, Any],
    *,
    command: bool = False,
) -> Any:
    decision = response.get("decision")
    if decision:
        return decision
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
            result=result_text,
            result_kind=result_kind,  # type: ignore[arg-type]
        )
    if item_type == "mcpToolCall":
        return Activity(
            kind="tool",
            status=status,  # type: ignore[arg-type]
            id=item_id,
            name=f"{item.get('server') or 'mcp'}:{item.get('tool') or ''}",
            summary=_truncate(json.dumps(item.get("arguments"), ensure_ascii=False)),
        )
    if item_type == "dynamicToolCall":
        return Activity(
            kind="tool",
            status=status,  # type: ignore[arg-type]
            id=item_id,
            name=str(item.get("tool") or "tool"),
            summary=_truncate(json.dumps(item.get("arguments"), ensure_ascii=False)),
        )
    if item_type in {"webSearch", "imageGeneration", "collabAgentToolCall"}:
        return Activity(
            kind="agent",
            status=status,  # type: ignore[arg-type]
            id=item_id,
            name=str(item_type),
            summary=_truncate(json.dumps(item, ensure_ascii=False)),
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
