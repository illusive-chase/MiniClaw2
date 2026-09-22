"""Tests for runner-owned inline preview repair retries."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from miniclaw2 import runner as runner_module
from miniclaw2.app import create_app
from miniclaw2.artifacts import stored_artifacts_dir, workspace_artifacts_dir
from miniclaw2.contextspace import (
    contextspace_root,
    create_planspace,
    resolve_project_binding,
)
from miniclaw2.domain import (
    ArtifactMode,
    ArtifactRef,
    Category,
    Node,
    NodeKind,
    NodeState,
    Project,
)
from miniclaw2.providers import AgentProviderContext, AgentProviderEvent
from miniclaw2.runner import NodeRunner
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)


class _RepairProvider:
    name = "stub"

    def __init__(self, *, repair_succeeds: bool) -> None:
        self.repair_succeeds = repair_succeeds
        self.prompts: list[str] = []

    async def run(self, context: AgentProviderContext):
        self.prompts.append(context.node.prompt)
        if len(self.prompts) > 1 and self.repair_succeeds:
            _write_own_preview(context)
        yield AgentProviderEvent(kind="session", session_id="stub-session")
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class _CancellingRepairProvider:
    name = "stub"

    def __init__(self, task_getter) -> None:
        self.task_getter = task_getter
        self.prompts: list[str] = []

    async def run(self, context: AgentProviderContext):
        self.prompts.append(context.node.prompt)
        yield AgentProviderEvent(kind="session", session_id="stub-session")
        if len(self.prompts) > 1:
            self.task_getter().cancel()
            await asyncio.sleep(3600)
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class _BareProvider:
    name = "stub"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def run(self, context: AgentProviderContext):
        self.prompts.append(context.node.prompt)
        yield AgentProviderEvent(kind="session", session_id="stub-session")

    async def interrupt(self) -> None:
        return None


class _DiffReviewProvider:
    name = "stub"

    async def run(self, context: AgentProviderContext):
        (Path(context.project.root_path) / "seed.txt").write_text(
            "changed by node\n", encoding="utf-8"
        )
        _write_own_preview(context)
        yield AgentProviderEvent(kind="session", session_id="stub-session")
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class _UnlanedArtifactProvider:
    name = "stub"

    async def run(self, context: AgentProviderContext):
        node = context.node
        outputs = workspace_artifacts_dir(context.project, node.id)
        (outputs / "report.md").write_text("# Report\n", encoding="utf-8")
        path = (
            Path(context.project.root_path)
            / ".miniclaw2"
            / "graph"
            / "runs"
            / node.id
            / "lanes"
            / "nodes"
            / node.id
            / "preview.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "id": node.id,
                    "kind": "agent",
                    "category": "regular",
                    "state": "done",
                    "ran_at": "2026-06-15T00:00:00+00:00",
                    "lane": "",
                    "motivation": "publish a report",
                    "summary": "report published",
                    "next_implications": "none",
                    "artifacts": ["report.md"],
                }
            ),
            encoding="utf-8",
        )
        yield AgentProviderEvent(kind="session", session_id="stub-session")
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class _SvgArtifactProvider:
    name = "stub"
    content = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 40"><text y="20">SVG 产物</text></svg>'

    async def run(self, context: AgentProviderContext):
        prompt = context.launch_instructions
        assert "one or more SVG files" in prompt
        outputs_match = re.search(r"To show a file to the human, write it under:\s*\n\s*([^\n]+)", prompt)
        preview_match = re.search(r"write your own preview at:\s*\n\s*([^\n]+)", prompt)
        assert outputs_match is not None
        assert preview_match is not None
        outputs = Path(outputs_match[1].strip())
        assert outputs.is_absolute()
        (outputs / "图表.svg").write_text(self.content, encoding="utf-8")
        preview = Path(context.project.root_path) / preview_match[1].strip()
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_text(json.dumps({
            "id": context.node.id,
            "kind": "agent",
            "category": "regular",
            "state": "done",
            "ran_at": "2026-09-13T00:00:00Z",
            "lane": context.node.planspace_id or "",
            "motivation": "验证 SVG 发布契约",
            "summary": "已按提示中的目录生成 SVG",
            "next_implications": "验证持久副本和 API",
            "artifacts": ["图表.svg"],
        }), encoding="utf-8")
        yield AgentProviderEvent(kind="session", session_id="svg-session")
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class _MismatchedArtifactProvider:
    name = "stub"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def run(self, context: AgentProviderContext):
        self.prompts.append(context.node.prompt)
        outputs = workspace_artifacts_dir(context.project, context.node.id)
        (outputs / "report.md").write_text("# Report\n", encoding="utf-8")
        (outputs / "data.json").write_text("{}\n", encoding="utf-8")
        preview_path = (
            Path(context.project.root_path)
            / ".miniclaw2"
            / "graph"
            / "runs"
            / context.node.id
            / "lanes"
            / (context.node.planspace_id or "")
            / "nodes"
            / context.node.id
            / "preview.json"
        )
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_text(
            json.dumps(
                {
                    "id": context.node.id,
                    "kind": "agent",
                    "category": "regular",
                    "state": "done",
                    "ran_at": "2026-09-16T00:00:00Z",
                    "lane": context.node.planspace_id or "",
                    "motivation": "publish a report",
                    "summary": "report written",
                    "next_implications": "none",
                    "artifacts": (
                        ["report.md"] if len(self.prompts) > 1 else ["data.json"]
                    ),
                }
            ),
            encoding="utf-8",
        )
        yield AgentProviderEvent(kind="session", session_id="artifact-repair")
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


def _write_own_preview(context: AgentProviderContext) -> None:
    node = context.node
    lane = node.planspace_id or ""
    path = (
        Path(context.project.root_path)
        / ".miniclaw2"
        / "graph"
        / "runs"
        / node.id
        / "lanes"
        / lane
        / "nodes"
        / node.id
        / "preview.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": node.id,
                "kind": "agent",
                "category": "regular",
                "state": "done",
                "ran_at": "2026-06-15T00:00:00+00:00",
                "lane": lane,
                "motivation": "m",
                "summary": "repaired",
                "next_implications": "none",
            }
        ),
        encoding="utf-8",
    )


class RunnerPreviewRepairTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.store_root = Path(self.tmp.name) / "store"
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        _init_repo(self.repo)
        self.store = Store(root=self.store_root)
        self.project = Project(root_path=str(self.repo))
        self.store.create_project(self.project)
        self.plug_id = create_planspace(
            self.project, title="repair-lane", mode="manual"
        )
        self.store.update_project(self.project)

    async def asyncTearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def _node(self) -> Node:
        node = Node(
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            model_preset_id="opus-4-7",
            category=Category.REGULAR,
            state=NodeState.QUEUED,
            planspace_id=self.plug_id,
            prompt="do work",
        )
        self.store.create_node(node)
        return node

    async def test_missing_preview_is_reprompted_and_can_repair(self) -> None:
        node = self._node()
        emitted: list[dict] = []

        async def on_event(payload: dict) -> None:
            emitted.append(payload)

        provider = _RepairProvider(repair_succeeds=True)
        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            await asyncio.wait_for(runner.run(), timeout=5.0)

        self.assertEqual(node.state, NodeState.DONE)
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("Repair attempt 1 of 3", provider.prompts[1])
        expected_path = (
            f".miniclaw2/graph/runs/{node.id}/lanes/{self.plug_id}"
            f"/nodes/{node.id}/preview.json"
        )
        self.assertIn(expected_path, provider.prompts[1])
        self.assertIn('"artifacts": []', provider.prompts[1])
        self.assertNotIn(
            f".miniclaw2/graph/lanes/{self.plug_id}",
            provider.prompts[1],
        )
        preview = self.store.read_node_preview(self.project.id, node.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn("repaired", preview)
        repair_events = [
            ev for ev in emitted
            if ev.get("type") == "activity"
            and ev.get("kind") == "agent"
            and ev.get("status") == "progress"
            and ev.get("name") == "Preview contract repair"
        ]
        self.assertEqual(len(repair_events), 1)
        self.assertFalse(
            any(
                ev.get("type") == "error"
                and "Preview contract repair" in ev.get("message", "")
                for ev in emitted
            )
        )

    async def test_diff_review_is_published_with_agent_artifacts(self) -> None:
        node = self._node()
        node.diff_review = True
        self.store.update_node(node)
        runner = NodeRunner(node, self.project, self.store, lambda _event: asyncio.sleep(0))

        with patch.object(runner_module, "_make_provider", return_value=_DiffReviewProvider()):
            await asyncio.wait_for(runner.run(), timeout=5.0)

        self.assertEqual(node.state, NodeState.DONE)
        self.assertIn("run-diff.json", [ref.name for ref in node.artifacts])
        artifact = json.loads(
            (stored_artifacts_dir(self.store, self.project.id, node.id) / "run-diff.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(artifact["kind"], "miniclaw2.diff/v1")
        self.assertEqual(artifact["files"][0]["path"], "seed.txt")
        refs = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", f"refs/miniclaw2/snapshots/{node.id}"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertEqual(refs, "")

    def test_launch_snapshot_preserves_queued_node_planspace(self) -> None:
        node = self._node()
        queued_planspace_id = node.planspace_id
        other_planspace_id = create_planspace(
            self.project, title="other-lane", mode="manual"
        )
        runner = NodeRunner(node, self.project, self.store, lambda _event: None)

        context_bundle = runner._snapshot_context_bundle()
        runner._snapshot_launch_settings(context_bundle)

        self.assertEqual(context_bundle.active_planspace_id, queued_planspace_id)
        self.assertEqual(node.planspace_id, queued_planspace_id)
        self.assertEqual(
            node.settings_snapshot.get("active_planspace_id"),
            queued_planspace_id,
        )

    async def test_missing_preview_stubs_after_three_failed_repairs(self) -> None:
        node = self._node()

        async def on_event(_payload: dict) -> None:
            return None

        provider = _RepairProvider(repair_succeeds=False)
        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            await asyncio.wait_for(runner.run(), timeout=5.0)

        self.assertEqual(node.state, NodeState.ERROR)
        self.assertEqual(len(provider.prompts), 4)
        preview = self.store.read_node_preview(self.project.id, node.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn("preview contract abandoned", preview)

    async def test_cancellation_during_repair_finalizes_cancelled_stub(self) -> None:
        node = self._node()
        emitted: list[dict] = []

        async def on_event(payload: dict) -> None:
            emitted.append(payload)

        task: asyncio.Task[None] | None = None

        def task_getter() -> asyncio.Task[None]:
            assert task is not None
            return task

        provider = _CancellingRepairProvider(task_getter)
        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            task = asyncio.create_task(runner.run())
            await asyncio.wait_for(task, timeout=5.0)

        self.assertEqual(node.state, NodeState.CANCELLED)
        self.assertIsNotNone(node.finished_at)
        self.assertEqual(len(provider.prompts), 2)
        self.assertEqual(emitted[-1].get("type"), "turn_done")
        preview = self.store.read_node_preview(self.project.id, node.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn('"state": "cancelled"', preview)
        self.assertIn("preview repair cancelled", preview)

    async def test_provider_stream_exhaustion_without_terminal_event_errors(self) -> None:
        node = self._node()
        emitted: list[dict] = []

        async def on_event(payload: dict) -> None:
            emitted.append(payload)

        provider = _BareProvider()
        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            await asyncio.wait_for(runner.run(), timeout=5.0)

        self.assertEqual(node.state, NodeState.ERROR)
        self.assertEqual(len(provider.prompts), 1)
        self.assertIn("without a terminal event", node.error or "")
        self.assertTrue(
            any(
                ev.get("type") == "error"
                and "without a terminal event" in ev.get("message", "")
                for ev in emitted
            )
        )
        preview = self.store.read_node_preview(self.project.id, node.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn('"state": "error"', preview)

    async def test_unlaned_run_persists_preview_artifact_declarations(self) -> None:
        node = self._node()
        node.planspace_id = None
        self.store.update_node(node)

        async def on_event(_payload: dict) -> None:
            return None

        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(
            runner_module,
            "_make_provider",
            return_value=_UnlanedArtifactProvider(),
        ):
            await asyncio.wait_for(runner.run(), timeout=5.0)

        self.assertEqual(node.state, NodeState.DONE)
        self.assertEqual([artifact.name for artifact in node.artifacts], ["report.md"])
        preview = self.store.read_node_preview(self.project.id, node.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn("report published", preview)
        durable = stored_artifacts_dir(self.store, self.project.id, node.id)
        self.assertEqual(
            (durable / "report.md").read_text(encoding="utf-8"),
            "# Report\n",
        )

    async def test_svg_follows_launch_contract_through_reap_publication_and_api(self) -> None:
        node = self._node()
        node.artifact_mode = ArtifactMode.SVG
        self.store.update_node(node)
        emitted: list[dict] = []

        async def on_event(payload: dict) -> None:
            emitted.append(payload)

        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=_SvgArtifactProvider()):
            await asyncio.wait_for(runner.run(), timeout=10.0)

        self.assertEqual(node.state, NodeState.DONE, node.error)
        self.assertEqual([(ref.name, ref.status) for ref in node.artifacts], [("图表.svg", "published")])
        self.assertTrue(any(
            event.get("type") == "node_updated"
            and event["node"]["state"] == "done"
            and event["node"]["artifacts"][0]["name"] == "图表.svg"
            for event in emitted
        ))
        durable = stored_artifacts_dir(self.store, self.project.id, node.id)
        self.assertEqual((durable / "图表.svg").read_text(encoding="utf-8"), _SvgArtifactProvider.content)
        (workspace_artifacts_dir(self.project, node.id) / "图表.svg").unlink()
        preview = json.loads(self.store.read_node_preview(self.project.id, node.id) or "{}")
        self.assertEqual(preview["artifacts"], ["图表.svg"])

        registry = ProjectRegistry(store=self.store)
        client = TestClient(create_app(registry))
        try:
            persisted = registry.get_node(self.project.id, node.id)
            assert persisted is not None
            self.assertEqual(persisted.artifacts, node.artifacts)
            url = f"/sessions/{self.project.id}/nodes/{node.id}/artifacts/图表.svg"
            inline = client.get(url)
            self.assertEqual(inline.status_code, 200, inline.text)
            self.assertEqual(inline.json()["sha256"], node.artifacts[0].sha256)
            raw = client.get(url, params={"raw": 1})
            self.assertEqual(raw.status_code, 200, raw.text)
            self.assertEqual(raw.content, _SvgArtifactProvider.content.encode("utf-8"))
            self.assertEqual(raw.headers["content-type"], "image/svg+xml")
            self.assertIn("%E5%9B%BE%E8%A1%A8.svg", raw.headers["content-disposition"])
        finally:
            client.close()

    async def test_wrong_artifact_mode_is_repaired_before_completion(self) -> None:
        node = self._node()
        node.artifact_mode = ArtifactMode.MARKDOWN
        self.store.update_node(node)

        async def on_event(_payload: dict) -> None:
            return None

        provider = _MismatchedArtifactProvider()
        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            await asyncio.wait_for(runner.run(), timeout=5.0)

        self.assertEqual(node.state, NodeState.DONE, node.error)
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn(
            "artifact_mode=markdown requires at least one published .md artifact",
            provider.prompts[1],
        )
        self.assertIn(
            '"artifacts": ["<bare Markdown artifact filename>.md"]',
            provider.prompts[1],
        )
        self.assertNotIn('"artifacts": ["data.json"]', provider.prompts[1])
        self.assertIn(
            str(workspace_artifacts_dir(self.project, node.id)),
            provider.prompts[1],
        )
        self.assertEqual(
            [(ref.name, ref.status) for ref in node.artifacts],
            [("report.md", "published")],
        )
        preview = json.loads(
            self.store.read_node_preview(self.project.id, node.id) or "{}"
        )
        self.assertEqual(preview["artifacts"], ["report.md"])

    def test_repair_artifacts_match_each_required_mode(self) -> None:
        node = self._node()
        node.artifacts = [
            ArtifactRef(
                name=name,
                bytes=1,
                mtime=1,
                sha256="hash",
                status="published",
            )
            for name in ("data.json", "page.html", "diagram.svg")
        ]
        outputs = workspace_artifacts_dir(self.project, node.id)

        expected_by_mode = {
            ArtifactMode.MARKDOWN: ["<bare Markdown artifact filename>.md"],
            ArtifactMode.HTML: ["page.html"],
            ArtifactMode.SVG: ["diagram.svg"],
        }
        for mode, expected in expected_by_mode.items():
            with self.subTest(mode=mode):
                node.artifact_mode = mode
                prompt = runner_module._preview_repair_prompt(
                    node,
                    "invalid artifact mode",
                    1,
                    outputs_path=outputs,
                )
                encoded = json.dumps(expected, ensure_ascii=False)
                self.assertIn(f'"artifacts": {encoded}', prompt)

    async def test_stale_active_planspace_errors_before_provider_launch(self) -> None:
        node = self._node()
        node.planspace_id = "planspaces.deleted"
        self.store.update_node(node)
        emitted: list[dict] = []

        async def on_event(payload: dict) -> None:
            emitted.append(payload)

        provider = _RepairProvider(repair_succeeds=True)
        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            await asyncio.wait_for(runner.run(), timeout=5.0)

        self.assertEqual(provider.prompts, [])
        self.assertEqual(node.state, NodeState.ERROR)
        self.assertIn("Stale launch settings", node.error or "")
        self.assertIn("planspaces.deleted", node.error or "")
        preview = self.store.read_node_preview(self.project.id, node.id)
        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn('"state": "error"', preview)
        self.assertIn("planspaces.deleted", preview)
        self.assertTrue(
            any(
                ev.get("type") == "error"
                and "planspaces.deleted" in ev.get("message", "")
                for ev in emitted
            )
        )
        self.assertEqual(emitted[-1].get("type"), "turn_done")

    async def test_unbound_queued_planspace_errors_before_provider_launch(self) -> None:
        node = self._node()
        queued_planspace_id = node.planspace_id
        assert queued_planspace_id is not None
        current_planspace_id = create_planspace(
            self.project, title="current-lane", mode="manual"
        )
        self.store.update_project(self.project)

        binding = resolve_project_binding(
            self.project, contextspace_root(self.store.root)
        )
        self.assertIsNotNone(binding)
        assert binding is not None
        binding_raw = dict(binding.raw)
        binding_raw["plugs"] = [
            ref
            for ref in binding_raw.get("plugs", [])
            if not isinstance(ref, dict) or ref.get("id") != queued_planspace_id
        ]
        binding.path.write_text(
            yaml.safe_dump(binding_raw, sort_keys=False), encoding="utf-8"
        )

        emitted: list[dict] = []

        async def on_event(payload: dict) -> None:
            emitted.append(payload)

        provider = _RepairProvider(repair_succeeds=True)
        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            await asyncio.wait_for(runner.run(), timeout=5.0)

        self.assertEqual(provider.prompts, [])
        self.assertEqual(node.state, NodeState.ERROR)
        self.assertIn("Stale launch settings", node.error or "")
        self.assertIn(queued_planspace_id, node.error or "")
        self.assertTrue(
            any(
                event.get("type") == "error"
                and queued_planspace_id in event.get("message", "")
                for event in emitted
            )
        )


if __name__ == "__main__":
    unittest.main()
