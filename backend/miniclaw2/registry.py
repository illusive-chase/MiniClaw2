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
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from .artifacts import validate_node_output_path
from .domain import Node, NodeKind, NodeOutputKind, NodeState, Project
from .runner import NodeRunner
from .store import Store
from .workspace import create_temporary_root, remove_temporary_root

logger = logging.getLogger(__name__)


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
        temporary: bool = False,
        scenario_name: str | None = None,
    ) -> Project:
        normalized_provider = (provider or "claude").lower()
        if normalized_provider not in {"claude", "codex"}:
            raise ValueError(f"unknown provider: {provider}")
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

    def delete_project(self, pid: str) -> bool:
        rt = self._runtimes.pop(pid, None)
        if rt is None:
            return False
        if rt.is_running():
            assert rt.runner_task is not None
            rt.runner_task.cancel()
        if rt.project.temporary:
            remove_temporary_root(rt.project.root_path)
        self.store.delete_project(pid)
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
        output_kind: str | None = None,
        output_path: str | None = None,
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
        if validate_node_output_path(output_path):
            return None

        node = Node(
            project_id=pid,
            kind=NodeKind.AGENT,
            state=NodeState.QUEUED,
            parent_node_id=resume_source.id if resume_source else None,
            provider=resume_source.provider if resume_source else rt.project.provider,
            provider_session_id=resume_source.provider_session_id if resume_source else None,
            sdk_session_id=resume_source.sdk_session_id if resume_source else None,
            output_kind=self._normalize_output_kind(output_kind),
            output_path=output_path,
            prompt=prompt,
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
        prompt: str,
        contract: str,
    ) -> NodeRunner | None:
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
            prompt=prompt,
            contract=contract,
        )
        self.store.create_node(node)

        runner = NodeRunner(node, rt.project, self.store, rt.broadcast)
        rt.runner = runner
        rt.runner_task = asyncio.create_task(runner.run())
        rt.runner_task.add_done_callback(lambda _t, _rt=rt: self._on_runner_done(_rt))
        return runner

    @staticmethod
    def _normalize_output_kind(value: str | None) -> NodeOutputKind:
        if value == "summary":
            return NodeOutputKind.SUMMARY
        if value == "interface":
            return NodeOutputKind.INTERFACE
        return NodeOutputKind.FREEFORM

    def _on_runner_done(self, rt: ProjectRuntime) -> None:
        finished_node = rt.runner.node if rt.runner else None
        rt.runner = None
        rt.runner_task = None

        if finished_node is None:
            return
        if (
            finished_node.kind in (NodeKind.AGENT, NodeKind.GATE)
            and finished_node.state is NodeState.DONE
            and bool(rt.project.settings_override.get("auto_commit"))
        ):
            self._spawn_op_commit(rt, finished_node)

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
