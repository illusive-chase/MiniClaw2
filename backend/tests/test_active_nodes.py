from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

from fastapi.testclient import TestClient

from miniclaw2.active_nodes import (
    TERMINAL_RECENCY_SECONDS,
    ActiveNodesIndex,
    collect_active_entries,
)
from miniclaw2.app import create_app
from miniclaw2.domain import (
    Category,
    GateKind,
    GateSubtype,
    HumanGate,
    Node,
    NodeKind,
    NodeState,
    Project,
)
from miniclaw2.registry import ProjectRegistry, ProjectRuntime
from miniclaw2.store import Store


def _registry(root: Path) -> tuple[Store, ProjectRegistry]:
    """A registry whose runtimes are populated without touching the real home.

    Initialization is forced while the store is still empty. Touching
    ``registry.store`` triggers ``initialize()``, and that sweeps
    non-terminal nodes with no live runner to ``cancelled`` — which would
    silently rewrite the RUNNING/WAITING nodes these tests set up.
    """
    store = Store(root=root / "store")
    registry = ProjectRegistry(store=store, initialize=True)
    registry._runtimes = {}
    return store, registry


def _add_project(
    store: Store,
    registry: ProjectRegistry,
    root_path: Path,
    *,
    name: str,
) -> Project:
    root_path.mkdir(parents=True, exist_ok=True)
    project = store.create_project(
        Project(root_path=str(root_path), name=name, machine_id=store.machine.id)
    )
    registry._runtimes[project.id] = ProjectRuntime(project)
    return project


def _add_node(
    store: Store,
    project: Project,
    *,
    state: NodeState,
    summary: str | None = None,
    prompt: str = "",
    planspace_id: str | None = None,
    finished_at: float | None = None,
) -> Node:
    node = store.create_node(
        Node(
            project_id=project.id,
            kind=NodeKind.AGENT,
            category=Category.REGULAR,
            model_preset_id=project.model_preset_id,
            state=state,
            summary=summary,
            prompt=prompt,
            planspace_id=planspace_id,
        )
    )
    if finished_at is not None:
        node.finished_at = finished_at
        store.update_node(node)
    return node


class ActiveNodesCollectionTests(unittest.TestCase):
    def test_lists_active_nodes_from_every_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            alpha = _add_project(store, registry, base / "alpha", name="alpha")
            beta = _add_project(store, registry, base / "beta", name="beta")

            running = _add_node(store, alpha, state=NodeState.RUNNING, summary="跑着")
            waiting = _add_node(store, beta, state=NodeState.WAITING, summary="等人")
            stale = _add_node(
                store,
                beta,
                state=NodeState.DONE,
                summary="很久以前完成",
                finished_at=time.time() - TERMINAL_RECENCY_SECONDS - 60,
            )

            entries = collect_active_entries(registry, ActiveNodesIndex())
            by_id = {entry.node_id: entry for entry in entries}

            self.assertEqual(set(by_id), {running.id, waiting.id})
            self.assertNotIn(stale.id, by_id)
            self.assertEqual(by_id[running.id].project_name, "alpha")
            self.assertEqual(by_id[waiting.id].state, "waiting")

    def test_recently_finished_nodes_are_listed(self) -> None:
        """The bell answers "what finished while I was elsewhere", not only
        "what is still going"."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            now = time.time()
            done = _add_node(
                store,
                project,
                state=NodeState.DONE,
                summary="刚完成",
                finished_at=now - 60,
            )
            cancelled = _add_node(
                store,
                project,
                state=NodeState.CANCELLED,
                summary="刚取消",
                finished_at=now - 120,
            )

            entries = collect_active_entries(registry, ActiveNodesIndex(), now=now)
            by_id = {entry.node_id: entry for entry in entries}
            self.assertEqual(set(by_id), {done.id, cancelled.id})
            self.assertEqual(by_id[done.id].finished_at, now - 60)
            self.assertIsNone(by_id[cancelled.id].gate)

    def test_terminal_nodes_leave_the_window_but_active_ones_never_do(self) -> None:
        """The window bounds how far back the panel looks. A node still running
        after two days is the user's problem *now* and must never age out."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            now = time.time()
            long_running = _add_node(
                store, project, state=NodeState.RUNNING, summary="跑了两天"
            )
            long_running.started_at = now - 48 * 3600
            store.update_node(long_running)
            for state in (NodeState.DONE, NodeState.ERROR, NodeState.CANCELLED):
                _add_node(
                    store,
                    project,
                    state=state,
                    summary=f"超窗 {state.value}",
                    finished_at=now - TERMINAL_RECENCY_SECONDS - 60,
                )

            entries = collect_active_entries(registry, ActiveNodesIndex(), now=now)
            self.assertEqual([entry.node_id for entry in entries], [long_running.id])

    def test_virtual_nodes_are_excluded(self) -> None:
        """Proposals outnumber executed nodes and would drown the signal."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            _add_node(store, project, state=NodeState.VIRTUAL, prompt="proposed")
            _add_node(store, project, state=NodeState.QUEUED, summary="排队")

            entries = collect_active_entries(registry, ActiveNodesIndex())
            self.assertEqual([entry.state for entry in entries], ["queued"])

    def test_recent_errors_are_listed_but_stale_ones_are_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            now = time.time()
            fresh = _add_node(
                store,
                project,
                state=NodeState.ERROR,
                summary="刚失败",
                finished_at=now - 60,
            )
            _add_node(
                store,
                project,
                state=NodeState.ERROR,
                summary="很久以前失败",
                finished_at=now - TERMINAL_RECENCY_SECONDS - 60,
            )

            entries = collect_active_entries(registry, ActiveNodesIndex(), now=now)
            self.assertEqual([entry.node_id for entry in entries], [fresh.id])
            self.assertEqual(entries[0].finished_at, now - 60)

    def test_label_prefers_summary_and_falls_back_to_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            with_summary = _add_node(
                store,
                project,
                state=NodeState.RUNNING,
                summary="摘要",
                prompt="提示词",
            )
            prompt_only = _add_node(
                store,
                project,
                state=NodeState.RUNNING,
                prompt="  只有\n提示词  ",
            )

            entries = {
                entry.node_id: entry
                for entry in collect_active_entries(registry, ActiveNodesIndex())
            }
            self.assertEqual(entries[with_summary.id].label, "摘要")
            self.assertEqual(entries[prompt_only.id].label, "只有 提示词")

    def test_non_native_nodes_are_omitted(self) -> None:
        """The backend refuses interaction_response for them; a row would lie."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            root_path = base / "shared"
            root_path.mkdir()
            project = store.create_project(
                Project(
                    root_path=str(root_path),
                    name="shared",
                    machine_id=store.machine.id,
                )
            )
            registry._runtimes[project.id] = ProjectRuntime(project)

            local = Node(
                project_id=project.id,
                kind=NodeKind.AGENT,
                category=Category.REGULAR,
                model_preset_id=project.model_preset_id,
                state=NodeState.RUNNING,
                summary="本机在跑",
            )
            remote = Node(
                project_id=project.id,
                kind=NodeKind.AGENT,
                category=Category.REGULAR,
                model_preset_id=project.model_preset_id,
                state=NodeState.RUNNING,
                summary="别的机器在跑",
            )
            project_dir = store.root / "projects" / project.id
            for machine_id, node in (
                (store.machine.id, local),
                ("remote-host", remote),
            ):
                path = (
                    project_dir / "hosts" / machine_id / "nodes" / node.id / "node.json"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(node.model_dump(exclude={"provider", "owner_host_id"})),
                    encoding="utf-8",
                )
            store.invalidate_owner_index()

            entries = collect_active_entries(registry, ActiveNodesIndex())
            self.assertEqual([entry.node_id for entry in entries], [local.id])

    def test_cache_reuses_unchanged_records_and_sees_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            node = _add_node(store, project, state=NodeState.RUNNING, summary="一")

            index = ActiveNodesIndex()
            first = collect_active_entries(registry, index)
            second = collect_active_entries(registry, index)
            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 1)

            node.summary = "二"
            node.state = NodeState.WAITING
            store.update_node(node)

            third = collect_active_entries(registry, index)
            self.assertEqual(len(third), 1)
            self.assertEqual(third[0].label, "二")
            self.assertEqual(third[0].state, "waiting")

    def test_cache_drops_entries_for_deleted_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            node = _add_node(store, project, state=NodeState.RUNNING, summary="一")
            index = ActiveNodesIndex()
            collect_active_entries(registry, index)
            self.assertEqual(len(index._cache[project.id]), 1)

            node_dir = store.node_dir(project.id, node.id)
            for path in node_dir.rglob("*"):
                if path.is_file():
                    path.unlink()

            self.assertEqual(collect_active_entries(registry, index), [])
            self.assertEqual(index._cache[project.id], {})

    def test_cache_drops_projects_that_disappear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            _add_node(store, project, state=NodeState.RUNNING, summary="一")
            index = ActiveNodesIndex()
            collect_active_entries(registry, index)
            self.assertIn(project.id, index._cache)

            registry._runtimes.pop(project.id)
            collect_active_entries(registry, index)
            self.assertNotIn(project.id, index._cache)

    def test_two_projects_do_not_evict_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            alpha = _add_project(store, registry, base / "alpha", name="alpha")
            beta = _add_project(store, registry, base / "beta", name="beta")
            _add_node(store, alpha, state=NodeState.RUNNING, summary="a")
            _add_node(store, beta, state=NodeState.RUNNING, summary="b")

            index = ActiveNodesIndex()
            collect_active_entries(registry, index)
            self.assertEqual(len(index._cache[alpha.id]), 1)
            self.assertEqual(len(index._cache[beta.id]), 1)
            self.assertEqual(len(collect_active_entries(registry, index)), 2)

    def test_read_only_store_lists_nothing(self) -> None:
        """Gate resolution asserts a writable store; a row would not resolve."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            _add_node(store, project, state=NodeState.WAITING, summary="等我")

            self.assertEqual(len(collect_active_entries(registry, ActiveNodesIndex())), 1)

            with patch.object(
                type(store),
                "read_only_reason",
                new_callable=PropertyMock,
                return_value="store schema is newer than this MiniClaw2 version",
            ):
                self.assertEqual(collect_active_entries(registry, ActiveNodesIndex()), [])

    def test_unreadable_record_is_skipped_without_failing_the_sweep(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            good = _add_node(store, project, state=NodeState.RUNNING, summary="好的")
            broken = _add_node(store, project, state=NodeState.RUNNING, summary="坏的")
            path = store.node_dir(project.id, broken.id) / "node.json"
            path.write_text("{ not json", encoding="utf-8")

            entries = collect_active_entries(registry, ActiveNodesIndex())
            self.assertEqual([entry.node_id for entry in entries], [good.id])


class ActiveNodesGateTests(unittest.TestCase):
    def test_gate_summary_reports_only_answerable_gates(self) -> None:
        from miniclaw2.runner import NodeRunner

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            node = _add_node(store, project, state=NodeState.WAITING, summary="等确认")

            async def _noop(_event: object) -> None:
                return None

            runner = NodeRunner(
                node=node, project=project, store=store, on_event=_noop
            )
            gate = HumanGate(
                node_id=node.id,
                kind=GateKind.INLINE,
                subtype=GateSubtype.PERMISSION,
                tool_name="Write",
                tool_input={"file_path": "/tmp/.env"},
            )
            runner._gate_records[gate.id] = gate

            # A record with no live future is not answerable and must not show.
            self.assertIsNone(runner.current_gate_summary())

            loop = asyncio.new_event_loop()
            try:
                runner._gates[gate.id] = loop.create_future()
                summary = runner.current_gate_summary()
            finally:
                loop.close()

            assert summary is not None
            self.assertEqual(summary["tool_name"], "Write")
            self.assertEqual(summary["subtype"], "permission")
            self.assertEqual(summary["summary"], "/tmp/.env")

            registry._runtimes[project.id].runners[node.id] = runner
            entries = collect_active_entries(registry, ActiveNodesIndex())
            self.assertEqual(len(entries), 1)
            assert entries[0].gate is not None
            self.assertEqual(entries[0].gate["tool_name"], "Write")

    def test_waiting_node_without_runner_degrades_to_no_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            _add_node(store, project, state=NodeState.WAITING, summary="等着")

            entries = collect_active_entries(registry, ActiveNodesIndex())
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].state, "waiting")
            self.assertIsNone(entries[0].gate)

    def test_ask_user_summary_uses_the_first_question(self) -> None:
        from miniclaw2.runner import _gate_summary_text

        gate = HumanGate(
            node_id="n1",
            subtype=GateSubtype.ASK_USER,
            tool_name="AskUserQuestion",
            tool_input={"questions": [{"question": "选哪个方案？", "header": "方案"}]},
        )
        self.assertEqual(_gate_summary_text(gate), "选哪个方案？")


class ActiveNodesEndpointTests(unittest.TestCase):
    def test_workspace_websocket_attaches_without_a_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            _store, registry = _registry(base)

            with TestClient(create_app(registry)) as client:
                with client.websocket_connect("/ws/-"):
                    self.assertEqual(len(registry._workspace_observers), 1)

            self.assertEqual(registry._workspace_observers, {})

    def test_endpoint_returns_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            node = _add_node(store, project, state=NodeState.RUNNING, summary="在跑")

            with TestClient(create_app(registry)) as client:
                res = client.get("/active-nodes")
                self.assertEqual(res.status_code, 200)
                body = res.json()

            self.assertIn("generated_at", body)
            self.assertEqual(len(body["entries"]), 1)
            entry = body["entries"][0]
            self.assertEqual(entry["node_id"], node.id)
            self.assertEqual(entry["project_name"], "p")
            self.assertEqual(entry["state"], "running")
            self.assertIsNone(entry["gate"])

    def test_endpoint_is_empty_when_nothing_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            _add_node(
                store,
                project,
                state=NodeState.DONE,
                summary="很久以前完成",
                finished_at=time.time() - TERMINAL_RECENCY_SECONDS - 60,
            )

            with TestClient(create_app(registry)) as client:
                body = client.get("/active-nodes").json()
            self.assertEqual(body["entries"], [])

    def test_recently_finished_nodes_do_not_block_a_self_update(self) -> None:
        """The sweep lists them for the bell; only busy nodes hold an update."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            now = time.time()
            for state in (NodeState.DONE, NodeState.ERROR, NodeState.CANCELLED):
                _add_node(
                    store,
                    project,
                    state=state,
                    summary=f"刚 {state.value}",
                    finished_at=now - 60,
                )

            with TestClient(create_app(registry)) as client:
                self.assertEqual(len(client.get("/active-nodes").json()["entries"]), 3)
                self.assertEqual(client.get("/self-update").json()["blockers"], [])

                waiting = _add_node(
                    store, project, state=NodeState.WAITING, summary="等我"
                )
                blockers = client.get("/self-update").json()["blockers"]

            self.assertEqual([item["node_id"] for item in blockers], [waiting.id])


class WorkspaceNodeEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_transition_pushes_active_entry_with_ephemeral_sequence(self) -> None:
        from miniclaw2.runner import NodeRunner

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            node = _add_node(store, project, state=NodeState.QUEUED, summary="执行")
            events: list[dict[str, object]] = []

            async def collect(event: dict[str, object]) -> None:
                events.append(event)

            registry.attach_workspace_observer(collect)
            runner = NodeRunner(
                node,
                project,
                store,
                lambda _event: asyncio.sleep(0),
                on_state_change=lambda changed, previous: registry._publish_workspace_node(
                    project, changed, previous
                ),
            )
            registry._runtimes[project.id].runners[node.id] = runner

            runner._transition(NodeState.RUNNING, started=True)
            await asyncio.sleep(0)

            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual(event["type"], "workspace_node_updated")
            self.assertEqual(event["seq"], 0)
            self.assertEqual(event["previous_state"], "queued")
            self.assertEqual(event["entry"]["node_id"], node.id)  # type: ignore[index]
            self.assertEqual(event["entry"]["state"], "running")  # type: ignore[index]

            runner._transition(NodeState.RUNNING)
            await asyncio.sleep(0)
            self.assertEqual(len(events), 1)

    async def test_hard_delete_event_is_distinct_from_visibility_removal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            node = _add_node(store, project, state=NodeState.VIRTUAL, summary="草稿")
            events: list[dict[str, object]] = []

            async def collect(event: dict[str, object]) -> None:
                events.append(event)

            registry.attach_workspace_observer(collect)
            await registry._publish_workspace_removed(project, node)

            self.assertEqual(events[0]["type"], "workspace_node_removed")
            self.assertTrue(events[0]["deleted"])
            self.assertEqual(events[0]["previous_state"], "virtual")

    async def test_deleting_a_project_retracts_its_feed_rows(self) -> None:
        """A deleted project must not leave rows that only fail when clicked.

        The feed's sole removal channel is ``workspace_node_removed``; without
        one per node the row survives until a re-fetch, and jumping to it hits
        a session that no longer exists.
        """
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            store, registry = _registry(base)
            project = _add_project(store, registry, base / "p", name="p")
            done = _add_node(
                store,
                project,
                state=NodeState.DONE,
                summary="完成",
                finished_at=time.time(),
            )
            waiting = _add_node(store, project, state=NodeState.WAITING, summary="等我")
            events: list[dict[str, object]] = []

            async def collect(event: dict[str, object]) -> None:
                events.append(event)

            registry.attach_workspace_observer(collect)
            self.assertTrue(registry.delete_project(project.id))
            # _schedule_workspace_removed defers to the running loop.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            self.assertEqual(
                {event["type"] for event in events}, {"workspace_node_removed"}
            )
            self.assertEqual(
                {event["node_id"] for event in events}, {done.id, waiting.id}
            )
            self.assertTrue(all(event["deleted"] for event in events))
            self.assertTrue(
                all(event["project_id"] == project.id for event in events)
            )


if __name__ == "__main__":
    unittest.main()
