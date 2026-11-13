"""The daemon's update pipeline against a real checkout and a stub backend."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from miniclaw2 import daemon


STUB_BACKEND = r'''
import json, os, subprocess, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

repo = sys.argv[2]
head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
if os.path.exists(os.path.join(repo, "crash")):
    sys.exit(3)
if os.path.exists(os.path.join(repo, "migrate")):
    with open(os.path.join(os.environ["MINICLAW_HOME"], "schema.json"), "w") as stream:
        stream.write('{"schema_version": 99}')
    sys.exit(4)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok", "head": head, "boot_id": os.environ["MINICLAW_DAEMON_BOOT_ID"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass

HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
'''


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@unittest.skipIf(sys.platform == "win32", "daemon mode is POSIX-only")
class DaemonUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="mcd-")
        base = Path(self.temp.name)
        self.home = base / "h"
        self.home.mkdir()
        (self.home / "schema.json").write_text('{"schema_version": 1}')
        self.remote = base / "remote.git"
        _git("init", "--bare", "-b", "main", str(self.remote), cwd=base)
        self.upstream = base / "upstream"
        _git("clone", str(self.remote), str(self.upstream), cwd=base)
        for repo in (self.upstream,):
            _git("config", "user.name", "t", cwd=repo)
            _git("config", "user.email", "t@t", cwd=repo)
        (self.upstream / "stub.py").write_text(STUB_BACKEND)
        _git("add", "stub.py", cwd=self.upstream)
        _git("commit", "-m", "initial", cwd=self.upstream)
        _git("push", "-u", "origin", "HEAD:main", cwd=self.upstream)
        self.repo = base / "repo"
        _git("clone", str(self.remote), str(self.repo), cwd=base)
        self.port = _free_port()
        self.config = daemon.DaemonConfig(
            home=self.home, repo=self.repo, port=self.port,
            backend_command=[sys.executable, str(self.repo / "stub.py"), str(self.port), str(self.repo)],
            health_timeout=10,
        )
        self.supervisor = daemon.Supervisor(self.config)
        self.thread = threading.Thread(target=self.supervisor.serve, daemon=True)
        self.thread.start()
        self.wait(lambda: self.supervisor.phase == "running" and self.healthy())

    def tearDown(self) -> None:
        self.supervisor.stop_event.set()
        self.thread.join(30)
        self.temp.cleanup()

    def healthy(self) -> bool:
        try:
            with daemon._HTTP.open(f"http://127.0.0.1:{self.port}/health", timeout=1) as response:
                return json.loads(response.read()).get("boot_id") == self.supervisor.backend.boot_id
        except OSError:
            return False

    def wait(self, predicate, timeout: float = 20) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        self.fail("condition not reached")

    def publish(self, filename: str, content: str = "x\n") -> str:
        (self.upstream / filename).write_text(content)
        _git("add", filename, cwd=self.upstream)
        _git("commit", "-m", filename, cwd=self.upstream)
        _git("push", cwd=self.upstream)
        _git("fetch", cwd=self.repo)
        return _git("rev-parse", "HEAD", cwd=self.upstream)

    def update(self, target: str) -> dict:
        response = daemon.request("update", home=self.home, target=target)
        update_id = response["update_id"]
        self.wait(lambda: (self.supervisor.run or {}).get("id") == update_id
                  and self.supervisor.run.get("status") != "running", timeout=40)
        return self.supervisor.run

    def test_status_over_the_control_socket(self) -> None:
        status = daemon.request("status", home=self.home)
        self.assertEqual(status["phase"], "running")
        self.assertEqual(status["head"], _git("rev-parse", "HEAD", cwd=self.repo))
        self.assertEqual(oct((daemon.socket_path(self.home)).stat().st_mode & 0o777), "0o600")
        with self.assertRaises(daemon.DaemonError):
            state = json.loads((self.home / "daemon" / "daemon.json").read_text())
            state["token"] = "wrong"
            (self.home / "daemon" / "daemon.json").write_text(json.dumps(state))
            daemon.request("status", home=self.home)

    def test_successful_update_restarts_on_the_new_head(self) -> None:
        old_boot = self.supervisor.backend.boot_id
        target = self.publish("feature.txt")
        run = self.update(target)
        self.assertEqual(run["status"], "succeeded", run)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=self.repo), target)
        self.assertNotEqual(self.supervisor.backend.boot_id, old_boot)
        self.assertTrue(self.healthy())
        self.assertEqual(daemon.last_update_run(self.home)["status"], "succeeded")

    def test_failed_start_rolls_back_to_the_old_head(self) -> None:
        old = _git("rev-parse", "HEAD", cwd=self.repo)
        target = self.publish("crash")
        run = self.update(target)
        self.assertEqual(run["status"], "rolled_back", run)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=self.repo), old)
        self.wait(self.healthy)

    def test_no_rollback_once_the_store_was_migrated(self) -> None:
        target = self.publish("migrate")
        run = self.update(target)
        self.assertEqual(run["status"], "failed", run)
        self.assertEqual(_git("rev-parse", "HEAD", cwd=self.repo), target)

    def test_dirty_checkout_is_rejected_and_backend_keeps_running(self) -> None:
        boot = self.supervisor.backend.boot_id
        target = self.publish("feature.txt")
        (self.repo / "local-edit.txt").write_text("mine\n")
        run = self.update(target)
        self.assertEqual(run["status"], "rejected", run)
        self.assertIn("未提交", run["error"])
        self.assertEqual(self.supervisor.backend.boot_id, boot)

    def test_unexpected_exit_is_restarted(self) -> None:
        process = self.supervisor.backend.process
        assert process is not None
        os.killpg(process.pid, 9)
        self.wait(lambda: self.supervisor.backend.restarts == 1 and self.healthy())

    def test_update_plan(self) -> None:
        self.assertEqual(
            daemon.update_plan(["backend/pyproject.toml", "frontend/src/App.tsx"]),
            {"pip": True, "npm": False, "frontend": True, "daemon": False},
        )
        self.assertTrue(daemon.update_plan(["frontend/package-lock.json"])["npm"])
        self.assertTrue(daemon.update_plan(["backend/miniclaw2/daemon.py"])["daemon"])


class DaemonImportBoundaryTests(unittest.TestCase):
    def test_supervisor_imports_only_the_standard_library(self) -> None:
        script = (
            "import sys, miniclaw2.daemon\n"
            "heavy = {'fastapi', 'uvicorn', 'pydantic', 'yaml', 'starlette', 'miniclaw2.app', 'miniclaw2.store'}\n"
            "print(sorted(heavy & set(sys.modules)))\n"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "[]")


if __name__ == "__main__":
    unittest.main()
