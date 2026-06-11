"""ProjectRegistry — in-memory orchestration over the disk Store.

The legacy "session" id maps 1:1 to a project id. Each user prompt
becomes a new agent :class:`Node`. New nodes start with a fresh
provider session by default; conversation continuation is explicit, not
inherited from timeline adjacency.

Within a project, only one node runs at a time (DESIGN §2.2).
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from .domain import (
    Node,
    NodeKind,
    NodeState,
    Project,
)
from .contextspace import delete_project_contextspace, review_guidance_output_relpath
from .language import normalize_preferred_language
from .runner import NodeRunner
from .store import Store
from .workspace import create_temporary_root, remove_temporary_root

logger = logging.getLogger(__name__)


_NO_GUIDANCE = (
    "# Review handoff unavailable\n\n"
    "_The previous agent did not write a review handoff._\n"
)
_UNSET: object = object()


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
        # Load existing projects from disk so they survive a restart.
        for project in self.store.list_projects():
            self._runtimes[project.id] = ProjectRuntime(project)

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
        scenario_name: str | None = None,
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
            root_path = cwd
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
            scenario_name=scenario_name,
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
        needs_review: bool | None = None,
        extra_planspace_loads: list[str] | None = None,
        scenario_step_id: str | None = None,
    ) -> NodeRunner | None:
        """Create a new agent node and launch its runner as a task.

        Returns the runner, or ``None`` if the project is unknown, a
        node is already running, or a requested resume source is
        invalid.
        """
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
            if resume_source.state not in {
                NodeState.DONE,
                NodeState.ERROR,
                NodeState.CANCELLED,
            }:
                return None
            if not (resume_source.provider_session_id or resume_source.sdk_session_id):
                return None

        extra_loads = [
            entry.strip()
            for entry in (extra_planspace_loads or [])
            if isinstance(entry, str) and entry.strip()
        ]
        settings_snapshot: dict[str, Any] = {}
        if extra_loads:
            settings_snapshot["extra_planspace_loads"] = extra_loads

        node = Node(
            project_id=pid,
            kind=NodeKind.AGENT,
            state=NodeState.QUEUED,
            parent_node_id=resume_source.id if resume_source else None,
            provider=resume_source.provider if resume_source else rt.project.provider,
            provider_session_id=resume_source.provider_session_id if resume_source else None,
            sdk_session_id=resume_source.sdk_session_id if resume_source else None,
            requires_review=bool(needs_review),
            prompt=prompt,
            scenario_step_id=scenario_step_id,
            settings_snapshot=settings_snapshot,
        )
        self.store.create_node(node)

        runner = NodeRunner(node, rt.project, self.store, rt.broadcast)
        rt.runner = runner
        rt.runner_task = asyncio.create_task(runner.run())
        rt.runner_task.add_done_callback(lambda _t, _rt=rt: self._on_runner_done(_rt))
        return runner

    def start_gate_node(
        self,
        pid: str,
        brief: str,
        *,
        scenario_step_id: str | None = None,
        parent_node_id: str | None = None,
    ) -> NodeRunner | None:
        """Create a passive gate node and launch its runner.

        Gates are pure human checkpoints — the runner skips any provider
        call and goes straight to ``awaiting_review`` with ``brief`` as
        the rendered contract.
        """
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        if rt.is_running():
            return None

        node = Node(
            project_id=pid,
            kind=NodeKind.GATE,
            state=NodeState.QUEUED,
            provider=rt.project.provider,
            contract=brief,
            scenario_step_id=scenario_step_id,
            parent_node_id=parent_node_id,
        )
        self.store.create_node(node)

        runner = NodeRunner(node, rt.project, self.store, rt.broadcast)
        rt.runner = runner
        rt.runner_task = asyncio.create_task(runner.run())
        rt.runner_task.add_done_callback(lambda _t, _rt=rt: self._on_runner_done(_rt))
        return runner

    def _on_runner_done(self, rt: ProjectRuntime) -> None:
        finished_node = rt.runner.node if rt.runner else None
        rt.runner = None
        rt.runner_task = None

        if finished_node is None:
            return
        spawned_op = False
        if (
            finished_node.kind in (NodeKind.AGENT, NodeKind.GATE)
            and finished_node.state is NodeState.DONE
            and bool(rt.project.settings_override.get("auto_commit"))
        ):
            self._spawn_op_commit(rt, finished_node)
            spawned_op = True
        if not spawned_op:
            self._advance_scenario_step(rt, finished_node)
            self._advance_user_gate(rt, finished_node)

    def _spawn_op_commit(self, rt: ProjectRuntime, agent_node: Node) -> None:
        op_node = Node(
            project_id=rt.project.id,
            kind=NodeKind.OP,
            op_kind="commit",
            state=NodeState.QUEUED,
            parent_node_id=agent_node.id,
            provider=agent_node.provider,
        )
        self.store.create_node(op_node)

        runner = NodeRunner(op_node, rt.project, self.store, rt.broadcast)
        rt.runner = runner
        rt.runner_task = asyncio.create_task(
            self._run_op_and_rewrite(rt, runner, agent_node.id)
        )
        rt.runner_task.add_done_callback(lambda _t, _rt=rt: self._on_runner_done(_rt))

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

    def _advance_scenario_step(
        self,
        rt: ProjectRuntime,
        finished_node: Node,
    ) -> None:
        """Advance the project's scenario cursor when a step finishes.

        Called after an agent/gate scenario step (or its auto-commit op
        child) terminates. Records the step in
        ``project.scenario_step_history`` and, if the step succeeded and
        the scenario has more steps, enqueues the next one.

        Non-DONE terminal states halt the scenario — the Verify card
        still appears since all nodes are terminal, but no further
        steps fire.
        """
        project = rt.project
        if not project.scenario_name:
            return

        # If we just finished an auto-commit op, route to its parent
        # scenario step. Ops themselves are not scenario steps.
        step_node = finished_node
        if finished_node.kind is NodeKind.OP and finished_node.parent_node_id:
            parent = self.store.load_node(project.id, finished_node.parent_node_id)
            if parent is not None and parent.scenario_step_id:
                step_node = parent
            else:
                return

        if not step_node.scenario_step_id:
            return

        # Avoid double-recording if the same step is reported twice
        # (e.g. agent->op both arrive here).
        history = list(project.scenario_step_history)
        if any(h.get("step_id") == step_node.scenario_step_id for h in history):
            already_recorded = True
        else:
            already_recorded = False
            entry: dict[str, Any] = {
                "step_id": step_node.scenario_step_id,
                "node_id": step_node.id,
                "terminal_state": step_node.state.value,
            }
            # Gate completions carry a derived decision so downstream
            # `when:` predicates can branch on the human's response.
            if step_node.kind is NodeKind.GATE and step_node.review_outcome:
                entry["decision"] = step_node.review_outcome
            history.append(entry)
            project.scenario_step_history = history
            self.store.update_project(project)

        # Halt the scenario on non-DONE terminal states.
        if step_node.state is not NodeState.DONE:
            return

        try:
            from .scenarios import load_scenario as _load_scenario
            scenario = _load_scenario(project.scenario_name)
        except Exception:  # noqa: BLE001
            logger.exception("scenario load failed during step advance")
            return

        step_idx = next(
            (
                i
                for i, spec in enumerate(scenario.nodes)
                if spec.id == step_node.scenario_step_id
            ),
            None,
        )
        if step_idx is None:
            return

        # Don't double-launch if the next step was already enqueued earlier
        # (e.g. recovered state on restart).
        if already_recorded:
            return

        # Walk forward, skipping steps whose `when:` predicate doesn't
        # match the recorded gate decision. This keeps the YAML linear
        # (no DAG) while supporting reject-driven branches.
        next_idx = step_idx + 1
        while next_idx < len(scenario.nodes):
            cand = scenario.nodes[next_idx]
            if self._step_when_matches(project, cand):
                break
            next_idx += 1
        if next_idx >= len(scenario.nodes):
            return

        next_spec = scenario.nodes[next_idx]
        if next_spec.kind == "agent":
            resume_node_id = self._resolve_resume_node(project, next_spec)
            self.start_node(
                project.id,
                next_spec.prompt,
                needs_review=next_spec.needs_review,
                scenario_step_id=next_spec.id,
                resume_from_node_id=resume_node_id,
            )
        elif next_spec.kind == "gate":
            brief = self._load_gate_brief(project, scenario, next_spec)
            parent_node_id = self._resolve_brief_source_node_id(project, next_spec)
            self.start_gate_node(
                project.id,
                brief,
                scenario_step_id=next_spec.id,
                parent_node_id=parent_node_id,
            )

    def _step_when_matches(self, project: Project, spec: Any) -> bool:
        """True if ``spec`` has no `when:` or its predicate matches history."""
        if not spec.when_step:
            return True
        for h in project.scenario_step_history:
            if h.get("step_id") == spec.when_step:
                return h.get("decision") == spec.when_decision
        return False

    def _resolve_resume_node(self, project: Project, spec: Any) -> str | None:
        """Return the node id matching ``spec.resume_from`` from history, if any."""
        if not spec.resume_from:
            return None
        for h in project.scenario_step_history:
            if h.get("step_id") == spec.resume_from:
                node_id = h.get("node_id")
                return node_id if isinstance(node_id, str) else None
        return None

    def _load_gate_brief(
        self,
        project: Project,
        scenario: Any,
        next_spec: Any,
    ) -> str:
        """Read the brief markdown the previous agent step produced.

        Falls back to a placeholder when the source step or brief file
        cannot be resolved so the gate still renders.
        """
        if not next_spec.brief_from:
            return next_spec.contract or _NO_GUIDANCE
        src_node_id = self._resolve_brief_source_node_id(project, next_spec)
        if not src_node_id:
            return f"_(brief source step `{next_spec.brief_from}` not found)_\n"
        src_node = self.store.load_node(project.id, src_node_id)
        if src_node is None:
            return f"_(brief source node `{src_node_id}` missing on disk)_\n"
        return self._read_review_guidance_from_node(project, src_node)

    def _resolve_brief_source_node_id(
        self,
        project: Project,
        next_spec: Any,
    ) -> str | None:
        if not getattr(next_spec, "brief_from", ""):
            return None
        for h in project.scenario_step_history:
            if h.get("step_id") == next_spec.brief_from:
                node_id = h.get("node_id")
                return node_id if isinstance(node_id, str) else None
        return None

    def _read_review_guidance_from_node(self, project: Project, src_node: Node) -> str:
        guidance = self._read_project_rel_text(
            project,
            review_guidance_output_relpath(src_node),
        )
        if guidance is None:
            return (
                f"_(review handoff file not written: "
                f"`{review_guidance_output_relpath(src_node)}`)_\n"
            )
        return guidance.strip() and guidance or _NO_GUIDANCE

    def _read_project_rel_text(self, project: Project, rel_path: str) -> str | None:
        rel = Path(rel_path)
        if rel.is_absolute() or any(part == ".." for part in rel.parts):
            return None
        root = Path(project.root_path).resolve()
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return None
        if not target.exists():
            return None
        try:
            return target.read_text(encoding="utf-8")
        except OSError:
            return None

    def _advance_user_gate(
        self,
        rt: ProjectRuntime,
        finished_node: Node,
    ) -> None:
        """Spawn a follow-up gate for a user-launched review-required agent.

        Mirrors the scenario expander's gate handoff: when an agent finishes
        DONE with ``requires_review`` and was not part of a scenario, read its
        transient review guidance and start a passive gate whose contract is
        that guidance. With auto_commit on, the source is the parent of the
        just-finished commit op.
        """
        project = rt.project
        source = finished_node
        if finished_node.kind is NodeKind.OP and finished_node.parent_node_id:
            parent = self.store.load_node(project.id, finished_node.parent_node_id)
            if parent is None:
                return
            source = parent
        if source.kind is not NodeKind.AGENT:
            return
        if source.scenario_step_id:
            return
        if not source.requires_review:
            return
        if source.state is not NodeState.DONE:
            return
        # Avoid double-spawn: if a gate already references this node as
        # parent, the handoff already happened.
        for existing in self.store.list_nodes(project.id):
            if (
                existing.kind is NodeKind.GATE
                and existing.parent_node_id == source.id
            ):
                return

        brief = self._read_review_guidance_from_node(project, source)
        self.start_gate_node(project.id, brief, parent_node_id=source.id)

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
