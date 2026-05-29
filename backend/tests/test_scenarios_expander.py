"""Tests for the scenario-step expander in ProjectRegistry.

Drives synthetic finished nodes through ``_on_runner_done`` and
``_advance_scenario_step`` without actually running a real provider —
mirrors the approach in ``test_op_node.py``.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from miniclaw2.domain import (
    Node,
    NodeKind,
    NodeOutputKind,
    NodeState,
    Project,
    default_node_output_path,
)
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


class _StubRunner:
    """Stand-in for ``rt.runner`` — only ``.node`` is read by the callback."""

    def __init__(self, node: Node) -> None:
        self.node = node


def _init_repo(path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)
    return (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


def _scenario_project(tmp: Path, *, auto_commit: bool = True) -> tuple[Store, Project]:
    repo = tmp / "repo"
    repo.mkdir()
    _init_repo(repo)
    store = Store(root=tmp / "store")
    settings = {"auto_commit": True} if auto_commit else {}
    project = Project(
        root_path=str(repo),
        scenario_name="gui-calculator",
        settings_override=settings,
    )
    store.create_project(project)
    return store, project


def _write_brief(root: str, node: Node, text: str) -> Path:
    rel = node.output_path or default_node_output_path(node.id, node.output_kind)
    assert rel is not None
    target = Path(root) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


class ScenarioExpanderTest(unittest.IsolatedAsyncioTestCase):
    async def test_advance_records_step_history_and_launches_next_gate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project = _scenario_project(tmp, auto_commit=False)

            build = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                    scenario_step_id="build",
                    output_kind=NodeOutputKind.REVIEW_BRIEF,
                )
            )
            _write_brief(
                project.root_path,
                build,
                "# How to run\n`python3 calculator.py`\n# What to verify\n- it opens\n# Response schema\n`{\"approved\": bool}`\n",
            )

            registry = ProjectRegistry(store=store)
            rt = registry._runtimes[project.id]
            registry._advance_scenario_step(rt, build)

            refreshed = store.load_project(project.id)
            assert refreshed is not None
            self.assertEqual(
                refreshed.scenario_step_history,
                [{"step_id": "build", "node_id": build.id, "terminal_state": "done"}],
            )

            nodes = store.list_nodes(project.id)
            self.assertEqual(len(nodes), 2)
            gate = nodes[-1]
            self.assertEqual(gate.kind, NodeKind.GATE)
            self.assertEqual(gate.scenario_step_id, "review")
            self.assertIn("How to run", gate.contract)
            self.assertIn("python3 calculator.py", gate.contract)

            # The expander spawned a runner task for the gate; cancel it so
            # the test cleans up without waiting for a human response.
            if rt.runner_task is not None:
                rt.runner_task.cancel()
                try:
                    await rt.runner_task
                except BaseException:
                    pass

    async def test_advance_halts_on_non_done_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project = _scenario_project(tmp, auto_commit=False)

            build = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.ERROR,
                    scenario_step_id="build",
                    output_kind=NodeOutputKind.REVIEW_BRIEF,
                )
            )

            registry = ProjectRegistry(store=store)
            rt = registry._runtimes[project.id]
            registry._advance_scenario_step(rt, build)

            refreshed = store.load_project(project.id)
            assert refreshed is not None
            self.assertEqual(
                refreshed.scenario_step_history,
                [{"step_id": "build", "node_id": build.id, "terminal_state": "error"}],
            )

            # Only the build node — no gate enqueued.
            self.assertEqual(len(store.list_nodes(project.id)), 1)
            self.assertIsNone(rt.runner_task)

    async def test_advance_routes_through_op_parent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project = _scenario_project(tmp, auto_commit=True)

            build = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                    scenario_step_id="build",
                    output_kind=NodeOutputKind.REVIEW_BRIEF,
                )
            )
            _write_brief(project.root_path, build, "# How to run\nfoo\n")

            op = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.OP,
                    op_kind="commit",
                    state=NodeState.DONE,
                    parent_node_id=build.id,
                )
            )

            registry = ProjectRegistry(store=store)
            rt = registry._runtimes[project.id]
            registry._advance_scenario_step(rt, op)

            refreshed = store.load_project(project.id)
            assert refreshed is not None
            self.assertEqual(len(refreshed.scenario_step_history), 1)
            self.assertEqual(refreshed.scenario_step_history[0]["step_id"], "build")

            nodes = store.list_nodes(project.id)
            self.assertEqual(len(nodes), 3)
            gate = nodes[-1]
            self.assertEqual(gate.kind, NodeKind.GATE)
            self.assertEqual(gate.scenario_step_id, "review")

            if rt.runner_task is not None:
                rt.runner_task.cancel()
                try:
                    await rt.runner_task
                except BaseException:
                    pass

    async def test_advance_at_last_step_enqueues_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project = _scenario_project(tmp, auto_commit=False)

            # Pre-record the build step in history; finish the review.
            review = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.GATE,
                    state=NodeState.DONE,
                    scenario_step_id="review",
                )
            )
            project.scenario_step_history = [
                {"step_id": "build", "node_id": "synth", "terminal_state": "done"}
            ]
            store.update_project(project)

            registry = ProjectRegistry(store=store)
            rt = registry._runtimes[project.id]
            registry._advance_scenario_step(rt, review)

            refreshed = store.load_project(project.id)
            assert refreshed is not None
            self.assertEqual(len(refreshed.scenario_step_history), 2)
            self.assertEqual(refreshed.scenario_step_history[-1]["step_id"], "review")
            # No new node enqueued.
            self.assertEqual(len(store.list_nodes(project.id)), 1)
            self.assertIsNone(rt.runner_task)

    async def test_advance_uses_placeholder_when_brief_missing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project = _scenario_project(tmp, auto_commit=False)

            build = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                    scenario_step_id="build",
                    output_kind=NodeOutputKind.REVIEW_BRIEF,
                )
            )
            # Intentionally no brief written.

            registry = ProjectRegistry(store=store)
            rt = registry._runtimes[project.id]
            registry._advance_scenario_step(rt, build)

            gate = store.list_nodes(project.id)[-1]
            self.assertEqual(gate.kind, NodeKind.GATE)
            self.assertIn("brief file not written", gate.contract)

            if rt.runner_task is not None:
                rt.runner_task.cancel()
                try:
                    await rt.runner_task
                except BaseException:
                    pass

    async def test_resume_fix_after_reject_branches_to_fix_on_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project = _scenario_project(tmp, auto_commit=False)
            # Override the scenario name set by the helper.
            project.scenario_name = "resume-fix-after-reject"
            store.update_project(project)

            build = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                    scenario_step_id="build",
                    output_kind=NodeOutputKind.REVIEW_BRIEF,
                    provider_session_id="claude-session-build",
                )
            )
            review = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.GATE,
                    state=NodeState.DONE,
                    scenario_step_id="review",
                    review_outcome="rejected",
                )
            )
            project.scenario_step_history = [
                {"step_id": "build", "node_id": build.id, "terminal_state": "done"}
            ]
            store.update_project(project)

            registry = ProjectRegistry(store=store)
            rt = registry._runtimes[project.id]
            registry._advance_scenario_step(rt, review)

            refreshed = store.load_project(project.id)
            assert refreshed is not None
            review_entry = next(
                h for h in refreshed.scenario_step_history if h["step_id"] == "review"
            )
            self.assertEqual(review_entry["decision"], "rejected")

            nodes = store.list_nodes(project.id)
            fix = nodes[-1]
            self.assertEqual(fix.kind, NodeKind.AGENT)
            self.assertEqual(fix.scenario_step_id, "fix")
            self.assertEqual(fix.parent_node_id, build.id)
            self.assertEqual(fix.provider_session_id, "claude-session-build")

            if rt.runner_task is not None:
                rt.runner_task.cancel()
                try:
                    await rt.runner_task
                except BaseException:
                    pass

    async def test_resume_fix_after_reject_skips_fix_on_approved(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project = _scenario_project(tmp, auto_commit=False)
            project.scenario_name = "resume-fix-after-reject"
            store.update_project(project)

            build = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                    scenario_step_id="build",
                    output_kind=NodeOutputKind.REVIEW_BRIEF,
                    provider_session_id="claude-session-build",
                )
            )
            review = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.GATE,
                    state=NodeState.DONE,
                    scenario_step_id="review",
                    review_outcome="approved",
                )
            )
            project.scenario_step_history = [
                {"step_id": "build", "node_id": build.id, "terminal_state": "done"}
            ]
            store.update_project(project)

            registry = ProjectRegistry(store=store)
            rt = registry._runtimes[project.id]
            registry._advance_scenario_step(rt, review)

            # Only build + review exist; fix should be skipped since
            # `when: review.rejected` does not match an approved review.
            nodes = store.list_nodes(project.id)
            self.assertEqual(len(nodes), 2)
            self.assertIsNone(rt.runner_task)

            refreshed = store.load_project(project.id)
            assert refreshed is not None
            review_entry = next(
                h for h in refreshed.scenario_step_history if h["step_id"] == "review"
            )
            self.assertEqual(review_entry["decision"], "approved")

    async def test_advance_ignores_non_scenario_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project = _scenario_project(tmp, auto_commit=False)

            user_node = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                    scenario_step_id=None,
                )
            )

            registry = ProjectRegistry(store=store)
            rt = registry._runtimes[project.id]
            registry._advance_scenario_step(rt, user_node)

            refreshed = store.load_project(project.id)
            assert refreshed is not None
            self.assertEqual(refreshed.scenario_step_history, [])
            self.assertEqual(len(store.list_nodes(project.id)), 1)
            self.assertIsNone(rt.runner_task)


if __name__ == "__main__":
    unittest.main()
