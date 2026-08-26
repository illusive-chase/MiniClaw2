from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from miniclaw2.domain import Node, Project, ReviewTarget
from miniclaw2.providers.base import (
    AgentProviderContext,
    AgentProviderEvent,
    GateRequest,
    ReviewSpec,
)
from miniclaw2.providers.claude import ClaudeProvider, _unknown_code_review_command
from miniclaw2.providers.claude_native import ClaudeNativeSession
from miniclaw2.providers.claude_native import hook_runtime
from miniclaw2.providers.claude_native.ask_payload import (
    format_ask_directive,
    parse_ask_payload,
)
from miniclaw2.providers.claude_native.input import InputWriter, SubmitResult
from miniclaw2.providers.claude_native.keybindings import SubmitKey
from miniclaw2.providers.claude_native.paths import jsonl_path, project_dir
from miniclaw2.providers.claude_native.spawn import (
    DISALLOWED_TOOLS,
    build_argv,
    build_env,
)


async def _request_gate(_gate: GateRequest) -> dict[str, Any]:
    return {"allow": True}


async def _ask_dispatcher(_payload: dict[str, Any]) -> dict[str, Any]:
    return {}


async def _collect(provider_events):
    return [event async for event in provider_events]


class ClaudeCodeReviewDetectionTest(unittest.TestCase):
    def test_unknown_slash_command_is_not_a_report(self) -> None:
        self.assertTrue(
            _unknown_code_review_command("Unknown slash command: /code-review")
        )
        self.assertFalse(_unknown_code_review_command("# Review\n\nNo findings."))
        self.assertFalse(_unknown_code_review_command(
            "# Review\n\nThe implementation correctly handles the literal message "
            "'Unknown slash command: /code-review' without treating this report "
            "as a command response. Additional analysis confirms no findings."
        ))


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
    def test_node_model_preset_resolves_claude_model(self) -> None:
        node = Node(project_id="p", prompt="hi", model_preset_id="opus-4-8")
        project = Project(
            root_path="/tmp/workspace",
            model_preset_id="gpt-5.6",
            settings_override={"model": "ignored-legacy-model"},
        )
        ctx = AgentProviderContext(
            node=node,
            project=project,
            request_gate_handler=_request_gate,
        )
        self.assertEqual(ClaudeProvider()._resolve_model(ctx), "claude-opus-4-8[1m]")
        self.assertEqual(ClaudeProvider()._resolve_effort(ctx), "xhigh")

    def test_project_settings_do_not_override_node_preset(self) -> None:
        node = Node(project_id="p", prompt="hi", model_preset_id="opus-4-8")
        project = Project(
            root_path="/tmp/workspace",
            model_preset_id="gpt-5.6",
            settings_override={"model": "legacy-project-model"},
        )
        ctx = AgentProviderContext(
            node=node,
            project=project,
            request_gate_handler=_request_gate,
        )
        self.assertEqual(ClaudeProvider()._resolve_model(ctx), "claude-opus-4-8[1m]")


class BuildArgvTest(unittest.TestCase):
    def test_fresh_session_disables_plan_mode_and_forces_bypass(self) -> None:
        args = build_argv(
            binary="/usr/local/bin/claude",
            session_id="abc-123",
            resume=False,
            model="claude-sonnet-4-6",
            effort="xhigh",
            system_prompt_append="Hello",
        )
        self.assertEqual(args[0], "/usr/local/bin/claude")
        self.assertIn("--session-id", args)
        self.assertIn("abc-123", args)
        self.assertIn("--model", args)
        self.assertEqual(args[args.index("--effort") + 1], "xhigh")
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
        # Keep Claude's default user/project/local setting sources enabled;
        # --settings above is an additional inline override.
        self.assertNotIn("--setting-sources", args)
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


class BuildEnvTest(unittest.TestCase):
    def test_inherits_configured_anthropic_base_url(self) -> None:
        with patch.dict(
            os.environ,
            {"ANTHROPIC_BASE_URL": "https://anthropic.example.test"},
            clear=True,
        ):
            env = build_env(
                hook_url="http://127.0.0.1:43123/hook",
                hook_token="hook-token",
                node_id="node-1",
                project_id="project-1",
                session_id="session-1",
            )

        self.assertEqual(
            env["ANTHROPIC_BASE_URL"],
            "https://anthropic.example.test",
        )
        self.assertEqual(env["MINICLAW_NODE_ID"], "node-1")


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
            {
                "response": {
                    "answers": {
                        "Which framework?": {"answers": ["React"]},
                    }
                }
            },
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

    def test_canonical_free_text_answer(self) -> None:
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
        directive = format_ask_directive(
            {
                "response": {
                    "answers": {
                        "Any thoughts?": {"answers": ["sure, sounds fine"]},
                    }
                }
            },
            parsed,
        )
        answers = directive["hookSpecificOutput"]["updatedInput"]["answers"]
        self.assertEqual(answers["Any thoughts?"], "sure, sounds fine")


class JsonlDrainTest(unittest.TestCase):
    def test_api_error_message_emits_terminal_error_instead_of_text(self) -> None:
        from miniclaw2.providers.claude_native.transcript import TranscriptTranslator

        events = TranscriptTranslator().translate({
            "type": "assistant",
            "sessionId": "session-error",
            "isApiErrorMessage": True,
            "error": "server_error",
            "message": {
                "content": [{
                    "type": "text",
                    "text": (
                        "API Error: Connection lost mid-response. "
                        "The response above may be incomplete."
                    ),
                }],
            },
        })

        self.assertEqual([event.kind for event in events], ["session", "error"])
        self.assertIn("Connection lost mid-response", events[-1].error or "")
        self.assertFalse(any(event.kind == "event" for event in events))

    def test_report_capture_prefers_longest_non_sidechain_assistant_text(self) -> None:
        from miniclaw2.providers.claude_native.transcript import TranscriptTranslator

        translator = TranscriptTranslator()
        translator.translate({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Detailed review report."}]},
        })
        translator.translate({
            "type": "assistant",
            "isSidechain": True,
            "message": {"content": [{"type": "text", "text": "x" * 200}]},
        })
        translator.translate({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Review complete."}]},
        })

        self.assertEqual(translator.last_assistant_text, "Detailed review report.")

    def test_bash_activity_keeps_full_command(self) -> None:
        from miniclaw2.providers.claude_native.transcript import TranscriptTranslator

        command = "printf '%s' " + "x" * 300
        events = TranscriptTranslator().translate(
            {
                "type": "assistant",
                "message": {
                    "id": "message-command",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-command",
                            "name": "Bash",
                            "input": {"command": command},
                        }
                    ],
                },
            }
        )

        self.assertEqual(len(events), 1)
        activity = events[0].event
        self.assertIsNotNone(activity)
        assert activity is not None
        self.assertEqual(activity.parameters, json.dumps({"command": command}))
        self.assertEqual(activity.command, command)

    def test_tool_activity_keeps_full_parameters(self) -> None:
        from miniclaw2.providers.claude_native.transcript import TranscriptTranslator

        file_path = "/tmp/" + "nested/" * 40 + "README.md"
        events = TranscriptTranslator().translate(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-read",
                            "name": "Read",
                            "input": {"file_path": file_path},
                        }
                    ],
                },
            }
        )

        activity = events[0].event
        self.assertIsNotNone(activity)
        assert activity is not None
        self.assertEqual(activity.parameters, json.dumps({"file_path": file_path}))
        self.assertTrue(activity.summary.endswith("..."))

    def test_non_conversation_records_do_not_emit_events_or_usage(self) -> None:
        from miniclaw2.providers.claude_native.transcript import TranscriptTranslator

        translator = TranscriptTranslator()

        self.assertEqual(
            translator.translate(
                {"type": "summary", "usage": {"input_tokens": 10}}
            ),
            [],
        )
        self.assertEqual(
            translator.translate(
                {"type": "result", "usage": {"input_tokens": 20}}
            ),
            [],
        )
        self.assertIsNone(translator.final_usage())

    def test_assistant_usage_deduplicates_message_fragments(self) -> None:
        from miniclaw2.providers.claude_native.transcript import TranscriptTranslator

        translator = TranscriptTranslator()
        for record in (
            {
                "type": "assistant",
                "message": {
                    "id": "message-1",
                    "content": [{"type": "thinking", "thinking": "work"}],
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": 5,
                        "cache_read_input_tokens": 7,
                        "cache_creation_input_tokens": 11,
                    },
                },
            },
            {
                "type": "assistant",
                "message": {
                    "id": "message-1",
                    "content": [{"type": "text", "text": "done"}],
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": 5,
                        "cache_read_input_tokens": 7,
                        "cache_creation_input_tokens": 11,
                    },
                },
            },
            {
                "type": "assistant",
                "message": {
                    "id": "message-2",
                    "content": [{"type": "text", "text": "next"}],
                    "usage": {
                        "input_tokens": 13,
                        "output_tokens": 17,
                        "cache_read_input_tokens": 19,
                        "cache_creation_input_tokens": 23,
                    },
                },
            },
        ):
            translator.translate(record)

        usage = translator.final_usage()
        assert usage is not None
        self.assertEqual(usage.input_tokens, 16)
        self.assertEqual(usage.output_tokens, 22)
        self.assertEqual(usage.cache_read_tokens, 26)
        self.assertEqual(usage.cache_creation_tokens, 34)
        self.assertTrue(usage.final)

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
    async def test_turn_complete_waits_for_in_flight_tool_result(self) -> None:
        """A verified Stop must not truncate a tool call still in flight.

        Second line of defense behind the ownership check: even a genuine
        turn-complete can land a poll before the matching ``tool_result``
        is written, and collecting the stream there loses the tail of the
        turn.
        """
        with tempfile.TemporaryDirectory() as raw:
            session = _stream_session(raw, _FakePty(alive=True))
            _write_jsonl(
                session._jsonl_path,
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "tool-1",
                                "name": "Bash",
                                "input": {"command": "echo hi"},
                            }
                        ],
                    },
                },
            )
            session._turn_complete_event = asyncio.Event()
            session._turn_complete_event.set()

            async def land_result() -> None:
                # The result arrives after the signal, as it does when a
                # nested process stops mid-tool-call.
                with session._jsonl_path.open("a", encoding="utf-8") as f:
                    f.write(
                        json.dumps({
                            "type": "user",
                            "message": {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": "tool-1",
                                        "content": "hi",
                                    }
                                ],
                            },
                        })
                        + "\n"
                    )

            with patch(
                "miniclaw2.providers.claude_native._STREAM_POLL_INTERVAL", 0
            ):
                collector = asyncio.ensure_future(
                    _collect(session.stream_events())
                )
                await asyncio.sleep(0)
                await land_result()
                events = await asyncio.wait_for(collector, timeout=5)

        activities = [
            event.event
            for event in events
            if (
                event.kind == "event"
                and event.event is not None
                and event.event.type == "activity"
            )
        ]
        self.assertTrue(
            any(a.status == "finish" for a in activities),
            "the tool_result that landed after the signal must be streamed",
        )
        self.assertEqual(events[-1].kind, "done")
        self.assertEqual(events[-1].final_state, "done")

    async def test_turn_complete_tool_drain_is_bounded(self) -> None:
        """A tool_result that never arrives must not strand the node.

        The stall check is skipped while tools are pending, so the drain
        window is the only bound on this path.
        """
        with tempfile.TemporaryDirectory() as raw:
            session = _stream_session(raw, _FakePty(alive=True))
            _write_jsonl(
                session._jsonl_path,
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "tool-orphan",
                                "name": "Bash",
                                "input": {"command": "sleep 999"},
                            }
                        ],
                    },
                },
            )
            session._turn_complete_event = asyncio.Event()
            session._turn_complete_event.set()

            with (
                patch(
                    "miniclaw2.providers.claude_native._STREAM_POLL_INTERVAL", 0
                ),
                patch(
                    "miniclaw2.providers.claude_native."
                    "_TURN_COMPLETE_TOOL_DRAIN_SECONDS",
                    0.05,
                ),
            ):
                events = await asyncio.wait_for(
                    _collect(session.stream_events()), timeout=5
                )

        self.assertEqual(events[-1].kind, "done")
        self.assertEqual(events[-1].final_state, "done")

    async def test_retarget_keeps_rotated_session_signalable(self) -> None:
        """Rotation must not lock the node out of its own turn-complete."""
        with tempfile.TemporaryDirectory() as raw:
            session = _stream_session(raw, _FakePty(alive=True))
            node_id = session._node_id
            session._turn_complete_event = hook_runtime.register_turn_complete(
                node_id, session.session_id
            )
            self.addCleanup(hook_runtime.unregister_turn_complete, node_id)

            session._retarget("rotated-session", Path(raw) / "rotated.jsonl")

            self.assertTrue(
                hook_runtime.signal_turn_complete(node_id, "rotated-session")
            )
            self.assertTrue(session._turn_complete_event.is_set())

    async def test_submit_retarget_without_marker_offset_seeks_to_eof(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            session = _stream_session(raw, _FakePty(alive=True))
            path = Path(raw) / "retarget.jsonl"
            _write_jsonl(
                path,
                {
                    "type": "system",
                    "subtype": "prior-turn-metadata",
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
                    "type": "system",
                    "subtype": "prior-turn-metadata",
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
                    "type": "system",
                    "subtype": "current-turn-metadata",
                },
            )

            session._retarget("new-session", path, stream_offset=offsets[2])
            session._turn_complete_event = asyncio.Event()
            session._turn_complete_event.set()
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
                    "type": "system",
                    "sessionId": actual_session_id,
                    "subtype": "prior-turn-metadata",
                },
                observed,
                {
                    "type": "system",
                    "sessionId": actual_session_id,
                    "subtype": "current-turn-metadata",
                },
            )
            _write_jsonl(session._jsonl_path, observed)
            session._turn_complete_event = asyncio.Event()
            session._turn_complete_event.set()

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
                    "type": "system",
                    "sessionId": actual_session_id,
                    "subtype": "prior-turn-metadata",
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
                patch(
                    "miniclaw2.providers.claude_native._STREAM_STALL_TIMEOUT_SECONDS",
                    0,
                ),
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

    async def test_stop_hook_emits_final_usage_then_done(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            session = _stream_session(raw, _FakePty(alive=True))
            session._turn_complete_event = asyncio.Event()
            session._turn_complete_event.set()
            _write_jsonl(
                session._jsonl_path,
                {
                    "type": "assistant",
                    "message": {
                        "id": "final-message",
                        "content": [{"type": "text", "text": "finished"}],
                        "usage": {
                            "input_tokens": 2,
                            "output_tokens": 3,
                            "cache_read_input_tokens": 5,
                            "cache_creation_input_tokens": 7,
                        },
                    },
                },
            )

            events = await _collect(session.stream_events())

        self.assertEqual(events[-1].kind, "done")
        self.assertEqual(events[-1].final_state, "done")
        usage_event = events[-2]
        self.assertEqual(usage_event.kind, "event")
        assert usage_event.event is not None
        self.assertEqual(usage_event.event.type, "usage")
        self.assertEqual(usage_event.event.input_tokens, 2)
        self.assertEqual(usage_event.event.output_tokens, 3)
        self.assertEqual(usage_event.event.cache_read_tokens, 5)
        self.assertEqual(usage_event.event.cache_creation_tokens, 7)
        self.assertTrue(usage_event.event.final)

    async def test_api_error_record_ends_stream_while_pty_remains_alive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            session = _stream_session(raw, _FakePty(alive=True))
            session._turn_complete_event = asyncio.Event()
            _write_jsonl(
                session._jsonl_path,
                {
                    "type": "assistant",
                    "isApiErrorMessage": True,
                    "error": "server_error",
                    "message": {
                        "content": [{
                            "type": "text",
                            "text": "API Error: Connection lost mid-response.",
                        }],
                    },
                },
            )

            events = await asyncio.wait_for(
                _collect(session.stream_events()), timeout=1
            )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "error")
        self.assertIn("Connection lost", events[0].error or "")

    async def test_api_error_emits_accumulated_usage_before_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            session = _stream_session(raw, _FakePty(alive=True))
            session._turn_complete_event = asyncio.Event()
            _write_jsonl(
                session._jsonl_path,
                {
                    "type": "assistant",
                    "message": {
                        "id": "successful-round",
                        "content": [{
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Read",
                            "input": {"file_path": "notes.txt"},
                        }],
                        "usage": {
                            "input_tokens": 11,
                            "output_tokens": 13,
                            "cache_read_input_tokens": 17,
                            "cache_creation_input_tokens": 19,
                        },
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "loaded",
                        }],
                    },
                },
                {
                    "type": "assistant",
                    "isApiErrorMessage": True,
                    "error": "server_error",
                    "message": {
                        "content": [{
                            "type": "text",
                            "text": "API Error: Connection lost mid-response.",
                        }],
                    },
                },
            )

            events = await asyncio.wait_for(
                _collect(session.stream_events()), timeout=1
            )

        self.assertEqual(events[-1].kind, "error")
        usage_event = events[-2]
        self.assertEqual(usage_event.kind, "event")
        assert usage_event.event is not None
        self.assertEqual(usage_event.event.type, "usage")
        self.assertEqual(usage_event.event.input_tokens, 11)
        self.assertEqual(usage_event.event.output_tokens, 13)
        self.assertEqual(usage_event.event.cache_read_tokens, 17)
        self.assertEqual(usage_event.event.cache_creation_tokens, 19)
        self.assertTrue(usage_event.event.final)

    async def test_pending_tool_is_not_killed_by_stall_watchdog(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            session = _stream_session(raw, _FakePty(alive=True))
            session._turn_complete_event = asyncio.Event()
            _write_jsonl(
                session._jsonl_path,
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "pdf-read",
                                "name": "Read",
                                "input": {"file_path": "paper.pdf"},
                            }
                        ],
                    },
                },
            )

            with (
                patch(
                    "miniclaw2.providers.claude_native._STREAM_STALL_TIMEOUT_SECONDS",
                    0,
                ),
                patch("miniclaw2.providers.claude_native._STREAM_POLL_INTERVAL", 0),
            ):
                task = asyncio.create_task(_collect(session.stream_events()))
                await asyncio.sleep(0.01)
                self.assertFalse(task.done())
                with session._jsonl_path.open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "type": "user",
                                "message": {
                                    "content": [
                                        {
                                            "type": "tool_result",
                                            "tool_use_id": "pdf-read",
                                            "content": "pages loaded",
                                        }
                                    ]
                                },
                            }
                        )
                        + "\n"
                    )
                session._turn_complete_event.set()
                events = await asyncio.wait_for(task, timeout=1)

        self.assertEqual(events[-1].kind, "done")
        self.assertTrue(
            any(
                event.kind == "event"
                and event.event is not None
                and event.event.type == "activity"
                and event.event.status == "finish"
                for event in events
            )
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
        self.assertIn("exited before a turn-complete signal", events[0].error or "")
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

    async def test_interrupt_stalled_alive_child_emits_cancelled_done(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pty = _FakePty(alive=True, exit_on_interrupt=False)
            session = _stream_session(raw, pty)

            with (
                patch(
                    "miniclaw2.providers.claude_native._STREAM_STALL_TIMEOUT_SECONDS",
                    0,
                ),
                patch("miniclaw2.providers.claude_native._STREAM_POLL_INTERVAL", 0),
            ):
                await session.interrupt()
                events = await _collect(session.stream_events())

        self.assertEqual(pty.writes, [b"\x03"])
        self.assertTrue(pty.alive)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "done")
        self.assertEqual(events[0].final_state, "cancelled")

    async def test_superseded_session_close_keeps_next_turn_signalable(
        self,
    ) -> None:
        """The preview-repair hang: turn 1's late ``close()`` must not
        deregister the repair turn's ``Stop`` waiter.
        """
        with tempfile.TemporaryDirectory() as raw:
            node_id = "node-repair-race"
            first = ClaudeNativeSession(
                cwd=raw,
                node_id=node_id,
                project_id="project-1",
                ask_dispatcher=_ask_dispatcher,
                data_dir=Path(raw) / "data",
            )
            first._pty = _FakePty(alive=True)
            first._session_ready_event = hook_runtime.register_session_ready(
                first.session_id
            )
            first._session_ready_key = first.session_id
            first._turn_complete_event = hook_runtime.register_turn_complete(
                node_id, first.session_id
            )
            self.assertTrue(
                hook_runtime.signal_turn_complete(node_id, first.session_id)
            )

            # The repair turn spawns while turn 1's generator is still
            # awaiting finalization.
            second = ClaudeNativeSession(
                cwd=raw,
                node_id=node_id,
                project_id="project-1",
                ask_dispatcher=_ask_dispatcher,
                data_dir=Path(raw) / "data",
            )
            second._pty = _FakePty(alive=True)
            second._input = _FakeInput()
            second._jsonl_path = Path(raw) / "repair.jsonl"
            second._session_ready_event = hook_runtime.register_session_ready(
                second.session_id
            )
            second._session_ready_key = second.session_id
            second._turn_complete_event = hook_runtime.register_turn_complete(
                node_id, second.session_id
            )
            self.addCleanup(hook_runtime.unregister_turn_complete, node_id)

            await first.close()

            self.assertTrue(
                hook_runtime.signal_turn_complete(node_id, second.session_id)
            )
            self.assertTrue(second._turn_complete_event.is_set())

            with patch(
                "miniclaw2.providers.claude_native._STREAM_POLL_INTERVAL", 0
            ):
                events = await asyncio.wait_for(
                    _collect(second.stream_events()), timeout=5
                )

        self.assertEqual(events[-1].kind, "done")
        self.assertEqual(events[-1].final_state, "done")

    async def test_claude_provider_turns_bare_native_exhaustion_into_error(self) -> None:
        class BareSession:
            session_id = "claude-session"

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

        node = Node(project_id="p", prompt="hi", model_preset_id="opus-4-7")
        project = Project(root_path="/tmp/workspace", model_preset_id="opus-4-7")
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
            session_id = "claude-session"
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
            model_preset_id="opus-4-7",
        )
        project = Project(root_path="/tmp/workspace", model_preset_id="opus-4-7")
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

    async def test_claude_review_appends_skill_suggestions_to_system_prompt(
        self,
    ) -> None:
        class RecordingSession:
            session_id = "claude-review-session"
            last_assistant_text = "# Review\n\nNo findings."
            seen_system_prompt: str | None = None

            def __init__(self, **kwargs: Any) -> None:
                RecordingSession.seen_system_prompt = kwargs.get(
                    "system_prompt_append"
                )

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
                yield AgentProviderEvent(kind="done", final_state="done")

            async def close(self) -> None:
                return None

        node = Node(project_id="p", prompt="review", model_preset_id="opus-4-7")
        project = Project(root_path="/tmp/workspace", model_preset_id="opus-4-7")
        context = AgentProviderContext(
            node=node,
            project=project,
            request_gate_handler=_request_gate,
            system_context="Suggested skill context",
        )

        with patch(
            "miniclaw2.providers.claude.ClaudeNativeSession",
            RecordingSession,
        ):
            events = await _collect(
                ClaudeProvider().run_review(
                    context,
                    ReviewSpec(target=ReviewTarget()),
                )
            )

        self.assertEqual(
            RecordingSession.seen_system_prompt,
            "Suggested skill context",
        )
        self.assertTrue(any(event.kind == "review" for event in events))


if __name__ == "__main__":
    unittest.main()
