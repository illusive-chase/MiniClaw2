from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from miniclaw2.domain import GateSubtype
from miniclaw2.providers.codex import CodexProvider, _CodexJsonRpcClient


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
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
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


class CodexProviderTest(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
