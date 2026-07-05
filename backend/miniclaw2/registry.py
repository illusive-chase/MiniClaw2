"""ProjectRegistry — in-memory orchestration over the disk Store."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from .contextspace import (
    contextspace_root,
    create_planspace,
    delete_project_contextspace,
    normalize_skill_ids,
    read_planspace_mode,
    resolve_project_binding,
    resolve_active_planspace,
    set_planspace_mode,
)
from .domain import (
    TERMINAL_NODE_STATES,
    Category,
    Node,
    NodeKind,
    NodeState,
    PlanspaceMode,
    Project,
    ReviewBrief,
    ReviewSubtype,
    normalize_planspace_mode,
)
from .language import normalize_preferred_language
from .preview import render_executed_preview, render_virtual_preview
from .runner import NodeRunner
from .store import Store
from .virtual_graph import has_cycle
from .workspace import create_temporary_root, remove_temporary_root

_STARTUP_INTERRUPT_REASON = "interrupted by backend restart"

logger = logging.getLogger(__name__)


_UNSET: object = object()

_PROMPTS_DIR = Path(__file__).with_name("prompts")
_CONCIERGE_TEMPLATE = "concierge_bootstrap.md"


@lru_cache(maxsize=1)
def _concierge_template() -> str:
    return (_PROMPTS_DIR / _CONCIERGE_TEMPLATE).read_text(encoding="utf-8")


def _render_concierge_prompt(seed: str) -> str:
    return _concierge_template().replace("<<user_seed>>", seed)


def _normalize_project_root(cwd: str, *, create_missing: bool = False) -> str:
    """Return a stable absolute project root for non-temporary projects."""
    if not cwd.strip():
        raise ValueError("cwd is required for non-temporary projects")
    try:
        expanded = Path(cwd).expanduser()
    except RuntimeError as exc:
        raise ValueError(f"cwd cannot be expanded: {cwd}") from exc
    if create_missing:
        try:
            expanded.mkdir(parents=True, exist_ok=True)
        except FileExistsError as exc:
            raise ValueError(f"cwd is not a directory: {expanded}") from exc
        except OSError as exc:
            reason = exc.strerror or str(exc)
            raise ValueError(f"cwd cannot be created: {expanded}: {reason}") from exc
    try:
        resolved = expanded.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"cwd does not exist: {expanded}") from exc
    except NotADirectoryError as exc:
        raise ValueError(f"cwd is not a directory: {expanded}") from exc
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise ValueError(f"cwd cannot be accessed: {expanded}: {reason}") from exc
    if not resolved.is_dir():
        raise ValueError(f"cwd is not a directory: {resolved}")
    return str(resolved)


class ProjectRuntime:
    """Per-project mutable runtime state — only one runner active at a time."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.runner: NodeRunner | None = None
        self.runner_task: asyncio.Task[None] | None = None
        self.observers: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}

    def is_running(self) -> bool:
        return self.runner_task is not None and not self.runner_task.done()

    def add_observer(
        self,
        on_event: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> str:
        token = uuid4().hex
        self.observers[token] = on_event
        return token

    def remove_observer(self, token: str) -> None:
        self.observers.pop(token, None)

    async def broadcast(self, event: dict[str, Any]) -> None:
        stale: list[str] = []
        for token, on_event in list(self.observers.items()):
            try:
                await on_event(event)
            except Exception:  # noqa: BLE001
                logger.debug("dropping failed project observer", exc_info=True)
                stale.append(token)
        for token in stale:
            self.remove_observer(token)


class ProjectRegistry:
    """Holds in-memory ProjectRuntime entries; persistence is via Store."""

    def __init__(self, store: Store | None = None) -> None:
        self.store = store or Store()
        self._runtimes: dict[str, ProjectRuntime] = {}
        for project in self.store.list_projects():
            self._runtimes[project.id] = ProjectRuntime(project)
            self._repair_stale_nodes(project.id)

    def _repair_stale_nodes(self, pid: str) -> None:
        """Mark nodes stuck in non-terminal states as cancelled on load.

        A previous process may have exited (crash, reload, kill) with a
        runner still driving nodes in RUNNING/WAITING/AWAITING_HUMAN_INPUT
        /QUEUED. Those runners are gone; the node states in the store are
        misleading. Sweep them to CANCELLED with a reason so the UI shows
        them as terminal and rerun becomes possible. Best-effort only —
        failures are logged and swallowed so a bad node doesn't block
        the whole registry from starting.
        """
        now = time.time()
        for node in self.store.list_nodes(pid):
            if node.state in TERMINAL_NODE_STATES:
                continue
            if node.state is NodeState.VIRTUAL:
                continue
            node.state = NodeState.CANCELLED
            node.error = (
                (node.error + "\n" if node.error else "") + _STARTUP_INTERRUPT_REASON
            )
            if node.started_at is None:
                node.started_at = node.created_at
            node.finished_at = now
            try:
                self.store.update_node(node)
            except Exception:  # noqa: BLE001
                logger.exception("failed to persist stale-node repair for %s", node.id)
                continue
            try:
                preview_text = render_executed_preview(
                    node,
                    motivation=(
                        node.prompt[:200]
                        if node.prompt
                        else "(no motivation recorded)"
                    ),
                    summary=_STARTUP_INTERRUPT_REASON,
                    next_implications=(
                        "node was mid-flight when the backend restarted; "
                        "rerun from the panel to try again"
                    ),
                )
                self.store.write_node_preview(pid, node.id, preview_text)
            except Exception:  # noqa: BLE001
                logger.exception("failed to write stale-node preview for %s", node.id)

    # ---- project CRUD ----

    def create_project(
        self,
        cwd: str | None,
        model: str | None = None,
        model_provider: str | None = None,
        name: str = "",
        provider: str | None = None,
        auto_commit: bool | None = None,
        permission_mode: str | None = None,
        approval_policy: str | None = None,
        sandbox: str | None = None,
        project_context_binding_id: str | None = None,
        preferred_language: str | None = None,
        temporary: bool = False,
        template_id: str | None = None,
        create_missing_cwd: bool = False,
    ) -> Project:
        normalized_provider = (provider or "claude").lower()
        if normalized_provider not in {"claude", "codex"}:
            raise ValueError(f"unknown provider: {provider}")
        normalized_language = normalize_preferred_language(preferred_language)
        if temporary:
            root_path = create_temporary_root()
        else:
            if not cwd:
                raise ValueError("cwd is required for non-temporary projects")
            root_path = _normalize_project_root(
                cwd, create_missing=create_missing_cwd
            )
        settings: dict[str, Any] = {}
        if model:
            settings["model"] = model
        if model_provider:
            settings["model_provider"] = model_provider
        if auto_commit is not None:
            settings["auto_commit"] = bool(auto_commit)
        if permission_mode is not None:
            settings["permission_mode"] = permission_mode
        if approval_policy is not None:
            settings["approval_policy"] = approval_policy
        if sandbox is not None:
            settings["sandbox"] = sandbox
        project = Project(
            root_path=root_path,
            name=name,
            provider=normalized_provider,
            preferred_language=normalized_language,
            project_context_binding_id=project_context_binding_id,
            settings_override=settings,
            temporary=temporary,
            template_id=template_id,
        )
        self.store.create_project(project)
        self._runtimes[project.id] = ProjectRuntime(project)
        return project

    def get_project(self, pid: str) -> Project | None:
        rt = self._runtimes.get(pid)
        return rt.project if rt else None

    def list_projects(self) -> list[Project]:
        return [rt.project for rt in self._runtimes.values()]

    def rename_project(self, pid: str, name: str) -> Project | None:
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        rt.project.name = name
        self.store.update_project(rt.project)
        return rt.project

    def update_project_preferences(
        self,
        pid: str,
        *,
        preferred_language: str | None | object = _UNSET,
    ) -> Project | None:
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        if preferred_language is not _UNSET:
            rt.project.preferred_language = normalize_preferred_language(
                preferred_language
            )
            settings = dict(rt.project.settings_override)
            settings.pop("preferred_language", None)
            settings.pop("language", None)
            rt.project.settings_override = settings
        self.store.update_project(rt.project)
        return rt.project

    def update_project_context(
        self,
        pid: str,
        *,
        project_context_binding_id: str | None | object = _UNSET,
        active_planspace_id: str | None | object = _UNSET,
    ) -> Project | None:
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        if project_context_binding_id is not _UNSET:
            rt.project.project_context_binding_id = (
                project_context_binding_id.strip()
                if isinstance(project_context_binding_id, str)
                and project_context_binding_id.strip()
                else None
            )
        if active_planspace_id is not _UNSET:
            settings = dict(rt.project.settings_override)
            if isinstance(active_planspace_id, str) and active_planspace_id.strip():
                settings["active_planspace_id"] = active_planspace_id.strip()
            else:
                settings.pop("active_planspace_id", None)
            rt.project.settings_override = settings
        self.store.update_project(rt.project)
        return rt.project

    def update_layout_hints(
        self,
        pid: str,
        updates: dict[str, dict[str, float]],
        *,
        remove: list[str] | None = None,
        layout_viewport: dict[str, float] | None = None,
    ) -> Project | None:
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        merged = dict(rt.project.layout_hints)
        for nid, pos in updates.items():
            if not isinstance(pos, dict):
                continue
            x = pos.get("x")
            y = pos.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue
            merged[nid] = {"x": float(x), "y": float(y)}
        for nid in remove or ():
            merged.pop(nid, None)
        rt.project.layout_hints = merged
        if layout_viewport is not None:
            x = layout_viewport.get("x")
            y = layout_viewport.get("y")
            zoom = layout_viewport.get("zoom")
            if (
                isinstance(x, (int, float))
                and isinstance(y, (int, float))
                and isinstance(zoom, (int, float))
                and math.isfinite(x)
                and math.isfinite(y)
                and math.isfinite(zoom)
                and zoom > 0
            ):
                rt.project.layout_viewport = {
                    "x": float(x),
                    "y": float(y),
                    "zoom": float(zoom),
                }
        self.store.update_project(rt.project)
        return rt.project

    def update_planspace_view(
        self,
        pid: str,
        planspaces: dict[str, dict[str, bool]],
    ) -> Project | None:
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        merged = dict(rt.project.planspace_view)
        for planspace_id, pref in planspaces.items():
            if not isinstance(planspace_id, str) or not planspace_id.strip():
                continue
            if not isinstance(pref, dict):
                continue
            current = dict(merged.get(planspace_id, {}))
            if "hidden" in pref:
                current["hidden"] = bool(pref["hidden"])
            if current:
                merged[planspace_id] = current
            else:
                merged.pop(planspace_id, None)
        rt.project.planspace_view = merged
        self.store.update_project(rt.project)
        return rt.project

    def update_planspace_mode(
        self,
        pid: str,
        planspace_id: str,
        mode: str,
    ) -> PlanspaceMode | None:
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        written = set_planspace_mode(
            rt.project,
            planspace_id,
            mode,
            store_root=self.store.root,
        )
        active = resolve_active_planspace(
            rt.project, contextspace_root(self.store.root)
        )
        active_lane = active[1].id if active is not None else ""
        if (
            written is PlanspaceMode.AUTO
            and planspace_id == active_lane
            and not rt.is_running()
        ):
            self._auto_promote_next_virtual(rt)
        return written

    def delete_project(self, pid: str) -> bool:
        rt = self._runtimes.get(pid)
        if rt is None:
            return False
        if rt.is_running():
            assert rt.runner_task is not None
            rt.runner_task.cancel()
        delete_project_contextspace(rt.project, store_root=self.store.root)
        if rt.project.temporary:
            remove_temporary_root(rt.project.root_path)
        self.store.delete_project(pid)
        self._runtimes.pop(pid, None)
        return True

    def attach_observer(
        self,
        pid: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> str | None:
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        return rt.add_observer(on_event)

    def detach_observer(self, pid: str, token: str | None) -> None:
        if token is None:
            return
        rt = self._runtimes.get(pid)
        if rt is not None:
            rt.remove_observer(token)

    def turn_count(self, pid: str) -> int:
        return len(self.store.list_nodes(pid))

    def is_running(self, pid: str) -> bool:
        rt = self._runtimes.get(pid)
        return bool(rt and rt.is_running())

    def list_nodes(self, pid: str) -> list[Node] | None:
        if pid not in self._runtimes:
            return None
        return self.store.list_nodes(pid)

    def get_node(self, pid: str, nid: str) -> Node | None:
        if pid not in self._runtimes:
            return None
        return self.store.load_node(pid, nid)

    def replay_node_events(
        self,
        pid: str,
        nid: str,
        since_seq: int = 0,
    ) -> list[dict[str, Any]] | None:
        if pid not in self._runtimes:
            return None
        if not nid:
            latest = self.store.latest_node(pid)
            if latest is None:
                return []
            nid = latest.id
        if self.store.load_node(pid, nid) is None:
            return None
        return self.store.replay_events(pid, nid, since_seq)

    # ---- node lifecycle ----

    def start_node(
        self,
        pid: str,
        prompt: str,
        *,
        resume_from_node_id: str | None = None,
        extra_skills: list[str] | None = None,
        agent_op_kind: str | None = None,
        category: Category = Category.REGULAR,
        subtype: ReviewSubtype | None = None,
        brief: ReviewBrief | None = None,
        parent_node_id: str | None = None,
        scheduled_deps: list[str] | None = None,
    ) -> NodeRunner | None:
        """Create a new agent node and launch its runner as a task."""
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        if rt.is_running():
            return None

        resume_source: Node | None = None
        if resume_from_node_id:
            resume_source = self.store.load_node(pid, resume_from_node_id)
            if resume_source is None:
                return None
            if resume_source.state not in TERMINAL_NODE_STATES:
                return None
            if not (resume_source.provider_session_id or resume_source.cli_session_id):
                return None

        extra_skill_ids = normalize_skill_ids(extra_skills)
        settings_snapshot: dict[str, Any] = {}
        if extra_skill_ids:
            settings_snapshot["extra_skills"] = extra_skill_ids

        active = resolve_active_planspace(
            rt.project, contextspace_root(self.store.root)
        )
        active_lane = active[1].id if active is not None else None
        actual_parent_id = resume_source.id if resume_source else parent_node_id
        node = Node(
            project_id=pid,
            kind=NodeKind.AGENT,
            agent_op_kind=agent_op_kind,
            category=category,
            subtype=subtype,
            brief=brief,
            state=NodeState.QUEUED,
            parent_node_id=actual_parent_id,
            planspace_id=active_lane,
            provider=resume_source.provider if resume_source else rt.project.provider,
            provider_session_id=resume_source.provider_session_id if resume_source else None,
            cli_session_id=resume_source.cli_session_id if resume_source else None,
            prompt=prompt,
            scheduled_deps=list(scheduled_deps or []),
            settings_snapshot=settings_snapshot,
        )
        self.store.create_node(node)
        if node.category is Category.REVIEW:
            try:
                virtual_preview_node = node.model_copy(
                    update={
                        "state": NodeState.VIRTUAL,
                        "prompt_draft": node.prompt,
                        "proposed_by": (
                            f"node:{actual_parent_id}"
                            if actual_parent_id
                            else f"user:{node.id}"
                        ),
                        "summary": node.brief.check_what if node.brief else node.prompt[:200],
                    }
                )
                self.store.write_node_preview(
                    pid, node.id, render_virtual_preview(virtual_preview_node)
                )
            except Exception:  # noqa: BLE001
                logger.exception("failed to seed review node virtual preview")

        runner = NodeRunner(node, rt.project, self.store, rt.broadcast)
        self._launch_runner(rt, runner)
        return runner

    def _launch_runner(
        self,
        rt: ProjectRuntime,
        runner: NodeRunner,
        *,
        coro: Awaitable[None] | None = None,
    ) -> None:
        rt.runner = runner
        rt.runner_task = asyncio.create_task(coro if coro is not None else runner.run())
        rt.runner_task.add_done_callback(lambda _t, _rt=rt: self._on_runner_done(_rt))

    def _on_runner_done(self, rt: ProjectRuntime) -> None:
        finished_node = rt.runner.node if rt.runner else None
        rt.runner = None
        rt.runner_task = None

        if finished_node is None:
            return
        spawned_op = False
        if (
            finished_node.kind is NodeKind.AGENT
            and finished_node.state is NodeState.DONE
            and bool(rt.project.settings_override.get("auto_commit"))
        ):
            self._spawn_op_commit(rt, finished_node)
            spawned_op = True
        if spawned_op:
            return
        if finished_node.state is not NodeState.DONE:
            return
        if not rt.project.settings_override.get("active_planspace_id"):
            return
        self._auto_promote_next_virtual(rt)

    def create_planspace_and_launch_concierge(
        self,
        pid: str,
        *,
        title: str,
        seed: str,
        mode: str | None = None,
    ) -> NodeRunner | None:
        """Create a new planspace, activate it, and launch the concierge.

        The concierge is a planning-category agent node whose prompt is
        the rendered ``concierge_bootstrap.md`` template with the user's
        free-form ``seed`` substituted in. Returns the runner on
        success, ``None`` if the project is already running.
        """
        rt = self._runtimes.get(pid)
        if rt is None or rt.is_running():
            return None
        if not seed.strip():
            raise ValueError("seed must be non-empty")
        normalized_mode = normalize_planspace_mode(mode)
        plug_id = create_planspace(
            rt.project,
            title=title or "Direction",
            mode=normalized_mode,
            store_root=self.store.root,
            seed_text=seed,
        )
        settings = dict(rt.project.settings_override)
        settings["active_planspace_id"] = plug_id
        rt.project.settings_override = settings
        self.store.update_project(rt.project)

        prompt_text = _render_concierge_prompt(seed.strip())
        node = Node(
            project_id=pid,
            kind=NodeKind.AGENT,
            category=Category.PLANNING,
            state=NodeState.QUEUED,
            planspace_id=plug_id,
            provider=rt.project.provider,
            prompt=prompt_text,
        )
        self.store.create_node(node)

        runner = NodeRunner(node, rt.project, self.store, rt.broadcast)
        self._launch_runner(rt, runner)
        return runner

    def create_blank_planspace(
        self,
        pid: str,
        *,
        title: str,
        seed: str,
        mode: str | None = None,
    ) -> Node | None:
        """Create a planspace and seed it with one empty editable virtual."""
        rt = self._runtimes.get(pid)
        if rt is None or rt.is_running():
            return None
        if not seed.strip():
            raise ValueError("seed must be non-empty")
        normalized_mode = normalize_planspace_mode(mode)
        plug_id = create_planspace(
            rt.project,
            title=title or seed.strip() or "Direction",
            mode=normalized_mode,
            store_root=self.store.root,
            seed_text=seed,
        )
        settings = dict(rt.project.settings_override)
        settings["active_planspace_id"] = plug_id
        rt.project.settings_override = settings
        self.store.update_project(rt.project)

        node = self.create_virtual(
            pid,
            prompt_draft="",
            category=Category.REGULAR,
            motivation="",
            scheduled_deps=[],
            planspace_id=plug_id,
        )
        if node is None:
            return None
        return node

    # ---- auto-promotion ----

    def _auto_promote_next_virtual(self, rt: ProjectRuntime) -> None:
        """Promote the next eligible virtual on the active lane if mode=auto."""
        project = rt.project
        active_lane = project.settings_override.get("active_planspace_id") or ""
        if not active_lane:
            return
        try:
            mode = read_planspace_mode(
                project, active_lane, store_root=self.store.root
            )
        except Exception:  # noqa: BLE001
            logger.exception("planspace mode lookup failed")
            return
        if mode is not PlanspaceMode.AUTO:
            return
        candidate = self._next_promotion_candidate(project.id, active_lane)
        if candidate is None:
            return
        self.promote_virtual(project.id, candidate.id)

    def promote_next_virtual(self, pid: str) -> None:
        """Run one auto-promotion pass for callers that just seeded a lane."""
        rt = self._runtimes.get(pid)
        if rt is None or rt.is_running():
            return
        self._auto_promote_next_virtual(rt)

    def _next_promotion_candidate(
        self, pid: str, lane_id: str
    ) -> Node | None:
        """Return the earliest-created eligible virtual on ``lane_id``.

        Eligible = ``state == VIRTUAL``, no ``obsolete_reason``, and every
        ``scheduled_deps`` parent is ``DONE`` or an obsoleted virtual.
        Manual promotion is allowed to inspect failed/cancelled upstream
        nodes, but auto mode must not advance past a failed verifier/review.
        """
        nodes = self.store.list_nodes(pid)
        lane_nodes = [n for n in nodes if (n.planspace_id or "") == lane_id]
        by_id: dict[str, Node] = {n.id: n for n in lane_nodes}

        def is_dep_auto_ready(dep_id: str) -> bool:
            parent = by_id.get(dep_id)
            if parent is None:
                parent = self.store.load_node(pid, dep_id)
            if parent is None:
                return True
            if parent.state is NodeState.DONE:
                return True
            if parent.state is NodeState.VIRTUAL and parent.obsolete_reason:
                return True
            return False

        eligible: list[Node] = []
        for n in lane_nodes:
            if n.state is not NodeState.VIRTUAL:
                continue
            if n.obsolete_reason:
                continue
            if not (n.prompt_draft or "").strip():
                continue
            if not all(is_dep_auto_ready(dep) for dep in n.scheduled_deps):
                continue
            eligible.append(n)
        if not eligible:
            return None
        eligible.sort(key=lambda n: (n.created_at, n.id))
        return eligible[0]

    def promote_virtual(self, pid: str, vid: str) -> NodeRunner | None:
        """Promote a virtual node to ``queued`` and launch its runner.

        Returns the runner or ``None`` if the node cannot be promoted
        (already executed, obsoleted, unresolved deps, or another node
        is currently running on the project).
        """
        rt = self._runtimes.get(pid)
        if rt is None or rt.is_running():
            return None
        node = self.store.load_node(pid, vid)
        if node is None:
            return None
        if node.state is not NodeState.VIRTUAL or node.obsolete_reason:
            return None
        if not (node.prompt_draft or "").strip():
            return None
        active = resolve_active_planspace(
            rt.project, contextspace_root(self.store.root)
        )
        active_lane = active[1].id if active is not None else ""
        node_lane = node.planspace_id or ""
        if node_lane != active_lane:
            return None
        for dep in node.scheduled_deps:
            parent = self.store.load_node(pid, dep)
            if parent is None:
                continue
            if parent.state in TERMINAL_NODE_STATES:
                continue
            if parent.state is NodeState.VIRTUAL and parent.obsolete_reason:
                continue
            return None
        if node.resume_from_node_id:
            resume_parent = self.store.load_node(pid, node.resume_from_node_id)
            if resume_parent is None:
                return None
            if resume_parent.state not in TERMINAL_NODE_STATES:
                return None
            if not (resume_parent.provider_session_id or resume_parent.cli_session_id):
                return None
            node.provider = resume_parent.provider
            node.provider_session_id = resume_parent.provider_session_id
            node.cli_session_id = resume_parent.cli_session_id
            node.parent_node_id = resume_parent.id
        try:
            self.store.write_node_preview(pid, node.id, render_virtual_preview(node))
        except Exception:  # noqa: BLE001
            logger.exception("failed to preserve virtual preview before promotion")
            return None
        node.state = NodeState.QUEUED
        if node.kind is NodeKind.AGENT:
            node.prompt = node.prompt_draft or node.prompt
        node.prompt_draft = None
        # Promote pending_extra_skills → settings_snapshot["extra_skills"]
        # so compose_context_bundle at launch sees them (see contextspace
        # ``_extra_skill_ids``).
        if node.pending_extra_skills:
            snapshot = dict(node.settings_snapshot)
            snapshot["extra_skills"] = list(node.pending_extra_skills)
            node.settings_snapshot = snapshot
            node.pending_extra_skills = []
        self.store.update_node(node)

        runner = NodeRunner(node, rt.project, self.store, rt.broadcast)
        self._launch_runner(rt, runner)
        try:
            asyncio.get_running_loop().create_task(rt.broadcast({
                "type": "node_updated",
                "node": node.model_dump(),
                "seq": 0,
            }))
        except RuntimeError:
            pass
        return runner

    def create_virtual(
        self,
        pid: str,
        *,
        prompt_draft: str,
        category: str | Category | None = Category.REGULAR,
        subtype: str | ReviewSubtype | None = None,
        brief: dict[str, Any] | ReviewBrief | None = None,
        motivation: str | None = None,
        scheduled_deps: list[str] | None = None,
        pending_extra_skills: list[str] | None = None,
        agent_op_kind: str | None = None,
        planspace_id: str | None = None,
        node_id: str | None = None,
        parent_node_id: str | None = None,
        resume_from_node_id: str | None = None,
    ) -> Node | None:
        """Create a user-authored editable virtual node.

        Returns the created node, or ``None`` when the project is missing or
        busy. Validation failures raise ``ValueError`` with a client-facing
        message.
        """
        rt = self._runtimes.get(pid)
        if rt is None or rt.is_running():
            return None

        lane_id = self._resolve_virtual_create_lane(rt, planspace_id)
        normalized_parent_id = self._normalize_virtual_parent(
            pid,
            lane_id=lane_id,
            parent_node_id=parent_node_id,
        )
        normalized_resume_id = self._normalize_virtual_resume_source(
            pid,
            lane_id=lane_id,
            resume_from_node_id=resume_from_node_id,
        )

        try:
            next_category = (
                category
                if isinstance(category, Category)
                else Category(str(category or Category.REGULAR.value))
            )
        except ValueError as exc:
            raise ValueError(f"unknown category: {category!r}") from exc

        next_subtype: ReviewSubtype | None = None
        if subtype not in (None, ""):
            try:
                next_subtype = (
                    subtype
                    if isinstance(subtype, ReviewSubtype)
                    else ReviewSubtype(str(subtype))
                )
            except ValueError as exc:
                raise ValueError(f"unknown review subtype: {subtype!r}") from exc

        next_brief: ReviewBrief | None = None
        if brief is not None:
            if isinstance(brief, ReviewBrief):
                next_brief = brief
            else:
                try:
                    next_brief = ReviewBrief.model_validate(brief)
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(f"invalid review brief: {exc}") from exc

        if next_category is not Category.REVIEW:
            next_subtype = None
            next_brief = None
        else:
            if next_subtype is None:
                raise ValueError("review virtuals require a subtype")
            if next_brief is None:
                raise ValueError("review virtuals require a brief")

        node_kwargs: dict[str, Any] = {}
        if node_id is not None:
            node_kwargs["id"] = node_id
        node = Node(
            **node_kwargs,
            project_id=pid,
            kind=NodeKind.AGENT,
            agent_op_kind=agent_op_kind,
            category=next_category,
            subtype=next_subtype,
            brief=next_brief,
            state=NodeState.VIRTUAL,
            parent_node_id=normalized_parent_id,
            planspace_id=lane_id,
            provider=rt.project.provider,
            prompt="",
            prompt_draft=str(prompt_draft),
            scheduled_deps=[],
            pending_extra_skills=normalize_skill_ids(pending_extra_skills),
            resume_from_node_id=normalized_resume_id,
            proposed_by="user",
            summary="" if motivation is None else str(motivation),
        )
        if self.store.load_node(pid, node.id) is not None:
            raise ValueError(f"node id {node.id!r} already exists")
        node.scheduled_deps = self._normalize_virtual_scheduled_deps(
            pid,
            virtual_id=node.id,
            lane_id=lane_id,
            scheduled_deps=scheduled_deps,
        )
        lane_nodes = self._lane_nodes_with(pid, lane_id, node)
        if has_cycle(lane_nodes):
            raise ValueError("scheduled_deps would introduce a cycle in the lane DAG")

        self.store.create_node(node)
        try:
            self.store.write_node_preview(pid, node.id, render_virtual_preview(node))
        except Exception:  # noqa: BLE001
            logger.exception("failed to write virtual preview after user create")
        try:
            asyncio.get_running_loop().create_task(rt.broadcast({
                "type": "node_updated",
                "node": node.model_dump(),
                "seq": 0,
            }))
        except RuntimeError:
            pass

        active_lane = rt.project.settings_override.get("active_planspace_id") or ""
        if active_lane == lane_id:
            try:
                mode = read_planspace_mode(
                    rt.project, active_lane, store_root=self.store.root
                )
            except Exception:  # noqa: BLE001
                logger.exception("planspace mode lookup failed")
            else:
                if mode is PlanspaceMode.AUTO and not rt.is_running():
                    self._auto_promote_next_virtual(rt)
                    return self.store.load_node(pid, node.id) or node
        return node

    def update_virtual(
        self,
        pid: str,
        vid: str,
        *,
        prompt_draft: str | None | object = _UNSET,
        category: str | Category | None | object = _UNSET,
        subtype: str | ReviewSubtype | None | object = _UNSET,
        brief: dict[str, Any] | ReviewBrief | None | object = _UNSET,
        motivation: str | None | object = _UNSET,
        scheduled_deps: list[str] | None | object = _UNSET,
        pending_extra_skills: list[str] | None | object = _UNSET,
        obsolete_reason: str | None | object = _UNSET,
    ) -> Node | None:
        """Update a virtual node in place.

        Returns the updated node. ``None`` means the project/node is missing, the
        project is busy, or the target is not an editable virtual. Validation
        failures raise ``ValueError`` with a client-facing message.
        """
        rt = self._runtimes.get(pid)
        if rt is None or rt.is_running():
            return None
        existing = self.store.load_node(pid, vid)
        if existing is None:
            return None
        if existing.kind is not NodeKind.AGENT or existing.state is not NodeState.VIRTUAL:
            return None

        update: dict[str, Any] = {}
        if prompt_draft is not _UNSET:
            if prompt_draft is None or not str(prompt_draft).strip():
                raise ValueError("prompt_draft must be non-empty")
            update["prompt_draft"] = str(prompt_draft)
        if motivation is not _UNSET:
            update["summary"] = "" if motivation is None else str(motivation)
        if obsolete_reason is not _UNSET:
            normalized_obsolete = (
                str(obsolete_reason).strip()
                if obsolete_reason is not None
                else ""
            )
            update["obsolete_reason"] = normalized_obsolete or None

        next_category = existing.category or Category.REGULAR
        if category is not _UNSET:
            if category is None:
                raise ValueError("category is required")
            try:
                next_category = category if isinstance(category, Category) else Category(str(category))
            except ValueError as exc:
                raise ValueError(f"unknown category: {category!r}") from exc
            update["category"] = next_category

        next_subtype = existing.subtype
        if subtype is not _UNSET:
            if subtype is None or subtype == "":
                next_subtype = None
            else:
                try:
                    next_subtype = (
                        subtype
                        if isinstance(subtype, ReviewSubtype)
                        else ReviewSubtype(str(subtype))
                    )
                except ValueError as exc:
                    raise ValueError(f"unknown review subtype: {subtype!r}") from exc
            update["subtype"] = next_subtype

        next_brief = existing.brief
        if brief is not _UNSET:
            if brief is None:
                next_brief = None
            elif isinstance(brief, ReviewBrief):
                next_brief = brief
            else:
                try:
                    next_brief = ReviewBrief.model_validate(brief)
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(f"invalid review brief: {exc}") from exc
            update["brief"] = next_brief

        if next_category is not Category.REVIEW:
            update["subtype"] = None
            update["brief"] = None
        else:
            if next_subtype is None:
                raise ValueError("review virtuals require a subtype")
            if next_brief is None:
                raise ValueError("review virtuals require a brief")
            update["subtype"] = next_subtype
            update["brief"] = next_brief

        if scheduled_deps is not _UNSET:
            deps = self._normalize_virtual_scheduled_deps(
                pid,
                virtual_id=existing.id,
                lane_id=existing.planspace_id or "",
                scheduled_deps=scheduled_deps,
            )
            update["scheduled_deps"] = deps

        if pending_extra_skills is not _UNSET:
            update["pending_extra_skills"] = normalize_skill_ids(
                pending_extra_skills
            )

        updated = existing.model_copy(update=update)
        updated = Node.model_validate(updated.model_dump())
        lane_id = updated.planspace_id or ""
        if has_cycle(self._lane_nodes_with(pid, lane_id, updated)):
            raise ValueError("scheduled_deps would introduce a cycle in the lane DAG")

        self.store.update_node(updated)
        try:
            self.store.write_node_preview(pid, updated.id, render_virtual_preview(updated))
        except Exception:  # noqa: BLE001
            logger.exception("failed to write virtual preview after user edit")
        try:
            asyncio.get_running_loop().create_task(rt.broadcast({
                "type": "node_updated",
                "node": updated.model_dump(),
                "seq": 0,
            }))
        except RuntimeError:
            pass
        active_lane = rt.project.settings_override.get("active_planspace_id") or ""
        if active_lane == lane_id:
            try:
                mode = read_planspace_mode(
                    rt.project, active_lane, store_root=self.store.root
                )
            except Exception:  # noqa: BLE001
                logger.exception("planspace mode lookup failed")
            else:
                if mode is PlanspaceMode.AUTO and not rt.is_running():
                    self._auto_promote_next_virtual(rt)
                    return self.store.load_node(pid, updated.id) or updated
        return updated

    def delete_virtual(self, pid: str, vid: str) -> tuple[bool, list[str]]:
        """Hard-delete an unrun virtual node.

        Returns ``(True, [])`` on success. Validation failures raise
        ``ValueError`` for 400-class errors; a non-empty blockers list means the
        caller should report a conflict.
        """
        rt = self._runtimes.get(pid)
        if rt is None:
            return False, []
        if rt.is_running():
            raise RuntimeError("turn in progress")
        node = self.store.load_node(pid, vid)
        if node is None:
            return False, []
        if node.state is not NodeState.VIRTUAL:
            raise ValueError("only virtual nodes can be deleted")

        nodes = self.store.list_nodes(pid)
        blockers = [
            other.id
            for other in nodes
            if other.id != vid
            and not other.obsolete_reason
            and vid in (other.scheduled_deps or [])
        ]
        if blockers:
            return False, blockers

        for other in nodes:
            if other.id == vid or vid not in (other.scheduled_deps or []):
                continue
            cleaned = [dep for dep in other.scheduled_deps if dep != vid]
            if cleaned == other.scheduled_deps:
                continue
            other.scheduled_deps = cleaned
            self.store.update_node(other)
            try:
                self.store.write_node_preview(pid, other.id, render_virtual_preview(other))
            except Exception:  # noqa: BLE001
                logger.exception("failed to write virtual preview after dep cleanup")

        if not self.store.delete_node(pid, vid):
            return False, []
        try:
            asyncio.get_running_loop().create_task(rt.broadcast({
                "type": "node_removed",
                "id": vid,
                "seq": 0,
            }))
        except RuntimeError:
            pass
        return True, []

    def rerun_node(self, pid: str, nid: str) -> Node | None:
        """Create a fresh virtual carrying the same prompt as a failed node.

        Used by the rerun UI for nodes in ERROR or CANCELLED state (e.g.,
        crashed by a backend restart). Returns ``None`` when the project
        or node is missing, the project is busy, or the target is not a
        rerunnable agent node. Validation failures raise ``ValueError``.
        """
        rt = self._runtimes.get(pid)
        if rt is None or rt.is_running():
            return None
        original = self.store.load_node(pid, nid)
        if original is None:
            return None
        if original.kind is not NodeKind.AGENT:
            raise ValueError("only agent nodes support rerun")
        if original.state not in {NodeState.ERROR, NodeState.CANCELLED}:
            raise ValueError("only error/cancelled nodes support rerun")
        prompt = (original.prompt or original.prompt_draft or "").strip()
        if not prompt:
            raise ValueError("original node has no prompt to rerun")

        virtual = self.create_virtual(
            pid,
            prompt_draft=prompt,
            category=original.category or Category.REGULAR,
            subtype=original.subtype,
            brief=original.brief,
            motivation=f"rerun of {original.id[:8]}",
            scheduled_deps=list(original.scheduled_deps or []),
            planspace_id=original.planspace_id,
            parent_node_id=original.parent_node_id,
            resume_from_node_id=original.resume_from_node_id,
        )
        if virtual is None:
            return None
        # create_virtual stamps proposed_by="user"; re-tag with the rerun
        # provenance so downstream previews carry the link back.
        fresh = self.store.load_node(pid, virtual.id) or virtual
        if fresh.state is NodeState.VIRTUAL:
            fresh.proposed_by = f"rerun:{original.id}"
            self.store.update_node(fresh)
            try:
                self.store.write_node_preview(
                    pid, fresh.id, render_virtual_preview(fresh)
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "failed to write rerun virtual preview for %s", fresh.id
                )
        return fresh

    def _resolve_virtual_create_lane(
        self,
        rt: ProjectRuntime,
        planspace_id: str | None,
    ) -> str:
        active = resolve_active_planspace(
            rt.project, contextspace_root(self.store.root)
        )
        active_lane = active[1].id if active is not None else ""
        requested = planspace_id.strip() if isinstance(planspace_id, str) else ""
        if not requested:
            if not active_lane:
                raise ValueError("active planspace is required")
            return active_lane
        if not requested.startswith("planspaces."):
            raise ValueError(f"unknown planspace: {requested}")
        if requested == active_lane:
            return requested
        binding = resolve_project_binding(
            rt.project, contextspace_root(self.store.root)
        )
        if binding is None:
            raise ValueError(f"unknown planspace: {requested}")
        if any(ref.id == requested for ref in binding.plugs):
            return requested
        raise ValueError(f"unknown planspace: {requested}")

    def _normalize_virtual_scheduled_deps(
        self,
        pid: str,
        *,
        virtual_id: str,
        lane_id: str,
        scheduled_deps: list[str] | None,
    ) -> list[str]:
        if scheduled_deps is None:
            return []
        if not isinstance(scheduled_deps, list):
            raise ValueError("scheduled_deps must be a list")
        deps: list[str] = []
        seen: set[str] = set()
        for raw_dep in scheduled_deps:
            if not isinstance(raw_dep, str) or not raw_dep.strip():
                raise ValueError("scheduled_deps entries must be non-empty strings")
            dep = raw_dep.strip()
            if dep == virtual_id:
                raise ValueError("scheduled_deps must not include the virtual itself")
            if dep in seen:
                continue
            dep_node = self.store.load_node(pid, dep)
            if dep_node is None:
                raise ValueError(f"scheduled_dep {dep!r} does not resolve")
            if (dep_node.planspace_id or "") != lane_id:
                raise ValueError(f"scheduled_dep {dep!r} is outside this lane")
            seen.add(dep)
            deps.append(dep)
        return deps

    def _normalize_virtual_parent(
        self,
        pid: str,
        *,
        lane_id: str,
        parent_node_id: str | None,
    ) -> str | None:
        if parent_node_id is None or not str(parent_node_id).strip():
            return None
        parent_id = str(parent_node_id).strip()
        parent = self.store.load_node(pid, parent_id)
        if parent is None:
            raise ValueError(f"parent_node_id {parent_id!r} does not resolve")
        if (parent.planspace_id or "") != lane_id:
            raise ValueError(f"parent_node_id {parent_id!r} is outside this lane")
        return parent_id

    def _normalize_virtual_resume_source(
        self,
        pid: str,
        *,
        lane_id: str,
        resume_from_node_id: str | None,
    ) -> str | None:
        if resume_from_node_id is None or not str(resume_from_node_id).strip():
            return None
        source_id = str(resume_from_node_id).strip()
        source = self.store.load_node(pid, source_id)
        if source is None:
            raise ValueError(f"resume_from_node_id {source_id!r} does not resolve")
        if source.kind is NodeKind.OP:
            raise ValueError("resume_from_node_id must reference an agent/verifier node")
        if (source.planspace_id or "") != lane_id:
            raise ValueError(f"resume_from_node_id {source_id!r} is outside this lane")
        if source.state not in TERMINAL_NODE_STATES:
            raise ValueError("resume_from_node_id must reference a terminal node")
        if not (source.provider_session_id or source.cli_session_id):
            raise ValueError("resume_from_node_id is not resumable")
        return source_id

    def _lane_nodes_with(
        self,
        pid: str,
        lane_id: str,
        node: Node,
    ) -> dict[str, Node]:
        by_id = {
            n.id: n
            for n in self.store.list_nodes(pid)
            if (n.planspace_id or "") == lane_id
        }
        by_id[node.id] = node
        return by_id

    def _spawn_op_commit(self, rt: ProjectRuntime, agent_node: Node) -> None:
        op_node = Node(
            project_id=rt.project.id,
            kind=NodeKind.OP,
            op_kind="commit",
            state=NodeState.QUEUED,
            parent_node_id=agent_node.id,
            provider=agent_node.provider,
            planspace_id=agent_node.planspace_id,
        )
        self.store.create_node(op_node)

        runner = NodeRunner(op_node, rt.project, self.store, rt.broadcast)
        self._launch_runner(
            rt, runner, coro=self._run_op_and_rewrite(rt, runner, agent_node.id)
        )

    async def _run_op_and_rewrite(
        self,
        rt: ProjectRuntime,
        runner: NodeRunner,
        agent_node_id: str,
    ) -> None:
        await runner.run()
        op_node = runner.node
        if (
            op_node.state is NodeState.DONE
            and op_node.commit_after
            and op_node.commit_after != op_node.commit_before
        ):
            fresh_agent = self.store.load_node(rt.project.id, agent_node_id)
            if fresh_agent is not None and fresh_agent.commit_after != op_node.commit_after:
                fresh_agent.commit_after = op_node.commit_after
                self.store.update_node(fresh_agent)
                await rt.broadcast({
                    "type": "node_updated",
                    "node": fresh_agent.model_dump(),
                    "seq": 0,
                })

    def interrupt(self, pid: str) -> bool:
        rt = self._runtimes.get(pid)
        if rt is None or not rt.is_running():
            return False
        assert rt.runner_task is not None
        if rt.runner is not None:
            asyncio.create_task(rt.runner.interrupt())
        rt.runner_task.cancel()
        return True

    def resolve_gate(
        self,
        pid: str,
        gate_id: str,
        *,
        allow: bool,
        decision: str | dict[str, Any] | None = None,
        message: str = "",
        updated_input: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        scope: str | None = None,
        interrupt: bool = False,
        permission_mode: str | None = None,
        clear_context: bool = False,
    ) -> bool:
        rt = self._runtimes.get(pid)
        if rt is None or rt.runner is None:
            return False
        return rt.runner.resolve_gate(
            gate_id,
            allow=allow,
            decision=decision,
            message=message,
            updated_input=updated_input,
            response=response,
            scope=scope,
            interrupt=interrupt,
            permission_mode=permission_mode,
            clear_context=clear_context,
        )
