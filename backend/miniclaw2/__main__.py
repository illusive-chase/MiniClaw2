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

from .migrations import (
    StoreMigrationError,
    check_store,
    dry_run_store_migration,
    migrate_store,
    repair_store,
)

VITE_PORT = 5173


def main() -> None:
    parser = argparse.ArgumentParser(prog="miniclaw2")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--log-level", default="info")
    store_actions = parser.add_mutually_exclusive_group()
    store_actions.add_argument(
        "--check-store",
        action="store_true",
        help="Validate the on-disk store without modifying it, then exit.",
    )
    store_actions.add_argument(
        "--repair-store",
        action="store_true",
        help="Back up and repair legacy records in the on-disk store, then exit.",
    )
    store_actions.add_argument(
        "--dry-run-migration",
        action="store_true",
        help="Report migration changes without modifying the store, then exit.",
    )
    parser.add_argument(
        "--store-path",
        type=Path,
        help="Store used by --check-store/--repair-store (defaults to MINICLAW_HOME).",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Spawn `npm run dev` alongside the backend for Vite HMR at :5173.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level.upper())
    if args.check_store or args.repair_store or args.dry_run_migration:
        store_root = args.store_path or Path(
            os.environ.get("MINICLAW_HOME", Path.home() / ".miniclaw2")
        )
        try:
            if args.repair_store:
                report = repair_store(store_root)
            elif args.dry_run_migration:
                report = dry_run_store_migration(store_root)
            else:
                report = check_store(store_root)
        except StoreMigrationError as exc:
            sys.exit(f"Store validation failed: {exc}")
        action = (
            "dry-run"
            if report.dry_run
            else "repaired"
            if report.repaired
            else "valid"
        )
        print(
            f"Store {action}: {report.root} "
            f"(schema {report.version_before} -> {report.version_after}, "
            f"changed {len(report.changed_files)} files)"
        )
        if report.backup_root is not None:
            print(f"Backup: {report.backup_root}")
        if report.audit_path is not None:
            print(f"Audit: {report.audit_path}")
        if report.dry_run and report.changed_files:
            print("Planned files:")
            for changed_file in report.changed_files:
                print(f"  {changed_file}")
        return

    store_root = Path(
        os.environ.get("MINICLAW_HOME", Path.home() / ".miniclaw2")
    )
    try:
        migration = migrate_store(store_root)
    except StoreMigrationError as exc:
        sys.exit(f"Store migration failed: {exc}")
    if migration.changed_files:
        print(
            f"Store migrated: {migration.root} "
            f"(schema {migration.version_before} -> {migration.version_after}, "
            f"changed {len(migration.changed_files)} files)"
        )
        if migration.backup_root is not None:
            print(f"Backup: {migration.backup_root}")

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
        print(f"frontend (Vite HMR): http://127.0.0.1:{VITE_PORT}")
        vite_proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=str(frontend_dir),
            start_new_session=True,
            env={**os.environ, "MINICLAW_BACKEND_URL": backend_url},
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
