from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from miniclaw2.domain import Node, Project
from miniclaw2.providers.base import (
    AgentProviderContext,
    AgentProviderEvent,
    GateRequest,
)
from miniclaw2.providers.claude import ClaudeProvider
from miniclaw2.providers.claude_native import ClaudeNativeSession
from miniclaw2.providers.claude_native.ask_payload import (
    format_ask_directive,
    parse_ask_payload,
)
from miniclaw2.providers.claude_native.input import InputWriter, SubmitResult
from miniclaw2.providers.claude_native.keybindings import SubmitKey
from miniclaw2.providers.claude_native.paths import jsonl_path, project_dir
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


class _FakeInput:
    def __init__(self) -> None:
        self.jsonl_path: Path | None = None

    def update_jsonl_path(self, path: Path) -> None:
        self.jsonl_path = path


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
    session._input = _FakeInput()
    session._pty = pty
    return session


def _write_jsonl(path: Path, *records: dict[str, Any]) -> list[int]:
    offsets: list[int] = []
    offset = 0
    chunks: list[str] = []
    for record in records:
        offsets.append(offset)
        line = json.dumps(record) + "\n"
        chunks.append(line)
        offset += len(line.encode("utf-8"))
    path.write_text("".join(chunks), encoding="utf-8")
    return offsets


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


class SubmitConfirmationFallbackTest(unittest.TestCase):
    def _writer(self, cwd: Path, data_dir: Path, jsonl_path: Path) -> InputWriter:
        return InputWriter(
            pty_write=lambda _data: None,
            submit_key=SubmitKey(raw="\r", label="enter"),
            jsonl_path=jsonl_path,
            expected_cwd=str(cwd),
            data_dir=data_dir,
        )

    def test_fingerprint_scan_uses_node_prompt_not_composed_header(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cwd = root / "workspace"
            cwd.mkdir()
            data_dir = root / "claude-data"
            current_project = project_dir(str(cwd), data_dir)
            current_project.mkdir(parents=True)

            header = "Shared framework launch instructions for every node"
            node_prompt = "Implement the unique payment reconciliation workflow"
            composed = f"{header}\n\n---\n\n{node_prompt}"

            fresh = current_project / "fresh-session.jsonl"
            stale = current_project / "stale-session.jsonl"
            _write_jsonl(
                fresh,
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": f"{node_prompt} with exact details",
                    },
                },
            )
            _write_jsonl(
                stale,
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": f"{header} copied from another node",
                    },
                },
            )
            os.utime(stale, None)

            writer = self._writer(cwd, data_dir, current_project / "current.jsonl")
            match = writer._fingerprint_scan(
                composed,
                confirmation_text=node_prompt,
            )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.session_id, "fresh-session")
        self.assertEqual(match.stream_offset, 0)

    def test_fingerprint_scan_stays_within_expected_project_hash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cwd = root / "workspace"
            other_cwd = root / "other-workspace"
            cwd.mkdir()
            other_cwd.mkdir()
            data_dir = root / "claude-data"
            project_dir(str(cwd), data_dir).mkdir(parents=True)
            other_project = project_dir(str(other_cwd), data_dir)
            other_project.mkdir(parents=True)

            node_prompt = "Implement the unique cross project sentinel"
            _write_jsonl(
                other_project / "other-session.jsonl",
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": node_prompt,
                    },
                },
            )

            writer = self._writer(
                cwd,
                data_dir,
                project_dir(str(cwd), data_dir) / "current.jsonl",
            )
            match = writer._fingerprint_scan(
                f"wrapper\n\n---\n\n{node_prompt}",
                confirmation_text=node_prompt,
            )

        self.assertIsNone(match)

    def test_fingerprint_scan_uses_latest_match_in_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cwd = root / "workspace"
            cwd.mkdir()
            data_dir = root / "claude-data"
            current_project = project_dir(str(cwd), data_dir)
            current_project.mkdir(parents=True)

            prompt = "Implement the duplicate fingerprint flow"
            transcript = current_project / "resumed-session.jsonl"
            offsets = _write_jsonl(
                transcript,
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": f"{prompt} from copied history",
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "stale copied response"}
                        ],
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": f"{prompt} fresh append",
                    },
                },
            )

            writer = self._writer(cwd, data_dir, current_project / "current.jsonl")
            match = writer._fingerprint_scan(prompt, confirmation_text=prompt)

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.session_id, "resumed-session")
        self.assertEqual(match.stream_offset, offsets[2])


class ClaudeNativeStreamTerminalTest(unittest.IsolatedAsyncioTestCase):
    async def test_submit_retarget_without_marker_offset_seeks_to_eof(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            session = _stream_session(raw, _FakePty(alive=True))
            path = Path(raw) / "retarget.jsonl"
            _write_jsonl(
                path,
                {
                    "type": "result",
                    "subtype": "success",
                },
            )

            session._retarget("new-session", path)
            self.assertEqual(session._jsonl_offset, path.stat().st_size)

    async def test_retarget_with_marker_offset_does_not_replay_history(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            session = _stream_session(raw, _FakePty(alive=True))
            path = Path(raw) / "retarget.jsonl"
            offsets = _write_jsonl(
                path,
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "stale response"}],
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": "fresh prompt",
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "fresh response"}],
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                },
            )

            session._retarget("new-session", path, stream_offset=offsets[2])
            events = await _collect(session.stream_events())

        text = "".join(
            event.event.text
            for event in events
            if (
                event.kind == "event"
                and event.event is not None
                and event.event.type == "text_delta"
            )
        )
        self.assertIn("fresh response", text)
        self.assertNotIn("stale response", text)
        self.assertEqual(events[-1].kind, "done")

    async def test_stream_retarget_continues_after_observed_target_record(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            session = _stream_session(raw, _FakePty(alive=True))
            actual_session_id = "actual-session"
            actual_path = jsonl_path(
                raw,
                actual_session_id,
                root / "data",
            )
            actual_path.parent.mkdir(parents=True)
            observed = {
                "type": "assistant",
                "sessionId": actual_session_id,
                "message": {
                    "content": [{"type": "text", "text": "fresh response"}],
                },
            }
            _write_jsonl(
                actual_path,
                {
                    "type": "assistant",
                    "sessionId": actual_session_id,
                    "message": {
                        "content": [{"type": "text", "text": "stale response"}],
                    },
                },
                {
                    "type": "result",
                    "sessionId": actual_session_id,
                    "subtype": "success",
                },
                observed,
                {
                    "type": "result",
                    "sessionId": actual_session_id,
                    "subtype": "success",
                },
            )
            _write_jsonl(session._jsonl_path, observed)

            events = await _collect(session.stream_events())

        text = "".join(
            event.event.text
            for event in events
            if (
                event.kind == "event"
                and event.event is not None
                and event.event.type == "text_delta"
            )
        )
        self.assertIn("fresh response", text)
        self.assertNotIn("stale response", text)
        self.assertEqual(events[-1].kind, "done")

    async def test_stream_retarget_without_matching_record_seeks_to_eof(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            session = _stream_session(raw, _FakePty(alive=True))
            actual_session_id = "actual-session"
            actual_path = jsonl_path(
                raw,
                actual_session_id,
                root / "data",
            )
            actual_path.parent.mkdir(parents=True)
            # The rotated file holds only copied prior history; the record
            # that announced the rotation is absent from it.
            _write_jsonl(
                actual_path,
                {
                    "type": "assistant",
                    "sessionId": actual_session_id,
                    "message": {
                        "content": [{"type": "text", "text": "stale response"}],
                    },
                },
                {
                    "type": "result",
                    "sessionId": actual_session_id,
                    "subtype": "success",
                },
            )
            _write_jsonl(
                session._jsonl_path,
                {
                    "type": "assistant",
                    "sessionId": actual_session_id,
                    "message": {
                        "content": [{"type": "text", "text": "rotation marker"}],
                    },
                },
            )

            with (
                patch("miniclaw2.providers.claude_native._STREAM_IDLE_TICK_LIMIT", 0),
                patch("miniclaw2.providers.claude_native._STREAM_POLL_INTERVAL", 0),
            ):
                events = await _collect(session.stream_events())

            self.assertEqual(session._jsonl_offset, actual_path.stat().st_size)

        text = "".join(
            event.event.text
            for event in events
            if (
                event.kind == "event"
                and event.event is not None
                and event.event.type == "text_delta"
            )
        )
        self.assertNotIn("stale response", text)

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

            async def send(
                self,
                _prompt: str,
                *,
                confirmation_text: str | None = None,
            ) -> SubmitResult:
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

    async def test_claude_provider_uses_node_prompt_for_submit_confirmation(
        self,
    ) -> None:
        class RecordingSession:
            cli_session_id = "claude-session"
            seen_prompt: str | None = None
            seen_confirmation_text: str | None = None

            def __init__(self, **_: Any) -> None:
                self.closed = False

            async def start(self) -> None:
                return None

            async def send(
                self,
                prompt: str,
                *,
                confirmation_text: str | None = None,
            ) -> SubmitResult:
                RecordingSession.seen_prompt = prompt
                RecordingSession.seen_confirmation_text = confirmation_text
                return SubmitResult(submitted=True)

            async def stream_events(self):
                yield AgentProviderEvent(kind="done", final_state="done")

            async def close(self) -> None:
                self.closed = True

        node = Node(
            project_id="p",
            prompt="Implement the concrete node prompt",
        )
        project = Project(root_path="/tmp/workspace")
        ctx = AgentProviderContext(
            node=node,
            project=project,
            request_gate_handler=_request_gate,
            launch_instructions="Shared launch instructions",
        )
        with patch(
            "miniclaw2.providers.claude.ClaudeNativeSession",
            RecordingSession,
        ):
            await _collect(ClaudeProvider().run(ctx))

        self.assertEqual(RecordingSession.seen_prompt, ctx.turn_text())
        self.assertEqual(
            RecordingSession.seen_confirmation_text,
            "Implement the concrete node prompt",
        )


if __name__ == "__main__":
    unittest.main()
