from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, call, patch

import miniclaw2.__main__ as cli


class MainTest(unittest.TestCase):
    def _run_dev(
        self,
        *,
        reload: bool,
    ) -> tuple[list[str], list[str], dict[str, str]]:
        argv = ["miniclaw2", "--dev"]
        if reload:
            argv.append("--reload")

        backend_proc = MagicMock()
        backend_proc.pid = 1233
        vite_proc = MagicMock()
        vite_proc.pid = 1234
        with (
            patch.object(sys, "argv", argv),
            patch.object(cli, "ensure_machine_identity") as ensure_identity,
            patch.object(cli.shutil, "which", return_value="/usr/bin/npm"),
            patch.object(cli.Path, "is_dir", return_value=True),
            patch.object(
                cli.subprocess, "Popen", side_effect=[backend_proc, vite_proc]
            ) as popen,
            patch.object(cli, "_wait_for_backend", return_value=True) as wait_ready,
            patch.object(cli, "_wait_for_dev_exit", return_value=0),
            patch.object(cli, "_stop_process_group") as stop_group,
            patch.dict(os.environ, {}, clear=True),
        ):
            cli.main()

        ensure_identity.assert_called_once()
        self.assertEqual(
            stop_group.call_args_list,
            [call(vite_proc), call(backend_proc)],
        )
        backend_call, vite_call = popen.call_args_list
        wait_ready.assert_called_once()
        self.assertEqual(
            wait_ready.call_args.args[:2],
            (backend_proc, "http://127.0.0.1:8000/health"),
        )
        self.assertEqual(
            backend_call.kwargs["env"][cli.DEV_INSTANCE_ENV],
            wait_ready.call_args.args[2],
        )
        return (
            backend_call.args[0],
            vite_call.args[0],
            vite_call.kwargs["env"],
        )

    def test_dev_disables_frontend_and_backend_reload_by_default(self) -> None:
        backend_command, vite_command, vite_env = self._run_dev(reload=False)

        self.assertNotIn("--reload", backend_command)
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

    def test_reload_enables_frontend_and_backend_reload(self) -> None:
        backend_command, _, vite_env = self._run_dev(reload=True)

        self.assertIn("--reload", backend_command)
        self.assertEqual(vite_env["MINICLAW_RELOAD"], "1")

    def test_dev_does_not_start_vite_before_backend_is_ready(self) -> None:
        backend_proc = MagicMock(pid=1233, returncode=7)
        with (
            patch.object(cli.subprocess, "Popen", return_value=backend_proc) as popen,
            patch.object(cli, "_wait_for_backend", return_value=False),
            patch.object(cli, "_stop_process_group"),
        ):
            result = cli._run_dev(
                host="127.0.0.1",
                port=8000,
                log_level="info",
                reload=False,
                frontend_dir=cli.Path("frontend"),
                backend_url="http://127.0.0.1:8000",
            )

        self.assertEqual(result, 7)
        self.assertEqual(popen.call_count, 1)

    def test_dev_interrupt_cleans_up_backend_during_startup(self) -> None:
        backend_proc = MagicMock(pid=1233)
        with (
            patch.object(cli.subprocess, "Popen", return_value=backend_proc),
            patch.object(cli, "_wait_for_backend", side_effect=KeyboardInterrupt),
            patch.object(cli, "_stop_process_group") as stop_group,
        ):
            result = cli._run_dev(
                host="127.0.0.1",
                port=8000,
                log_level="info",
                reload=False,
                frontend_dir=cli.Path("frontend"),
                backend_url="http://127.0.0.1:8000",
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            stop_group.call_args_list,
            [call(None), call(backend_proc)],
        )

    def test_dev_installs_sigterm_handler_before_spawning_backend(self) -> None:
        backend_proc = MagicMock(pid=1233, returncode=1)
        previous_handler = object()

        def spawn(*args: object, **kwargs: object) -> MagicMock:
            set_signal.assert_called_once_with(
                cli.signal.SIGTERM,
                cli._request_dev_shutdown,
            )
            return backend_proc

        with (
            patch.object(
                cli.signal,
                "signal",
                return_value=previous_handler,
            ) as set_signal,
            patch.object(cli, "_wait_for_backend", return_value=False),
            patch.object(cli, "_stop_process_group"),
        ):
            with patch.object(cli.subprocess, "Popen", side_effect=spawn):
                cli._run_dev(
                    host="127.0.0.1",
                    port=8000,
                    log_level="info",
                    reload=False,
                    frontend_dir=cli.Path("frontend"),
                    backend_url="http://127.0.0.1:8000",
                )

        self.assertEqual(
            set_signal.call_args_list,
            [
                call(cli.signal.SIGTERM, cli._request_dev_shutdown),
                call(cli.signal.SIGTERM, previous_handler),
            ],
        )

    def test_backend_readiness_rejects_a_different_server(self) -> None:
        proc = MagicMock()
        proc.poll.side_effect = [None, 1]
        response = MagicMock(status=200)
        response.headers = {cli.DEV_INSTANCE_HEADER: "other-instance"}
        context = MagicMock()
        context.__enter__.return_value = response
        with (
            patch.object(cli._DEV_HTTP_OPENER, "open", return_value=context),
            patch.object(cli.time, "sleep"),
        ):
            ready = cli._wait_for_backend(proc, "http://127.0.0.1/health", "ours")

        self.assertFalse(ready)

    def test_backend_readiness_accepts_its_instance_token(self) -> None:
        proc = MagicMock()
        proc.poll.return_value = None
        response = MagicMock(status=200)
        response.headers = {cli.DEV_INSTANCE_HEADER: "ours"}
        context = MagicMock()
        context.__enter__.return_value = response
        with patch.object(cli._DEV_HTTP_OPENER, "open", return_value=context):
            ready = cli._wait_for_backend(proc, "http://127.0.0.1/health", "ours")

        self.assertTrue(ready)

    def test_backend_readiness_opener_disables_proxies(self) -> None:
        self.assertEqual(cli._DEV_PROXY_HANDLER.proxies, {})

    def test_stop_process_group_escalates_after_timeout(self) -> None:
        proc = MagicMock(pid=1234)
        with (
            patch.object(cli, "_signal_group") as signal_group,
            patch.object(
                cli,
                "_wait_for_process_group_exit",
                side_effect=[False, True],
            ),
        ):
            cli._stop_process_group(proc)

        self.assertEqual(
            signal_group.call_args_list,
            [
                call(1234, cli.signal.SIGTERM),
                call(1234, cli.signal.SIGKILL),
            ],
        )

    def test_stop_process_group_signals_descendants_after_leader_exits(self) -> None:
        proc = MagicMock(pid=1234)
        proc.poll.return_value = 0
        with (
            patch.object(cli, "_signal_group") as signal_group,
            patch.object(cli, "_wait_for_process_group_exit", return_value=True),
        ):
            cli._stop_process_group(proc)

        signal_group.assert_called_once_with(1234, cli.signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
