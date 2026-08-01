from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from miniclaw2 import app as app_module
from miniclaw2.providers.claude_native import hook_runtime
from miniclaw2.providers.claude_native.hook_installer import install_hooks


class HookInstallerTest(unittest.TestCase):
    def test_installed_hooks_include_explicit_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            settings = Path(raw) / "settings.json"

            install_hooks(settings)

            data = json.loads(settings.read_text(encoding="utf-8"))
            pre_tool = data["hooks"]["PreToolUse"][0]["hooks"][0]
            session_start = data["hooks"]["SessionStart"][0]["hooks"][0]
            stop = data["hooks"]["Stop"][0]["hooks"][0]
            self.assertEqual(pre_tool["timeout"], 2_147_000)
            self.assertEqual(session_start["timeout"], 15)
            self.assertEqual(stop["timeout"], 15)
            self.assertIn("--turn-complete", stop["command"])


class HookBridgeTest(unittest.TestCase):
    def test_ask_http_request_has_no_socket_timeout(self) -> None:
        from miniclaw2 import claude_hook_bridge

        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        with (
            patch.dict(
                os.environ,
                {
                    "MINICLAW_HOOK_URL": "http://127.0.0.1:43123/hook/ask",
                    "MINICLAW_HOOK_TOKEN": "token",
                    "MINICLAW_NODE_ID": "node-1",
                },
            ),
            patch.object(
                claude_hook_bridge.sys,
                "stdin",
                io.StringIO(json.dumps({
                    "hook_event_name": "PreToolUse",
                    "tool_name": "AskUserQuestion",
                })),
            ),
            patch.object(
                claude_hook_bridge.urlrequest,
                "urlopen",
                return_value=response,
            ) as urlopen,
        ):
            result = claude_hook_bridge.main([])

        self.assertEqual(result, 0)
        self.assertIsNone(urlopen.call_args.kwargs["timeout"])

    def test_turn_complete_posts_stop_signal(self) -> None:
        from miniclaw2 import claude_hook_bridge

        with (
            patch.dict(
                os.environ,
                {
                    "MINICLAW_HOOK_URL": "http://127.0.0.1:43123/hook/ask",
                    "MINICLAW_HOOK_TOKEN": "token",
                    "MINICLAW_NODE_ID": "node-1",
                },
            ),
            patch.object(
                claude_hook_bridge.sys,
                "stdin",
                io.StringIO(json.dumps({"hook_event_name": "Stop"})),
            ),
            patch.object(claude_hook_bridge.urlrequest, "urlopen") as urlopen,
        ):
            result = claude_hook_bridge.main(["--turn-complete"])

        request = urlopen.call_args.args[0]
        self.assertEqual(result, 0)
        self.assertEqual(
            request.full_url,
            "http://127.0.0.1:43123/hook/turn-complete",
        )
        self.assertEqual(json.loads(request.data), {"node_id": "node-1"})


class HookAskRouteTest(unittest.TestCase):
    def test_hook_turn_complete_signals_active_node(self) -> None:
        node_id = "node-complete"
        event = hook_runtime.register_turn_complete(node_id)
        try:
            with (
                tempfile.TemporaryDirectory() as raw,
                patch.dict(
                    os.environ,
                    {"MINICLAW_HOME": str(Path(raw) / "store")},
                ),
                patch.object(app_module, "install_hooks", return_value=Path(raw)),
                TestClient(app_module.create_app()) as client,
            ):
                res = client.post(
                    "/hook/turn-complete",
                    json={"node_id": node_id},
                    headers={"Authorization": f"Bearer {hook_runtime.token()}"},
                )
        finally:
            hook_runtime.unregister_turn_complete(node_id)

        self.assertEqual(res.status_code, 200)
        self.assertTrue(event.is_set())

    def test_hook_ask_waits_for_dispatcher(self) -> None:
        async def slow_dispatcher(_payload: dict) -> dict:
            await asyncio.sleep(0.03)
            return {"ok": True}

        node_id = "node-timeout"
        hook_runtime.register_ask_dispatcher(node_id, slow_dispatcher)
        try:
            with (
                tempfile.TemporaryDirectory() as raw,
                patch.dict(
                    os.environ,
                    {"MINICLAW_HOME": str(Path(raw) / "store")},
                ),
                patch.object(app_module, "install_hooks", return_value=Path(raw)),
                TestClient(app_module.create_app()) as client,
            ):
                res = client.post(
                    "/hook/ask",
                    json={"node_id": node_id, "payload": {"tool_input": {}}},
                    headers={"Authorization": f"Bearer {hook_runtime.token()}"},
                )
        finally:
            hook_runtime.unregister_ask_dispatcher(node_id)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"ok": True})

    def test_http_request_records_actual_hook_port(self) -> None:
        hook_runtime.set_port(0)
        with (
            patch.dict(os.environ, {"MINICLAW2_HOOK_PORT": "", "MINICLAW2_PORT": ""}),
            tempfile.TemporaryDirectory() as raw,
            patch.dict(
                os.environ,
                {"MINICLAW_HOME": str(Path(raw) / "store")},
            ),
            patch.object(app_module, "install_hooks", return_value=Path(raw)),
            TestClient(app_module.create_app()) as client,
        ):
            res = client.get("/sessions")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(hook_runtime.get_port(), 80)
        hook_runtime.set_port(0)

    def test_http_request_preserves_explicit_hook_port_override(self) -> None:
        hook_runtime.set_port(0)
        with (
            patch.dict(os.environ, {"MINICLAW2_HOOK_PORT": "43123"}),
            tempfile.TemporaryDirectory() as raw,
            patch.dict(
                os.environ,
                {"MINICLAW_HOME": str(Path(raw) / "store")},
            ),
            patch.object(app_module, "install_hooks", return_value=Path(raw)),
            TestClient(app_module.create_app()) as client,
        ):
            res = client.get("/sessions")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(hook_runtime.ask_url(), "http://127.0.0.1:43123/hook/ask")
        hook_runtime.set_port(0)


if __name__ == "__main__":
    unittest.main()
