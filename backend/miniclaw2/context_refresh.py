"""Out-of-band CONTEXT.md init / refresh tasks.

Runs a framework-owned agent (preset prompt) against the project's provider
without going through ``NodeRunner``: no node row, no ``events.jsonl``, no
WebSocket broadcast. The agent writes ``CONTEXT.md`` itself via its ``Write``
tool; the framework only books the meta file on success.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .domain import Node, NodeKind, Project
from .model_catalog import provider_for_model_preset
from .providers import AgentProvider, AgentProviderContext, GateRequest

logger = logging.getLogger(__name__)


_PROMPT_DIR = Path(__file__).with_name("prompts")
_TOOL_ALLOWLIST: tuple[str, ...] = ("Read", "Glob", "Grep", "Write")


@dataclass(slots=True)
class ContextTask:
    project_id: str
    mode: str
    started_at: float
    task: asyncio.Task[None] | None = field(default=None)
    provider: AgentProvider | None = field(default=None)


_TASKS: dict[str, ContextTask] = {}


def context_refresh_status(project_id: str) -> dict[str, Any]:
    record = _TASKS.get(project_id)
    if record is None or record.task is None or record.task.done():
        return {"running": False}
    return {
        "running": True,
        "mode": record.mode,
        "started_at": record.started_at,
    }


def start_context_task(project: Project, *, mode: str) -> dict[str, Any]:
    """Start an async CONTEXT.md init/refresh task.

    The task deliberately runs outside the node runner: no provider-tracked
    node row, no events.jsonl writes, no WebSocket broadcast.
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
    record = ContextTask(
        project_id=project.id,
        mode=mode,
        started_at=started_at,
    )
    record.task = asyncio.create_task(_run_agent_context_task(project, record))
    _TASKS[project.id] = record

    def cleanup(done: asyncio.Task[None], *, pid: str = project.id) -> None:
        current = _TASKS.get(pid)
        if current is not None and current.task is done:
            _TASKS.pop(pid, None)

    record.task.add_done_callback(cleanup)
    return context_refresh_status(project.id)


async def cancel_context_task(project_id: str) -> bool:
    """Cancel a running context task. Returns whether anything was cancelled."""

    record = _TASKS.get(project_id)
    if record is None or record.task is None or record.task.done():
        return False
    if record.provider is not None:
        try:
            await record.provider.interrupt()
        except Exception:  # noqa: BLE001
            logger.exception("provider interrupt failed during context cancel")
    record.task.cancel()
    return True


async def _run_agent_context_task(project: Project, record: ContextTask) -> None:
    from .runner import _make_provider  # local import to avoid cycle

    preset = _load_preset(record.mode)
    provider_name = provider_for_model_preset(project.model_preset_id)
    provider = _make_provider(provider_name)
    record.provider = provider

    node = Node(
        project_id=project.id,
        kind=NodeKind.AGENT,
        model_preset_id=project.model_preset_id,
        prompt=preset,
    )
    context = AgentProviderContext(
        node=node,
        project=project,
        request_gate_handler=_auto_deny_gate,
        system_context="",
        launch_instructions="",
        minimal_mode=True,
        tool_allowlist=list(_TOOL_ALLOWLIST),
    )

    context_path = Path(project.root_path) / "CONTEXT.md"
    pre_mtime = _safe_mtime(context_path)

    try:
        terminal_seen = False
        async for ev in provider.run(context):
            if ev.kind == "error":
                terminal_seen = True
                raise RuntimeError(ev.error or "provider error")
            if ev.kind == "done":
                terminal_seen = True
                break
        if not terminal_seen:
            raise RuntimeError(
                f"{provider.name} provider stream ended without a terminal event"
            )
    except asyncio.CancelledError:
        try:
            await provider.interrupt()
        except Exception:  # noqa: BLE001
            logger.exception("provider interrupt failed during cancellation")
        raise

    if record.mode == "init" and not context_path.exists():
        raise RuntimeError(
            "agent finished without writing CONTEXT.md during init",
        )

    post_mtime = _safe_mtime(context_path)
    rewritten = post_mtime is not None and post_mtime != pre_mtime

    _write_meta(project, mode=record.mode, started_at=record.started_at, rewritten=rewritten)


def _load_preset(mode: str) -> str:
    name = "context_init.md" if mode == "init" else "context_refresh.md"
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


async def _auto_deny_gate(_: GateRequest) -> dict[str, Any]:
    return {"allow": False, "message": "out-of-band agent cannot prompt the user"}


def _safe_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _write_meta(
    project: Project,
    *,
    mode: str,
    started_at: float,
    rewritten: bool,
) -> None:
    meta_path = Path(project.root_path) / ".miniclaw2" / "context.meta.json"
    _atomic_write_text(
        meta_path,
        json.dumps(
            {
                "updated_at": started_at,
                "source": mode,
                "rewritten": rewritten,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
