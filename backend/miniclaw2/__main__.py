"""Run with `python -m miniclaw2` or the `miniclaw2` script."""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

import uvicorn

from .global_config import (
    SyncSettings,
    load_global_config,
    miniclaw_home,
    save_global_config,
)
from .store import Store
from .sync import (
    SyncError,
    bootstrap_store,
    ensure_machine_identity,
    resolve_machine_copy,
    resolve_machine_rename,
)

VITE_HOST = "127.0.0.1"
VITE_PORT = 5173
DEV_POLL_INTERVAL_SECONDS = 0.1
DEV_HTTP_TIMEOUT_SECONDS = 0.25
DEV_STOP_TIMEOUT_SECONDS = 2.0
DEV_INSTANCE_ENV = "MINICLAW_DEV_INSTANCE_TOKEN"
DEV_INSTANCE_HEADER = "X-MiniClaw-Dev-Instance"
_DEV_PROXY_HANDLER = ProxyHandler({})
_DEV_HTTP_OPENER = build_opener(_DEV_PROXY_HANDLER)


class _DevShutdown(BaseException):
    """Interrupt the supervisor loop so its finally block can reap children."""


def _request_dev_shutdown(_signum: int, _frame: object) -> None:
    raise _DevShutdown


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "migrations":
        from .migrations.cli import main as migrations_main

        migrations_main(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "machine":
        _machine_cli(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        _sync_cli(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(
        prog="miniclaw2",
        epilog="设备身份管理：miniclaw2 machine {rename,copy} [--label 名称]",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Reload on source changes (backend and, with --dev, frontend).",
    )
    parser.add_argument("--log-level", default="info")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Spawn the Vite dev server alongside the backend at :5173.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level.upper())
    try:
        ensure_machine_identity(miniclaw_home())
    except (SyncError, OSError) as exc:
        parser.exit(1, f"设备身份检查失败：{exc}\n")
    # Broadcast the port to child processes (claude hook bridge reads
    # it via MINICLAW_HOOK_URL and MINICLAW_HOOK_TOKEN from its env at
    # spawn time; keeping this here lets the app compute the URL before
    # any spawn happens).
    os.environ["MINICLAW2_HOOK_PORT"] = str(args.port)

    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    if args.dev:
        if args.port == VITE_PORT:
            parser.error(
                f"--port {VITE_PORT} collides with the Vite dev server; "
                "pick a different backend port"
            )
        if shutil.which("npm") is None:
            sys.exit("npm not found on PATH; install Node.js to use --dev")
        if not (frontend_dir / "node_modules").is_dir():
            sys.exit(
                f"{frontend_dir / 'node_modules'} missing; "
                "run `npm install` in frontend/"
            )
        # 0.0.0.0 means "listen on all interfaces" — not a valid connect
        # target, so the proxy has to dial 127.0.0.1 instead.
        proxy_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
        backend_url = f"http://{proxy_host}:{args.port}"
        exit_code = _run_dev(
            host=args.host,
            port=args.port,
            log_level=args.log_level,
            reload=args.reload,
            frontend_dir=frontend_dir,
            backend_url=backend_url,
        )
        if exit_code:
            raise SystemExit(exit_code)
        return

    # Prod: FastAPI serves the built frontend from the same origin.
    # __main__ is the only writer of MINICLAW_FRONTEND_DIST — tests
    # never invoke this module, so the app factory's mount stays
    # inert under pytest. Users can override via env for installed
    # (non-editable) layouts.
    os.environ.setdefault(
        "MINICLAW_FRONTEND_DIST", str(frontend_dir / "dist")
    )

    uvicorn.run(
        "miniclaw2.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


def _run_dev(
    *,
    host: str,
    port: int,
    log_level: str,
    reload: bool,
    frontend_dir: Path,
    backend_url: str,
) -> int:
    """Supervise backend and frontend without exposing a half-ready proxy."""
    backend_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "miniclaw2.app:app",
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        log_level,
    ]
    if reload:
        backend_command.append("--reload")

    backend_proc: subprocess.Popen[bytes] | None = None
    vite_proc: subprocess.Popen[bytes] | None = None
    previous_sigterm = signal.signal(signal.SIGTERM, _request_dev_shutdown)
    try:
        instance_token = secrets.token_urlsafe(24)
        print(f"backend:            http://{host}:{port}", flush=True)
        backend_proc = subprocess.Popen(
            backend_command,
            start_new_session=True,
            env={**os.environ, DEV_INSTANCE_ENV: instance_token},
        )
        if not _wait_for_backend(
            backend_proc,
            f"{backend_url}/health",
            instance_token,
        ):
            return backend_proc.returncode or 1

        frontend_mode = "Vite HMR" if reload else "Vite, reload off"
        print(
            f"frontend ({frontend_mode}): http://{VITE_HOST}:{VITE_PORT}",
            flush=True,
        )
        vite_proc = subprocess.Popen(
            [
                "npm",
                "run",
                "dev",
                "--",
                "--host",
                VITE_HOST,
                "--port",
                str(VITE_PORT),
            ],
            cwd=str(frontend_dir),
            start_new_session=True,
            env={
                **os.environ,
                "MINICLAW_BACKEND_URL": backend_url,
                "MINICLAW_RELOAD": "1" if reload else "0",
            },
        )
        return _wait_for_dev_exit(backend_proc, vite_proc)
    except (KeyboardInterrupt, _DevShutdown):
        return 0
    finally:
        try:
            _stop_process_group(vite_proc)
            _stop_process_group(backend_proc)
        except (KeyboardInterrupt, _DevShutdown):
            for proc in (vite_proc, backend_proc):
                if proc is not None:
                    _signal_group(proc.pid, signal.SIGKILL)
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)


def _wait_for_backend(
    proc: subprocess.Popen[bytes],
    health_url: str,
    instance_token: str,
) -> bool:
    while proc.poll() is None:
        try:
            with _DEV_HTTP_OPENER.open(
                health_url,
                timeout=DEV_HTTP_TIMEOUT_SECONDS,
            ) as response:
                if (
                    response.status < 500
                    and response.headers.get(DEV_INSTANCE_HEADER) == instance_token
                    and proc.poll() is None
                ):
                    return True
        except (OSError, URLError):
            pass
        time.sleep(DEV_POLL_INTERVAL_SECONDS)
    return False


def _wait_for_dev_exit(
    backend_proc: subprocess.Popen[bytes],
    vite_proc: subprocess.Popen[bytes],
) -> int:
    while True:
        backend_code = backend_proc.poll()
        if backend_code is not None:
            return backend_code
        vite_code = vite_proc.poll()
        if vite_code is not None:
            return vite_code
        time.sleep(DEV_POLL_INTERVAL_SECONDS)


def _stop_process_group(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None:
        return
    _signal_group(proc.pid, signal.SIGTERM)
    if not _wait_for_process_group_exit(proc, DEV_STOP_TIMEOUT_SECONDS):
        _signal_group(proc.pid, signal.SIGKILL)
        _wait_for_process_group_exit(proc, DEV_STOP_TIMEOUT_SECONDS)
    proc.poll()


def _wait_for_process_group_exit(
    proc: subprocess.Popen[bytes],
    timeout: float,
) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        proc.poll()
        if not _process_group_exists(proc.pid):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(DEV_POLL_INTERVAL_SECONDS, remaining))


def _process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _signal_group(pgid: int, signum: int) -> None:
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        pass


def _machine_cli(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="miniclaw2 machine",
        description="请先停止使用此 Store 的进程。rename 确认同一设备；copy 创建新设备身份，不继承原设备的本地绑定。",
    )
    parser.add_argument("action", choices=["rename", "copy"])
    parser.add_argument("--label", help="可选的设备显示名称")
    args = parser.parse_args(argv)
    resolve = resolve_machine_rename if args.action == "rename" else resolve_machine_copy
    try:
        identity = resolve(miniclaw_home(), label=args.label)
    except (SyncError, OSError) as exc:
        parser.exit(1, f"设备身份更新失败：{exc}\n")
    print(f"设备身份已更新：{identity.label} ({identity.id})")


def _sync_cli(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="miniclaw2 sync")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="configure metadata sync")
    init_parser.add_argument("git_url")
    args = parser.parse_args(argv)

    root = miniclaw_home()
    try:
        existing = list(root.iterdir()) if root.exists() else []
        if existing:
            store = Store(root)
            store.sync.setup_existing_store(args.git_url)
        else:
            bootstrap_store(root, args.git_url)
            store = Store(root)
            if store.sync.remote_url() is None:
                store.sync.setup_existing_store(args.git_url)
        config = load_global_config(root)
        save_global_config(
            config.model_copy(
                update={"sync": SyncSettings(remote_url=args.git_url.strip())}
            ),
            root,
        )
        store.sync.schedule_commit("configure metadata sync")
        store.sync.sync_now()
    except SyncError as exc:
        parser.exit(1, f"metadata sync setup failed: {exc}\n")
    print(f"metadata sync configured at {root}")


if __name__ == "__main__":
    main()
