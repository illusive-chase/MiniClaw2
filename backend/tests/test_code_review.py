from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from miniclaw2.artifacts import stored_artifact_path
from miniclaw2.contextspace import create_planspace
from miniclaw2.domain import (
    Category,
    Node,
    NodeState,
    Project,
    ReviewBrief,
    ReviewSubtype,
)
from miniclaw2.global_config import (
    CodeReviewSettings,
    load_global_config,
    save_global_config,
)
from miniclaw2.providers import (
    AgentProviderEvent,
    ReviewFinding,
    ReviewReport,
)
from miniclaw2.registry import ProjectRegistry
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


def _init_repo(path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class _ReviewProvider:
    name = "stub"

    def __init__(self, *, mutate: Path | None = None) -> None:
        self.mutate = mutate
        self.calls = 0

    async def run_review(self, context, spec):
        self.calls += 1
        self.spec = spec
        yield AgentProviderEvent(kind="session", session_id="review-session")
        if self.mutate is not None:
            self.mutate.write_text("changed during review\n", encoding="utf-8")
        yield AgentProviderEvent(
            kind="review",
            report=ReviewReport(
                raw_markdown="# Review\n\nOne actionable issue.",
                findings=[
                    ReviewFinding(
                        title="Handle the edge case",
                        body="The new path is unchecked.",
                        file="change.py",
                        line_start=7,
                        priority="P1",
                        confidence=0.95,
                    )
                ],
                verdict="patch is incorrect",
                explanation="An edge case remains.",
            ),
        )
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class _ReviewThenErrorProvider(_ReviewProvider):
    async def run_review(self, context, spec):
        async for event in super().run_review(context, spec):
            if event.kind == "done":
                yield AgentProviderEvent(kind="error", error="turn failed after report")
            else:
                yield event


class CodeReviewRunnerTests(unittest.IsolatedAsyncioTestCase):
    def _setup(self, root: Path) -> tuple[Path, Store, Project, Node]:
        repo = root / "repo"
        repo.mkdir()
        _init_repo(repo)
        store = Store(root=root / "store")
        project = Project(root_path=str(repo))
        store.create_project(project)
        node = store.create_node(
            Node(
                project_id=project.id,
                category=Category.REVIEW,
                subtype=ReviewSubtype.CODE_REVIEW,
                model_preset_id="gpt-5.5",
            )
        )
        return repo, store, project, node

    async def test_clean_tree_short_circuits_without_provider(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, store, project, node = self._setup(Path(raw))
            provider = _ReviewProvider()
            runner = NodeRunner(node, project, store, lambda _event: asyncio.sleep(0))

            with patch("miniclaw2.runner._make_provider", return_value=provider):
                await runner.run()

            self.assertEqual(node.state, NodeState.DONE)
            self.assertEqual(provider.calls, 0)
            self.assertIn("working tree clean", node.summary or "")
            snapshot = store.node_dir(project.id, node.id) / "reviewed-diff.patch"
            self.assertIn("nothing to review", snapshot.read_text(encoding="utf-8"))
            self.assertTrue(repo.exists())

    async def test_published_report_survives_failed_turn_end(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, store, project, node = self._setup(Path(raw))
            (repo / "change.py").write_text("print('new')\n", encoding="utf-8")
            provider = _ReviewThenErrorProvider()
            runner = NodeRunner(node, project, store, lambda _event: asyncio.sleep(0))

            with patch("miniclaw2.runner._make_provider", return_value=provider):
                await runner.run()

            self.assertEqual(node.state, NodeState.ERROR)
            report = stored_artifact_path(
                store, project.id, node.id, "code-review-report.md"
            )
            self.assertIn("One actionable issue", report.read_text(encoding="utf-8"))
            self.assertEqual(
                [artifact.name for artifact in node.artifacts if artifact.status == "published"],
                ["code-review-report.md", "code-review-findings.json"],
            )
            preview = json.loads(store.read_node_preview(project.id, node.id) or "{}")
            self.assertIn("turn ended error", preview["summary"])
            self.assertEqual(
                preview["artifacts"],
                ["code-review-report.md", "code-review-findings.json"],
            )

    async def test_report_snapshot_artifacts_and_preview_are_published(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, store, project, node = self._setup(Path(raw))
            (repo / "change.py").write_text("print('new')\n", encoding="utf-8")
            provider = _ReviewProvider()
            runner = NodeRunner(node, project, store, lambda _event: asyncio.sleep(0))

            with patch("miniclaw2.runner._make_provider", return_value=provider):
                await runner.run()

            self.assertEqual(node.state, NodeState.DONE)
            snapshot = store.node_dir(project.id, node.id) / "reviewed-diff.patch"
            self.assertIn("change.py", snapshot.read_text(encoding="utf-8"))
            report = stored_artifact_path(store, project.id, node.id, "code-review-report.md")
            findings = stored_artifact_path(
                store, project.id, node.id, "code-review-findings.json"
            )
            self.assertIn("One actionable issue", report.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(findings.read_text(encoding="utf-8"))[0]["line_start"], 7)
            preview = json.loads(store.read_node_preview(project.id, node.id) or "{}")
            self.assertEqual(preview["subtype"], "code_review")
            self.assertEqual(
                preview["artifacts"],
                ["code-review-report.md", "code-review-findings.json"],
            )
            self.assertIn("change.py:7", preview["next_implications"])

    async def test_worktree_divergence_marks_report_stale(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, store, project, node = self._setup(Path(raw))
            changed = repo / "seed.txt"
            changed.write_text("before review\n", encoding="utf-8")
            provider = _ReviewProvider(mutate=changed)
            runner = NodeRunner(node, project, store, lambda _event: asyncio.sleep(0))

            with patch("miniclaw2.runner._make_provider", return_value=provider):
                await runner.run()

            preview = json.loads(store.read_node_preview(project.id, node.id) or "{}")
            self.assertIn("snapshot is stale", preview["summary"])
            report = stored_artifact_path(store, project.id, node.id, "code-review-report.md")
            self.assertIn("snapshot is stale", report.read_text(encoding="utf-8"))

    async def test_codex_focus_limitation_is_recorded_in_report_and_preview(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, store, project, node = self._setup(Path(raw))
            (repo / "change.py").write_text("print('new')\n", encoding="utf-8")
            node.brief = ReviewBrief(
                check_what="focus on parsing",
                expected="",
                abnormal="",
            )
            store.update_node(node)
            runner = NodeRunner(
                node, project, store, lambda _event: asyncio.sleep(0)
            )

            with patch(
                "miniclaw2.runner._make_provider", return_value=_ReviewProvider()
            ):
                await runner.run()

            report = stored_artifact_path(
                store, project.id, node.id, "code-review-report.md"
            ).read_text(encoding="utf-8")
            preview = json.loads(
                store.read_node_preview(project.id, node.id) or "{}"
            )
            self.assertIn("focus text was not applied", report)
            self.assertIn("focus text was not applied", preview["summary"])
            self.assertNotEqual(preview["motivation"], "focus on parsing")


class CodeReviewVirtualTests(unittest.TestCase):
    def test_empty_prompt_virtual_promotes_with_default_target(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            store = Store(root=root / "store")
            project = Project(root_path=str(repo))
            store.create_project(project)
            registry = ProjectRegistry(store=store)
            lane = create_planspace(
                project, title="Work", mode="manual", store_root=store.root
            )
            runtime = registry._runtimes[project.id]
            runtime.project.active_planspace_id = lane
            store.update_project(runtime.project)

            virtual = registry.create_virtual(
                project.id,
                prompt_draft="",
                category=Category.REVIEW,
                subtype=ReviewSubtype.CODE_REVIEW,
                planspace_id=lane,
                model_preset_id="gpt-5.5",
                _allow_compatibility_model_preset=True,
            )
            assert virtual is not None
            promoted = registry.promote_virtual(project.id, virtual.id)

            assert promoted is not None
            self.assertEqual(promoted.state, NodeState.QUEUED)
            self.assertEqual(promoted.review_target.type, "uncommitted")  # type: ignore[union-attr]


class _ControlledRunner:
    instances: dict[str, "_ControlledRunner"] = {}

    def __init__(self, node, project, store, on_event, **_kwargs) -> None:
        self.node = node
        self.store = store
        self.release = asyncio.Event()
        self.__class__.instances[node.id] = self

    async def run(self) -> None:
        self.node.state = NodeState.RUNNING
        self.store.update_node(self.node)
        await self.release.wait()
        self.node.state = NodeState.DONE
        self.store.update_node(self.node)

    async def interrupt(self) -> None:
        return None


class CodeReviewSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_spawn_assigns_review_to_active_planspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            store = Store(root=root / "store")
            project = Project(root_path=str(repo))
            store.create_project(project)
            registry = ProjectRegistry(store=store)
            first_lane = create_planspace(
                project, title="First", mode="manual", store_root=store.root
            )
            active_lane = create_planspace(
                project, title="Active", mode="manual", store_root=store.root
            )
            runtime = registry._runtimes[project.id]
            runtime.project.active_planspace_id = active_lane
            runtime.project.planspace_selection_explicit = True
            store.update_project(runtime.project)

            with patch.object(registry, "_schedule_queued"):
                review = await registry.spawn_code_review(project.id)

            assert review is not None
            self.assertNotEqual(review.planspace_id, first_lane)
            self.assertEqual(review.planspace_id, active_lane)

    async def test_spawn_is_idempotent_while_review_is_in_flight(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            store = Store(root=root / "store")
            project = Project(root_path=str(repo), model_preset_id="opus-4-8")
            store.create_project(project)
            registry = ProjectRegistry(store=store)
            runtime = registry._runtimes[project.id]
            _ControlledRunner.instances.clear()
            probe_barrier = threading.Barrier(2)

            def concurrent_probe(_cwd: str) -> bool:
                probe_barrier.wait(timeout=2)
                return True

            with (
                patch("miniclaw2.registry.NodeRunner", _ControlledRunner),
                patch("miniclaw2.registry.is_git_repo", side_effect=concurrent_probe),
            ):
                first, second = await asyncio.gather(
                    registry.spawn_code_review(project.id),
                    registry.spawn_code_review(project.id),
                )

                assert first is not None and second is not None
                self.assertEqual(first.id, second.id)
                self.assertEqual(first.model_preset_id, "gpt-5.6")
                reviews = [
                    node for node in store.list_nodes(project.id)
                    if node.subtype is ReviewSubtype.CODE_REVIEW
                ]
                self.assertEqual(len(reviews), 1)
                await asyncio.sleep(0)
                _ControlledRunner.instances[first.id].release.set()
                task = runtime.runner_tasks.get(first.id)
                if task is not None:
                    await task

    async def test_spawn_uses_configured_code_review_preset(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            store = Store(root=root / "store")
            config = load_global_config(store.root)
            save_global_config(
                config.model_copy(
                    update={
                        "code_review": CodeReviewSettings(
                            model_preset_id="opus-4-8"
                        )
                    }
                ),
                store.root,
            )
            project = Project(root_path=str(repo), model_preset_id="gpt-5.6")
            store.create_project(project)
            registry = ProjectRegistry(store=store)
            runtime = registry._runtimes[project.id]
            _ControlledRunner.instances.clear()

            with patch("miniclaw2.registry.NodeRunner", _ControlledRunner):
                review = await registry.spawn_code_review(project.id)
                assert review is not None
                self.assertEqual(review.model_preset_id, "opus-4-8")
                await asyncio.sleep(0)
                _ControlledRunner.instances[review.id].release.set()
                task = runtime.runner_tasks.get(review.id)
                if task is not None:
                    await task

    async def test_review_drains_then_excludes_later_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            store = Store(root=root / "store")
            project = Project(root_path=str(repo), concurrency=2)
            store.create_project(project)
            registry = ProjectRegistry(store=store)
            runtime = registry._runtimes[project.id]
            first = store.create_node(
                Node(project_id=project.id, model_preset_id="gpt-5.5")
            )
            later = store.create_node(
                Node(project_id=project.id, model_preset_id="gpt-5.5", created_at=first.created_at + 2)
            )
            _ControlledRunner.instances.clear()
            with patch("miniclaw2.registry.NodeRunner", _ControlledRunner):
                registry._schedule_queued(runtime)
                await asyncio.sleep(0)
                review = await registry.spawn_code_review(project.id)
                assert review is not None
                await asyncio.sleep(0)
                self.assertEqual(set(runtime.runners), {first.id, later.id})

                _ControlledRunner.instances[first.id].release.set()
                _ControlledRunner.instances[later.id].release.set()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.assertEqual(set(runtime.runners), {review.id})

                trailing = store.create_node(
                    Node(project_id=project.id, model_preset_id="gpt-5.5")
                )
                registry._schedule_queued(runtime)
                self.assertNotIn(trailing.id, runtime.runners)

                _ControlledRunner.instances[review.id].release.set()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.assertIn(trailing.id, runtime.runners)
                _ControlledRunner.instances[trailing.id].release.set()
                await asyncio.sleep(0)


if __name__ == "__main__":
    unittest.main()
