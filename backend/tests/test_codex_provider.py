from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

from miniclaw2.domain import GateSubtype
from miniclaw2.providers.codex import (
    CodexProvider,
    _CodexJsonRpcClient,
    _CODEX_STDIO_BUFFER_LIMIT_BYTES,
    _thread_params,
    _turn_params,
)


class _FakeStdin:
    def __init__(self, process: "_FakeProcess") -> None:
        self._process = process
        self.writes: list[dict[str, Any]] = []

    def write(self, data: bytes) -> None:
        payload = json.loads(data.decode("utf-8"))
        self.writes.append(payload)
        if self._process.on_request is not None:
            self._process.on_request(payload)

    async def drain(self) -> None:
        await asyncio.sleep(0)


class _FakeProcess:
    def __init__(
        self,
        *,
        on_request: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.on_request = on_request
        self.stdin = _FakeStdin(self)
        self.stdout = asyncio.StreamReader(limit=_CODEX_STDIO_BUFFER_LIMIT_BYTES)
        self.stderr = asyncio.StreamReader(limit=_CODEX_STDIO_BUFFER_LIMIT_BYTES)
        self.returncode: int | None = None
        self._wait_event = asyncio.Event()

    async def wait(self) -> int:
        await self._wait_event.wait()
        return self.returncode or 0

    def terminate(self) -> None:
        self._finish(-15)

    def kill(self) -> None:
        self._finish(-9)

    def feed_stdout(self, payload: dict[str, Any]) -> None:
        self.stdout.feed_data((json.dumps(payload) + "\n").encode("utf-8"))

    def feed_stdout_eof(self, returncode: int = 0) -> None:
        self._finish(returncode)
        self.stdout.feed_eof()

    def feed_stderr(self, text: str) -> None:
        self.stderr.feed_data((text + "\n").encode("utf-8"))

    def feed_stderr_eof(self) -> None:
        self.stderr.feed_eof()

    def _finish(self, returncode: int) -> None:
        if self.returncode is None:
            self.returncode = returncode
        self._wait_event.set()


class _FakeGateContext:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.requests: list[Any] = []

    async def request_gate(self, gate: Any) -> dict[str, Any]:
        self.requests.append(gate)
        return self.response


class _FakeProviderContext:
    def __init__(self, *, settings_override: dict[str, Any] | None = None) -> None:
        from miniclaw2.domain import Node, Project

        self.node = Node(project_id="project-1", prompt="Create README.md")
        self.project = Project(
            root_path="/tmp/workspace",
            provider="codex",
            settings_override=settings_override or {},
        )
        self.system_context = ""
        self.gates: list[Any] = []

    async def request_gate(self, gate: Any) -> dict[str, Any]:
        self.gates.append(gate)
        return {"allow": False, "interrupt": False}


class CodexProviderTest(unittest.IsolatedAsyncioTestCase):
    def test_minimal_thread_and_turn_params_pin_context_sandbox(self) -> None:
        ctx = _FakeProviderContext(
            settings_override={
                "approval_policy": "untrusted",
                "sandbox": "danger-full-access",
            }
        )
        ctx.minimal_mode = True

        thread_params = _thread_params(ctx, {"cwd": "/tmp/workspace"})  # type: ignore[arg-type]
        turn_params = _turn_params(ctx, "thread-1", "refresh context")  # type: ignore[arg-type]

        self.assertEqual(thread_params["approvalPolicy"], "never")
        self.assertEqual(thread_params["sandbox"], "workspace-write")
        self.assertEqual(turn_params["approvalPolicy"], "never")
        expected_project_root = str(Path("/tmp/workspace").resolve(strict=False))
        self.assertEqual(
            turn_params["sandboxPolicy"],
            {
                "type": "workspaceWrite",
                "writableRoots": [expected_project_root],
                "networkAccess": False,
                "excludeTmpdirEnvVar": True,
                "excludeSlashTmp": True,
            },
        )

    def test_regular_thread_and_turn_params_pin_project_workspace_write(self) -> None:
        ctx = _FakeProviderContext()

        thread_params = _thread_params(ctx, {"cwd": "/tmp/workspace"})  # type: ignore[arg-type]
        turn_params = _turn_params(ctx, "thread-1", "build")  # type: ignore[arg-type]

        expected_project_root = str(Path("/tmp/workspace").resolve(strict=False))
        self.assertEqual(thread_params["sandbox"], "workspace-write")
        self.assertEqual(
            turn_params["sandboxPolicy"],
            {
                "type": "workspaceWrite",
                "writableRoots": [expected_project_root],
                "networkAccess": False,
            },
        )

    def test_explicit_read_only_sandbox_is_preserved(self) -> None:
        ctx = _FakeProviderContext(settings_override={"sandbox": "read-only"})

        thread_params = _thread_params(ctx, {"cwd": "/tmp/workspace"})  # type: ignore[arg-type]
        turn_params = _turn_params(ctx, "thread-1", "inspect")  # type: ignore[arg-type]

        self.assertEqual(thread_params["sandbox"], "read-only")
        self.assertNotIn("sandboxPolicy", turn_params)

    async def test_app_server_starts_in_project_cwd(self) -> None:
        fake_proc = _FakeProcess()
        with patch(
            "miniclaw2.providers.codex.asyncio.create_subprocess_exec",
            return_value=fake_proc,
        ) as create_subprocess_exec:
            async with _CodexJsonRpcClient(cwd="/tmp/workspace"):
                pass

        self.assertEqual(
            create_subprocess_exec.call_args.kwargs["cwd"],
            str(Path("/tmp/workspace").resolve(strict=False)),
        )

    async def test_initialize_fails_when_app_server_exits_before_reply(self) -> None:
        def on_request(payload: dict[str, Any]) -> None:
            fake_proc.feed_stderr("codex app-server stderr: unable to start")
            fake_proc.feed_stdout_eof(returncode=1)

        fake_proc = _FakeProcess(on_request=on_request)
        with patch(
            "miniclaw2.providers.codex.asyncio.create_subprocess_exec",
            return_value=fake_proc,
        ):
            async with _CodexJsonRpcClient() as client:
                with self.assertRaisesRegex(
                    RuntimeError,
                    r"Codex app-server closed stdout before completing the Codex request[\s\S]*stderr tail:",
                ):
                    await client.initialize()

    async def test_receive_raises_when_app_server_exits_after_turn_start(self) -> None:
        def on_request(payload: dict[str, Any]) -> None:
            fake_proc.feed_stdout(
                {
                    "id": payload["id"],
                    "result": {
                        "turn": {
                            "id": "turn-1",
                        }
                    },
                }
            )

        fake_proc = _FakeProcess(on_request=on_request)
        with patch(
            "miniclaw2.providers.codex.asyncio.create_subprocess_exec",
            return_value=fake_proc,
        ):
            async with _CodexJsonRpcClient() as client:
                result = await client.request("turn/start", {"threadId": "thread-1"})
                self.assertEqual(result["turn"]["id"], "turn-1")
                receive_task = asyncio.create_task(client.receive())
                await asyncio.sleep(0)
                fake_proc.feed_stderr("codex app-server stderr: turn completed")
                fake_proc.feed_stdout_eof(returncode=1)
                with self.assertRaisesRegex(
                    RuntimeError,
                    r"Codex app-server closed stdout before completing the Codex request[\s\S]*stderr tail:",
                ):
                    await receive_task

    async def test_request_timeout_is_bounded(self) -> None:
        fake_proc = _FakeProcess()
        with patch(
            "miniclaw2.providers.codex.asyncio.create_subprocess_exec",
            return_value=fake_proc,
        ):
            async with _CodexJsonRpcClient() as client:
                with self.assertRaisesRegex(
                    TimeoutError,
                    r"Codex request turn/start timed out after 0\.0",
                ):
                    await client.request(
                        "turn/start",
                        {"threadId": "thread-1"},
                        timeout=0.01,
                    )

    async def test_stdout_reader_accepts_large_json_rpc_lines(self) -> None:
        fake_proc = _FakeProcess()
        with patch(
            "miniclaw2.providers.codex.asyncio.create_subprocess_exec",
            return_value=fake_proc,
        ) as create_subprocess_exec:
            async with _CodexJsonRpcClient() as client:
                fake_proc.feed_stdout(
                    {
                        "method": "item/commandExecution/outputDelta",
                        "params": {
                            "itemId": "cmd-1",
                            "delta": "x" * (96 * 1024),
                        },
                    }
                )
                message = await client.receive()

        self.assertEqual(message["method"], "item/commandExecution/outputDelta")
        self.assertEqual(len(message["params"]["delta"]), 96 * 1024)
        self.assertEqual(
            create_subprocess_exec.call_args.kwargs["limit"],
            _CODEX_STDIO_BUFFER_LIMIT_BYTES,
        )

    async def test_modern_and_legacy_approval_decisions_map_differently(self) -> None:
        provider = CodexProvider()

        modern_ctx = _FakeGateContext({"allow": True, "scope": "session"})
        modern_result = await provider._handle_server_request(
            {
                "id": 1,
                "method": "item/commandExecution/requestApproval",
                "params": {"command": "echo hi"},
            },
            modern_ctx,  # type: ignore[arg-type]
        )
        self.assertEqual(modern_result, {"decision": "acceptForSession"})
        self.assertEqual(modern_ctx.requests[0].subtype, GateSubtype.PERMISSION)
        self.assertEqual(modern_ctx.requests[0].tool_name, "commandExecution")

        legacy_ctx = _FakeGateContext({"allow": True, "scope": "session"})
        legacy_result = await provider._handle_server_request(
            {
                "id": 2,
                "method": "execCommandApproval",
                "params": {"command": "echo hi"},
            },
            legacy_ctx,  # type: ignore[arg-type]
        )
        self.assertEqual(legacy_result, {"decision": "approved_for_session"})
        self.assertEqual(legacy_ctx.requests[0].subtype, GateSubtype.PERMISSION)
        self.assertEqual(legacy_ctx.requests[0].tool_name, "commandExecution")

        deny_ctx = _FakeGateContext({"allow": False, "interrupt": True})
        deny_result = await provider._handle_server_request(
            {
                "id": 3,
                "method": "applyPatchApproval",
                "params": {"patch": "diff"},
            },
            deny_ctx,  # type: ignore[arg-type]
        )
        self.assertEqual(deny_result, {"decision": "abort"})

    async def test_mcp_elicitation_is_explicitly_declined(self) -> None:
        provider = CodexProvider()
        ctx = _FakeGateContext({"allow": True})
        result = await provider._handle_server_request(
            {
                "id": 4,
                "method": "mcpServer/elicitation/request",
                "params": {
                    "serverName": "example",
                    "threadId": "thread-1",
                    "message": "Choose one",
                    "mode": "form",
                    "requestedSchema": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            ctx,  # type: ignore[arg-type]
        )
        self.assertEqual(
            result,
            {
                "action": "decline",
                "content": None,
                "_meta": {"reason": "MiniClaw2 does not support MCP elicitation yet."},
            },
        )
        self.assertEqual(ctx.requests, [])

    async def test_token_usage_keeps_last_context_and_cumulative_output(self) -> None:
        provider = CodexProvider()
        events = [
            event
            async for event in provider._handle_message(
                {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "tokenUsage": {
                            "last": {
                                "inputTokens": 100,
                                "outputTokens": 30,
                                "cachedInputTokens": 20,
                                "cacheCreationInputTokens": 5,
                            },
                            "total": {
                                "outputTokens": 300,
                                "cacheCreationInputTokens": 40,
                            },
                        }
                    },
                },
                _FakeProviderContext(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
            )
        ]

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "event")
        usage = events[0].event
        assert usage is not None
        self.assertEqual(usage.input_tokens, 100)
        self.assertEqual(usage.output_tokens, 30)
        self.assertEqual(usage.cache_read_tokens, 20)
        self.assertEqual(usage.cache_creation_tokens, 5)
        self.assertEqual(usage.cumulative_output_tokens, 300)
        self.assertEqual(usage.cumulative_cache_creation_tokens, 40)

    async def test_thread_start_uses_project_sandbox_override(self) -> None:
        provider = CodexProvider()
        ctx = _FakeProviderContext(
            settings_override={
                "approval_policy": "never",
                "sandbox": "workspace-write",
            }
        )

        captured_requests: list[dict[str, Any]] = []

        class _ClientStub:
            async def initialize(self) -> dict[str, Any]:
                return {}

            async def request(self, method: str, params: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
                captured_requests.append({"method": method, "params": params})
                if method == "thread/start":
                    return {"thread": {"id": "thread-1"}}
                if method == "turn/start":
                    return {"turn": {"id": "turn-1"}}
                raise AssertionError(method)

            async def receive(self) -> dict[str, Any]:
                return {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}

            async def respond(self, *_args: Any, **_kwargs: Any) -> None:
                return None

        class _ClientCtx:
            async def __aenter__(self_nonlocal) -> Any:
                return _ClientStub()

            async def __aexit__(self_nonlocal, *_exc: object) -> None:
                return None

        async def _run_once() -> list[dict[str, Any]]:
            original_client = provider._client
            try:
                provider._client = None
                with patch("miniclaw2.providers.codex._CodexJsonRpcClient", return_value=_ClientCtx()):
                    events = []
                    async for ev in provider.run(ctx):  # type: ignore[arg-type]
                        events.append(ev)
                        if ev.kind == "done":
                            break
                    return captured_requests
            finally:
                provider._client = original_client

        requests = await _run_once()
        self.assertGreaterEqual(len(requests), 2)
        self.assertEqual(requests[0]["method"], "thread/start")
        self.assertEqual(requests[0]["params"]["sandbox"], "workspace-write")
        self.assertEqual(requests[0]["params"]["approvalPolicy"], "never")
        self.assertEqual(requests[0]["params"]["cwd"], "/tmp/workspace")


if __name__ == "__main__":
    unittest.main()
