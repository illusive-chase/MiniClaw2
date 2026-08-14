"""ProjectRegistry — in-memory orchestration over the disk Store."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import shutil
import time
from dataclasses import asdict, dataclass
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from .artifacts import workspace_artifacts_dir
from .contextspace import (
    clear_owned_binding_local_paths,
    contextspace_root,
    create_planspace,
    delete_planspace,
    delete_project_contextspace,
    normalize_principle_ids,
    read_planspace_mode,
    resolve_project_binding,
    resolve_active_planspace,
    set_planspace_mode,
)
from .events import NodeRemoved, GitStatus
from .git_state import (
    ensure_miniclaw_git_excluded,
    git_status,
    is_git_repo,
    normalized_origin_url,
    root_commits,
)
from .skills import expand_skill_selections
from .domain import (
    AUTHORING_AGENT_OP_KINDS,
    KNOWN_AGENT_OP_KINDS,
    TERMINAL_NODE_STATES,
    Category,
    Node,
    NodeKind,
    NodeState,
    PlanspaceMode,
    Project,
    ReviewBrief,
    ReviewSubtype,
    ReviewTarget,
    normalize_planspace_mode,
)
from .language import normalize_preferred_language
from .materialize import ARTIFACTS_DIRNAME, GRAPH_DIRNAME, lane_root
from .model_catalog import (
    default_code_review_model_preset_id,
    default_model_preset_id,
    normalize_active_model_preset_id,
    normalize_model_preset_id,
)
from .preview import render_executed_preview, render_virtual_preview
from .runner import NodeRunner
from .store import Store
from .virtual_graph import has_cycle
from .workspace import create_temporary_root, remove_temporary_root

_STARTUP_INTERRUPT_REASON = "interrupted by backend restart"

logger = logging.getLogger(__name__)


_UNSET: object = object()


def _virtual_requires_prompt(subtype: ReviewSubtype | None) -> bool:
    return subtype is not ReviewSubtype.CODE_REVIEW


@dataclass(frozen=True)
class VirtualPromotionResult:
    node: Node | None
    code: str | None = None
    message: str | None = None
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanspaceCreationResult:
    node: Node
    activated: bool


class NonNativeProjectError(PermissionError):
    def __init__(self, project: Project) -> None:
        label = project.machine_label or project.machine_id or "another machine"
        super().__init__(f'project is read-only here; it is native to "{label}"')
        self.project = project


class NonNativeNodeError(PermissionError):
    def __init__(self, node: Node) -> None:
        owner = node.owner_host_id or "another device"
        super().__init__(f'node is read-only here; it belongs to host "{owner}"')
        self.node = node

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
    """Per-project mutable runtime state for concurrent node execution."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.runners: dict[str, NodeRunner] = {}
        self.runner_tasks: dict[str, asyncio.Task[None]] = {}
        self.background_tasks: set[asyncio.Task[Any]] = set()
        self.priority_node_ids: list[str] = []
        self.deferred_until_idle_node_ids: set[str] = set()
        self.closed = False
        self.reap_lock = asyncio.Lock()
        self.observers: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}

    @property
    def active_count(self) -> int:
        return sum(1 for task in self.runner_tasks.values() if not task.done())

    def has_capacity(self) -> bool:
        return self.active_count < self.project.concurrency

    def is_running(self) -> bool:
        return self.active_count > 0

    def get_runner(self, node_id: str) -> NodeRunner | None:
        return self.runners.get(node_id)

    def remove_runner(self, node_id: str) -> NodeRunner | None:
        self.runner_tasks.pop(node_id, None)
        return self.runners.pop(node_id, None)

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

    def __init__(
        self,
        store: Store | None = None,
        *,
        initialize: bool = True,
    ) -> None:
        self._store = store
        self._runtimes: dict[str, ProjectRuntime] = {}
        self._initialized = False
        if initialize:
            self.initialize()

    @property
    def store(self) -> Store:
        self.initialize()
        assert self._store is not None
        return self._store

    def initialize(self) -> None:
        if self._initialized:
            return
        store = self._store or Store()
        self._store = store
        self._initialized = True
        store.sync.add_pre_commit_callback(self._record_host_heads)
        for project in store.list_projects():
            self._runtimes[project.id] = ProjectRuntime(project)
            if self.is_native_project(project) and store.read_only_reason is None:
                self._repair_stale_nodes(project.id)

    def _record_host_heads(self) -> None:
        """Stamp each shared project's local repository state before sync."""
        for runtime in list(self._runtimes.values()):
            project = runtime.project
            if project.sharing != "shared" or not self.is_native_project(project):
                continue
            status = git_status(project.root_path)
            if not status.is_repo or not status.head:
                continue
            self.store.write_host_head(
                project.id,
                {
                    "head": status.head,
                    "branch": status.branch or "",
                    "recorded_at": time.time(),
                    "dirty": status.dirty_count > 0,
                },
            )

    def schedule_all(self) -> None:
        """Fill execution slots for durable queued work after startup."""
        if self.store.read_only_reason is not None:
            return
        for runtime in self._runtimes.values():
            if self.is_native_project(runtime.project):
                self._schedule_queued(runtime)

    def reload_from_store(self) -> None:
        """Refresh project metadata after a successful manual merge."""
        loaded = {project.id: project for project in self.store.list_projects()}
        for project_id, project in loaded.items():
            runtime = self._runtimes.get(project_id)
            if runtime is None:
                self._runtimes[project_id] = ProjectRuntime(project)
            elif not runtime.is_running():
                runtime.project = project
        for project_id in list(self._runtimes):
            runtime = self._runtimes[project_id]
            if project_id not in loaded and not runtime.is_running():
                self._runtimes.pop(project_id, None)

    def is_native_project(self, project: Project) -> bool:
        if project.sharing == "shared":
            return self.store.has_host_binding(project.id, self.store.machine.id)
        return bool(project.machine_id) and project.machine_id == self.store.machine.id

    def is_native_node(self, project: Project, node: Node) -> bool:
        if project.sharing != "shared":
            return self.is_native_project(project)
        return node.owner_host_id == self.store.machine.id

    def _can_resume_provider_session(self, node: Node) -> bool:
        if node.origin_machine_id:
            return node.origin_machine_id == self.store.machine.id
        if node.owner_host_id:
            return node.owner_host_id == self.store.machine.id
        return True

    def is_native(self, pid: str) -> bool:
        project = self.get_project(pid)
        return project is not None and self.is_native_project(project)

    def require_native(self, pid: str) -> Project:
        self.store.assert_writable()
        project = self.get_project(pid)
        if project is None:
            raise KeyError(pid)
        if not self.is_native_project(project):
            raise NonNativeProjectError(project)
        return project

    def require_native_node(self, project: Project, node: Node) -> Node:
        if not self.is_native_node(project, node):
            raise NonNativeNodeError(node)
        return node

    def require_project_owner(self, pid: str) -> Project:
        project = self.require_native(pid)
        if (
            project.sharing == "shared"
            and project.machine_id != self.store.machine.id
        ):
            raise NonNativeProjectError(project)
        return project

    def _repair_stale_nodes(self, pid: str) -> None:
        """Mark nodes stuck in non-terminal states as cancelled on load.

        A previous process may have exited (crash, reload, kill) with a
        runner still driving nodes in RUNNING/WAITING/AWAITING_HUMAN_INPUT
        states. Those runners are gone; the node states in the store are
        misleading. Sweep them to CANCELLED with a reason so the UI shows
        them as terminal and rerun becomes possible. Best-effort only —
        failures are logged and swallowed so a bad node doesn't block
        the whole registry from starting.
        """
        now = time.time()
        project = self.get_project(pid)
        if project is None:
            return
        for node in self.store.list_nodes(pid):
            if not self.is_native_node(project, node):
                continue
            if node.state in TERMINAL_NODE_STATES:
                continue
            if node.state in {NodeState.VIRTUAL, NodeState.QUEUED}:
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
        model_preset_id: str | None = None,
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
        concurrency: int = 1,
    ) -> Project:
        if provider is not None:
            raise ValueError("provider is no longer accepted; use model_preset_id")
        if model is not None or model_provider is not None:
            raise ValueError("model/model_provider are no longer accepted; use model_preset_id")
        normalized_model_preset_id = (
            normalize_active_model_preset_id(
                model_preset_id,
                store_root=self.store.root,
            )
            if model_preset_id is not None
            else default_model_preset_id(store_root=self.store.root)
        )
        normalized_language = normalize_preferred_language(preferred_language)
        if temporary:
            root_path = create_temporary_root()
        else:
            if not cwd:
                raise ValueError("cwd is required for non-temporary projects")
            root_path = _normalize_project_root(
                cwd, create_missing=create_missing_cwd
            )
        exclude_error = ensure_miniclaw_git_excluded(root_path)
        if exclude_error:
            logger.debug(
                "failed to add .miniclaw2 to git exclude for %s: %s",
                root_path,
                exclude_error,
            )
        settings: dict[str, Any] = {}
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
            model_preset_id=normalized_model_preset_id,
            concurrency=concurrency,
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

    def enable_sharing(self, pid: str) -> Project | None:
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        project = self.require_native(pid)
        if project.sharing == "shared":
            return project
        if project.temporary:
            raise ValueError("temporary projects cannot be shared")
        if self.is_running(pid):
            raise RuntimeError("project must be idle before enabling sharing")
        roots = root_commits(project.root_path)
        if not roots:
            raise ValueError("project must be a Git repository with at least one commit")

        project_dir = self.store.root / "projects" / pid
        backup = (
            self.store.root
            / "migration-backups"
            / f"host-partition-v7-{int(time.time())}-{pid}-{uuid4().hex[:8]}"
            / "projects"
            / pid
        )
        shutil.copytree(project_dir, backup)

        host_dir = project_dir / "hosts" / self.store.machine.id
        host_dir.mkdir(parents=True, exist_ok=True)
        nodes_dir = project_dir / "nodes"
        if nodes_dir.exists():
            shutil.move(str(nodes_dir), str(host_dir / "nodes"))
        else:
            (host_dir / "nodes").mkdir(parents=True, exist_ok=True)
        aliases = project_dir / "git_aliases.json"
        if aliases.exists():
            shutil.move(str(aliases), str(host_dir / "git_aliases.json"))
        self.store._write_json(
            host_dir / "host.json",
            {
                "label": self.store.machine.label,
                "bound_at": time.time(),
                "repo": {
                    "root_commit": roots[0],
                    "root_commits": roots,
                    "origin_url": normalized_origin_url(project.root_path),
                },
            },
        )
        project.sharing = "shared"
        clear_owned_binding_local_paths(project, store_root=self.store.root)
        self.store.invalidate_owner_index()
        self.store.update_project(project)
        self.store.sync.schedule_commit(f'enable sharing for project "{project.name or pid}"')
        return project

    def join_shared_project(self, pid: str, root_path: str) -> Project | None:
        self.store.assert_writable()
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        project = rt.project
        if project.sharing != "shared":
            raise ValueError("project is not shared")
        if self.is_native_project(project):
            raise ValueError("project is already enabled on this device")
        root = Path(root_path).expanduser()
        if not root.exists():
            raise ValueError(f"path does not exist: {root}")
        if not root.is_dir():
            raise ValueError(f"path is not a directory: {root}")
        resolved = str(root.resolve())
        if not is_git_repo(resolved):
            raise ValueError("path is not a Git repository")
        roots = root_commits(resolved)
        if not roots:
            raise ValueError("repository has no root commit")
        hosts = self.store.list_hosts(pid)
        expected = next(
            (
                host.get("repo", {}).get("root_commit")
                for host in hosts
                if isinstance(host.get("repo"), dict)
                and host.get("repo", {}).get("root_commit")
            ),
            None,
        )
        if expected is None:
            raise ValueError("shared project has no repository fingerprint")
        if roots[0] != expected:
            raise ValueError("repository fingerprint does not match the shared project")

        exclude_error = ensure_miniclaw_git_excluded(resolved)
        if exclude_error:
            logger.warning(
                "failed to exclude MiniClaw2 generated paths in %s: %s",
                resolved,
                exclude_error,
            )

        host_dir = self.store.root / "projects" / pid / "hosts" / self.store.machine.id
        source_layout = self._shared_layout_seed(pid, project.machine_id)
        self.store._write_json(
            host_dir / "host.json",
            {
                "label": self.store.machine.label,
                "bound_at": time.time(),
                "repo": {
                    "root_commit": roots[0],
                    "root_commits": roots,
                    "origin_url": normalized_origin_url(resolved),
                },
            },
        )
        self.store._write_json(host_dir / "local.json", {"root_path": resolved})
        self.store._write_json(host_dir / "layout.json", source_layout)
        (host_dir / "nodes").mkdir(parents=True, exist_ok=True)
        project.root_path = resolved
        project.layout_hints = dict(source_layout.get("layout_hints", {}))
        project.layout_viewport = source_layout.get("layout_viewport")
        self.store.invalidate_owner_index()
        self.store.sync.schedule_commit(f'join shared project "{project.name or pid}"')
        return project

    def _shared_layout_seed(self, pid: str, preferred_mid: str) -> dict[str, Any]:
        hosts_dir = self.store.root / "projects" / pid / "hosts"
        candidates = [hosts_dir / preferred_mid / "layout.json"]
        candidates.extend(sorted(hosts_dir.glob("*/layout.json")))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(payload, dict):
                return {
                    "layout_hints": payload.get("layout_hints", {}),
                    "layout_viewport": payload.get("layout_viewport"),
                }
        return {"layout_hints": {}, "layout_viewport": None}

    def rename_project(self, pid: str, name: str) -> Project | None:
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        self.require_native(pid)
        rt.project.name = name
        self.store.update_project(rt.project)
        return rt.project

    def update_project_tags(self, pid: str, tag_ids: list[str]) -> Project | None:
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        self.require_native(pid)
        known = {tag.id for tag in self.store.list_tags()}
        normalized: list[str] = []
        seen: set[str] = set()
        for tag_id in tag_ids:
            if tag_id in known and tag_id not in seen:
                normalized.append(tag_id)
                seen.add(tag_id)
        rt.project.tag_ids = normalized
        self.store.update_project(rt.project)
        return rt.project

    def delete_tag(self, tag_id: str) -> bool:
        if not self.store.delete_tag(tag_id):
            return False
        changed = self.store.remove_tag_from_projects(tag_id)
        for pid in changed:
            rt = self._runtimes.get(pid)
            if rt is None:
                continue
            rt.project.tag_ids = [
                existing for existing in rt.project.tag_ids if existing != tag_id
            ]
        return True

    def update_project_preferences(
        self,
        pid: str,
        *,
        preferred_language: str | None | object = _UNSET,
        concurrency: int | object = _UNSET,
    ) -> Project | None:
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        self.require_native(pid)
        if preferred_language is not _UNSET:
            rt.project.preferred_language = normalize_preferred_language(
                preferred_language
            )
        if concurrency is not _UNSET:
            validated = Project.model_validate(
                {
                    **rt.project.model_dump(exclude={"provider"}),
                    "concurrency": concurrency,
                }
            )
            rt.project.concurrency = validated.concurrency
        self.store.update_project(rt.project)
        self._schedule_queued(rt)
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
        self.require_native(pid)
        if project_context_binding_id is not _UNSET:
            rt.project.project_context_binding_id = (
                project_context_binding_id.strip()
                if isinstance(project_context_binding_id, str)
                and project_context_binding_id.strip()
                else None
            )
        if active_planspace_id is not _UNSET:
            rt.project.active_planspace_id = (
                active_planspace_id.strip()
                if isinstance(active_planspace_id, str)
                and active_planspace_id.strip()
                else None
            )
            rt.project.planspace_selection_explicit = True
        self.store.update_project(rt.project)
        if active_planspace_id is not _UNSET:
            self._auto_promote_eligible_virtuals(rt)
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
        self.require_native(pid)
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
        self.require_native(pid)
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
        self.require_native(pid)
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
        ):
            self._auto_promote_eligible_virtuals(rt)
        self.store.sync.schedule_commit(f"update planspace {planspace_id}")
        return written

    def delete_planspace(self, pid: str, planspace_id: str) -> tuple[bool, list[str]]:
        """Hard-delete one planspace and every node that lives in it.

        Returns ``(True, [])`` on success. A non-empty second element lists
        node ids that are still queued or running in the lane, which the
        caller should report as a conflict. ``(False, [])`` means the project
        or the planspace does not exist. Deleting the active lane raises
        ``ValueError`` — the caller must activate another lane first.
        """
        rt = self._runtimes.get(pid)
        if rt is None:
            return False, []
        self.require_native(pid)
        lane_id = planspace_id.strip()
        if not lane_id:
            raise ValueError("planspace id is required")

        root = contextspace_root(self.store.root)
        binding = resolve_project_binding(rt.project, root)
        if binding is None or not any(ref.id == lane_id for ref in binding.plugs):
            return False, []

        active = resolve_active_planspace(rt.project, root)
        active_lane = active[1].id if active is not None else ""
        if lane_id == active_lane:
            raise ValueError(
                "cannot delete the active planspace; activate another one first"
            )

        nodes = self.store.list_nodes(pid)
        lane_nodes = [n for n in nodes if (n.planspace_id or "") == lane_id]
        # A runner persists its terminal state before awaiting its final
        # broadcasts, so a node can read as terminal while its task is still
        # finalizing. Deleting then lets the callback emit events for — or even
        # auto-commit into — a lane that no longer exists, so runtime
        # ownership counts as busy until _on_runner_done drops the task.
        busy = [
            n.id
            for n in lane_nodes
            if n.id in rt.runner_tasks
            or (
                n.state is not NodeState.VIRTUAL
                and n.state not in TERMINAL_NODE_STATES
            )
        ]
        if busy:
            return False, busy

        # Drop dangling scheduled_deps in surviving lanes before the nodes go,
        # mirroring delete_virtual. Cross-lane deps are rejected at reap time,
        # but a stale store can still carry one.
        doomed = {n.id for n in lane_nodes}
        for other in nodes:
            if other.id in doomed or not (other.scheduled_deps or []):
                continue
            cleaned = [dep for dep in other.scheduled_deps if dep not in doomed]
            if cleaned == other.scheduled_deps:
                continue
            if not self.is_native_node(rt.project, other):
                continue
            other.scheduled_deps = cleaned
            self.store.update_node(other)
            try:
                self.store.write_node_preview(
                    pid, other.id, render_virtual_preview(other)
                )
            except Exception:  # noqa: BLE001
                logger.exception("failed to write virtual preview after lane delete")

        removed: list[str] = []
        for node in lane_nodes:
            if self.store.delete_node(pid, node.id):
                removed.append(node.id)
            self._remove_workspace_artifacts(rt.project, node.id)
            rt.deferred_until_idle_node_ids.discard(node.id)
        rt.priority_node_ids = [
            node_id for node_id in rt.priority_node_ids if node_id not in doomed
        ]

        delete_planspace(rt.project, lane_id, store_root=self.store.root)
        self._remove_lane_projection(rt.project, lane_id)

        view = dict(rt.project.planspace_view)
        view.pop(lane_id, None)
        rt.project.planspace_view = view
        hints = dict(rt.project.layout_hints)
        hints.pop(f"planspace:{lane_id}", None)
        for node_id in removed:
            hints.pop(node_id, None)
        rt.project.layout_hints = hints
        self.store.update_project(rt.project)
        self.store.sync.schedule_commit(f"delete planspace {lane_id}")

        for node_id in removed:
            try:
                asyncio.get_running_loop().create_task(
                    rt.broadcast(NodeRemoved(id=node_id).model_dump())
                )
            except RuntimeError:
                break
        return True, []

    def _remove_workspace_artifacts(self, project: Project, node_id: str) -> None:
        """Delete a node's workspace output directory, if any.

        The store copy only holds declared artifacts, so the workspace source
        must go too for a lane delete to be the hard delete the UI promises.
        Best effort: removing the node record is the primary semantics, so a
        failure here is logged instead of aborting the delete.
        """
        if not node_id or node_id in {".", ".."} or Path(node_id).name != node_id:
            return
        try:
            path = workspace_artifacts_dir(project, node_id)
            expected_parent = Path(project.root_path) / ARTIFACTS_DIRNAME
            if path.resolve().parent != expected_parent.resolve():
                return
            if path.exists():
                shutil.rmtree(path)
        except OSError:
            logger.exception("failed to remove workspace artifacts for %s", node_id)

    def _remove_lane_projection(self, project: Project, lane_id: str) -> None:
        """Delete the durable materialized lane subtree, if any."""
        try:
            path = lane_root(project, lane_id)
            expected_parent = Path(project.root_path) / GRAPH_DIRNAME
            if path.resolve().parent != expected_parent.resolve():
                return
            if path.exists():
                shutil.rmtree(path)
        except OSError:
            logger.exception("failed to remove lane projection for %s", lane_id)

    def delete_project(self, pid: str) -> bool:
        rt = self._runtimes.get(pid)
        if rt is None:
            return False
        self.require_project_owner(pid)
        rt.closed = True
        for runner in list(rt.runners.values()):
            try:
                asyncio.get_running_loop().create_task(runner.interrupt())
            except RuntimeError:
                pass
        for task in list(rt.runner_tasks.values()):
            task.add_done_callback(
                lambda _task, _rt=rt: self._finalize_deleted_project(_rt)
            )
            task.cancel()
        delete_project_contextspace(rt.project, store_root=self.store.root)
        if rt.project.temporary:
            remove_temporary_root(rt.project.root_path)
        self.store.delete_project(pid)
        self._runtimes.pop(pid, None)
        return True

    def _finalize_deleted_project(self, rt: ProjectRuntime) -> None:
        if any(not task.done() for task in rt.runner_tasks.values()):
            return
        self.store.delete_project(rt.project.id)
        if rt.project.temporary:
            remove_temporary_root(rt.project.root_path)

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

    def active_count(self, pid: str) -> int:
        rt = self._runtimes.get(pid)
        return rt.active_count if rt is not None else 0

    def queued_count(self, pid: str) -> int:
        rt = self._runtimes.get(pid)
        if rt is None:
            return 0
        return sum(
            1
            for node in self.store.list_nodes(pid)
            if node.state is NodeState.QUEUED
            and self.is_native_node(rt.project, node)
        )

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
        extra_principles: list[str] | None = None,
        extra_skills: list[str | dict[str, Any]] | None = None,
        agent_op_kind: str | None = None,
        model_preset_id: str | None = None,
        category: Category = Category.REGULAR,
        subtype: ReviewSubtype | None = None,
        brief: ReviewBrief | None = None,
        parent_node_id: str | None = None,
        scheduled_deps: list[str] | None = None,
    ) -> Node | None:
        """Persist a queued agent node and schedule it when capacity exists."""
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        self.require_native(pid)

        resume_source: Node | None = None
        if resume_from_node_id:
            resume_source = self.store.load_node(pid, resume_from_node_id)
            if resume_source is None:
                return None
            if resume_source.state not in TERMINAL_NODE_STATES:
                return None
            if (
                not resume_source.provider_session_id
                and self._can_resume_provider_session(resume_source)
            ):
                return None

        if resume_source is not None:
            if (
                model_preset_id is not None
                and normalize_model_preset_id(
                    model_preset_id, store_root=self.store.root
                )
                != resume_source.model_preset_id
            ):
                raise ValueError(
                    "resume nodes inherit model_preset_id from their source node"
                )
            next_model_preset_id = resume_source.model_preset_id
        else:
            if model_preset_id is None:
                next_model_preset_id = rt.project.model_preset_id
            else:
                next_model_preset_id = normalize_model_preset_id(
                    model_preset_id, store_root=self.store.root
                )
                if next_model_preset_id != rt.project.model_preset_id:
                    next_model_preset_id = normalize_active_model_preset_id(
                        next_model_preset_id, store_root=self.store.root
                    )
        extra_principle_ids = normalize_principle_ids(extra_principles)
        skill_selections = expand_skill_selections(
            extra_skills,
            store_root=self.store.root,
        )
        settings_snapshot: dict[str, Any] = {}
        if extra_principle_ids:
            settings_snapshot["extra_principles"] = extra_principle_ids
        if skill_selections:
            settings_snapshot["extra_skills"] = skill_selections

        active = resolve_active_planspace(
            rt.project, contextspace_root(self.store.root)
        )
        active_lane = active[1].id if active is not None else None
        resume_locally = bool(
            resume_source is not None
            and self._can_resume_provider_session(resume_source)
        )
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
            model_preset_id=next_model_preset_id,
            provider_session_id=(
                resume_source.provider_session_id if resume_locally else None
            ),
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

        self._schedule_queued(rt)
        return self.store.load_node(pid, node.id) or node

    def _launch_node(
        self,
        rt: ProjectRuntime,
        node: Node,
        *,
        coro: Awaitable[None] | None = None,
    ) -> Node | None:
        if node.id in rt.runner_tasks or not rt.has_capacity():
            return None
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return None
        runner = NodeRunner(
            node,
            rt.project,
            self.store,
            rt.broadcast,
            reap_lock=rt.reap_lock,
        )
        task = asyncio.create_task(coro if coro is not None else runner.run())
        rt.runners[node.id] = runner
        rt.runner_tasks[node.id] = task
        rt.deferred_until_idle_node_ids.discard(node.id)
        task.add_done_callback(
            lambda _task, _rt=rt, _node_id=node.id: self._on_runner_done(
                _rt, _node_id, _task
            )
        )
        return runner

    def _schedule_queued(self, rt: ProjectRuntime) -> None:
        if not self.is_native_project(rt.project):
            return
        if not rt.is_running():
            rt.deferred_until_idle_node_ids.clear()
        while rt.has_capacity():
            if self._exclusive_node_active(rt):
                return
            priority = [
                node_id
                for node_id in rt.priority_node_ids
                if node_id not in rt.runner_tasks
                and (
                    (node := self.store.load_node(rt.project.id, node_id))
                    is not None
                    and node.state is NodeState.QUEUED
                    and self.is_native_node(rt.project, node)
                )
            ]
            rt.priority_node_ids = priority
            if priority:
                node = self.store.load_node(rt.project.id, priority[0])
                if (
                    node is not None
                    and self._is_exclusive_node(node)
                    and rt.active_count
                ):
                    return
                if node is not None and self._launch_node(rt, node) is not None:
                    rt.priority_node_ids.pop(0)
                    if self._is_exclusive_node(node):
                        return
                    continue
                return
            queued = sorted(
                (
                    node
                    for node in self.store.list_nodes(rt.project.id)
                    if node.state is NodeState.QUEUED
                    and node.id not in rt.runner_tasks
                    and node.id not in rt.deferred_until_idle_node_ids
                    and self.is_native_node(rt.project, node)
                ),
                key=lambda node: (node.created_at, node.id),
            )
            if not queued:
                return
            if self._is_exclusive_node(queued[0]) and rt.active_count:
                return
            if self._launch_node(rt, queued[0]) is None:
                return
            if self._is_exclusive_node(queued[0]):
                return

    @staticmethod
    def _is_exclusive_node(node: Node) -> bool:
        return (
            node.kind is NodeKind.OP and node.op_kind == "pull"
        ) or node.subtype is ReviewSubtype.CODE_REVIEW

    @classmethod
    def _exclusive_node_active(cls, rt: ProjectRuntime) -> bool:
        return any(
            cls._is_exclusive_node(runner.node)
            for runner in rt.runners.values()
        )

    @classmethod
    def _pull_active(cls, rt: ProjectRuntime) -> bool:
        return any(
            runner.node.kind is NodeKind.OP and runner.node.op_kind == "pull"
            for runner in rt.runners.values()
        )

    def _queued_pull_exists(self, rt: ProjectRuntime) -> bool:
        return any(
            node.kind is NodeKind.OP
            and node.op_kind == "pull"
            and node.state is NodeState.QUEUED
            and self.is_native_node(rt.project, node)
            for node in self.store.list_nodes(rt.project.id)
        )

    def _on_runner_done(
        self,
        rt: ProjectRuntime,
        node_id: str,
        task: asyncio.Task[None] | None = None,
    ) -> None:
        runner = rt.remove_runner(node_id)
        if runner is None:
            return
        if rt.closed:
            return
        finished_node = runner.node
        if task is not None and task.cancelled() and finished_node.state is NodeState.QUEUED:
            finished_node.state = NodeState.CANCELLED
            finished_node.error = "cancelled before runner start"
            finished_node.started_at = finished_node.created_at
            finished_node.finished_at = time.time()
            self.store.update_node(finished_node)
        if (
            finished_node.kind is NodeKind.OP
            and finished_node.op_kind == "commit"
            and finished_node.state is NodeState.DONE
            and finished_node.parent_node_id
            and finished_node.commit_after
            and finished_node.commit_after != finished_node.commit_before
        ):
            fresh_agent = self.store.load_node(
                rt.project.id, finished_node.parent_node_id
            )
            if (
                fresh_agent is not None
                and fresh_agent.commit_after != finished_node.commit_after
            ):
                fresh_agent.commit_after = finished_node.commit_after
                self.store.update_node(fresh_agent)
                try:
                    asyncio.get_running_loop().create_task(rt.broadcast({
                        "type": "node_updated",
                        "node_id": fresh_agent.id,
                        "node": fresh_agent.model_dump(),
                        "seq": 0,
                    }))
                except RuntimeError:
                    pass
        spawned_op = False
        if (
            finished_node.kind is NodeKind.AGENT
            and finished_node.state is NodeState.DONE
            and bool(rt.project.settings_override.get("auto_commit"))
        ):
            self._spawn_op_commit(rt, finished_node)
            spawned_op = True
        if not spawned_op and finished_node.state is NodeState.DONE:
            self._auto_promote_eligible_virtuals(rt)
        if not spawned_op:
            self._broadcast_git_status(rt)
        self._schedule_queued(rt)

    def _broadcast_git_status(self, rt: ProjectRuntime) -> None:
        """Schedule a node-less, ephemeral Git status event."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def publish() -> None:
            status = await asyncio.to_thread(git_status, rt.project.root_path)
            await rt.broadcast(GitStatus(**asdict(status)).model_dump())

        task = loop.create_task(publish())
        rt.background_tasks.add(task)

        def finish(done: asyncio.Task[None]) -> None:
            rt.background_tasks.discard(done)
            if done.cancelled():
                return
            error = done.exception()
            if error is not None:
                logger.error(
                    "failed to broadcast git status",
                    exc_info=(type(error), error, error.__traceback__),
                )

        task.add_done_callback(finish)

    def git_status(self, pid: str):
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        return git_status(rt.project.root_path)

    def quiescent(self, pid: str) -> bool:
        rt = self._runtimes.get(pid)
        if rt is None:
            return False
        return not rt.is_running() and self.queued_count(pid) == 0

    def spawn_git_op(self, pid: str, op_kind: str, *, message: str = "") -> Node | None:
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        self.require_native(pid)
        if op_kind not in {"commit", "pull"}:
            raise ValueError(f"unknown git op_kind: {op_kind}")
        node = Node(
            project_id=pid,
            kind=NodeKind.OP,
            op_kind=op_kind,
            state=NodeState.QUEUED,
            prompt=message.strip(),
            model_preset_id=rt.project.model_preset_id,
        )
        self.store.create_node(node)
        rt.priority_node_ids.append(node.id)
        self._schedule_queued(rt)
        return self.store.load_node(pid, node.id) or node

    async def spawn_code_review(self, pid: str) -> Node | None:
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        self.require_native(pid)
        for existing in self.store.list_nodes(pid):
            if (
                existing.subtype is ReviewSubtype.CODE_REVIEW
                and existing.state in {NodeState.QUEUED, NodeState.RUNNING}
            ):
                return existing
        if not await asyncio.to_thread(is_git_repo, rt.project.root_path):
            raise ValueError("code review requires a Git repository")
        # The Git probe yields to the event loop, so re-check before the
        # atomic create step to deduplicate concurrent POST requests.
        for existing in self.store.list_nodes(pid):
            if (
                existing.subtype is ReviewSubtype.CODE_REVIEW
                and existing.state in {NodeState.QUEUED, NodeState.RUNNING}
            ):
                return existing
        node = Node(
            project_id=pid,
            kind=NodeKind.AGENT,
            category=Category.REVIEW,
            subtype=ReviewSubtype.CODE_REVIEW,
            review_target=ReviewTarget(),
            state=NodeState.QUEUED,
            model_preset_id=default_code_review_model_preset_id(
                store_root=self.store.root
            ),
        )
        self.store.create_node(node)
        rt.priority_node_ids.append(node.id)
        self._schedule_queued(rt)
        return self.store.load_node(pid, node.id) or node

    async def git_push(self, pid: str):
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        self.require_native(pid)
        if self._pull_active(rt) or await asyncio.to_thread(self._queued_pull_exists, rt):
            from .git_state import git_status as read_git_status

            return await asyncio.to_thread(read_git_status, rt.project.root_path), "pull in progress"
        from .git_state import git_push
        return await asyncio.to_thread(git_push, rt.project.root_path)

    def create_planspace_and_launch_concierge(
        self,
        pid: str,
        *,
        title: str,
        seed: str,
        mode: str | None = None,
        provider: str | None = None,
        model_preset_id: str | None = None,
    ) -> PlanspaceCreationResult | None:
        """Create a new planspace and launch its concierge.

        The concierge is a planning-category agent node whose prompt is
        the rendered ``concierge_bootstrap.md`` template with the user's
        free-form ``seed`` substituted in. A new lane is activated only when
        the project is idle; otherwise the concierge remains queued without
        changing the current lane.
        """
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        self.require_native(pid)
        if not seed.strip():
            raise ValueError("seed must be non-empty")
        if provider is not None:
            raise ValueError("provider is no longer accepted; use model_preset_id")
        next_model_preset_id = (
            normalize_active_model_preset_id(
                model_preset_id, store_root=self.store.root
            )
            if model_preset_id is not None
            else rt.project.model_preset_id
        )
        normalized_mode = normalize_planspace_mode(mode)
        activated = not rt.is_running()
        self._preserve_implicit_active_planspace(rt, activated=activated)
        plug_id = create_planspace(
            rt.project,
            title=title or "Direction",
            mode=normalized_mode,
            store_root=self.store.root,
            seed_text=seed,
        )
        if activated:
            rt.project.active_planspace_id = plug_id
        rt.project.planspace_selection_explicit = True
        self.store.update_project(rt.project)

        prompt_text = _render_concierge_prompt(seed.strip())
        node = Node(
            project_id=pid,
            kind=NodeKind.AGENT,
            category=Category.PLANNING,
            state=NodeState.QUEUED,
            planspace_id=plug_id,
            model_preset_id=next_model_preset_id,
            prompt=prompt_text,
        )
        self.store.create_node(node)

        if not activated:
            rt.deferred_until_idle_node_ids.add(node.id)
        self._schedule_queued(rt)
        return PlanspaceCreationResult(
            node=self.store.load_node(pid, node.id) or node,
            activated=activated,
        )

    def create_blank_planspace(
        self,
        pid: str,
        *,
        title: str,
        seed: str,
        mode: str | None = None,
        provider: str | None = None,
        model_preset_id: str | None = None,
    ) -> PlanspaceCreationResult | None:
        """Create a planspace and seed it with one empty editable virtual."""
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        self.require_native(pid)
        if not seed.strip():
            raise ValueError("seed must be non-empty")
        if provider is not None:
            raise ValueError("provider is no longer accepted; use model_preset_id")
        next_model_preset_id = (
            normalize_active_model_preset_id(
                model_preset_id, store_root=self.store.root
            )
            if model_preset_id is not None
            else rt.project.model_preset_id
        )
        normalized_mode = normalize_planspace_mode(mode)
        activated = not rt.is_running()
        self._preserve_implicit_active_planspace(rt, activated=activated)
        plug_id = create_planspace(
            rt.project,
            title=title or seed.strip() or "Direction",
            mode=normalized_mode,
            store_root=self.store.root,
            seed_text=seed,
        )
        if activated:
            rt.project.active_planspace_id = plug_id
        rt.project.planspace_selection_explicit = True
        self.store.update_project(rt.project)

        node = self.create_virtual(
            pid,
            prompt_draft="",
            category=Category.REGULAR,
            motivation="",
            scheduled_deps=[],
            model_preset_id=next_model_preset_id,
            _allow_compatibility_model_preset=True,
            planspace_id=plug_id,
        )
        if node is None:
            return None
        return PlanspaceCreationResult(node=node, activated=activated)

    def _preserve_implicit_active_planspace(
        self,
        rt: ProjectRuntime,
        *,
        activated: bool,
    ) -> None:
        """Make a single-lane implicit selection durable before adding a lane."""
        if activated or rt.project.active_planspace_id:
            return
        active = resolve_active_planspace(
            rt.project, contextspace_root(self.store.root)
        )
        if active is None:
            return
        rt.project.active_planspace_id = active[1].id
        self.store.update_project(rt.project)

    # ---- auto-promotion ----

    def _auto_promote_eligible_virtuals(self, rt: ProjectRuntime) -> None:
        """Queue every currently eligible virtual on an auto planspace."""
        project = rt.project
        active_lane = project.active_planspace_id or ""
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
        while True:
            candidate = self._next_promotion_candidate(project.id, active_lane)
            if candidate is None:
                break
            if self.promote_virtual(project.id, candidate.id) is None:
                break
        self._schedule_queued(rt)

    def promote_next_virtual(self, pid: str) -> None:
        """Run one auto-promotion pass for callers that just seeded a lane."""
        rt = self._runtimes.get(pid)
        if rt is None:
            return
        self._auto_promote_eligible_virtuals(rt)

    def _next_promotion_candidate(
        self, pid: str, lane_id: str
    ) -> Node | None:
        """Return the earliest-created eligible virtual on ``lane_id``.

        Eligible = ``state == VIRTUAL``, no ``obsolete_reason``, and every
        ``scheduled_deps`` parent is ``DONE`` or an obsoleted virtual.
        Manual promotion is allowed to inspect failed/cancelled upstream
        nodes, but auto mode must not advance past a failed verifier/review.
        """
        project = self.get_project(pid)
        if project is None:
            return None
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
            if not self.is_native_node(project, n):
                continue
            if n.state is not NodeState.VIRTUAL:
                continue
            if n.obsolete_reason:
                continue
            if _virtual_requires_prompt(n.subtype) and not (
                n.prompt_draft or ""
            ).strip():
                continue
            if not all(is_dep_auto_ready(dep) for dep in n.scheduled_deps):
                continue
            eligible.append(n)
        if not eligible:
            return None
        eligible.sort(key=lambda n: (n.created_at, n.id))
        return eligible[0]

    def promote_virtual(self, pid: str, vid: str) -> Node | None:
        """Promote an eligible virtual node to the durable ``queued`` state."""
        result = self.promote_virtual_result(pid, vid)
        return result.node if result.code is None else None

    def claim_foreign_virtual(self, pid: str, vid: str) -> Node:
        """Copy a foreign virtual into this host without mutating its source."""
        rt = self._runtimes.get(pid)
        if rt is None:
            raise KeyError(pid)
        project = self.require_native(pid)
        source = self.store.load_node(pid, vid)
        if source is None:
            raise KeyError(vid)
        if source.state is not NodeState.VIRTUAL:
            raise ValueError("only virtual nodes can be claimed")
        if self.is_native_node(project, source):
            raise ValueError("local virtual nodes must use promote")
        if source.obsolete_reason:
            raise ValueError("obsolete virtual nodes cannot be claimed")

        local_claim = next(
            (
                claim
                for claim in self.store.list_claims(pid).get(source.id, [])
                if claim.get("claimed_by") == self.store.machine.id
            ),
            None,
        )
        if local_claim is not None:
            existing = self.store.load_node(pid, str(local_claim["as_node"]))
            if (
                existing is not None
                and self.is_native_node(project, existing)
                and existing.promoted_from == source.id
            ):
                return existing

        settings_snapshot: dict[str, Any] = {}
        if source.pending_extra_principles:
            settings_snapshot["extra_principles"] = list(
                source.pending_extra_principles
            )
        if source.pending_extra_skills:
            settings_snapshot["extra_skills"] = [
                dict(item) for item in source.pending_extra_skills
            ]
        claimed = Node(
            project_id=pid,
            kind=source.kind,
            agent_op_kind=source.agent_op_kind,
            state=NodeState.QUEUED,
            planspace_id=source.planspace_id,
            model_preset_id=source.model_preset_id,
            prompt=source.prompt_draft or source.prompt,
            prompt_draft=source.prompt_draft,
            category=source.category,
            subtype=source.subtype,
            brief=source.brief,
            review_target=source.review_target,
            scheduled_deps=list(source.scheduled_deps),
            settings_snapshot=settings_snapshot,
            verify_script_ref=source.verify_script_ref,
            promoted_from=source.id,
        )
        self.store.create_node(claimed)
        self.store.write_claim(
            pid,
            source.id,
            {
                "as_node": claimed.id,
                "claimed_at": time.time(),
            },
        )
        self._schedule_queued(rt)
        return self.store.load_node(pid, claimed.id) or claimed

    def promote_virtual_result(self, pid: str, vid: str) -> VirtualPromotionResult:
        """Promote a virtual and preserve a machine-readable conflict reason.

        The API uses this richer result to make retries idempotent and to avoid
        collapsing unrelated validation failures into an opaque HTTP 409.
        Internal scheduler callers retain the narrower ``promote_virtual`` API.
        """
        rt = self._runtimes.get(pid)
        if rt is None:
            return VirtualPromotionResult(
                None,
                "project_unavailable",
                "Project runtime is unavailable.",
            )
        self.require_native(pid)
        node = self.store.load_node(pid, vid)
        if node is None:
            return VirtualPromotionResult(
                None,
                "virtual_not_found",
                "Virtual node was not found.",
            )
        self.require_native_node(rt.project, node)
        if node.state is not NodeState.VIRTUAL:
            if node.proposed_by:
                return VirtualPromotionResult(
                    node,
                    "already_promoted",
                    "Virtual node has already been promoted.",
                )
            return VirtualPromotionResult(
                None,
                "node_not_virtual",
                "Node is not a promotable virtual.",
            )
        if node.obsolete_reason:
            return VirtualPromotionResult(
                None,
                "virtual_obsolete",
                "Obsolete virtual nodes cannot be promoted.",
            )
        if (
            _virtual_requires_prompt(node.subtype)
            and not (node.prompt_draft or "").strip()
        ):
            return VirtualPromotionResult(
                None,
                "prompt_required",
                "Virtual node needs a prompt before it can be promoted.",
            )
        active = resolve_active_planspace(
            rt.project, contextspace_root(self.store.root)
        )
        active_lane = active[1].id if active is not None else ""
        node_lane = node.planspace_id or ""
        if node_lane != active_lane:
            return VirtualPromotionResult(
                None,
                "outside_active_planspace",
                "Virtual node is outside the active planspace.",
            )
        blockers: list[str] = []
        for dep in node.scheduled_deps:
            parent = self.store.load_node(pid, dep)
            if parent is None:
                continue
            if parent.state in TERMINAL_NODE_STATES:
                continue
            if parent.state is NodeState.VIRTUAL and parent.obsolete_reason:
                continue
            blockers.append(dep)
        if blockers:
            return VirtualPromotionResult(
                None,
                "dependencies_not_terminal",
                "Virtual node has dependencies that are not terminal.",
                tuple(blockers),
            )
        if node.resume_from_node_id:
            resume_parent = self.store.load_node(pid, node.resume_from_node_id)
            if resume_parent is None:
                return VirtualPromotionResult(
                    None,
                    "resume_source_missing",
                    "Continuation source node was not found.",
                )
            if resume_parent.state not in TERMINAL_NODE_STATES:
                return VirtualPromotionResult(
                    None,
                    "resume_source_not_terminal",
                    "Continuation source node is not terminal.",
                    (resume_parent.id,),
                )
            if (
                not resume_parent.provider_session_id
                and self._can_resume_provider_session(resume_parent)
            ):
                return VirtualPromotionResult(
                    None,
                    "resume_session_unavailable",
                    "Continuation source has no provider session to resume.",
                )
            node.model_preset_id = resume_parent.model_preset_id
            node.provider_session_id = (
                resume_parent.provider_session_id
                if self._can_resume_provider_session(resume_parent)
                else None
            )
            node.parent_node_id = resume_parent.id
        try:
            self.store.write_node_preview(pid, node.id, render_virtual_preview(node))
        except Exception:  # noqa: BLE001
            logger.exception("failed to preserve virtual preview before promotion")
            return VirtualPromotionResult(
                None,
                "preview_write_failed",
                "Virtual preview could not be preserved before promotion.",
            )
        node.state = NodeState.QUEUED
        if node.kind is NodeKind.AGENT:
            node.prompt = node.prompt_draft or node.prompt
        node.prompt_draft = None
        # Promote pending_extra_principles → settings_snapshot["extra_principles"]
        # so compose_context_bundle at launch sees them (see contextspace
        # ``_extra_principle_ids``).
        if node.pending_extra_principles:
            snapshot = dict(node.settings_snapshot)
            snapshot["extra_principles"] = list(node.pending_extra_principles)
            node.settings_snapshot = snapshot
            node.pending_extra_principles = []
        if node.pending_extra_skills:
            snapshot = dict(node.settings_snapshot)
            snapshot["extra_skills"] = list(node.pending_extra_skills)
            node.settings_snapshot = snapshot
            node.pending_extra_skills = []
        self.store.update_node(node)

        try:
            asyncio.get_running_loop().create_task(rt.broadcast({
                "type": "node_updated",
                "node_id": node.id,
                "node": node.model_dump(),
                "seq": 0,
            }))
        except RuntimeError:
            pass
        self._schedule_queued(rt)
        return VirtualPromotionResult(self.store.load_node(pid, node.id) or node)

    def dequeue_node(self, pid: str, nid: str) -> Node | None:
        """Return a manually scheduled queued node to editable virtual state."""
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        self.require_native(pid)
        node = self.store.load_node(pid, nid)
        if node is not None:
            self.require_native_node(rt.project, node)
        if (
            node is None
            or node.state is not NodeState.QUEUED
            or node.kind is NodeKind.OP
            or node.id in rt.runner_tasks
        ):
            return None
        lane_id = node.planspace_id or ""
        if not lane_id:
            return None
        try:
            mode = read_planspace_mode(
                rt.project, lane_id, store_root=self.store.root
            )
        except Exception:  # noqa: BLE001
            logger.exception("planspace mode lookup failed during dequeue")
            return None
        if mode is PlanspaceMode.AUTO:
            return None

        snapshot = dict(node.settings_snapshot)
        pending_extra_principles = normalize_principle_ids(
            snapshot.pop("extra_principles", [])
        )
        pending_extra_skills = expand_skill_selections(
            snapshot.pop("extra_skills", []),
            store_root=self.store.root,
        )
        resume_from_node_id = node.resume_from_node_id
        if resume_from_node_id is None and node.provider_session_id:
            resume_from_node_id = node.parent_node_id

        virtual = node.model_copy(
            update={
                "state": NodeState.VIRTUAL,
                "prompt": "",
                "prompt_draft": node.prompt,
                "pending_extra_principles": pending_extra_principles,
                "pending_extra_skills": pending_extra_skills,
                "resume_from_node_id": resume_from_node_id,
                "provider_session_id": None,
                "provider_turn_id": None,
                "settings_snapshot": snapshot,
                "proposed_by": node.proposed_by or "user",
                "promoted_from": None,
                "started_at": None,
                "finished_at": None,
            }
        )
        self.store.write_node_preview(
            pid, virtual.id, render_virtual_preview(virtual)
        )
        self.store.update_node(virtual)
        rt.priority_node_ids = [
            node_id for node_id in rt.priority_node_ids if node_id != virtual.id
        ]

        try:
            asyncio.get_running_loop().create_task(rt.broadcast({
                "type": "node_updated",
                "node_id": virtual.id,
                "node": virtual.model_dump(),
                "seq": 0,
            }))
        except RuntimeError:
            pass
        return self.store.load_node(pid, virtual.id) or virtual

    def create_virtual(
        self,
        pid: str,
        *,
        prompt_draft: str,
        category: str | Category | None = Category.REGULAR,
        subtype: str | ReviewSubtype | None = None,
        brief: dict[str, Any] | ReviewBrief | None = None,
        review_target: dict[str, Any] | ReviewTarget | None = None,
        motivation: str | None = None,
        scheduled_deps: list[str] | None = None,
        pending_extra_principles: list[str] | None = None,
        pending_extra_skills: list[str | dict[str, Any]] | None = None,
        agent_op_kind: str | None = None,
        provider: str | None = None,
        model_preset_id: str | None = None,
        planspace_id: str | None = None,
        node_id: str | None = None,
        parent_node_id: str | None = None,
        resume_from_node_id: str | None = None,
        _allow_compatibility_model_preset: bool = False,
        _allow_nonterminal_resume: bool = False,
        _proposed_by: str = "user",
        _template_instance_id: str | None = None,
        _defer_auto_promotion: bool = False,
        _created_at: float | None = None,
    ) -> Node | None:
        """Create a user-authored editable virtual node.

        Returns the created node, or ``None`` when the project is missing.
        Validation failures raise ``ValueError`` with a client-facing message.
        """
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        self.require_native(pid)
        if provider is not None:
            raise ValueError("provider is no longer accepted; use model_preset_id")

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
            allow_nonterminal=_allow_nonterminal_resume,
        )
        resume_source_for_provider: Node | None = None
        if normalized_resume_id:
            resume_source_for_provider = self.store.load_node(pid, normalized_resume_id)
        if resume_source_for_provider is not None:
            if (
                model_preset_id is not None
                and normalize_model_preset_id(
                    model_preset_id, store_root=self.store.root
                )
                != resume_source_for_provider.model_preset_id
            ):
                raise ValueError(
                    "resume virtuals inherit model_preset_id from their source node"
                )
            next_model_preset_id = resume_source_for_provider.model_preset_id
        else:
            if model_preset_id is None:
                next_model_preset_id = rt.project.model_preset_id
            else:
                next_model_preset_id = normalize_model_preset_id(
                    model_preset_id, store_root=self.store.root
                )
                if not _allow_compatibility_model_preset:
                    next_model_preset_id = normalize_active_model_preset_id(
                        next_model_preset_id, store_root=self.store.root
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

        next_review_target: ReviewTarget | None = None
        if review_target is not None:
            try:
                next_review_target = (
                    review_target
                    if isinstance(review_target, ReviewTarget)
                    else ReviewTarget.model_validate(review_target)
                )
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"invalid review_target: {exc}") from exc

        if next_category is not Category.REVIEW:
            if next_review_target is not None:
                raise ValueError(
                    "review_target is only valid on code_review virtuals"
                )
            next_subtype = None
            next_brief = None
            next_review_target = None
        else:
            if next_subtype is None:
                raise ValueError("review virtuals require a subtype")
            if (
                next_subtype is not ReviewSubtype.CODE_REVIEW
                and next_brief is None
            ):
                raise ValueError("review virtuals require a brief")
            if next_subtype is ReviewSubtype.CODE_REVIEW:
                next_review_target = next_review_target or ReviewTarget()
            elif next_review_target is not None:
                raise ValueError("review_target is only valid on code_review virtuals")

        if agent_op_kind is not None:
            if agent_op_kind not in KNOWN_AGENT_OP_KINDS:
                raise ValueError(f"unknown agent_op_kind: {agent_op_kind!r}")
            if (
                agent_op_kind in AUTHORING_AGENT_OP_KINDS
                and next_category is not Category.REGULAR
            ):
                raise ValueError(f"{agent_op_kind} requires category=regular")

        node_kwargs: dict[str, Any] = {}
        if node_id is not None:
            node_kwargs["id"] = node_id
        if _created_at is not None:
            node_kwargs["created_at"] = _created_at
        node = Node(
            **node_kwargs,
            project_id=pid,
            kind=NodeKind.AGENT,
            agent_op_kind=agent_op_kind,
            category=next_category,
            subtype=next_subtype,
            brief=next_brief,
            review_target=next_review_target,
            state=NodeState.VIRTUAL,
            parent_node_id=normalized_parent_id,
            planspace_id=lane_id,
            model_preset_id=next_model_preset_id,
            prompt="",
            prompt_draft=str(prompt_draft),
            scheduled_deps=[],
            pending_extra_principles=normalize_principle_ids(pending_extra_principles),
            pending_extra_skills=expand_skill_selections(
                pending_extra_skills,
                store_root=self.store.root,
            ),
            resume_from_node_id=normalized_resume_id,
            proposed_by=_proposed_by,
            template_instance_id=_template_instance_id,
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
                "node_id": node.id,
                "node": node.model_dump(),
                "seq": 0,
            }))
        except RuntimeError:
            pass

        active_lane = rt.project.active_planspace_id or ""
        if not _defer_auto_promotion and active_lane == lane_id:
            try:
                mode = read_planspace_mode(
                    rt.project, active_lane, store_root=self.store.root
                )
            except Exception:  # noqa: BLE001
                logger.exception("planspace mode lookup failed")
            else:
                if mode is PlanspaceMode.AUTO:
                    self._auto_promote_eligible_virtuals(rt)
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
        review_target: dict[str, Any] | ReviewTarget | None | object = _UNSET,
        motivation: str | None | object = _UNSET,
        scheduled_deps: list[str] | None | object = _UNSET,
        pending_extra_principles: list[str] | None | object = _UNSET,
        pending_extra_skills: list[str | dict[str, Any]] | None | object = _UNSET,
        agent_op_kind: str | None | object = _UNSET,
        provider: str | None | object = _UNSET,
        model_preset_id: str | None | object = _UNSET,
        obsolete_reason: str | None | object = _UNSET,
    ) -> Node | None:
        """Update a virtual node in place.

        Returns the updated node. ``None`` means the project/node is missing, the
        or the target is not an editable virtual. Validation
        failures raise ``ValueError`` with a client-facing message.
        """
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        self.require_native(pid)
        existing = self.store.load_node(pid, vid)
        if existing is None:
            return None
        self.require_native_node(rt.project, existing)
        if existing.kind is not NodeKind.AGENT or existing.state is not NodeState.VIRTUAL:
            return None

        update: dict[str, Any] = {}
        if prompt_draft is not _UNSET:
            update["prompt_draft"] = (
                "" if prompt_draft is None else str(prompt_draft)
            )
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

        next_review_target = existing.review_target
        if review_target is not _UNSET:
            if review_target is None:
                next_review_target = None
            elif isinstance(review_target, ReviewTarget):
                next_review_target = review_target
            else:
                try:
                    next_review_target = ReviewTarget.model_validate(review_target)
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(f"invalid review_target: {exc}") from exc
            update["review_target"] = next_review_target

        if next_category is not Category.REVIEW:
            if review_target is not _UNSET and next_review_target is not None:
                raise ValueError(
                    "review_target is only valid on code_review virtuals"
                )
            next_subtype = None
            next_brief = None
            next_review_target = None
            update["subtype"] = None
            update["brief"] = None
            update["review_target"] = None
        else:
            if next_subtype is None:
                raise ValueError("review virtuals require a subtype")
            if (
                next_subtype is not ReviewSubtype.CODE_REVIEW
                and next_brief is None
            ):
                raise ValueError("review virtuals require a brief")
            update["subtype"] = next_subtype
            update["brief"] = next_brief
            if next_subtype is ReviewSubtype.CODE_REVIEW:
                update["review_target"] = next_review_target or ReviewTarget()
            elif next_review_target is not None:
                if review_target is _UNSET:
                    next_review_target = None
                    update["review_target"] = None
                else:
                    raise ValueError(
                        "review_target is only valid on code_review virtuals"
                    )
        next_agent_op_kind = existing.agent_op_kind
        if agent_op_kind is not _UNSET:
            if agent_op_kind is None or str(agent_op_kind) == "":
                next_agent_op_kind = None
            else:
                next_agent_op_kind = str(agent_op_kind)
                if next_agent_op_kind not in KNOWN_AGENT_OP_KINDS:
                    raise ValueError(
                        f"unknown agent_op_kind: {agent_op_kind!r}"
                    )
            update["agent_op_kind"] = next_agent_op_kind
        if (
            next_agent_op_kind in AUTHORING_AGENT_OP_KINDS
            and next_category is not Category.REGULAR
        ):
            raise ValueError(
                f"{next_agent_op_kind} requires category=regular"
            )

        next_prompt_draft = update.get("prompt_draft", existing.prompt_draft or "")
        if (
            any(value is not _UNSET for value in (prompt_draft, category, subtype))
            and _virtual_requires_prompt(next_subtype)
            and not str(next_prompt_draft).strip()
        ):
            raise ValueError("prompt_draft must be non-empty")

        if scheduled_deps is not _UNSET:
            deps = self._normalize_virtual_scheduled_deps(
                pid,
                virtual_id=existing.id,
                lane_id=existing.planspace_id or "",
                scheduled_deps=scheduled_deps,
            )
            update["scheduled_deps"] = deps

        if pending_extra_principles is not _UNSET:
            update["pending_extra_principles"] = normalize_principle_ids(
                pending_extra_principles
            )

        if pending_extra_skills is not _UNSET:
            update["pending_extra_skills"] = expand_skill_selections(
                pending_extra_skills,
                store_root=self.store.root,
            )

        if provider is not _UNSET:
            raise ValueError("provider is no longer accepted; use model_preset_id")

        if model_preset_id is not _UNSET:
            if model_preset_id is None:
                raise ValueError("model_preset_id is required")
            next_model_preset_id = normalize_model_preset_id(
                str(model_preset_id), store_root=self.store.root
            )
            if existing.resume_from_node_id:
                resume_source = self.store.load_node(pid, existing.resume_from_node_id)
                source_model_preset_id = (
                    resume_source.model_preset_id
                    if resume_source is not None
                    else existing.model_preset_id
                )
                if next_model_preset_id != source_model_preset_id:
                    raise ValueError(
                        "resume virtuals inherit model_preset_id from their source node"
                    )
            if next_model_preset_id != existing.model_preset_id:
                next_model_preset_id = normalize_active_model_preset_id(
                    next_model_preset_id, store_root=self.store.root
                )
            update["model_preset_id"] = next_model_preset_id
        updated = existing.model_copy(update=update)
        updated = Node.model_validate(
            updated.model_dump(exclude={"provider", "owner_host_id"})
        )
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
                "node_id": updated.id,
                "node": updated.model_dump(),
                "seq": 0,
            }))
        except RuntimeError:
            pass
        active_lane = rt.project.active_planspace_id or ""
        if active_lane == lane_id:
            try:
                mode = read_planspace_mode(
                    rt.project, active_lane, store_root=self.store.root
                )
            except Exception:  # noqa: BLE001
                logger.exception("planspace mode lookup failed")
            else:
                if mode is PlanspaceMode.AUTO:
                    self._auto_promote_eligible_virtuals(rt)
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
        self.require_native(pid)
        node = self.store.load_node(pid, vid)
        if node is None:
            return False, []
        self.require_native_node(rt.project, node)
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
            if not self.is_native_node(rt.project, other):
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
            event = NodeRemoved(id=vid).model_dump()
            asyncio.get_running_loop().create_task(rt.broadcast(event))
        except RuntimeError:
            pass
        return True, []

    def rerun_node(self, pid: str, nid: str) -> Node | None:
        """Create a fresh virtual carrying the same prompt as a failed node.

        Used by the rerun UI for nodes in ERROR or CANCELLED state (e.g.,
        crashed by a backend restart). Returns ``None`` when the project
        or node is missing, or the target is not a
        rerunnable agent node. Validation failures raise ``ValueError``.
        """
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        self.require_native(pid)
        original = self.store.load_node(pid, nid)
        if original is None:
            return None
        if original.kind is not NodeKind.AGENT:
            raise ValueError("only agent nodes support rerun")
        if original.state not in {NodeState.ERROR, NodeState.CANCELLED}:
            raise ValueError("only error/cancelled nodes support rerun")
        prompt = (original.prompt or original.prompt_draft or "").strip()
        if _virtual_requires_prompt(original.subtype) and not prompt:
            raise ValueError("original node has no prompt to rerun")

        virtual = self.create_virtual(
            pid,
            prompt_draft=prompt,
            category=original.category or Category.REGULAR,
            subtype=original.subtype,
            brief=original.brief,
            review_target=original.review_target,
            motivation=f"rerun of {original.id[:8]}",
            scheduled_deps=list(original.scheduled_deps or []),
            pending_extra_principles=normalize_principle_ids(
                original.settings_snapshot.get("extra_principles")
            ),
            pending_extra_skills=expand_skill_selections(
                original.settings_snapshot.get("extra_skills"),
                store_root=self.store.root,
            ),
            agent_op_kind=original.agent_op_kind,
            model_preset_id=original.model_preset_id,
            _allow_compatibility_model_preset=True,
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
        allow_nonterminal: bool = False,
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
        if allow_nonterminal:
            return source_id
        if source.state not in TERMINAL_NODE_STATES:
            raise ValueError("resume_from_node_id must reference a terminal node")
        if (
            not source.provider_session_id
            and self._can_resume_provider_session(source)
        ):
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
            model_preset_id=agent_node.model_preset_id,
            planspace_id=agent_node.planspace_id,
        )
        self.store.create_node(op_node)
        rt.priority_node_ids.append(op_node.id)
        self._schedule_queued(rt)

    def interrupt(self, pid: str, node_id: str) -> bool:
        rt = self._runtimes.get(pid)
        if rt is None:
            return False
        self.require_native(pid)
        node = self.store.load_node(pid, node_id)
        if node is not None:
            self.require_native_node(rt.project, node)
        runner = rt.get_runner(node_id)
        task = rt.runner_tasks.get(node_id)
        if runner is None or task is None or task.done():
            return False
        asyncio.create_task(runner.interrupt())
        task.cancel()
        return True

    def resolve_gate(
        self,
        pid: str,
        gate_id: str,
        *,
        node_id: str | None = None,
        allow: bool,
        message: str = "",
        updated_input: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        scope: str | None = None,
        interrupt: bool = False,
        permission_mode: str | None = None,
        clear_context: bool = False,
    ) -> bool:
        rt = self._runtimes.get(pid)
        if rt is None:
            return False
        self.require_native(pid)
        runners = (
            [rt.get_runner(node_id)]
            if node_id is not None
            else list(rt.runners.values())
        )
        for runner in runners:
            if runner is not None and runner.resolve_gate(
                gate_id,
                allow=allow,
                message=message,
                updated_input=updated_input,
                response=response,
                scope=scope,
                interrupt=interrupt,
                permission_mode=permission_mode,
                clear_context=clear_context,
            ):
                return True
        return False
