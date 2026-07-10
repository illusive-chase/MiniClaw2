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

VITE_PORT = 5173


def main() -> None:
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
        print(f"frontend ({frontend_mode}): http://127.0.0.1:{VITE_PORT}")
        vite_proc = subprocess.Popen(
            ["npm", "run", "dev"],
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


if __name__ == "__main__":
    main()
