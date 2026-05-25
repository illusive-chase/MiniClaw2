from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from miniclaw2.domain import Node, NodeKind, NodeState, Project
from miniclaw2.registry import ProjectRegistry
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


class _StubRunner:
    """Mimics the slice of NodeRunner that ``_on_runner_done`` reads."""

    def __init__(self, node: Node) -> None:
        self.node = node


def _init_repo(path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"], cwd=path, check=True
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return head


def _project_with_agent(
    tmp: Path,
    *,
    auto_commit: bool,
    agent_state: NodeState,
) -> tuple[Store, Project, Node, str]:
    repo = tmp / "repo"
    repo.mkdir()
    initial = _init_repo(repo)

    store = Store(root=tmp / "store")
    settings: dict[str, object] = {}
    if auto_commit:
        settings["auto_commit"] = True
    project = Project(root_path=str(repo), settings_override=settings)
    store.create_project(project)

    agent = store.create_node(
        Node(
            project_id=project.id,
            kind=NodeKind.AGENT,
            state=agent_state,
            commit_before=initial,
            commit_after=initial,
        )
    )
    return store, project, agent, initial


class OpCommitRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_commit_op_writes_commit_and_rewrites_agent_commit_after(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project, agent, initial = _project_with_agent(
                tmp, auto_commit=True, agent_state=NodeState.DONE
            )
            # Simulate the agent leaving uncommitted changes in the repo.
            (Path(project.root_path) / "added.txt").write_text("hi\n", encoding="utf-8")

            registry = ProjectRegistry(store=store)
            rt = registry._runtimes[project.id]
            rt.runner = _StubRunner(agent)  # type: ignore[assignment]

            registry._on_runner_done(rt)
            self.assertIsNotNone(rt.runner_task)
            await rt.runner_task  # type: ignore[arg-type]

            nodes = store.list_nodes(project.id)
            self.assertEqual(len(nodes), 2)
            op_node = nodes[-1]
            self.assertEqual(op_node.kind, NodeKind.OP)
            self.assertEqual(op_node.op_kind, "commit")
            self.assertEqual(op_node.state, NodeState.DONE)
            self.assertEqual(op_node.commit_before, initial)
            self.assertIsNotNone(op_node.commit_after)
            self.assertNotEqual(op_node.commit_after, initial)
            self.assertTrue((op_node.summary or "").startswith("commit "))

            updated_agent = store.load_node(project.id, agent.id)
            assert updated_agent is not None
            self.assertEqual(updated_agent.commit_after, op_node.commit_after)

    async def test_commit_op_no_changes_marks_done_with_summary(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project, agent, initial = _project_with_agent(
                tmp, auto_commit=True, agent_state=NodeState.DONE
            )
            # Repo is clean; no agent changes to commit.

            registry = ProjectRegistry(store=store)
            rt = registry._runtimes[project.id]
            rt.runner = _StubRunner(agent)  # type: ignore[assignment]

            registry._on_runner_done(rt)
            assert rt.runner_task is not None
            await rt.runner_task

            op_node = store.list_nodes(project.id)[-1]
            self.assertEqual(op_node.state, NodeState.DONE)
            self.assertEqual(op_node.summary, "no changes to commit")
            self.assertEqual(op_node.commit_before, initial)
            self.assertEqual(op_node.commit_after, initial)

            updated_agent = store.load_node(project.id, agent.id)
            assert updated_agent is not None
            self.assertEqual(updated_agent.commit_after, initial)

    async def test_no_auto_commit_when_project_setting_off(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project, agent, _initial = _project_with_agent(
                tmp, auto_commit=False, agent_state=NodeState.DONE
            )
            (Path(project.root_path) / "x.txt").write_text("x", encoding="utf-8")

            registry = ProjectRegistry(store=store)
            rt = registry._runtimes[project.id]
            rt.runner = _StubRunner(agent)  # type: ignore[assignment]

            registry._on_runner_done(rt)

            self.assertIsNone(rt.runner)
            self.assertIsNone(rt.runner_task)
            self.assertEqual(len(store.list_nodes(project.id)), 1)

    async def test_no_auto_commit_when_agent_did_not_reach_done(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project, agent, _initial = _project_with_agent(
                tmp, auto_commit=True, agent_state=NodeState.ERROR
            )

            registry = ProjectRegistry(store=store)
            rt = registry._runtimes[project.id]
            rt.runner = _StubRunner(agent)  # type: ignore[assignment]

            registry._on_runner_done(rt)

            self.assertIsNone(rt.runner)
            self.assertIsNone(rt.runner_task)
            self.assertEqual(len(store.list_nodes(project.id)), 1)


class OpCommitDirectRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_op_runner_with_unknown_op_kind_errors(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            repo = tmp / "repo"
            repo.mkdir()
            _init_repo(repo)

            store = Store(root=tmp / "store")
            project = Project(root_path=str(repo))
            store.create_project(project)
            op_node = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.OP,
                    op_kind="unknown",
                    state=NodeState.QUEUED,
                )
            )

            emitted: list[dict[str, object]] = []

            async def on_event(payload: dict[str, object]) -> None:
                emitted.append(payload)

            runner = NodeRunner(op_node, project, store, on_event)
            await runner.run()

            self.assertEqual(op_node.state, NodeState.ERROR)
            self.assertIn("unknown op_kind", op_node.error or "")


if __name__ == "__main__":
    unittest.main()
