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
from miniclaw2.providers.claude_native.ask_payload import (
    format_ask_directive,
    parse_ask_payload,
)
from miniclaw2.providers.claude_native.spawn import build_argv, DISALLOWED_TOOLS


async def _request_gate(_gate: GateRequest) -> dict[str, Any]:
    return {"allow": True}


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


if __name__ == "__main__":
    unittest.main()
