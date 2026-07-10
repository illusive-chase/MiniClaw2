from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
            self.assertGreater(pre_tool["timeout"], 600)
            self.assertEqual(session_start["timeout"], 15)


class AskTimeoutChainTest(unittest.TestCase):
    def test_ask_timeout_chain_is_strictly_ordered(self) -> None:
        """Each layer must give up before the layer beneath it kills the
        transport: runner gate < /hook/ask wait < bridge HTTP < hook entry."""
        from miniclaw2 import claude_hook_bridge
        from miniclaw2.providers import claude as claude_provider
        from miniclaw2.providers.claude_native import hook_installer

        self.assertLess(
            claude_provider._ASK_GATE_TIMEOUT_SECONDS,
            app_module._HOOK_ASK_TIMEOUT_SECONDS,
        )
        self.assertLess(
            app_module._HOOK_ASK_TIMEOUT_SECONDS,
            claude_hook_bridge._ASK_TIMEOUT_SECONDS,
        )
        self.assertLess(
            claude_hook_bridge._ASK_TIMEOUT_SECONDS,
            hook_installer._ASK_HOOK_TIMEOUT_SECONDS,
        )


class HookAskRouteTest(unittest.TestCase):
    def test_hook_ask_timeout_returns_passthrough_status(self) -> None:
        async def slow_dispatcher(_payload: dict) -> dict:
            await asyncio.sleep(1)
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
                patch.object(app_module, "_HOOK_ASK_TIMEOUT_SECONDS", 0.01),
                TestClient(app_module.create_app()) as client,
            ):
                res = client.post(
                    "/hook/ask",
                    json={"node_id": node_id, "payload": {"tool_input": {}}},
                    headers={"Authorization": f"Bearer {hook_runtime.token()}"},
                )
        finally:
            hook_runtime.unregister_ask_dispatcher(node_id)

        self.assertEqual(res.status_code, 504)
        self.assertEqual(res.json()["error"], "ask dispatch timed out")

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
