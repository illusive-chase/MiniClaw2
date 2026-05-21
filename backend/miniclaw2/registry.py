"""ProjectRegistry — in-memory orchestration over the disk Store.

The legacy "session" id maps 1:1 to a project id. Each user prompt
becomes a new agent :class:`Node` whose ``parent_node_id`` points at
the previous node in that project, and whose ``sdk_session_id``
inherits from the predecessor so the SDK resumes the conversation.

Within a project, only one node runs at a time (DESIGN §2.2).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .domain import Node, NodeKind, NodeState, Project
from .runner import NodeRunner
from .store import Store

logger = logging.getLogger(__name__)


class ProjectRuntime:
    """Per-project mutable runtime state — only one runner active at a time."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.runner: NodeRunner | None = None
        self.runner_task: asyncio.Task[None] | None = None

    def is_running(self) -> bool:
        return self.runner_task is not None and not self.runner_task.done()


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
        self, cwd: str, model: str | None = None, name: str = ""
    ) -> Project:
        project = Project(
            root_path=cwd,
            name=name,
            settings_override={"model": model} if model else {},
        )
        self.store.create_project(project)
        self._runtimes[project.id] = ProjectRuntime(project)
        return project

    def get_project(self, pid: str) -> Project | None:
        rt = self._runtimes.get(pid)
        return rt.project if rt else None

    def list_projects(self) -> list[Project]:
        return [rt.project for rt in self._runtimes.values()]

    def delete_project(self, pid: str) -> bool:
        rt = self._runtimes.pop(pid, None)
        if rt is None:
            return False
        if rt.is_running():
            assert rt.runner_task is not None
            rt.runner_task.cancel()
        self.store.delete_project(pid)
        return True

    def turn_count(self, pid: str) -> int:
        return len(self.store.list_nodes(pid))

    # ---- node lifecycle ----

    def start_node(
        self,
        pid: str,
        prompt: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> NodeRunner | None:
        """Create a new agent node and launch its runner as a task.

        Returns the runner, or ``None`` if the project is unknown or a
        node is already running.
        """
        rt = self._runtimes.get(pid)
        if rt is None:
            return None
        if rt.is_running():
            return None

        latest = self.store.latest_node(pid)
        node = Node(
            project_id=pid,
            kind=NodeKind.AGENT,
            state=NodeState.QUEUED,
            parent_node_id=latest.id if latest else None,
            sdk_session_id=latest.sdk_session_id if latest else None,
            prompt=prompt,
        )
        self.store.create_node(node)

        runner = NodeRunner(node, rt.project, self.store, on_event)
        rt.runner = runner
        rt.runner_task = asyncio.create_task(runner.run())
        rt.runner_task.add_done_callback(lambda _t, _rt=rt: _on_runner_done(_rt))
        return runner

    def interrupt(self, pid: str) -> bool:
        rt = self._runtimes.get(pid)
        if rt is None or not rt.is_running():
            return False
        assert rt.runner_task is not None
        rt.runner_task.cancel()
        return True

    def resolve_gate(
        self,
        pid: str,
        gate_id: str,
        *,
        allow: bool,
        message: str = "",
        updated_input: dict[str, Any] | None = None,
        permission_mode: str | None = None,
        clear_context: bool = False,
    ) -> bool:
        rt = self._runtimes.get(pid)
        if rt is None or rt.runner is None:
            return False
        return rt.runner.resolve_gate(
            gate_id,
            allow=allow,
            message=message,
            updated_input=updated_input,
            permission_mode=permission_mode,
            clear_context=clear_context,
        )


def _on_runner_done(rt: ProjectRuntime) -> None:
    rt.runner = None
    rt.runner_task = None
