from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import miniclaw2.__main__ as cli


class MainTest(unittest.TestCase):
    def _run_dev(
        self,
        *,
        reload: bool,
    ) -> tuple[list[str], dict[str, str], bool]:
        argv = ["miniclaw2", "--dev"]
        if reload:
            argv.append("--reload")

        vite_proc = MagicMock()
        vite_proc.pid = 1234
        with (
            patch.object(sys, "argv", argv),
            patch.object(cli.shutil, "which", return_value="/usr/bin/npm"),
            patch.object(cli.Path, "is_dir", return_value=True),
            patch.object(cli.subprocess, "Popen", return_value=vite_proc) as popen,
            patch.object(cli.uvicorn, "run") as uvicorn_run,
            patch.object(cli, "_signal_group") as signal_group,
            patch.dict(os.environ, {}, clear=True),
        ):
            cli.main()

        signal_group.assert_called_once_with(vite_proc.pid, cli.signal.SIGTERM)
        vite_command = popen.call_args.args[0]
        vite_env = popen.call_args.kwargs["env"]
        backend_reload = uvicorn_run.call_args.kwargs["reload"]
        return vite_command, vite_env, backend_reload

    def test_dev_disables_frontend_and_backend_reload_by_default(self) -> None:
        vite_command, vite_env, backend_reload = self._run_dev(reload=False)

        self.assertEqual(
            vite_command,
            [
                "npm",
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                "5173",
            ],
        )
        self.assertEqual(vite_env["MINICLAW_RELOAD"], "0")
        self.assertFalse(backend_reload)

    def test_reload_enables_frontend_and_backend_reload(self) -> None:
        _, vite_env, backend_reload = self._run_dev(reload=True)

        self.assertEqual(vite_env["MINICLAW_RELOAD"], "1")
        self.assertTrue(backend_reload)


if __name__ == "__main__":
    unittest.main()
