"""A small supervisor that keeps MiniClaw2 running and applies source updates.

    miniclaw2 daemon start|run|stop|restart|status|logs

The supervisor depends on the standard library only — it must never import
FastAPI, uvicorn, pydantic, or any other MiniClaw2 module — so that an update
which breaks the backend's dependencies still leaves a process able to report
the failure and roll the checkout back. It is also importable by the backend
for the client half (``request``, ``last_update_run``) without side effects.

Process model::

    supervisor (flock daemon.lock, control.sock 0600)
      ├─ backend: python -m uvicorn miniclaw2.app:app   (always)
      └─ vite:    npm run dev                            (--dev only)

Children run in their own process groups and are restarted with backoff when
they exit unexpectedly; five crashes in ten minutes park the supervisor in
``crashloop`` until ``daemon restart``. The update pipeline stops the backend
before touching the checkout, because a live Python process that lazily
imports a module after the files changed would mix two versions.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


SOCKET_ENV = "MINICLAW_DAEMON_SOCKET"
TOKEN_ENV = "MINICLAW_DAEMON_TOKEN"
BOOT_ID_ENV = "MINICLAW_DAEMON_BOOT_ID"
PASSCODE_ENV = "MINICLAW_PASSCODE"

DIRECTORY = "daemon"
STATE_FILENAME = "daemon.json"
LOCK_FILENAME = "daemon.lock"
SOCKET_FILENAME = "control.sock"
RUNS_FILENAME = "update-runs.jsonl"
CURRENT_RUN_FILENAME = "update-current.json"
LOG_DIRECTORY = "logs"
LOG_ROTATE_BYTES = 10 * 1024 * 1024

STOP_GRACE_SECONDS = 20.0
HEALTH_TIMEOUT_SECONDS = 120.0
STEP_TIMEOUT_SECONDS = 15 * 60.0
POLL_SECONDS = 0.5
CRASH_WINDOW_SECONDS = 600.0
CRASH_LIMIT = 5
#: A child that stayed up this long is considered stable again.
STABLE_SECONDS = 60.0
MAX_BACKOFF_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 5.0
#: Unix socket paths are limited to about 104 bytes on macOS.
MAX_SOCKET_PATH = 100

_HTTP = build_opener(ProxyHandler({}))
logger = logging.getLogger("miniclaw2.daemon")


class DaemonError(RuntimeError):
    """A user-facing daemon failure."""


# ---------------------------------------------------------------- paths


def miniclaw_home() -> Path:
    base = os.environ.get("MINICLAW_HOME")
    return Path(base).expanduser() if base else Path.home() / ".miniclaw2"


def daemon_dir(home: Path) -> Path:
    return home / DIRECTORY


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def socket_path(home: Path) -> Path:
    preferred = daemon_dir(home) / SOCKET_FILENAME
    if len(str(preferred)) <= MAX_SOCKET_PATH:
        return preferred
    import hashlib

    digest = hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:16]
    return Path("/tmp") / f"miniclaw2-daemon-{os.getuid()}-{digest}.sock"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _open_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > LOG_ROTATE_BYTES:
            path.replace(path.with_name(path.name + ".1"))
    except OSError:
        pass
    return path.open("ab")


def process_start(pid: int) -> str | None:
    """An identity that changes when ``pid`` is reused."""
    if pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="ignore")
            boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        except OSError:
            return None
        tail = raw[raw.rfind(")") + 1:].split()
        return f"linux:{boot}:{tail[19]}" if len(tail) >= 20 else None
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return f"ps:{value}" if result.returncode == 0 and value else None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------------------------------------------------------------- client


def request(op: str, *, home: Path | None = None, timeout: float = REQUEST_TIMEOUT_SECONDS,
            **payload: Any) -> dict[str, Any]:
    """Send one request to the running supervisor.

    Inside a supervised backend the socket and token come from the
    environment; a CLI caller reads them from ``daemon.json``.
    """
    path_value = os.environ.get(SOCKET_ENV) if home is None else None
    token = os.environ.get(TOKEN_ENV) if home is None else None
    if not path_value or not token:
        state = _read_json(daemon_dir(home or miniclaw_home()) / STATE_FILENAME) or {}
        path_value = state.get("socket")
        token = state.get("token")
    if not isinstance(path_value, str) or not isinstance(token, str):
        raise DaemonError("MiniClaw2 daemon 未运行")
    message = json.dumps({"op": op, "token": token, **payload}).encode() + b"\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(path_value)
            client.sendall(message)
            chunks: list[bytes] = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if chunk.endswith(b"\n"):
                    break
    except OSError as exc:
        raise DaemonError(f"无法连接 MiniClaw2 daemon：{exc}") from exc
    try:
        response = json.loads(b"".join(chunks).decode("utf-8"))
    except ValueError as exc:
        raise DaemonError("MiniClaw2 daemon 返回了无法解析的响应") from exc
    if not isinstance(response, dict):
        raise DaemonError("MiniClaw2 daemon 返回了无法解析的响应")
    if response.get("ok") is False:
        raise DaemonError(str(response.get("error") or "daemon 拒绝了请求"))
    return response


def supervised() -> bool:
    """Whether this process is a backend started by the supervisor."""
    return bool(os.environ.get(SOCKET_ENV) and os.environ.get(TOKEN_ENV))


def last_update_run(home: Path) -> dict[str, Any] | None:
    path = daemon_dir(home) / RUNS_FILENAME
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - 65536))
            lines = stream.read().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            value = json.loads(line.decode("utf-8"))
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


# ---------------------------------------------------------------- update plan


def update_plan(paths: list[str] | tuple[str, ...] | set[str]) -> dict[str, bool]:
    """Which rebuild steps a diff between two source revisions requires."""
    changed = set(paths)
    lockfile = bool(changed & {"frontend/package.json", "frontend/package-lock.json"})
    return {
        "pip": "backend/pyproject.toml" in changed,
        "npm": lockfile,
        "frontend": any(path.startswith("frontend/") for path in changed),
        "daemon": "backend/miniclaw2/daemon.py" in changed,
    }


# ---------------------------------------------------------------- supervisor


@dataclass
class DaemonConfig:
    home: Path
    repo: Path
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "info"
    dev: bool = False
    vite_port: int = 5173
    python: str = sys.executable
    #: Overrides for tests; production derives both from the fields above.
    backend_command: list[str] | None = None
    health_timeout: float = HEALTH_TIMEOUT_SECONDS

    def argv(self) -> list[str]:
        args = ["--host", self.host, "--port", str(self.port), "--log-level", self.log_level]
        if self.dev:
            args += ["--dev", "--vite-port", str(self.vite_port)]
        return args

    @property
    def connect_host(self) -> str:
        return "127.0.0.1" if self.host in {"0.0.0.0", "::", ""} else self.host


@dataclass
class Child:
    name: str
    process: subprocess.Popen[bytes] | None = None
    started_at: float | None = None
    boot_id: str | None = None
    restarts: int = 0
    crashes: deque[float] = field(default_factory=deque)
    restart_at: float | None = None
    last_exit: int | None = None

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def payload(self) -> dict[str, Any]:
        return {
            "pid": self.process.pid if self.running() and self.process else None,
            "started_at": self.started_at if self.running() else None,
            "restarts": self.restarts,
            "last_exit": self.last_exit,
            "boot_id": self.boot_id,
        }


class UpdateFailed(Exception):
    def __init__(self, step: str, detail: str) -> None:
        super().__init__(detail)
        self.step = step
        self.detail = detail


class Supervisor:
    def __init__(self, config: DaemonConfig) -> None:
        self.config = config
        self.directory = daemon_dir(config.home)
        self.token = secrets.token_urlsafe(24)
        self.socket = socket_path(config.home)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.phase = "starting"
        self.crashloop: str | None = None
        self.backend = Child("backend")
        self.vite = Child("vite")
        self.run: dict[str, Any] | None = None
        self.started_at = time.time()
        self._server: socket.socket | None = None
        self._lock_file: Any = None
        self._passcode = os.environ.get(PASSCODE_ENV)

    # ---- lifecycle ----

    def acquire(self) -> None:
        import fcntl

        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock_file = (self.directory / LOCK_FILENAME).open("a+b")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_file.close()
            raise DaemonError("此 MINICLAW_HOME 已有一个 MiniClaw2 daemon 在运行") from exc

    def serve(self, *, resume: dict[str, Any] | None = None) -> int:
        self.acquire()
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, lambda *_: self.stop_event.set())
            signal.signal(signal.SIGINT, lambda *_: self.stop_event.set())
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
        self._start_control()
        self._write_state()
        logger.info("daemon 已启动 pid=%s home=%s repo=%s", os.getpid(), self.config.home, self.config.repo)
        try:
            if resume is not None:
                self.run = resume
                self.phase = "updating"
                threading.Thread(target=self._resume_update, daemon=True).start()
            else:
                self._start_children()
                self.phase = "running"
            self._write_state()
            while not self.stop_event.is_set():
                self._watch()
                self.stop_event.wait(POLL_SECONDS)
        finally:
            self.phase = "stopping"
            self._stop_children()
            self._close_control()
            (self.directory / STATE_FILENAME).unlink(missing_ok=True)
            if self._lock_file is not None:
                self._lock_file.close()
                self._lock_file = None
            logger.info("daemon 已停止")
        return 0

    def _write_state(self) -> None:
        _write_json(self.directory / STATE_FILENAME, {
            "pid": os.getpid(),
            "process_start": process_start(os.getpid()),
            "started_at": self.started_at,
            "socket": str(self.socket),
            "token": self.token,
            "config": {
                "host": self.config.host, "port": self.config.port, "dev": self.config.dev,
                "vite_port": self.config.vite_port, "log_level": self.config.log_level,
                "repo": str(self.config.repo),
            },
        })
        (self.directory / STATE_FILENAME).chmod(0o600)

    # ---- children ----

    def _backend_command(self) -> list[str]:
        if self.config.backend_command is not None:
            return list(self.config.backend_command)
        return [
            self.config.python, "-m", "uvicorn", "miniclaw2.app:app",
            "--host", self.config.host, "--port", str(self.config.port),
            "--log-level", self.config.log_level, "--timeout-graceful-shutdown", "10",
        ]

    def _start_backend(self) -> None:
        boot_id = uuid.uuid4().hex
        env = {
            **os.environ,
            "MINICLAW_HOME": str(self.config.home),
            SOCKET_ENV: str(self.socket),
            TOKEN_ENV: self.token,
            BOOT_ID_ENV: boot_id,
            "MINICLAW2_HOOK_PORT": str(self.config.port),
        }
        if self._passcode is not None:
            env[PASSCODE_ENV] = self._passcode
        if not self.config.dev:
            env.setdefault("MINICLAW_FRONTEND_DIST", str(self.config.repo / "frontend" / "dist"))
        log = _open_log(self.directory / LOG_DIRECTORY / "backend.log")
        try:
            process = subprocess.Popen(
                self._backend_command(), cwd=str(self.config.repo), env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            log.close()
        self.backend.process = process
        self.backend.started_at = time.time()
        self.backend.boot_id = boot_id
        self.backend.restart_at = None
        logger.info("后端已启动 pid=%s boot=%s", process.pid, boot_id)

    def _start_vite(self) -> None:
        if not self.config.dev:
            return
        backend_url = f"http://{self.config.connect_host}:{self.config.port}"
        log = _open_log(self.directory / LOG_DIRECTORY / "vite.log")
        try:
            process = subprocess.Popen(
                ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(self.config.vite_port)],
                cwd=str(self.config.repo / "frontend"),
                env={**os.environ, "MINICLAW_BACKEND_URL": backend_url, "MINICLAW_RELOAD": "1"},
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            log.close()
        self.vite.process = process
        self.vite.started_at = time.time()
        self.vite.restart_at = None
        logger.info("Vite 已启动 pid=%s port=%s", process.pid, self.config.vite_port)

    def _start_children(self) -> None:
        self._start_backend()
        self._start_vite()

    def _stop_child(self, child: Child) -> None:
        process = child.process
        if process is None:
            return
        child.process = None
        if process.poll() is None or _group_exists(process.pid):
            _signal_group(process.pid, signal.SIGTERM)
            deadline = time.monotonic() + STOP_GRACE_SECONDS
            while time.monotonic() < deadline:
                process.poll()
                if not _group_exists(process.pid):
                    break
                time.sleep(0.1)
            else:
                logger.warning("%s 未在宽限期内退出，强制结束", child.name)
                _signal_group(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        child.last_exit = process.returncode

    def _stop_children(self) -> None:
        self._stop_child(self.vite)
        self._stop_child(self.backend)

    def _watch(self) -> None:
        with self.lock:
            if self.phase != "running":
                return
            now = time.time()
            for child, start in ((self.backend, self._start_backend), (self.vite, self._start_vite)):
                if child is self.vite and not self.config.dev:
                    continue
                if child.restart_at is not None:
                    if now >= child.restart_at:
                        child.restarts += 1
                        start()
                    continue
                if child.process is None or child.process.poll() is None:
                    continue
                child.last_exit = child.process.returncode
                uptime = now - (child.started_at or now)
                child.process = None
                if uptime >= STABLE_SECONDS:
                    child.crashes.clear()
                child.crashes.append(now)
                while child.crashes and now - child.crashes[0] > CRASH_WINDOW_SECONDS:
                    child.crashes.popleft()
                if len(child.crashes) >= CRASH_LIMIT:
                    self.crashloop = (
                        f"{child.name} 在 {CRASH_WINDOW_SECONDS / 60:g} 分钟内退出 {len(child.crashes)} 次"
                        f"（最近退出码 {child.last_exit}）；已停止自动重启，修复后运行 miniclaw2 daemon restart"
                    )
                    self.phase = "crashloop"
                    logger.error("%s", self.crashloop)
                    return
                delay = min(MAX_BACKOFF_SECONDS, 2 ** (len(child.crashes) - 1))
                child.restart_at = now + delay
                logger.warning("%s 意外退出（退出码 %s），%s 秒后重启", child.name, child.last_exit, delay)

    def restart(self) -> None:
        with self.lock:
            if self.phase == "updating":
                raise DaemonError("正在更新，稍后再重启")
            self.phase = "restarting"
        self._stop_children()
        with self.lock:
            self.crashloop = None
            self.backend.crashes.clear()
            self.vite.crashes.clear()
            self._start_children()
            self.phase = "running"

    # ---- control socket ----

    def _start_control(self) -> None:
        self.socket.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous = os.umask(0o177)
        try:
            server.bind(str(self.socket))
        finally:
            os.umask(previous)
        os.chmod(self.socket, 0o600)
        server.listen(8)
        server.settimeout(0.5)
        self._server = server
        threading.Thread(target=self._accept, daemon=True).start()

    def _close_control(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
        self.socket.unlink(missing_ok=True)

    def _accept(self) -> None:
        while self._server is not None and not self.stop_event.is_set():
            server = self._server
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(connection,), daemon=True).start()

    def _handle(self, connection: socket.socket) -> None:
        with connection:
            connection.settimeout(REQUEST_TIMEOUT_SECONDS)
            data = b""
            try:
                while not data.endswith(b"\n") and len(data) < 65536:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                message = json.loads(data.decode("utf-8"))
                if not isinstance(message, dict) or not secrets.compare_digest(
                    str(message.get("token", "")), self.token,
                ):
                    response: dict[str, Any] = {"ok": False, "error": "unauthorized"}
                else:
                    response = self.dispatch(message)
            except (OSError, ValueError) as exc:
                response = {"ok": False, "error": str(exc)}
            except DaemonError as exc:
                response = {"ok": False, "error": str(exc)}
            try:
                connection.sendall(json.dumps(response, ensure_ascii=False).encode() + b"\n")
            except OSError:
                pass

    def dispatch(self, message: dict[str, Any]) -> dict[str, Any]:
        op = message.get("op")
        if op == "status":
            return {"ok": True, **self.snapshot()}
        if op == "update":
            target = message.get("target")
            if not isinstance(target, str) or not target:
                raise DaemonError("缺少更新目标")
            return {"ok": True, "update_id": self.begin_update(target, running_head=message.get("running_head"))}
        if op == "restart":
            threading.Thread(target=self._restart_quietly, daemon=True).start()
            return {"ok": True}
        if op == "stop":
            self.stop_event.set()
            return {"ok": True}
        raise DaemonError(f"未知操作：{op}")

    def _restart_quietly(self) -> None:
        try:
            self.restart()
        except DaemonError as exc:
            logger.warning("重启被拒绝：%s", exc)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "pid": os.getpid(),
                "started_at": self.started_at,
                "phase": self.phase,
                "crashloop": self.crashloop,
                "config": {
                    "host": self.config.host, "port": self.config.port,
                    "dev": self.config.dev, "vite_port": self.config.vite_port,
                },
                "repo": str(self.config.repo),
                "head": _git_output(self.config.repo, "rev-parse", "HEAD"),
                "children": {"backend": self.backend.payload(), "vite": self.vite.payload()},
                "update": self.run,
            }

    # ---- update pipeline ----

    def begin_update(self, target: str, *, running_head: str | None = None) -> str:
        with self.lock:
            if self.phase != "running":
                raise DaemonError(f"daemon 当前状态为 {self.phase}，无法开始更新")
            run_id = uuid.uuid4().hex[:12]
            self.run = {
                "id": run_id, "target": target, "old_head": None, "new_head": None,
                "running_head": running_head,
                "status": "running", "phase": "verify", "started_at": time.time(),
                "finished_at": None, "error": None, "plan": None, "steps": [],
                "schema_before": None, "dev": self.config.dev,
            }
            self.phase = "updating"
        threading.Thread(target=self._update, daemon=True).start()
        return run_id

    def _step(self, name: str, action: Callable[[], str | None]) -> None:
        assert self.run is not None
        with self.lock:
            self.run["phase"] = name
            record = {"step": name, "started_at": time.time(), "finished_at": None, "ok": None, "detail": None}
            self.run["steps"].append(record)
        _write_json(self.directory / CURRENT_RUN_FILENAME, self.run)
        logger.info("更新步骤：%s", name)
        try:
            detail = action()
        except UpdateFailed as exc:
            record.update(finished_at=time.time(), ok=False, detail=exc.detail)
            raise
        except Exception as exc:  # noqa: BLE001
            record.update(finished_at=time.time(), ok=False, detail=str(exc))
            raise UpdateFailed(name, str(exc)) from exc
        record.update(finished_at=time.time(), ok=True, detail=detail)

    def _update(self) -> None:
        assert self.run is not None
        run = self.run
        repo = self.config.repo
        schema = self.config.home / "schema.json"
        try:
            run["schema_before"] = schema.read_text(encoding="utf-8") if schema.exists() else None
        except OSError:
            run["schema_before"] = None

        def verify() -> str:
            head = _git_output(repo, "rev-parse", "HEAD")
            if head is None:
                raise UpdateFailed("verify", "无法读取当前源码版本")
            run["old_head"] = head
            target = _git_output(repo, "rev-parse", "--verify", "--quiet", f"{run['target']}^{{commit}}")
            if target is None:
                raise UpdateFailed("verify", f"本地没有目标提交 {run['target'][:12]}，请先检查远端")
            run["target"] = target
            if _git(repo, "merge-base", "--is-ancestor", "HEAD", target).returncode != 0:
                raise UpdateFailed("verify", "目标版本无法从当前版本快进")
            if _git_output(repo, "status", "--porcelain", "--untracked-files=all"):
                raise UpdateFailed("verify", "源码工作区有未提交改动")
            ahead = _git_output(repo, "rev-list", "--count", "@{upstream}..HEAD")
            if target != head and ahead not in (None, "0"):
                raise UpdateFailed("verify", "源码存在尚未推送的本地提交")
            return f"{head[:8]} → {target[:8]}"

        def plan() -> str:
            base = run.get("running_head") or run["old_head"]
            changed = _git_output(repo, "diff", "--name-only", base, run["target"])
            if changed is None:
                raise UpdateFailed("plan", "无法比较运行版本与目标版本")
            run["plan"] = update_plan([line for line in changed.splitlines() if line.strip()])
            return ", ".join(key for key, needed in run["plan"].items() if needed) or "无需安装或构建"

        try:
            self._step("verify", verify)
            self._step("plan", plan)
            self._step("stop", self._stop_for_update)
            self._step("checkout", lambda: self._checkout(run["target"]))
            self._apply_rebuild(run)
            if run["plan"]["daemon"]:
                self._step("self", self._prepare_reload)
                self._reload(run)
                return
            self._finish_start(run)
        except UpdateFailed as failure:
            self._recover(run, failure)

    def _resume_update(self) -> None:
        """Continue a run after ``os.execv`` replaced the supervisor."""
        assert self.run is not None
        try:
            self._finish_start(self.run)
        except UpdateFailed as failure:
            self._recover(self.run, failure)

    def _finish_start(self, run: dict[str, Any]) -> None:
        self._step("start", self._start_after_update)
        self._step("health", lambda: self._await_health(run["target"]))
        self._step("finish", lambda: self._cleanup_dist_backup())
        run["new_head"] = run["target"]
        self._complete(run, "succeeded")

    def _stop_for_update(self) -> str:
        self._stop_child(self.backend)
        assert self.run is not None
        if self.config.dev and (self.run["plan"]["npm"] or self.run["plan"]["daemon"]):
            self._stop_child(self.vite)
        return "后端已停止"

    def _checkout(self, target: str) -> str:
        merged = _git(self.config.repo, "merge", "--ff-only", target)
        if merged.returncode != 0:
            raise UpdateFailed("checkout", (merged.stderr or merged.stdout).strip() or "git merge --ff-only 失败")
        return target[:12]

    def _apply_rebuild(self, run: dict[str, Any]) -> None:
        plan = run["plan"]
        if plan["pip"]:
            self._step("pip", lambda: self._command("pip", [self.config.python, "-m", "pip", "install", "-e", "backend"], self.config.repo))
        if plan["npm"]:
            frontend = self.config.repo / "frontend"
            command = ["npm", "ci"] if (frontend / "package-lock.json").exists() else ["npm", "install"]
            self._step("npm", lambda: self._command("npm", command, frontend))
        if plan["frontend"] and not self.config.dev:
            self._step("build", self._build)

    def _build(self) -> str:
        frontend = self.config.repo / "frontend"
        dist, previous = frontend / "dist", frontend / "dist.prev"
        if previous.exists():
            shutil.rmtree(previous)
        if dist.exists():
            dist.replace(previous)
        try:
            return self._command("build", ["npm", "run", "build"], frontend)
        except UpdateFailed:
            self._restore_dist()
            raise

    def _restore_dist(self) -> None:
        frontend = self.config.repo / "frontend"
        dist, previous = frontend / "dist", frontend / "dist.prev"
        if previous.exists():
            if dist.exists():
                shutil.rmtree(dist)
            previous.replace(dist)

    def _cleanup_dist_backup(self) -> str | None:
        previous = self.config.repo / "frontend" / "dist.prev"
        if previous.exists():
            shutil.rmtree(previous, ignore_errors=True)
        return None

    def _command(self, step: str, command: list[str], cwd: Path) -> str:
        log_path = self.directory / LOG_DIRECTORY / "update.log"
        with _open_log(log_path) as log:
            log.write(f"\n$ ({cwd}) {' '.join(command)}\n".encode())
            log.flush()
            try:
                result = subprocess.run(
                    command, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, timeout=STEP_TIMEOUT_SECONDS, check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise UpdateFailed(step, f"{' '.join(command)} 无法执行：{exc}") from exc
        if result.returncode != 0:
            raise UpdateFailed(step, f"{' '.join(command)} 退出码 {result.returncode}：{_tail(log_path)}")
        return " ".join(command)

    def _prepare_reload(self) -> str:
        probe = subprocess.run(
            [self.config.python, "-c", "import miniclaw2.daemon"],
            cwd="/", capture_output=True, text=True, timeout=60, check=False,
        )
        if probe.returncode != 0:
            raise UpdateFailed("self", f"新版本 daemon 无法导入：{(probe.stderr or probe.stdout).strip()[-800:]}")
        self._stop_child(self.vite)
        return "重新加载 daemon"

    def _reload(self, run: dict[str, Any]) -> None:
        _write_json(self.directory / CURRENT_RUN_FILENAME, run)
        logger.info("重新加载 daemon（pid 不变）")
        self._close_control()
        for handler in logging.getLogger().handlers:
            handler.flush()
        argv = [self.config.python, "-m", "miniclaw2.daemon", "run", *self.config.argv(), "--resume-update"]
        os.execv(self.config.python, argv)

    def _start_after_update(self) -> str:
        with self.lock:
            self._start_backend()
            if self.config.dev and not self.vite.running():
                self._start_vite()
        return f"pid {self.backend.process.pid if self.backend.process else '?'}"

    def _await_health(self, expected_head: str) -> str:
        url = f"http://{self.config.connect_host}:{self.config.port}/health"
        deadline = time.monotonic() + self.config.health_timeout
        last = "后端尚未响应"
        while time.monotonic() < deadline:
            process = self.backend.process
            if process is None or process.poll() is not None:
                code = process.returncode if process is not None else None
                raise UpdateFailed("health", f"新后端启动后退出（退出码 {code}）：{_tail(self.directory / LOG_DIRECTORY / 'backend.log')}")
            try:
                with _HTTP.open(url, timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (OSError, URLError, ValueError) as exc:
                last = str(exc)
            else:
                if payload.get("boot_id") == self.backend.boot_id:
                    if payload.get("head") != expected_head:
                        raise UpdateFailed("health", f"新后端报告的版本 {str(payload.get('head'))[:12]} 不是目标版本")
                    if payload.get("status") in {"ok", "maintenance"}:
                        return f"status={payload.get('status')}"
                    last = f"status={payload.get('status')}"
                else:
                    last = "端口上应答的不是本次启动的后端"
            time.sleep(POLL_SECONDS)
        raise UpdateFailed("health", f"等待新后端就绪超时：{last}")

    def _store_migrated(self, run: dict[str, Any]) -> bool:
        schema = self.config.home / "schema.json"
        try:
            current = schema.read_text(encoding="utf-8") if schema.exists() else None
        except OSError:
            return True
        return current != run.get("schema_before")

    def _recover(self, run: dict[str, Any], failure: UpdateFailed) -> None:
        logger.error("更新失败于 %s：%s", failure.step, failure.detail)
        run["error"] = f"{failure.step}：{failure.detail}"
        old_head = run.get("old_head")
        moved = old_head is not None and _git_output(self.config.repo, "rev-parse", "HEAD") != old_head
        if failure.step in {"verify", "plan"} or (failure.step == "stop" and not moved):
            with self.lock:
                if not self.backend.running():
                    self._start_backend()
                if self.config.dev and not self.vite.running():
                    self._start_vite()
            self._complete(run, "rejected")
            return
        if self._store_migrated(run):
            # The new version already rewrote the store; old code would only
            # open it read-only, so keep the new code and report instead.
            with self.lock:
                if not self.backend.running():
                    self._start_backend()
            self._complete(run, "failed")
            return
        try:
            self._stop_child(self.backend)
            if moved:
                reset = _git(self.config.repo, "reset", "--keep", old_head)
                if reset.returncode != 0:
                    raise UpdateFailed("rollback", (reset.stderr or reset.stdout).strip())
            self._restore_dist()
            plan = run.get("plan") or {}
            if plan.get("pip"):
                self._command("rollback", [self.config.python, "-m", "pip", "install", "-e", "backend"], self.config.repo)
            if plan.get("npm"):
                frontend = self.config.repo / "frontend"
                command = ["npm", "ci"] if (frontend / "package-lock.json").exists() else ["npm", "install"]
                self._command("rollback", command, frontend)
            with self.lock:
                self._start_backend()
                if self.config.dev and not self.vite.running():
                    self._start_vite()
            run["new_head"] = old_head
            self._complete(run, "rolled_back")
        except UpdateFailed as rollback:
            run["error"] = f"{run['error']}；回滚也失败：{rollback.detail}"
            with self.lock:
                if not self.backend.running():
                    self._start_backend()
            self._complete(run, "failed")

    def _complete(self, run: dict[str, Any], status: str) -> None:
        with self.lock:
            run["status"] = status
            run["phase"] = "done"
            run["finished_at"] = time.time()
            run.pop("schema_before", None)
            self.backend.crashes.clear()
            self.phase = "running"
        path = self.directory / RUNS_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(run, ensure_ascii=False) + "\n")
        (self.directory / CURRENT_RUN_FILENAME).unlink(missing_ok=True)
        logger.info("更新结束：%s", status)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, check=False,
    )


def _git_output(repo: Path, *args: str) -> str | None:
    try:
        result = _git(repo, *args)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _tail(path: Path, lines: int = 12) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 8192))
            text = stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _signal_group(pgid: int, signum: int) -> None:
    try:
        os.killpg(pgid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------------------------------------------------------------- CLI


def _configure_logging(home: Path, *, foreground: bool) -> None:
    path = daemon_dir(home) / LOG_DIRECTORY / "daemon.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(path, maxBytes=LOG_ROTATE_BYTES, backupCount=1, encoding="utf-8"),
    ]
    if foreground:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers, force=True)


def _config(args: argparse.Namespace) -> DaemonConfig:
    return DaemonConfig(
        home=miniclaw_home().expanduser().resolve(), repo=repo_root(),
        host=args.host, port=args.port, log_level=args.log_level,
        dev=args.dev, vite_port=args.vite_port,
    )


def _running_state(home: Path) -> dict[str, Any] | None:
    state = _read_json(daemon_dir(home) / STATE_FILENAME)
    if state is None:
        return None
    pid = state.get("pid")
    if type(pid) is not int or not _alive(pid):
        return None
    recorded = state.get("process_start")
    if recorded is not None and process_start(pid) != recorded:
        return None
    return state


def _preflight(config: DaemonConfig) -> None:
    if sys.platform == "win32":
        raise DaemonError("daemon 模式暂不支持 Windows，请使用 python -m miniclaw2")
    if config.dev:
        if shutil.which("npm") is None:
            raise DaemonError("--dev 需要 npm，请先安装 Node.js")
        if not (config.repo / "frontend" / "node_modules").is_dir():
            raise DaemonError("frontend/node_modules 不存在，请先在 frontend/ 运行 npm install")
        if config.port == config.vite_port:
            raise DaemonError("后端端口不能与 Vite 端口相同")
    elif not (config.repo / "frontend" / "dist" / "index.html").is_file():
        raise DaemonError("frontend/dist 尚未构建，请先在 frontend/ 运行 npm run build")


def _wait_until_serving(home: Path, process: subprocess.Popen[bytes], timeout: float = 90.0) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return None
        try:
            status = request("status", home=home, timeout=2)
        except DaemonError:
            time.sleep(0.5)
            continue
        backend = status.get("children", {}).get("backend", {})
        config = status.get("config", {})
        host = "127.0.0.1" if config.get("host") in {"0.0.0.0", "::", ""} else config.get("host")
        try:
            with _HTTP.open(f"http://{host}:{config.get('port')}/health", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("boot_id") == backend.get("boot_id"):
                return status
        except (OSError, URLError, ValueError):
            pass
        if status.get("phase") == "crashloop":
            return status
        time.sleep(0.5)
    return None


def _print_status(status: dict[str, Any]) -> None:
    config = status.get("config", {})
    print(f"daemon:   pid {status.get('pid')} · {status.get('phase')}")
    print(f"源码:     {str(status.get('head') or '?')[:12]} · {status.get('repo')}")
    for name in ("backend", "vite"):
        child = status.get("children", {}).get(name) or {}
        if name == "vite" and not config.get("dev"):
            continue
        uptime = f" · 已运行 {int(time.time() - child['started_at'])} 秒" if child.get("started_at") else ""
        print(f"{name:<9} pid {child.get('pid') or '—'}{uptime} · 重启 {child.get('restarts', 0)} 次")
    host = config.get("host")
    print(f"地址:     http://{'127.0.0.1' if host in {'0.0.0.0', '::'} else host}:{config.get('dev') and config.get('vite_port') or config.get('port')}")
    if status.get("crashloop"):
        print(f"注意:     {status['crashloop']}")
    run = status.get("update")
    if run:
        print(f"最近更新: {run.get('status')} · {str(run.get('old_head') or '')[:8]} → {str(run.get('target') or '')[:8]}"
              + (f" · {run.get('error')}" if run.get("error") else ""))


def _logs(home: Path, which: str, lines: int, follow: bool) -> None:
    path = daemon_dir(home) / LOG_DIRECTORY / f"{which}.log"
    if not path.exists():
        print(f"{path} 不存在")
        return
    print(_tail(path, lines))
    if not follow:
        return
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        try:
            while True:
                chunk = stream.read()
                if chunk:
                    sys.stdout.write(chunk.decode("utf-8", errors="replace"))
                    sys.stdout.flush()
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="miniclaw2 daemon",
        description="后台守护 MiniClaw2 后端（--dev 时还有 Vite），并执行一键更新与失败回滚。",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("start", "run"):
        command = sub.add_parser(name, help="后台启动" if name == "start" else "前台运行（供 launchd/systemd/tmux）")
        command.add_argument("--host", default="127.0.0.1")
        command.add_argument("--port", type=int, default=8000)
        command.add_argument("--log-level", default="info")
        command.add_argument("--dev", action="store_true", help="同时守护 Vite 开发服务器")
        command.add_argument("--vite-port", type=int, default=5173)
        command.add_argument("--passcode", default=None, help="网页四位密码；也可用 MINICLAW_PASSCODE 提供")
        if name == "run":
            command.add_argument("--resume-update", action="store_true", help=argparse.SUPPRESS)
    sub.add_parser("stop", help="停止 daemon 及其子进程")
    sub.add_parser("restart", help="重启子进程（也用于解除 crashloop）")
    sub.add_parser("status", help="查看状态")
    logs = sub.add_parser("logs", help="查看日志")
    logs.add_argument("which", nargs="?", default="backend", choices=["backend", "vite", "daemon", "update"])
    logs.add_argument("-n", type=int, default=60)
    logs.add_argument("-f", "--follow", action="store_true")
    args = parser.parse_args(argv)
    home = miniclaw_home().expanduser().resolve()
    try:
        if args.action in {"start", "run"}:
            if args.passcode is not None:
                if len(args.passcode) != 4 or not args.passcode.isdigit():
                    parser.error("--passcode 必须是四位数字")
                os.environ[PASSCODE_ENV] = args.passcode
            config = _config(args)
            if args.action == "run":
                resume = None
                if getattr(args, "resume_update", False):
                    resume = _read_json(daemon_dir(home) / CURRENT_RUN_FILENAME)
                else:
                    _preflight(config)
                _configure_logging(home, foreground=True)
                return Supervisor(config).serve(resume=resume)
            if _running_state(home) is not None:
                raise DaemonError("daemon 已在运行；用 miniclaw2 daemon status 查看")
            _preflight(config)
            out = _open_log(daemon_dir(home) / LOG_DIRECTORY / "daemon.out")
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "miniclaw2.daemon", "run", *config.argv()],
                    stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                    start_new_session=True, cwd="/",
                    env={**os.environ, "MINICLAW_HOME": str(home)},
                )
            finally:
                out.close()
            print("正在启动 MiniClaw2 daemon…", flush=True)
            status = _wait_until_serving(home, process)
            if status is None:
                print(_tail(daemon_dir(home) / LOG_DIRECTORY / "daemon.out"), file=sys.stderr)
                print(_tail(daemon_dir(home) / LOG_DIRECTORY / "backend.log"), file=sys.stderr)
                raise DaemonError("daemon 未能在限定时间内就绪；用 miniclaw2 daemon logs 查看原因")
            _print_status(status)
            return 0
        if args.action == "logs":
            _logs(home, args.which, args.n, args.follow)
            return 0
        if args.action == "status":
            try:
                _print_status(request("status", home=home))
            except DaemonError:
                print("daemon 未运行")
                last = last_update_run(home)
                if last:
                    print(f"最近更新: {last.get('status')} · {last.get('error') or ''}")
                return 1
            return 0
        if args.action == "restart":
            request("restart", home=home)
            print("已请求重启子进程")
            return 0
        if args.action == "stop":
            state = _running_state(home)
            if state is None:
                print("daemon 未运行")
                return 0
            try:
                request("stop", home=home)
            except DaemonError:
                os.kill(int(state["pid"]), signal.SIGTERM)
            deadline = time.monotonic() + STOP_GRACE_SECONDS * 2 + 10
            while time.monotonic() < deadline and _alive(int(state["pid"])):
                time.sleep(0.3)
            print("daemon 已停止" if not _alive(int(state["pid"])) else "daemon 仍在退出中")
            return 0
    except DaemonError as exc:
        print(f"miniclaw2 daemon: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
