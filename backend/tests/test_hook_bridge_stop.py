"""The hook bridge's ``Stop`` and subagent branches.

``Stop`` stopped being a fire-and-forget report: its reply now decides
whether the turn may end, and a refusal has to reach Claude verbatim as
a block directive. These tests pin that round trip and the fail-open
paths around it.
"""

from __future__ import annotations

import io
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from miniclaw2 import claude_hook_bridge


_ENV = {
    "MINICLAW_HOOK_URL": "http://127.0.0.1:43123/hook/ask",
    "MINICLAW_HOOK_TOKEN": "token",
    "MINICLAW_NODE_ID": "node-1",
}


def _urlopen_returning(payload: dict) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=response)


class StopBranchTest(unittest.TestCase):
    def _run(self, payload: dict, urlopen: MagicMock) -> tuple[int, str]:
        stdout = io.StringIO()
        with (
            patch.dict(os.environ, _ENV),
            patch.object(
                claude_hook_bridge.sys, "stdin", io.StringIO(json.dumps(payload))
            ),
            patch.object(claude_hook_bridge.sys, "stdout", stdout),
            patch.object(claude_hook_bridge.urlrequest, "urlopen", urlopen),
        ):
            code = claude_hook_bridge.main(["--turn-complete"])
        return code, stdout.getvalue()

    def test_block_directive_is_echoed_to_claude(self) -> None:
        """This is what actually holds the turn open.

        The backend refuses, and Claude only learns of the refusal by
        reading the directive on stdout.
        """
        urlopen = _urlopen_returning(
            {"decision": "block", "reason": "1 subagent still running"}
        )

        code, out = self._run(
            {
                "hook_event_name": "Stop",
                "session_id": "session-1",
                "stop_hook_active": True,
            },
            urlopen,
        )

        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out),
            {"decision": "block", "reason": "1 subagent still running"},
        )
        body = json.loads(urlopen.call_args.args[0].data)
        self.assertIs(body["stop_hook_active"], True)

    def test_stop_forwards_the_in_flight_subagent_snapshot(self) -> None:
        """The backend decides on the registry as it stands right now.

        A ledger accumulated from earlier hook calls cannot answer this:
        a subagent reports itself as running in its own ``SubagentStop``,
        so only the parent's ``Stop`` snapshot shows the registry empty.
        """
        urlopen = _urlopen_returning({"ok": True, "accepted": True})

        self._run(
            {
                "hook_event_name": "Stop",
                "session_id": "session-1",
                "background_tasks": [
                    {"id": "agent-7", "type": "subagent", "status": "running"},
                    {"id": "sh-1", "type": "shell", "status": "running"},
                ],
            },
            urlopen,
        )

        body = json.loads(urlopen.call_args.args[0].data)
        # A leftover shell task is an ordinary pattern (a spawned server,
        # a tail) and must not hold the node's turn open.
        self.assertEqual(body["running_agent_ids"], ["agent-7"])

    def test_ordinary_acknowledgement_prints_nothing(self) -> None:
        code, out = self._run(
            {"hook_event_name": "Stop", "session_id": "session-1"},
            _urlopen_returning({"ok": True, "accepted": True}),
        )

        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_block_without_a_reason_is_dropped(self) -> None:
        """Claude requires ``reason`` whenever ``decision`` is ``block``.

        A half-formed directive is worse than none, so the turn is
        allowed to end instead.
        """
        _code, out = self._run(
            {"hook_event_name": "Stop", "session_id": "session-1"},
            _urlopen_returning({"decision": "block", "reason": "   "}),
        )

        self.assertEqual(out, "")

    def test_unreachable_backend_lets_the_turn_end(self) -> None:
        """Fail open.

        Holding the turn open on a failed request would strand the node
        for a reason that has nothing to do with subagents.
        """
        code, out = self._run(
            {"hook_event_name": "Stop", "session_id": "session-1"},
            MagicMock(side_effect=OSError("connection refused")),
        )

        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_malformed_reply_lets_the_turn_end(self) -> None:
        response = MagicMock()
        response.read.return_value = b"not json"
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)

        _code, out = self._run(
            {"hook_event_name": "Stop", "session_id": "session-1"},
            MagicMock(return_value=response),
        )

        self.assertEqual(out, "")


class SubagentBranchTest(unittest.TestCase):
    def test_start_and_stop_report_the_agent_id(self) -> None:
        for flag, phase, event in (
            ("--subagent-start", "start", "SubagentStart"),
            ("--subagent-stop", "stop", "SubagentStop"),
        ):
            with self.subTest(flag=flag):
                payload = {
                    "hook_event_name": event,
                    "session_id": "session-1",
                    "agent_id": "agent-7",
                    "agent_type": "Explore",
                }
                if phase == "stop":
                    payload["background_tasks"] = []
                with (
                    patch.dict(os.environ, _ENV),
                    patch.object(
                        claude_hook_bridge.sys,
                        "stdin",
                        io.StringIO(json.dumps(payload)),
                    ),
                    patch.object(
                        claude_hook_bridge.urlrequest, "urlopen"
                    ) as urlopen,
                ):
                    claude_hook_bridge.main([flag])

                request = urlopen.call_args.args[0]
                self.assertEqual(
                    request.full_url, "http://127.0.0.1:43123/hook/subagent"
                )
                expected = {
                    "node_id": "node-1",
                    "phase": phase,
                    "agent_id": "agent-7",
                    "agent_type": "Explore",
                    "session_id": "session-1",
                }
                if phase == "stop":
                    expected["running_agent_ids"] = []
                self.assertEqual(json.loads(request.data), expected)

    def test_stop_forwards_the_in_flight_subagent_snapshot(self) -> None:
        """The snapshot is what distinguishes yielding from finishing.

        A subagent that backgrounds a shell command fires ``SubagentStop``
        while still listed as running, and is resumed under the same
        ``agent_id``. Only ``subagent`` entries are forwarded: a leftover
        ``shell`` task is an ordinary pattern and must not hold the node's
        turn open.
        """
        payload = {
            "hook_event_name": "SubagentStop",
            "session_id": "session-1",
            "agent_id": "agent-7",
            "agent_type": "Explore",
            "background_tasks": [
                {"id": "agent-7", "type": "subagent", "status": "running"},
                {"id": "sh-1", "type": "shell", "status": "running"},
            ],
        }
        with (
            patch.dict(os.environ, _ENV),
            patch.object(
                claude_hook_bridge.sys, "stdin", io.StringIO(json.dumps(payload))
            ),
            patch.object(claude_hook_bridge.urlrequest, "urlopen") as urlopen,
        ):
            claude_hook_bridge.main(["--subagent-stop"])

        body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(body["running_agent_ids"], ["agent-7"])

    def test_a_payload_with_no_snapshot_sends_null_not_empty(self) -> None:
        """``None`` and ``[]`` mean different things downstream.

        The array is documented as present only when the task registry is
        reachable. Reporting a missing snapshot as an empty one would
        retire every tracked subagent at once.
        """
        payload = {
            "hook_event_name": "SubagentStop",
            "session_id": "session-1",
            "agent_id": "agent-7",
        }
        with (
            patch.dict(os.environ, _ENV),
            patch.object(
                claude_hook_bridge.sys, "stdin", io.StringIO(json.dumps(payload))
            ),
            patch.object(claude_hook_bridge.urlrequest, "urlopen") as urlopen,
        ):
            claude_hook_bridge.main(["--subagent-stop"])

        body = json.loads(urlopen.call_args.args[0].data)
        self.assertIsNone(body["running_agent_ids"])

    def test_payload_without_an_agent_id_posts_nothing(self) -> None:
        """``agent_id`` is what pairs a start with its stop.

        Registering an entry we could never retire would block the node's
        turn until the budget ran out, for no real subagent.
        """
        with (
            patch.dict(os.environ, _ENV),
            patch.object(
                claude_hook_bridge.sys,
                "stdin",
                io.StringIO(json.dumps({"hook_event_name": "SubagentStart"})),
            ),
            patch.object(claude_hook_bridge.urlrequest, "urlopen") as urlopen,
        ):
            code = claude_hook_bridge.main(["--subagent-start"])

        self.assertEqual(code, 0)
        urlopen.assert_not_called()

    def test_backend_failure_is_silent(self) -> None:
        with (
            patch.dict(os.environ, _ENV),
            patch.object(
                claude_hook_bridge.sys,
                "stdin",
                io.StringIO(
                    json.dumps(
                        {"hook_event_name": "SubagentStop", "agent_id": "agent-7"}
                    )
                ),
            ),
            patch.object(
                claude_hook_bridge.urlrequest,
                "urlopen",
                MagicMock(side_effect=OSError("refused")),
            ),
        ):
            self.assertEqual(claude_hook_bridge.main(["--subagent-stop"]), 0)


if __name__ == "__main__":
    unittest.main()
