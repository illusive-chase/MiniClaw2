"""Run with `python -m miniclaw2` or the `miniclaw2` script."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

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
    machine_hostname_mismatch,
    resolve_machine_copy,
    resolve_machine_rename,
)

VITE_HOST = "127.0.0.1"
VITE_PORT = 5173


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        _sync_cli(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(prog="miniclaw2")
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
    _resolve_identity_mismatch(miniclaw_home())

    # Broadcast the port to child processes (claude hook bridge reads
    # it via MINICLAW_HOOK_URL and MINICLAW_HOOK_TOKEN from its env at
    # spawn time; keeping this here lets the app compute the URL before
    # any spawn happens).
    os.environ["MINICLAW2_HOOK_PORT"] = str(args.port)

    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    vite_proc: subprocess.Popen[bytes] | None = None

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
        print(f"backend:            http://{args.host}:{args.port}")
        frontend_mode = "Vite HMR" if args.reload else "Vite, reload off"
        print(f"frontend ({frontend_mode}): http://{VITE_HOST}:{VITE_PORT}")
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
                "MINICLAW_RELOAD": "1" if args.reload else "0",
            },
        )
    else:
        # Prod: FastAPI serves the built frontend from the same origin.
        # __main__ is the only writer of MINICLAW_FRONTEND_DIST — tests
        # never invoke this module, so the app factory's mount stays
        # inert under pytest. Users can override via env for installed
        # (non-editable) layouts.
        os.environ.setdefault(
            "MINICLAW_FRONTEND_DIST", str(frontend_dir / "dist")
        )

    try:
        uvicorn.run(
            "miniclaw2.app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level=args.log_level,
        )
    finally:
        if vite_proc is not None:
            vite_proc.terminate()
            try:
                vite_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                vite_proc.kill()


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
            _resolve_identity_mismatch(root)
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


def _resolve_identity_mismatch(root: Path) -> None:
    identity = ensure_machine_identity(root)
    if not machine_hostname_mismatch(identity):
        return
    if not sys.stdin.isatty():
        raise SystemExit(
            "machine hostname differs from machine.json; run MiniClaw2 in a "
            "terminal once to resolve renamed machine versus copied store"
        )
    answer = input(
        f'machine.json belongs to "{identity.hostname}", but this host is different. '
        "Was the machine [r]enamed or was the store [c]opied? "
    ).strip().lower()
    if answer in {"r", "rename", "renamed"}:
        resolve_machine_rename(root)
        return
    if answer in {"c", "copy", "copied"}:
        resolve_machine_copy(root)
        return
    raise SystemExit("identity not changed; start MiniClaw2 again and choose r or c")


if __name__ == "__main__":
    main()
