from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from miniclaw2.artifacts import (
    load_node_artifact,
    summarize_node_artifact,
    validate_node_output_path,
)
from miniclaw2.domain import Node, NodeOutputKind, NodeState, Project
from miniclaw2.providers.base import AgentProviderEvent
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


class _ArtifactWritingProvider:
    name = "stub"

    async def run(self, context: Any):
        path = Path(context.project.root_path) / context.node.output_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if context.node.output_kind is NodeOutputKind.INTERFACE:
            path.write_text(
                '{"kind":"interface","summary":"Interface complete.","purpose":"p","method":"m","result":{"ok":true},"files":[]}',
                encoding="utf-8",
            )
        else:
            path.write_text(
                "# Purpose\nSummarize the node.\n\n# Method\nUsed a stub.\n\n# Result\nDone.\n",
                encoding="utf-8",
            )
        yield AgentProviderEvent(kind="session", session_id="stub-session")
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class NodeArtifactTest(unittest.TestCase):
    def test_summary_artifact_loads_markdown_and_derives_purpose_summary(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            node = Node(
                project_id="p1",
                output_kind=NodeOutputKind.SUMMARY,
                output_path="out/result.md",
            )
            (root / "out").mkdir()
            (root / "out" / "result.md").write_text(
                "# Purpose\n"
                "Describe the work.\n\n"
                "# Method\n"
                "Ran tests.\n\n"
                "# Result\n"
                "All passed.\n",
                encoding="utf-8",
            )

            artifact = load_node_artifact(str(root), node)

            self.assertTrue(artifact.exists)
            self.assertEqual(artifact.kind, "summary")
            self.assertIn("# Purpose", artifact.content or "")
            self.assertEqual(summarize_node_artifact(node, artifact), "Describe the work.")

    def test_interface_artifact_loads_json_and_uses_summary_key(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            node = Node(
                project_id="p1",
                output_kind=NodeOutputKind.INTERFACE,
                output_path="out/result.json",
            )
            (root / "out").mkdir()
            (root / "out" / "result.json").write_text(
                '{"kind":"interface","summary":"Computed result.","purpose":"p","method":"m","result":{"ok":true},"files":[]}',
                encoding="utf-8",
            )

            artifact = load_node_artifact(str(root), node)

            self.assertTrue(artifact.exists)
            self.assertIsInstance(artifact.data, dict)
            self.assertEqual(artifact.data["result"], {"ok": True})
            self.assertEqual(summarize_node_artifact(node, artifact), "Computed result.")

    def test_interface_artifact_reports_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            node = Node(
                project_id="p1",
                output_kind=NodeOutputKind.INTERFACE,
                output_path="result.json",
            )
            (root / "result.json").write_text("{bad", encoding="utf-8")

            artifact = load_node_artifact(str(root), node)

            self.assertTrue(artifact.exists)
            self.assertIn("invalid JSON", artifact.error or "")
            self.assertIn("output invalid", summarize_node_artifact(node, artifact) or "")

    def test_interface_artifact_reports_schema_errors(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            node = Node(
                project_id="p1",
                output_kind=NodeOutputKind.INTERFACE,
                output_path="result.json",
            )
            (root / "result.json").write_text(
                '{"kind":"note","summary":"Bad shape."}',
                encoding="utf-8",
            )

            artifact = load_node_artifact(str(root), node)

            self.assertTrue(artifact.exists)
            self.assertIn("missing required keys", artifact.error or "")
            self.assertIn("output invalid", summarize_node_artifact(node, artifact) or "")

    def test_summary_artifact_reports_missing_required_sections(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            node = Node(
                project_id="p1",
                output_kind=NodeOutputKind.SUMMARY,
                output_path="result.md",
            )
            (root / "result.md").write_text("# Purpose\nOnly one section.\n", encoding="utf-8")

            artifact = load_node_artifact(str(root), node)

            self.assertTrue(artifact.exists)
            self.assertIn("missing required sections", artifact.error or "")
            self.assertIn("output invalid", summarize_node_artifact(node, artifact) or "")

    def test_missing_artifact_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            node = Node(project_id="p1", output_kind=NodeOutputKind.SUMMARY)

            artifact = load_node_artifact(str(root), node)

            self.assertFalse(artifact.exists)
            self.assertEqual(artifact.path, f".miniclaw2/outputs/{node.id}/result.md")
            self.assertIn("output missing", summarize_node_artifact(node, artifact) or "")

    def test_output_path_validation_rejects_absolute_and_parent_paths(self) -> None:
        self.assertIsNone(validate_node_output_path("out/result.json"))
        self.assertIsNone(validate_node_output_path(".miniclaw2/outputs/n/result.md"))
        self.assertIn("project-relative", validate_node_output_path("/tmp/result.json") or "")
        self.assertIn("..", validate_node_output_path("../result.json") or "")
        self.assertIn("..", validate_node_output_path("out/../result.json") or "")

    def test_path_escape_is_reported_when_loading_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            node = Node(
                project_id="p1",
                output_kind=NodeOutputKind.SUMMARY,
                output_path="../result.md",
            )

            artifact = load_node_artifact(str(root), node)

            self.assertFalse(artifact.exists)
            self.assertEqual(artifact.error, "output path escapes project root")


class NodeArtifactRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_runner_injects_summary_contract_and_updates_node_summary(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            root = tmp / "project"
            root.mkdir()
            store = Store(root=tmp / "store")
            project = Project(root_path=str(root))
            store.create_project(project)
            node = store.create_node(
                Node(project_id=project.id, output_kind=NodeOutputKind.SUMMARY)
            )
            emitted: list[dict[str, object]] = []

            async def on_event(payload: dict[str, object]) -> None:
                emitted.append(payload)

            with patch(
                "miniclaw2.runner._make_provider",
                return_value=_ArtifactWritingProvider(),
            ):
                runner = NodeRunner(node, project, store, on_event)
                await runner.run()

            self.assertEqual(node.state, NodeState.DONE)
            self.assertEqual(node.output_path, f".miniclaw2/outputs/{node.id}/result.md")
            self.assertIn("Write a markdown file", node.output_contract_snapshot)
            self.assertEqual(node.summary, "Summarize the node.")
            self.assertTrue((root / node.output_path).exists())

    async def test_runner_injects_interface_contract_and_updates_node_summary(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            root = tmp / "project"
            root.mkdir()
            store = Store(root=tmp / "store")
            project = Project(root_path=str(root))
            store.create_project(project)
            node = store.create_node(
                Node(project_id=project.id, output_kind=NodeOutputKind.INTERFACE)
            )
            emitted: list[dict[str, object]] = []

            async def on_event(payload: dict[str, object]) -> None:
                emitted.append(payload)

            with patch(
                "miniclaw2.runner._make_provider",
                return_value=_ArtifactWritingProvider(),
            ):
                runner = NodeRunner(node, project, store, on_event)
                await runner.run()

            self.assertEqual(node.state, NodeState.DONE)
            self.assertEqual(node.output_path, f".miniclaw2/outputs/{node.id}/result.json")
            self.assertIn("Write JSON", node.output_contract_snapshot)
            self.assertEqual(node.summary, "Interface complete.")
            self.assertTrue((root / node.output_path).exists())


if __name__ == "__main__":
    unittest.main()
