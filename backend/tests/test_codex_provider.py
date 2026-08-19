from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from miniclaw2.domain import GateSubtype, ReviewTarget
from miniclaw2.providers.base import ReviewSpec
from miniclaw2.providers.codex import (
    CodexRpcError,
    CodexProvider,
    _CodexJsonRpcClient,
    _CODEX_STDIO_BUFFER_LIMIT_BYTES,
    _configure_skill_roots,
    _codex_dynamic_tools_capable,
    _codex_user_input_response,
    _activity_from_item,
    _thread_params,
    _turn_params,
    _review_report_from_codex,
)


class CodexUserInputResponseTest(unittest.TestCase):
    def test_native_review_json_maps_to_normalized_report(self) -> None:
        report = _review_report_from_codex(json.dumps({
            "findings": [{
                "title": "Bug",
                "body": "Details",
                "priority": 1,
                "confidence_score": 0.9,
                "code_location": {
                    "absolute_file_path": "/repo/a.py",
                    "line_range": {"start": 4, "end": 5},
                },
            }],
            "overall_correctness": "patch is incorrect",
            "overall_explanation": "A bug remains.",
        }))

        self.assertEqual(report.verdict, "patch is incorrect")
        self.assertEqual(report.findings[0].line_start, 4)  # type: ignore[index]
        self.assertIn("# Findings", report.raw_markdown)

    def test_accepts_only_canonical_nested_answers(self) -> None:
        self.assertEqual(
            _codex_user_input_response(
                {
                    "response": {
                        "answers": {
                            "framework": {"answers": ["React"]},
                            "checks": {"answers": ["types", "tests"]},
                        }
                    }
                }
            ),
            {
                "answers": {
                    "framework": {"answers": ["React"]},
                    "checks": {"answers": ["types", "tests"]},
                }
            },
        )

    def test_does_not_accept_legacy_top_level_or_scalar_answers(self) -> None:
        self.assertEqual(
            _codex_user_input_response(
                {
                    "answers": {"framework": "React"},
                    "updated_input": {"answers": {"checks": ["types"]}},
                }
            ),
            {"answers": {}},
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
    def __init__(
        self,
        *,
        settings_override: dict[str, Any] | None = None,
        model_preset_id: str = "gpt-5.6",
    ) -> None:
        from miniclaw2.domain import Node, Project

        self.node = Node(
            project_id="project-1",
            prompt="Create README.md",
            model_preset_id=model_preset_id,
        )
        self.project = Project(
            root_path="/tmp/workspace",
            model_preset_id=model_preset_id,
            settings_override=settings_override or {},
        )
        self.system_context = ""
        self.gates: list[Any] = []

    async def request_gate(self, gate: Any) -> dict[str, Any]:
        self.gates.append(gate)
        return {"allow": False, "interrupt": False}


class CodexProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_skill_root_failure_marks_audit_without_raising(self) -> None:
        suggestion = (
            'The skill "Alpha" is available and likely relevant to this task.'
        )
        audit = {
            "materialized_path": "/tmp/alpha",
            "failed": False,
        }
        context = _FakeProviderContext()
        context.launch_instructions = f"Keep this instruction.\n{suggestion}"
        context.system_context = suggestion
        context.skill_materialization = SimpleNamespace(
            extra_roots=["/tmp/alpha"],
            audit=[audit],
            failed_suggestions=[suggestion],
        )

        class _ClientStub:
            async def request(
                self, method: str, params: dict[str, Any]
            ) -> dict[str, Any]:
                self.method = method
                self.params = params
                raise CodexRpcError(-32601, "method not found")

        await _configure_skill_roots(
            _ClientStub(),  # type: ignore[arg-type]
            context,  # type: ignore[arg-type]
        )

        self.assertTrue(audit["failed"])
        self.assertIn("skills/extraRoots/set failed", audit["error"])
        self.assertEqual(context.launch_instructions, "Keep this instruction.")
        self.assertEqual(context.system_context, "")

    async def test_review_thread_receives_system_context_as_instructions(self) -> None:
        captured: list[tuple[str, dict[str, Any]]] = []

        class _ClientStub:
            async def initialize(self) -> dict[str, Any]:
                return {"serverInfo": {"version": "0.200.0"}}

            async def request(
                self, method: str, params: dict[str, Any], **_kwargs: Any
            ) -> dict[str, Any]:
                captured.append((method, params))
                if method == "thread/start":
                    return {"thread": {"id": "thread-1"}}
                if method == "review/start":
                    return {"turn": {"id": "turn-1"}}
                raise AssertionError(method)

            async def receive(self) -> dict[str, Any]:
                return {
                    "method": "turn/completed",
                    "params": {"turn": {"status": "completed"}},
                }

            async def respond(self, *_args: Any, **_kwargs: Any) -> None:
                return None

        class _ClientCtx:
            async def __aenter__(self) -> Any:
                return _ClientStub()

            async def __aexit__(self, *_exc: object) -> None:
                return None

        context = _FakeProviderContext()
        context.system_context = "Suggested skill context"
        with patch(
            "miniclaw2.providers.codex._CodexJsonRpcClient",
            return_value=_ClientCtx(),
        ):
            events = [
                event
                async for event in CodexProvider().run_review(
                    context,  # type: ignore[arg-type]
                    ReviewSpec(target=ReviewTarget()),
                )
            ]

        thread_params = next(params for method, params in captured if method == "thread/start")
        self.assertEqual(
            thread_params["developerInstructions"], "Suggested skill context"
        )
        self.assertEqual(events[-1].kind, "done")

    async def test_review_error_classification_uses_json_rpc_code(self) -> None:
        async def collect_for(code: int) -> list[Any]:
            class _ClientStub:
                async def initialize(self) -> dict[str, Any]:
                    return {"serverInfo": {"version": "0.200.0"}}

                async def request(
                    self, method: str, _params: dict[str, Any], **_kwargs: Any
                ) -> dict[str, Any]:
                    if method == "thread/start":
                        return {"thread": {"id": "thread-1"}}
                    if method == "review/start":
                        raise CodexRpcError(
                            code, "invalid params for method review/start"
                        )
                    raise AssertionError(method)

            class _ClientCtx:
                async def __aenter__(self) -> Any:
                    return _ClientStub()

                async def __aexit__(self, *_exc: object) -> None:
                    return None

            provider = CodexProvider()
            context = _FakeProviderContext()
            with patch(
                "miniclaw2.providers.codex._CodexJsonRpcClient",
                return_value=_ClientCtx(),
            ):
                return [
                    event async for event in provider.run_review(
                        context,  # type: ignore[arg-type]
                        ReviewSpec(target=ReviewTarget()),
                    )
                ]

        missing_method = await collect_for(-32601)
        invalid_params = await collect_for(-32602)

        self.assertIn("0.144.1 or newer", missing_method[-1].error or "")
        self.assertIn("invalid params", invalid_params[-1].error or "")
        self.assertNotIn("0.144.1 or newer", invalid_params[-1].error or "")

    def test_command_activity_keeps_full_command(self) -> None:
        command = "printf '%s' " + "x" * 300

        activity = _activity_from_item(
            {"type": "commandExecution", "id": "cmd-1", "command": command},
            "start",
        )

        self.assertIsNotNone(activity)
        assert activity is not None
        self.assertEqual(activity.summary, command[:200] + "...")
        self.assertEqual(activity.parameters, command)
        self.assertEqual(activity.command, command)

    def test_dynamic_tool_activity_keeps_full_parameters(self) -> None:
        arguments = {"file_path": "/tmp/" + "nested/" * 40 + "README.md"}

        activity = _activity_from_item(
            {
                "type": "dynamicToolCall",
                "id": "tool-1",
                "tool": "read",
                "arguments": arguments,
            },
            "start",
        )

        self.assertIsNotNone(activity)
        assert activity is not None
        self.assertEqual(activity.parameters, json.dumps(arguments, ensure_ascii=False))
        self.assertTrue(activity.summary.endswith("..."))

    async def test_command_output_delta_preserves_untruncated_result(self) -> None:
        provider = CodexProvider()
        delta = "0123456789" * 50

        events = [
            event
            async for event in provider._handle_message(
                {
                    "method": "item/commandExecution/outputDelta",
                    "params": {"itemId": "cmd-1", "delta": delta},
                },
                _FakeProviderContext(),  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
            )
        ]

        self.assertEqual(len(events), 1)
        activity = events[0].event
        self.assertIsNotNone(activity)
        assert activity is not None
        self.assertEqual(activity.summary, delta[:200] + "...")
        self.assertEqual(activity.result, delta)
        self.assertEqual(activity.result_kind, "stdout")

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
        self.assertEqual(thread_params["model"], "gpt-5.6-sol")
        self.assertNotIn("modelProvider", thread_params)
        self.assertEqual(thread_params["reasoningEffort"], "high")
        self.assertNotIn("dynamicTools", thread_params)
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
        self.assertEqual(thread_params["model"], "gpt-5.6-sol")
        self.assertNotIn("modelProvider", thread_params)
        self.assertEqual(thread_params["reasoningEffort"], "high")
        dynamic_tools = thread_params["dynamicTools"]
        self.assertEqual(len(dynamic_tools), 1)
        self.assertEqual(dynamic_tools[0]["type"], "function")
        self.assertEqual(dynamic_tools[0]["name"], "ask_user")
        question_schema = dynamic_tools[0]["inputSchema"]["properties"]["questions"]
        self.assertEqual(question_schema["minItems"], 1)
        self.assertEqual(question_schema["maxItems"], 3)
        self.assertEqual(
            question_schema["items"]["required"],
            ["id", "header", "question", "options"],
        )
        self.assertEqual(
            turn_params["sandboxPolicy"],
            {
                "type": "workspaceWrite",
                "writableRoots": [expected_project_root],
                "networkAccess": False,
            },
        )

    def test_resumed_thread_does_not_repeat_dynamic_tools(self) -> None:
        ctx = _FakeProviderContext()

        params = _thread_params(  # type: ignore[arg-type]
            ctx,
            {"threadId": "thread-1", "cwd": "/tmp/workspace"},
        )

        self.assertNotIn("dynamicTools", params)
        self.assertNotIn("serviceName", params)

    def test_dynamic_tools_require_codex_0146(self) -> None:
        self.assertFalse(
            _codex_dynamic_tools_capable(
                {"serverInfo": {"version": "0.145.0"}}
            )
        )
        self.assertTrue(
            _codex_dynamic_tools_capable(
                {"serverInfo": {"version": "0.146.0"}}
            )
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

    async def test_receive_waits_for_late_turn_completed_event(self) -> None:
        fake_proc = _FakeProcess()
        with patch(
            "miniclaw2.providers.codex.asyncio.create_subprocess_exec",
            return_value=fake_proc,
        ):
            async with _CodexJsonRpcClient() as client:
                receive_task = asyncio.create_task(client.receive())
                await asyncio.sleep(0.01)
                self.assertFalse(receive_task.done())
                fake_proc.feed_stdout(
                    {
                        "method": "turn/completed",
                        "params": {"turn": {"status": "completed"}},
                    }
                )
                message = await asyncio.wait_for(receive_task, timeout=1)

        self.assertEqual(message["method"], "turn/completed")

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

    async def test_stdout_reader_preserves_json_rpc_error_code(self) -> None:
        def on_request(payload: dict[str, Any]) -> None:
            fake_proc.feed_stdout({
                "id": payload["id"],
                "error": {
                    "code": -32602,
                    "message": "invalid params for method review/start",
                },
            })

        fake_proc = _FakeProcess(on_request=on_request)
        with patch(
            "miniclaw2.providers.codex.asyncio.create_subprocess_exec",
            return_value=fake_proc,
        ):
            async with _CodexJsonRpcClient() as client:
                with self.assertRaises(CodexRpcError) as raised:
                    await client.request("review/start", {})

        self.assertEqual(raised.exception.code, -32602)
        self.assertIn("invalid params", str(raised.exception))

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

    async def test_dynamic_ask_user_maps_gate_and_returns_answers(self) -> None:
        provider = CodexProvider()
        ctx = _FakeGateContext(
            {
                "response": {
                    "answers": {
                        "framework": {"answers": [" React "]},
                        "checks": {"answers": ["types", "tests"]},
                    }
                }
            }
        )
        result = await provider._handle_server_request(
            {
                "id": 41,
                "method": "item/tool/call",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "callId": "call-1",
                    "namespace": None,
                    "tool": "ask_user",
                    "arguments": {
                        "questions": [
                            {
                                "id": "framework",
                                "header": "Framework",
                                "question": "Which framework?",
                                "options": [
                                    {
                                        "label": "React",
                                        "description": "Use React.",
                                    }
                                ],
                            },
                            {
                                "id": "checks",
                                "header": "Checks",
                                "question": "Which checks?",
                                "multiSelect": True,
                                "options": [],
                            },
                        ]
                    },
                },
            },
            ctx,  # type: ignore[arg-type]
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["contentItems"][0]["type"], "inputText")
        self.assertEqual(
            json.loads(result["contentItems"][0]["text"]),
            {
                "answers": {
                    "framework": {"answers": ["React"]},
                    "checks": {"answers": ["types", "tests"]},
                }
            },
        )
        self.assertEqual(len(ctx.requests), 1)
        gate = ctx.requests[0]
        self.assertEqual(gate.subtype, GateSubtype.ASK_USER)
        self.assertEqual(gate.tool_name, "ask_user")
        self.assertEqual(gate.provider_request_id, "41")
        self.assertEqual(gate.tool_input["questions"][1]["multiSelect"], True)
        self.assertEqual(
            gate.response_hint,
            {
                "codex_method": "item/tool/call",
                "codex_call_id": "call-1",
                "dynamic_tool": "ask_user",
            },
        )

    async def test_dynamic_ask_user_rejects_invalid_arguments_without_gate(self) -> None:
        provider = CodexProvider()
        ctx = _FakeGateContext({"response": {"answers": {}}})

        result = await provider._handle_server_request(
            {
                "id": 42,
                "method": "item/tool/call",
                "params": {
                    "namespace": None,
                    "tool": "ask_user",
                    "arguments": {"questions": []},
                },
            },
            ctx,  # type: ignore[arg-type]
        )

        self.assertFalse(result["success"])
        self.assertIn("1 到 3 个问题", result["contentItems"][0]["text"])
        self.assertEqual(ctx.requests, [])

    async def test_unknown_dynamic_tool_fails_without_permission_gate(self) -> None:
        provider = CodexProvider()
        ctx = _FakeGateContext({"allow": True})

        result = await provider._handle_server_request(
            {
                "id": 43,
                "method": "item/tool/call",
                "params": {
                    "namespace": None,
                    "tool": "other_tool",
                    "arguments": {},
                },
            },
            ctx,  # type: ignore[arg-type]
        )

        self.assertFalse(result["success"])
        self.assertIn("other_tool", result["contentItems"][0]["text"])
        self.assertEqual(ctx.requests, [])

    async def test_dynamic_ask_user_fails_when_gate_has_no_answers(self) -> None:
        provider = CodexProvider()
        ctx = _FakeGateContext({"allow": False, "message": "node ended"})

        result = await provider._handle_server_request(
            {
                "id": 44,
                "method": "item/tool/call",
                "params": {
                    "namespace": None,
                    "tool": "ask_user",
                    "arguments": {
                        "questions": [
                            {
                                "id": "confirm",
                                "header": "Confirm",
                                "question": "Continue?",
                                "options": [],
                            }
                        ]
                    },
                },
            },
            ctx,  # type: ignore[arg-type]
        )

        self.assertFalse(result["success"])
        self.assertIn("node ended", result["contentItems"][0]["text"])
        self.assertEqual(len(ctx.requests), 1)

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

    async def test_retryable_error_notification_does_not_end_turn(self) -> None:
        provider = CodexProvider()
        events = [
            event
            async for event in provider._handle_message(
                {
                    "method": "error",
                    "params": {
                        "error": {"message": "Reconnecting... 1/5"},
                        "willRetry": True,
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                    },
                },
                _FakeProviderContext(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
            )
        ]

        self.assertEqual(events, [])

    async def test_non_retryable_error_notification_ends_turn(self) -> None:
        provider = CodexProvider()
        events = [
            event
            async for event in provider._handle_message(
                {
                    "method": "error",
                    "params": {
                        "error": {"message": "connection failed"},
                        "willRetry": False,
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                    },
                },
                _FakeProviderContext(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
            )
        ]

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "error")
        self.assertEqual(events[0].error, "connection failed")

    async def test_thread_start_uses_project_sandbox_override(self) -> None:
        provider = CodexProvider()
        ctx = _FakeProviderContext(
            settings_override={
                "approval_policy": "never",
                "sandbox": "workspace-write",
            }
        )
        ctx.skill_materialization = SimpleNamespace(
            extra_roots=["/tmp/alpha"],
            audit=[],
            env_overrides={},
        )

        captured_requests: list[dict[str, Any]] = []

        class _ClientStub:
            async def initialize(self) -> dict[str, Any]:
                return {}

            async def request(self, method: str, params: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
                captured_requests.append({"method": method, "params": params})
                if method == "skills/extraRoots/set":
                    return {}
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
        self.assertGreaterEqual(len(requests), 3)
        self.assertEqual(requests[0]["method"], "skills/extraRoots/set")
        self.assertEqual(requests[0]["params"], {"extraRoots": ["/tmp/alpha"]})
        self.assertEqual(requests[1]["method"], "thread/start")
        self.assertEqual(requests[1]["params"]["sandbox"], "workspace-write")
        self.assertEqual(requests[1]["params"]["approvalPolicy"], "never")
        self.assertEqual(requests[1]["params"]["cwd"], "/tmp/workspace")


if __name__ == "__main__":
    unittest.main()
