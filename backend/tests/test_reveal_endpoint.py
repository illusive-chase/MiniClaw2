from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from miniclaw2 import file_manager
from miniclaw2.app import create_app


class RevealEndpointTest(unittest.TestCase):
    """Cover the reveal endpoint without launching a real file manager."""

    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self._cwd = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()
        self._cwd.cleanup()
        self._home.cleanup()

    def _create_session(self) -> str:
        res = self.client.post(
            "/sessions",
            json={"cwd": self._cwd.name, "model_preset_id": "opus-4-8"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["id"]

    def test_session_info_exposes_bound_root_path(self) -> None:
        sid = self._create_session()
        body = self.client.get(f"/sessions/{sid}").json()
        self.assertEqual(
            body["root_path"], str(Path(self._cwd.name).resolve())
        )

    def test_reveal_spawns_platform_launcher_for_project_root(self) -> None:
        sid = self._create_session()
        root = str(Path(self._cwd.name).resolve())

        with patch.object(file_manager.subprocess, "Popen") as popen:
            res = self.client.post(f"/sessions/{sid}/reveal")

        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["root_path"], root)
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0][-1], root)

    def test_reveal_reports_missing_directory_rather_than_spawning(self) -> None:
        sid = self._create_session()
        self._cwd.cleanup()

        with patch.object(file_manager.subprocess, "Popen") as popen:
            res = self.client.post(f"/sessions/{sid}/reveal")

        self.assertEqual(res.status_code, 400, res.text)
        popen.assert_not_called()

    def test_reveal_reports_absent_launcher_binary(self) -> None:
        sid = self._create_session()

        with patch.object(
            file_manager.subprocess, "Popen", side_effect=FileNotFoundError
        ):
            res = self.client.post(f"/sessions/{sid}/reveal")

        self.assertEqual(res.status_code, 400, res.text)

    def test_reveal_rejects_unknown_session(self) -> None:
        res = self.client.post("/sessions/does-not-exist/reveal")
        self.assertEqual(res.status_code, 404, res.text)


class RevealCommandTest(unittest.TestCase):
    def test_each_supported_platform_gets_its_own_launcher(self) -> None:
        expected = {
            "darwin": "open",
            "win32": "explorer",
            "linux": "xdg-open",
        }
        for platform, binary in expected.items():
            with self.subTest(platform=platform):
                with patch.object(file_manager.sys, "platform", platform):
                    self.assertEqual(
                        file_manager.reveal_command("/tmp/x"), [binary, "/tmp/x"]
                    )

    def test_unknown_platform_raises_unsupported(self) -> None:
        with patch.object(file_manager.sys, "platform", "sunos5"):
            with self.assertRaises(file_manager.RevealUnsupportedError):
                file_manager.reveal_command("/tmp/x")


if __name__ == "__main__":
    unittest.main()
