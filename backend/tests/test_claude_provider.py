from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from miniclaw2.domain import Node, Project
from miniclaw2.providers.base import AgentProviderContext, GateRequest
from miniclaw2.providers.claude import ClaudeProvider
from miniclaw2.providers.claude_native import ClaudeNativeSession
from miniclaw2.providers.claude_native.ask_payload import (
    format_ask_directive,
    parse_ask_payload,
)
from miniclaw2.providers.claude_native.input import SubmitResult
from miniclaw2.providers.claude_native.spawn import build_argv, DISALLOWED_TOOLS


async def _request_gate(_gate: GateRequest) -> dict[str, Any]:
    return {"allow": True}


async def _ask_dispatcher(_payload: dict[str, Any]) -> dict[str, Any]:
    return {}


async def _collect(provider_events):
    return [event async for event in provider_events]


class _FakePty:
    def __init__(
        self,
        *,
        alive: bool = True,
        exitstatus: int | None = None,
        signalstatus: int | None = None,
        exit_on_interrupt: bool = True,
    ) -> None:
        self.alive = alive
        self.exitstatus = exitstatus
        self.signalstatus = signalstatus
        self.exit_on_interrupt = exit_on_interrupt
        self.writes: list[bytes] = []

    def isalive(self) -> bool:
        return self.alive

    def write(self, data: bytes) -> None:
        self.writes.append(data)
        if data == b"\x03" and self.exit_on_interrupt:
            self.alive = False
            self.signalstatus = 2

    def terminate(self, force: bool = False) -> None:
        self.alive = False
        if force:
            self.signalstatus = 9


def _stream_session(raw: str, pty: _FakePty) -> ClaudeNativeSession:
    root = Path(raw)
    session = ClaudeNativeSession(
        cwd=raw,
        node_id="node-1",
        project_id="project-1",
        ask_dispatcher=_ask_dispatcher,
        data_dir=root / "data",
    )
    session._jsonl_path = root / "turn.jsonl"
    session._input = object()
    session._pty = pty
    return session


class ClaudeProviderModelResolutionTest(unittest.TestCase):
    def test_settings_override_takes_precedence(self) -> None:
        node = Node(project_id="p", prompt="hi")
        project = Project(
            root_path="/tmp/workspace",
            settings_override={"model": "claude-opus-4-7"},
        )
        ctx = AgentProviderContext(
            node=node,
            project=project,
            request_gate_handler=_request_gate,
        )
        self.assertEqual(ClaudeProvider()._resolve_model(ctx), "claude-opus-4-7")

    def test_missing_model_returns_none(self) -> None:
        node = Node(project_id="p", prompt="hi")
        project = Project(root_path="/tmp/workspace")
        ctx = AgentProviderContext(
            node=node,
            project=project,
            request_gate_handler=_request_gate,
        )
        clean_env = {
            k: v for k, v in os.environ.items() if k != "MINICLAW_ANTHROPIC_MODEL"
        }
        with patch.dict(os.environ, clean_env, clear=True):
            self.assertIsNone(ClaudeProvider()._resolve_model(ctx))


class BuildArgvTest(unittest.TestCase):
    def test_fresh_session_disables_plan_mode_and_forces_bypass(self) -> None:
        args = build_argv(
            binary="/usr/local/bin/claude",
            session_id="abc-123",
            resume=False,
            model="claude-sonnet-4-6",
            system_prompt_append="Hello",
        )
        self.assertEqual(args[0], "/usr/local/bin/claude")
        self.assertIn("--session-id", args)
        self.assertIn("abc-123", args)
        self.assertIn("--model", args)
        self.assertIn("--dangerously-skip-permissions", args)
        # Both belt-and-suspenders per §7: --settings JSON + the flag.
        settings_idx = args.index("--settings")
        payload = json.loads(args[settings_idx + 1])
        self.assertTrue(payload["skipDangerousModePermissionPrompt"])
        self.assertEqual(payload["permissions"]["defaultMode"], "bypassPermissions")
        # Plan-mode tools always disabled.
        disallowed_idx = args.index("--disallowed-tools")
        self.assertEqual(
            set(args[disallowed_idx + 1].split(",")),
            set(DISALLOWED_TOOLS),
        )
        # System prompt append.
        self.assertIn("--append-system-prompt", args)

    def test_resume_uses_resume_flag(self) -> None:
        args = build_argv(
            binary="claude",
            session_id="ffff",
            resume=True,
            model=None,
            system_prompt_append="",
        )
        self.assertIn("--resume", args)
        self.assertIn("ffff", args)
        self.assertNotIn("--session-id", args)
        self.assertNotIn("--model", args)
        self.assertNotIn("--append-system-prompt", args)


class AskPayloadRoundtripTest(unittest.TestCase):
    def test_parse_and_format_single_question(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {
                "questions": [
                    {
                        "question": "Which framework?",
                        "header": "framework",
                        "options": [
                            {"label": "React", "description": "UI lib"},
                            {"label": "Vue", "description": "Progressive"},
                        ],
                    }
                ]
            },
        }
        parsed = parse_ask_payload(payload)
        assert parsed is not None
        self.assertEqual(len(parsed.questions), 1)
        self.assertEqual(parsed.questions[0].options[0].label, "React")

        directive = format_ask_directive(
            {"updated_input": {"answers": {"Which framework?": "React"}}},
            parsed,
        )
        answers = directive["hookSpecificOutput"]["updatedInput"]["answers"]
        self.assertEqual(answers["Which framework?"], "React")
        # Raw questions written back verbatim.
        raw = directive["hookSpecificOutput"]["updatedInput"]["questions"]
        self.assertEqual(raw[0]["header"], "framework")

    def test_parse_rejects_non_ask_payload(self) -> None:
        self.assertIsNone(parse_ask_payload({"tool_input": {}}))
        self.assertIsNone(parse_ask_payload({"tool_input": {"questions": []}}))

    def test_free_text_fallback_from_decision(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {
                "questions": [
                    {
                        "question": "Any thoughts?",
                        "options": [{"label": "Yes"}, {"label": "No"}],
                    }
                ]
            },
        }
        parsed = parse_ask_payload(payload)
        assert parsed is not None
        directive = format_ask_directive({"decision": "sure, sounds fine"}, parsed)
        answers = directive["hookSpecificOutput"]["updatedInput"]["answers"]
        self.assertEqual(answers["Any thoughts?"], "sure, sounds fine")


class JsonlDrainTest(unittest.TestCase):
    def test_partial_line_is_preserved(self) -> None:
        from miniclaw2.providers.claude_native.transcript import drain

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "session.jsonl"
            path.write_text('{"type":"user"}\n{"type":"assist', encoding="utf-8")
            result = drain(path, 0)
            self.assertEqual(len(result.events), 1)
            self.assertEqual(result.pending_tail, '{"type":"assist')

    def test_truncation_resets_offset(self) -> None:
        from miniclaw2.providers.claude_native.transcript import drain

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "session.jsonl"
            path.write_text(
                '{"type":"user"}\n{"type":"summary"}\n',
                encoding="utf-8",
            )
            first = drain(path, 0)
            self.assertEqual(len(first.events), 2)
            # Simulate truncation.
            path.write_text('{"type":"user"}\n', encoding="utf-8")
            second = drain(path, first.new_offset)
            self.assertEqual(len(second.events), 1)


class ProjectHashTest(unittest.TestCase):
    def test_dashes_and_dots_normalized(self) -> None:
        from miniclaw2.providers.claude_native.paths import project_hash

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "some.thing" / "sub_dir"
            root.mkdir(parents=True)
            hashed = project_hash(str(root))
            self.assertNotIn("/", hashed)
            self.assertNotIn(".", hashed)
            self.assertNotIn("_", hashed)


class ClaudeNativeStreamTerminalTest(unittest.IsolatedAsyncioTestCase):
    async def test_result_record_emits_explicit_done(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            session = _stream_session(raw, _FakePty(alive=True))
            session._jsonl_path.write_text(
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "usage": {"input_tokens": 1, "output_tokens": 2},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            events = await _collect(session.stream_events())

        self.assertEqual(events[-1].kind, "done")
        self.assertEqual(events[-1].final_state, "done")
        self.assertTrue(
            any(ev.kind == "event" and ev.event.type == "usage" for ev in events)
        )

    async def test_child_death_before_terminal_record_emits_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            session = _stream_session(
                raw,
                _FakePty(alive=False, exitstatus=7),
            )

            events = await _collect(session.stream_events())

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "error")
        self.assertIn("exited before an end-of-turn marker", events[0].error or "")
        self.assertIn("exit status 7", events[0].error or "")

    async def test_interrupt_child_exit_emits_cancelled_done(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pty = _FakePty(alive=True)
            session = _stream_session(raw, pty)

            await session.interrupt()
            events = await _collect(session.stream_events())

        self.assertEqual(pty.writes, [b"\x03"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "done")
        self.assertEqual(events[0].final_state, "cancelled")

    async def test_interrupt_idle_alive_child_emits_cancelled_done(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pty = _FakePty(alive=True, exit_on_interrupt=False)
            session = _stream_session(raw, pty)

            with (
                patch("miniclaw2.providers.claude_native._STREAM_IDLE_TICK_LIMIT", 0),
                patch("miniclaw2.providers.claude_native._STREAM_POLL_INTERVAL", 0),
            ):
                await session.interrupt()
                events = await _collect(session.stream_events())

        self.assertEqual(pty.writes, [b"\x03"])
        self.assertTrue(pty.alive)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "done")
        self.assertEqual(events[0].final_state, "cancelled")

    async def test_claude_provider_turns_bare_native_exhaustion_into_error(self) -> None:
        class BareSession:
            cli_session_id = "claude-session"

            def __init__(self, **_: Any) -> None:
                self.closed = False

            async def start(self) -> None:
                return None

            async def send(self, _prompt: str) -> SubmitResult:
                return SubmitResult(submitted=True)

            async def stream_events(self):
                if False:
                    yield None

            async def close(self) -> None:
                self.closed = True

        node = Node(project_id="p", prompt="hi")
        project = Project(root_path="/tmp/workspace")
        ctx = AgentProviderContext(
            node=node,
            project=project,
            request_gate_handler=_request_gate,
        )
        with patch(
            "miniclaw2.providers.claude.ClaudeNativeSession",
            BareSession,
        ):
            events = await _collect(ClaudeProvider().run(ctx))

        self.assertEqual(events[-1].kind, "error")
        self.assertIn("without a terminal event", events[-1].error or "")


if __name__ == "__main__":
    unittest.main()
