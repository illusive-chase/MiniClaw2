"""What this host owes its remotes and what its remotes have for it.

Three objects sync, and they answer different questions:

- The metadata store and bound project checkouts are **outbound**: does this
  host hold work the remote lacks, and since when. That is derived from local
  refs with no network IO, and only becomes a red dot once it is older than a
  host-local threshold.
- The MiniClaw2 source checkout is **inbound**: does the remote have a release
  this host has not installed. A background probe (``ls-remote`` first, fetch
  only on a difference) answers that and announces each new upstream commit
  once.

Probes only ever read: they move tracking refs and nothing else. Writing a
remote stays a user action (FUTURES §2.9).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .domain import ProjectPersistenceMode
from .git_state import WorktreeDebt, worktree_debt
from .self_update import UpdateChecker, UpdateError, UpdateState
from .sync import SyncError
from .sync_local import SyncLocal, SyncLocalSettings, sync_local

if TYPE_CHECKING:
    from .registry import ProjectRegistry


logger = logging.getLogger(__name__)

PROJECT_SCAN_SECONDS = 5 * 60.0
PROJECT_SCAN_TIMEOUT_SECONDS = 10.0
LOOP_TICK_SECONDS = 30.0
MAX_BACKOFF_SECONDS = 2 * 3600.0
#: A host that has never probed waits this long after startup, so a backend
#: that restarts in a loop does not hammer either remote.
FIRST_PROBE_DELAY_SECONDS = 60.0
#: Set to any value to keep the background loop off the network entirely
#: (the test suite does; local scans and publishing still run).
PROBES_DISABLED_ENV = "MINICLAW_DISABLE_REMOTE_PROBES"
#: Consecutive probe failures before the overview calls the probe broken.
FAILURE_NOTICE_THRESHOLD = 3


def classify_outbound(
    *, configured: bool, failed: bool, since: float | None, threshold: float, now: float,
) -> str:
    if not configured:
        return "unconfigured"
    if failed:
        return "failed"
    if since is None:
        return "clean"
    return "overdue" if now - since >= threshold else "pending"


@dataclass
class ProbeHealth:
    """Backoff bookkeeping for one remote, mirrored into ``.sync-local``."""

    section: str
    local: SyncLocal
    next_at: float = 0.0
    failures: int = 0

    def load(self) -> dict[str, Any]:
        record = self.local.section(self.section)
        failures = record.get("failures")
        self.failures = failures if type(failures) is int else 0
        return record

    def due(self, now: float, interval: float) -> bool:
        if self.next_at == 0.0:
            last = self.load().get("last_checked_at")
            self.next_at = (
                float(last) + interval if isinstance(last, (int, float))
                else now + FIRST_PROBE_DELAY_SECONDS
            )
        return now >= self.next_at

    def succeeded(self, now: float, interval: float, **extra: Any) -> None:
        self.failures = 0
        self.next_at = now + interval
        self.local.update_section(self.section, {
            "last_checked_at": now, "last_ok_at": now, "last_check_ok": True,
            "last_error": None, "failures": 0, **extra,
        })

    def failed(self, now: float, interval: float, error: str) -> None:
        self.failures += 1
        self.next_at = now + min(MAX_BACKOFF_SECONDS, interval * (2 ** min(self.failures - 1, 8)))
        self.local.update_section(self.section, {
            "last_checked_at": now, "last_check_ok": False,
            "last_error": error[:500], "failures": self.failures,
        })


class SyncOverview:
    """Derives the sync overview and runs the read-only probes in the background.

    The probes and scans run on worker threads; the overview itself is
    rebuilt on demand from local refs and the cached project debts. A category
    change in any object is pushed as ``sync_overview_changed``.
    """

    def __init__(
        self,
        registry: "ProjectRegistry",
        update_checker: UpdateChecker,
        *,
        daemon_active: Callable[[], bool] = lambda: False,
    ) -> None:
        self.registry = registry
        self.update_checker = update_checker
        self.daemon_active = daemon_active
        self._project_debts: dict[str, tuple[float, WorktreeDebt]] = {}
        self._debts_lock = threading.Lock()
        self._task: asyncio.Task[None] | None = None
        self._last_categories: dict[str, str] | None = None
        self._metadata_probe: ProbeHealth | None = None
        self._source_probe: ProbeHealth | None = None
        self._last_project_scan = 0.0
        self._wake: asyncio.Event | None = None
        self._publish: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    @property
    def local(self) -> SyncLocal:
        return sync_local(self.registry.store.root)

    def settings(self) -> SyncLocalSettings:
        return self.local.settings()

    # ---- lifecycle ----

    def start(self, publish: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        if self._task is not None:
            return
        self._publish = publish
        self._wake = asyncio.Event()
        self._metadata_probe = ProbeHealth("metadata_probe", self.local)
        self._source_probe = ProbeHealth("source_probe", self.local)
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def poke(self) -> None:
        """Re-evaluate soon (after a sync, a push, a settings change)."""
        if self._wake is not None:
            self._wake.set()

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("同步概览后台检查失败")
            assert self._wake is not None
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=LOOP_TICK_SECONDS)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def _tick(self) -> None:
        registry = self.registry
        if registry._storage_sync_pending or registry._self_update_pending:
            return
        settings = self.settings()
        now = time.time()
        sync = registry.store.sync
        moved = False
        probes = os.environ.get(PROBES_DISABLED_ENV) is None
        assert self._metadata_probe is not None and self._source_probe is not None
        interval = settings.metadata_probe_minutes * 60.0
        if probes and settings.metadata_probe_enabled and sync.configured and self._metadata_probe.due(now, interval):
            try:
                changed = await asyncio.to_thread(sync.probe_remote)
            except SyncError as exc:
                self._metadata_probe.failed(now, interval, str(exc))
            else:
                if changed is not None:
                    self._metadata_probe.succeeded(now, interval)
                    moved = moved or bool(changed)
        interval = settings.source_probe_minutes * 60.0
        if probes and settings.source_probe_enabled and self.update_checker.enabled and self._source_probe.due(now, interval):
            try:
                await asyncio.to_thread(self._probe_source)
            except UpdateError as exc:
                self._source_probe.failed(now, interval, str(exc))
            else:
                self._source_probe.succeeded(now, interval)
        if now - self._last_project_scan >= PROJECT_SCAN_SECONDS:
            self._last_project_scan = now
            await asyncio.to_thread(self.scan_projects)
        await self.publish_if_changed(force_hosts=moved)

    def _probe_source(self) -> None:
        self.update_checker.probe_remote()

    # ---- project debts ----

    def _eligible_projects(self) -> list[Any]:
        registry = self.registry
        projects = []
        for project in registry.list_projects():
            if (
                project.persistence_mode is ProjectPersistenceMode.DURABLE
                and not project.temporary
                and project.archived_at is None
                and registry.store.is_bound_here(project.id)
            ):
                projects.append(project)
        return projects

    def scan_projects(self, project_ids: set[str] | None = None) -> None:
        for project in self._eligible_projects():
            if project_ids is not None and project.id not in project_ids:
                continue
            self.refresh_project(project.id, project.root_path)

    def refresh_project(self, project_id: str, root_path: str) -> WorktreeDebt:
        debt = worktree_debt(root_path, timeout=PROJECT_SCAN_TIMEOUT_SECONDS)
        now = time.time()
        with self._debts_lock:
            self._project_debts[project_id] = (now, debt)
        ledger = self.local.section("projects").get(project_id)
        first_dirty = ledger.get("first_seen_dirty_at") if isinstance(ledger, dict) else None
        if debt.dirty_count and not isinstance(first_dirty, (int, float)):
            self.local.update_section("projects", {project_id: {"first_seen_dirty_at": now}})
        elif not debt.dirty_count and first_dirty is not None:
            self.local.update_section("projects", {project_id: None})
        return debt

    def schedule_project_refresh(self, project_id: str) -> None:
        """Called after a commit, push, or pull; runs off the event loop."""
        project = self.registry.get_project(project_id)
        if project is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def refresh() -> None:
            await asyncio.to_thread(self.refresh_project, project.id, project.root_path)
            await self.publish_if_changed()

        loop.create_task(refresh())

    # ---- overview ----

    def overview(self) -> dict[str, Any]:
        now = time.time()
        settings = self.settings()
        store = self.registry.store
        status = store.sync.status()
        metadata_probe = self.local.section("metadata_probe")
        metadata_threshold = settings.metadata_overdue_hours * 3600.0
        metadata = {
            "state": classify_outbound(
                configured=status["configured"], failed=status["last_sync_failed"],
                since=status["debt_since"], threshold=metadata_threshold, now=now,
            ),
            "unpushed_commits": status["unpushed_commits"],
            "unpushed_since": status["unpushed_since"],
            "since": status["debt_since"],
            "dirty": status["dirty"],
            "last_sync_at": status["last_sync_at"],
            "last_sync_failed": status["last_sync_failed"],
            "remote_ahead": status["remote"]["behind"],
            "remote_probe": _probe_payload(metadata_probe, settings.metadata_probe_enabled),
            "overdue_after_seconds": metadata_threshold,
        }
        projects = self._project_entries(now, settings.project_overdue_hours * 3600.0)
        return {
            "generated_at": now,
            "metadata": metadata,
            "projects": projects,
            "projects_overdue": sum(1 for entry in projects if entry["state"] == "overdue"),
            "projects_pending": sum(1 for entry in projects if entry["state"] == "pending"),
            "project_overdue_after_seconds": settings.project_overdue_hours * 3600.0,
            "source": self._source_entry(settings),
            "settings": settings.model_dump(),
        }

    def _project_entries(self, now: float, threshold: float) -> list[dict[str, Any]]:
        ledger = self.local.section("projects")
        with self._debts_lock:
            debts = dict(self._project_debts)
        entries: list[dict[str, Any]] = []
        for project in self._eligible_projects():
            cached = debts.get(project.id)
            if cached is None:
                continue
            scanned_at, debt = cached
            if not debt.is_repo:
                continue
            reasons: list[str] = []
            since_candidates: list[float] = []
            if debt.dirty_count:
                reasons.append("dirty")
                record = ledger.get(project.id)
                first_seen = record.get("first_seen_dirty_at") if isinstance(record, dict) else None
                for value in (first_seen, debt.dirty_mtime):
                    if isinstance(value, (int, float)):
                        since_candidates.append(float(value))
            if debt.upstream and debt.ahead:
                reasons.append("ahead")
                if debt.ahead_since is not None:
                    since_candidates.append(debt.ahead_since)
            since = min(since_candidates) if since_candidates else (now if reasons else None)
            state = "clean" if not reasons else "overdue" if now - since >= threshold else "pending"
            entries.append({
                "id": project.id,
                "name": project.name or project.id,
                "state": state,
                "dirty_count": debt.dirty_count,
                "ahead": debt.ahead,
                "behind": debt.behind,
                "branch": debt.branch,
                "upstream": debt.upstream,
                "no_upstream": debt.branch is not None and debt.upstream is None,
                "since": since if reasons else None,
                "reasons": reasons,
                "scanned_at": scanned_at,
                "error": debt.error,
            })
        return entries

    def _source_entry(self, settings: SyncLocalSettings) -> dict[str, Any]:
        record = self.local.section("source_probe")
        state: UpdateState = self.update_checker.state()
        if not state.is_repo:
            category = "disabled"
        elif not settings.source_probe_enabled and not state.behind:
            category = "disabled"
        elif state.behind > 0 and state.available and not state.dirty:
            category = "available"
        elif state.behind > 0:
            category = "blocked"
        elif record.get("last_check_ok") is False and _failures(record) >= FAILURE_NOTICE_THRESHOLD:
            category = "error"
        else:
            category = "current"
        return {
            "state": category,
            "behind": state.behind,
            "ahead": state.ahead,
            "dirty": state.dirty,
            "head": state.head,
            "upstream": state.upstream,
            "commits": [{"sha": item.sha, "title": item.title} for item in state.commits[-20:]],
            "error": state.error,
            "probe": _probe_payload(record, settings.source_probe_enabled),
            "daemon": {"active": self.daemon_active()},
        }

    # ---- push ----

    async def publish_if_changed(self, *, force_hosts: bool = False) -> None:
        if self._publish is None:
            return
        try:
            payload = await asyncio.to_thread(self.overview)
        except Exception:  # noqa: BLE001
            logger.debug("无法计算同步概览", exc_info=True)
            return
        categories = {
            "metadata": payload["metadata"]["state"],
            "source": payload["source"]["state"],
            **{f"project:{entry['id']}": entry["state"] for entry in payload["projects"]},
        }
        previous, self._last_categories = self._last_categories, categories
        if previous is not None and previous != categories:
            await self._publish({"type": "sync_overview_changed", "seq": 0})
        if force_hosts:
            await self._publish({"type": "hosts_changed", "seq": 0})
        source = payload["source"]
        upstream_head = source["commits"][-1]["sha"] if source["commits"] else None
        if source["state"] in {"available", "blocked"} and upstream_head:
            announced = self.local.section("source_probe").get("notified_sha")
            if announced != upstream_head:
                self.local.update_section("source_probe", {"notified_sha": upstream_head})
                await self._publish({
                    "type": "source_update_available",
                    "seq": 0,
                    "behind": source["behind"],
                    "target": upstream_head,
                    "title": source["commits"][-1]["title"],
                })


def _failures(record: dict[str, Any]) -> int:
    failures = record.get("failures")
    return failures if type(failures) is int else 0


def _probe_payload(record: dict[str, Any], enabled: bool) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "last_checked_at": record.get("last_checked_at"),
        "last_ok_at": record.get("last_ok_at"),
        "ok": record.get("last_check_ok"),
        "error": record.get("last_error"),
        "failures": _failures(record),
    }
