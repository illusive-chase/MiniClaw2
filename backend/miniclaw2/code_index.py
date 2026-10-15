"""Schedules and caches the code-reading page's Python analysis.

The analysis itself runs in a child process (``code_analysis.py`` under
``sys.executable -I``); this module only decides when one is needed, feeds
it, and owns the cache under ``$MINICLAW_HOME/cache/code-index/<project>``.
The cache is per host and derived, so ``cache`` is excluded from metadata
sync and migrations (``migrations/inventory.py``).

Status lives in memory. After a backend restart a project reads as idle
until the next ``ensure`` compares the Git-visible Python files against the
manifest, which is also what makes an unchanged project cost one
``git ls-files`` and a ``stat`` per file, with no child process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import code_analysis
from .code_analysis import ANALYZER_VERSION, cache_file_for
from .code_files import ANALYSIS_CAP, list_python_files

logger = logging.getLogger(__name__)

SCHEMA = 1
GLOBAL_LIMIT = 2
#: A child that prints nothing for this long is killed.
STALL_TIMEOUT = 600.0


@dataclass(slots=True)
class IndexState:
    state: str = "idle"  # idle | running | ready | error
    phase: str | None = None
    done: int = 0
    total: int = 0
    failed: int = 0
    updated_at: float | None = None
    error: str | None = None
    process: asyncio.subprocess.Process | None = None
    task: asyncio.Task[None] | None = None
    dirty: bool = False
    priority: list[str] = field(default_factory=list)
    root: str = ""
    #: Serializes "scan, then maybe start": two POSTs must not both start a run.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def public(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "phase": self.phase,
            "done": self.done,
            "total": self.total,
            "failed": self.failed,
            "updated_at": self.updated_at,
            "error": self.error,
        }


class CodeIndexService:
    def __init__(
        self,
        home: Path | Callable[[], Path] | None = None,
        *,
        stall_timeout: float = STALL_TIMEOUT,
    ) -> None:
        self._home = home
        self._states: dict[str, IndexState] = {}
        self._slots: asyncio.Semaphore | None = None
        self.stall_timeout = stall_timeout

    # paths -------------------------------------------------------------

    def home(self) -> Path:
        if callable(self._home):
            return self._home()
        if self._home is not None:
            return self._home
        from .global_config import miniclaw_home

        return miniclaw_home()

    def cache_dir(self, pid: str) -> Path:
        return self.home() / "cache" / "code-index" / pid

    def _semaphore(self) -> asyncio.Semaphore:
        if self._slots is None:
            self._slots = asyncio.Semaphore(GLOBAL_LIMIT)
        return self._slots

    # cache metadata ----------------------------------------------------

    def _expected_meta(self, root: Path) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "analyzer_version": ANALYZER_VERSION,
            "python_version": list(sys.version_info[:2]),
            "root_path": str(root.resolve()),
        }

    def _prepare_cache(self, pid: str, root: Path) -> Path:
        """Wipe the cache if it was built by another analyser, Python or root."""
        directory = self.cache_dir(pid)
        expected = self._expected_meta(root)
        meta_path = directory / "meta.json"
        try:
            current = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = None
        if current != expected:
            if directory.exists():
                shutil.rmtree(directory, ignore_errors=True)
            directory.mkdir(parents=True, exist_ok=True)
            code_analysis.write_atomic(meta_path, json.dumps(expected).encode())
        return directory

    def _read_manifest(self, directory: Path) -> dict[str, dict[str, Any]]:
        try:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return manifest if isinstance(manifest, dict) else {}

    def _scan(self, pid: str, root: Path) -> tuple[list[dict[str, Any]], dict[str, str], bool]:
        """Return ``(files, reuse, changed)`` — runs in a worker thread."""
        directory = self._prepare_cache(pid, root)
        manifest = self._read_manifest(directory)
        files: list[dict[str, Any]] = []
        changed = False
        real_root = root.resolve()
        for path in list_python_files(root):
            try:
                # A symlink out of the project is listed but never analysed:
                # its names and docstrings would otherwise reach hovers.
                if not (root / path).resolve().is_relative_to(real_root):
                    continue
                stat = (root / path).stat()
            except OSError:
                continue
            entry = {"path": path, "mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
            files.append(entry)
            previous = manifest.get(path)
            if (
                not isinstance(previous, dict)
                or previous.get("mtime_ns") != stat.st_mtime_ns
                or previous.get("size") != stat.st_size
            ):
                changed = True
            elif stat.st_size <= ANALYSIS_CAP and not cache_file_for(directory, path).exists():
                changed = True
        if set(manifest) != {entry["path"] for entry in files}:
            changed = True
        reuse = {
            path: value["sha256"]
            for path, value in manifest.items()
            if isinstance(value, dict) and isinstance(value.get("sha256"), str)
        }
        return files, reuse, changed

    # public API --------------------------------------------------------

    def status(self, pid: str) -> dict[str, Any]:
        return self._states.get(pid, IndexState()).public()

    async def ensure(self, pid: str, root: Path, prioritize: list[str] | None = None) -> dict[str, Any]:
        state = self._states.setdefault(pid, IndexState())
        prioritize = [p for p in (prioritize or []) if isinstance(p, str)]
        async with state.lock:
            return await self._ensure_locked(pid, root, state, prioritize)

    async def _ensure_locked(
        self, pid: str, root: Path, state: IndexState, prioritize: list[str]
    ) -> dict[str, Any]:
        if state.state == "running":
            state.dirty = True
            state.priority.extend(p for p in prioritize if p not in state.priority)
            await self._forward_priority(state, prioritize)
            return state.public()
        files, reuse, changed = await asyncio.to_thread(self._scan, pid, root)
        if not changed:
            state.state = "ready"
            state.phase = None
            state.error = None
            if state.updated_at is None:
                state.updated_at = time.time()
                state.total = state.done = len(files)
            return state.public()
        state.state = "running"
        state.phase = "parse"
        state.done = 0
        state.total = len(files)
        state.failed = 0
        state.error = None
        state.dirty = False
        state.root = str(root)
        state.priority = list(prioritize)
        state.task = asyncio.create_task(self._run(pid, root, files, reuse))
        return state.public()

    async def _forward_priority(self, state: IndexState, paths: list[str]) -> None:
        process = state.process
        if not paths or process is None or process.stdin is None:
            return
        try:
            process.stdin.write((json.dumps({"prioritize": paths}) + "\n").encode())
            await process.stdin.drain()
        except (ConnectionError, RuntimeError, OSError):
            pass

    async def _run(self, pid: str, root: Path, files: list[dict[str, Any]], reuse: dict[str, str]) -> None:
        state = self._states[pid]
        try:
            async with self._semaphore():
                await self._spawn(pid, root, files, reuse, state)
        except asyncio.CancelledError:
            state.state = "idle"
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("code index run failed for %s", pid)
            state.state = "error"
            state.error = str(exc) or type(exc).__name__
        finally:
            state.process = None
            state.task = None
            state.updated_at = time.time()
        if state.dirty and state.state != "error" and pid in self._states:
            state.dirty = False
            priority, state.priority = state.priority, []
            await self.ensure(pid, root, priority)

    async def _spawn(
        self,
        pid: str,
        root: Path,
        files: list[dict[str, Any]],
        reuse: dict[str, str],
        state: IndexState,
    ) -> None:
        directory = self.cache_dir(pid)
        directory.mkdir(parents=True, exist_ok=True)
        script = Path(code_analysis.__file__).resolve()
        # -I: no script dir, cwd or user site on sys.path, no PYTHON* env.
        # cwd is the cache dir, so a project file named `ast.py` is never
        # importable even by accident.
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(script),
            cwd=str(directory),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        state.process = process
        job = {
            "root": str(root),
            "cache_dir": str(directory),
            "files": files,
            "reuse": reuse,
            "priority": state.priority,
        }
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        process.stdin.write((json.dumps(job) + "\n").encode())
        await process.stdin.drain()
        stderr_task = asyncio.create_task(process.stderr.read())
        finished: dict[str, Any] | None = None
        try:
            while True:
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), self.stall_timeout)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    state.state = "error"
                    state.error = "分析进程长时间无响应，已终止"
                    return
                if not line:
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                kind = event.get("event")
                if kind == "progress":
                    state.phase = event.get("phase")
                    state.done = int(event.get("done", 0))
                    state.total = int(event.get("total", 0))
                elif kind == "done":
                    finished = event
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        finally:
            if process.stdin is not None and not process.stdin.is_closing():
                process.stdin.close()
        code = await process.wait()
        stderr = (await stderr_task).decode("utf-8", "replace").strip()
        if stderr:
            logger.warning("code analysis stderr for %s: %s", pid, stderr[-4000:])
        if finished is None or code != 0:
            state.state = "error"
            state.error = f"分析进程异常退出（{code}）"
            return
        state.state = "ready"
        state.phase = None
        state.failed = int(finished.get("failed", 0))
        state.done = state.total = int(finished.get("files", state.total))

    def read_result(self, pid: str, path: str) -> bytes | None:
        try:
            return cache_file_for(self.cache_dir(pid), path).read_bytes()
        except OSError:
            return None

    async def forget(self, pid: str) -> None:
        """Stop any run and delete the project's cache (delete / unbind)."""
        state = self._states.pop(pid, None)
        if state is not None:
            await self._stop(state)
        directory = self.cache_dir(pid)
        await asyncio.to_thread(shutil.rmtree, directory, True)

    async def _stop(self, state: IndexState) -> None:
        process = state.process
        if process is not None and process.returncode is None:
            process.kill()
            try:
                await process.wait()
            except Exception:  # noqa: BLE001
                pass
        task = state.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:  # noqa: BLE001
                pass

    async def shutdown(self) -> None:
        states = list(self._states.values())
        for state in states:
            state.dirty = False
            await self._stop(state)
