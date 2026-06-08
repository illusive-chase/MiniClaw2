"""Out-of-band CONTEXT.md init / refresh tasks."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import Project


@dataclass(slots=True)
class ContextTask:
    project_id: str
    mode: str
    started_at: float
    task: asyncio.Task[None]


_TASKS: dict[str, ContextTask] = {}
_GENERATED_START = "<!-- MINICLAW2:PROJECT-DIGEST:START -->"
_GENERATED_END = "<!-- MINICLAW2:PROJECT-DIGEST:END -->"


def context_refresh_status(project_id: str) -> dict[str, Any]:
    task = _TASKS.get(project_id)
    if task is None or task.task.done():
        return {"running": False}
    return {
        "running": True,
        "mode": task.mode,
        "started_at": task.started_at,
    }


def start_context_task(project: Project, *, mode: str) -> dict[str, Any]:
    """Start an async CONTEXT.md init/refresh task.

    The task deliberately runs outside the node runner: no provider session,
    no node, and no events.jsonl write.
    """

    if mode not in {"init", "refresh"}:
        raise ValueError("mode must be init or refresh")
    existing = _TASKS.get(project.id)
    if existing is not None and not existing.task.done():
        raise RuntimeError("context refresh already running")

    context_path = Path(project.root_path) / "CONTEXT.md"
    if mode == "init" and context_path.exists():
        raise ValueError("CONTEXT.md already exists")
    if mode == "refresh" and not context_path.exists():
        raise ValueError("CONTEXT.md does not exist")

    started_at = time.time()
    task = asyncio.create_task(asyncio.to_thread(_write_context, project, mode, started_at))
    record = ContextTask(
        project_id=project.id,
        mode=mode,
        started_at=started_at,
        task=task,
    )
    _TASKS[project.id] = record

    def cleanup(done: asyncio.Task[None], *, pid: str = project.id) -> None:
        current = _TASKS.get(pid)
        if current is not None and current.task is done:
            _TASKS.pop(pid, None)

    task.add_done_callback(cleanup)
    return context_refresh_status(project.id)


def _write_context(project: Project, mode: str, started_at: float) -> None:
    root = Path(project.root_path)
    digest = _repo_digest(root)
    existing = ""
    if mode == "refresh":
        try:
            existing = (root / "CONTEXT.md").read_text(encoding="utf-8")
        except OSError:
            existing = ""
    text = _render_context(project, mode=mode, digest=digest, existing=existing)
    context_path = root / "CONTEXT.md"
    _atomic_write_text(context_path, text)
    meta_path = root / ".miniclaw2" / "context.meta.json"
    _atomic_write_text(
        meta_path,
        json.dumps(
            {
                "updated_at": started_at,
                "source": mode,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )


def _repo_digest(root: Path) -> dict[str, Any]:
    top_level: list[str] = []
    headers: dict[str, str] = {}
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        entries = []

    for entry in entries[:80]:
        if entry.name in {".git", ".miniclaw2", "node_modules", "__pycache__"}:
            continue
        suffix = "/" if entry.is_dir() else ""
        top_level.append(f"{entry.name}{suffix}")

    key_names = {
        "README.md",
        "pyproject.toml",
        "package.json",
        "vite.config.ts",
        "tsconfig.json",
        "Cargo.toml",
        "go.mod",
        "requirements.txt",
    }
    for name in sorted(key_names):
        path = root / name
        if not path.exists() or not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        headers[name] = "\n".join(lines[:40]).strip()

    return {"top_level": top_level, "headers": headers}


def _render_context(
    project: Project,
    *,
    mode: str,
    digest: dict[str, Any],
    existing: str,
) -> str:
    title = project.name.strip() or Path(project.root_path).name or "Project"
    top_level = digest.get("top_level") if isinstance(digest, dict) else []
    headers = digest.get("headers") if isinstance(digest, dict) else {}
    tree = "\n".join(f"- {item}" for item in top_level if isinstance(item, str))
    header_notes: list[str] = []
    if isinstance(headers, dict):
        for name, text in headers.items():
            if not isinstance(name, str) or not isinstance(text, str):
                continue
            first = next((line.strip() for line in text.splitlines() if line.strip()), "")
            if first:
                header_notes.append(f"- `{name}` starts with: {first[:160]}")
            else:
                header_notes.append(f"- `{name}` is present.")
    header_text = "\n".join(header_notes)
    generated = _render_generated_digest(tree=tree, header_text=header_text)
    if mode == "refresh" and existing.strip():
        marked = _replace_generated_digest(existing, generated)
        if marked is not None:
            return marked
        return _ensure_trailing_newline(
            (
                f"# {title} Context\n\n"
                "This is a plan-free project handbook loaded at the start of each run.\n\n"
                f"{generated}\n\n"
                "## Existing Project Guidance\n\n"
                f"{existing.rstrip()}"
            )
        )
    return _ensure_trailing_newline(
        (
            f"# {title} Context\n\n"
            "This is a plan-free project handbook loaded at the start of each run.\n\n"
            f"{generated}\n\n"
            "## Notes For Agents\n\n"
            "- Re-read the repository before making claims about current behavior.\n"
            "- Keep plans, decisions, and open questions in direction notebooks, not here.\n"
            "- Treat this file as editable project guidance.\n"
        )
    )


def _render_generated_digest(*, tree: str, header_text: str) -> str:
    return (
        f"{_GENERATED_START}\n"
        "<!-- This block is regenerated by MiniClaw2. Add hand-written guidance outside it. -->\n\n"
        "## Project Shape\n\n"
        f"{tree or '- No top-level files were readable.'}\n\n"
        "## Useful File Signals\n\n"
        f"{header_text or '- No standard project metadata files were found.'}\n"
        f"{_GENERATED_END}"
    )


def _replace_generated_digest(existing: str, generated: str) -> str | None:
    start = existing.find(_GENERATED_START)
    if start < 0:
        return None
    end = existing.find(_GENERATED_END, start + len(_GENERATED_START))
    if end < 0:
        return None
    end += len(_GENERATED_END)
    parts = [
        part
        for part in (
            existing[:start].rstrip(),
            generated,
            existing[end:].lstrip(),
        )
        if part
    ]
    return _ensure_trailing_newline("\n\n".join(parts))


def _ensure_trailing_newline(text: str) -> str:
    return text.rstrip() + "\n"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
