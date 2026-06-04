from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from miniclaw2.contextspace import (
    apply_memory_delta_artifact,
    apply_memory_delta_inbox,
    compose_context_bundle,
    load_context_bundle_for_node,
    memory_delta_output_relpath,
)
from miniclaw2.domain import Node, NodeState, Project
from miniclaw2.providers.base import AgentProviderEvent
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


def _write_contextspace(store_root: Path, project_root: Path) -> Path:
    ctx = store_root / "contextspace"
    (ctx / "bindings" / "projects").mkdir(parents=True)
    (ctx / "plugs" / "planspaces" / "memory" / "inbox").mkdir(parents=True)
    (ctx / "plugs" / "skills" / "python-testing").mkdir(parents=True)

    (ctx / "bindings" / "projects" / "project.test.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "id: project.test",
                "project:",
                "  name: Test Project",
                "  local_paths:",
                f"    - {project_root}",
                "plugs:",
                "  - id: planspaces.memory",
                "    role: status-plan",
                "    injection: turn",
                "    enabled: true",
                "    auto_update: true",
                "  - id: skills.python-testing",
                "    role: skill",
                "    injection: turn",
                "    enabled: true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (ctx / "plugs" / "planspaces" / "memory" / "manifest.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "id: planspaces.memory",
                "kind: planspace",
                "title: Memory Protocol",
                "injection:",
                "  STATUS.md: turn",
                "  PLAN.md: turn",
                "max_chars:",
                "  STATUS.md: 4000",
                "  PLAN.md: 6000",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (ctx / "plugs" / "planspaces" / "memory" / "STATUS.md").write_text(
        "Current status: implement contextspace.\n",
        encoding="utf-8",
    )
    (ctx / "plugs" / "planspaces" / "memory" / "PLAN.md").write_text(
        "Current plan: ship the first slice.\n",
        encoding="utf-8",
    )
    (ctx / "plugs" / "skills" / "python-testing" / "manifest.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "id: skills.python-testing",
                "kind: skill",
                "title: Python Testing",
                "injection: turn",
                "max_chars: 6000",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (ctx / "plugs" / "skills" / "python-testing" / "CONTEXT.md").write_text(
        "Use pytest for focused regression tests.\n",
        encoding="utf-8",
    )
    return ctx


class _CaptureProvider:
    name = "capture"

    def __init__(self) -> None:
        self.contexts: list[Any] = []

    async def run(self, context: Any):
        self.contexts.append(context)
        yield AgentProviderEvent(kind="session", session_id="stub-session")
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class _MemoryDeltaWritingProvider:
    name = "memory-delta"

    def __init__(self, before_write: Any | None = None) -> None:
        self.before_write = before_write
        self.contexts: list[Any] = []

    async def run(self, context: Any):
        self.contexts.append(context)
        if self.before_write is not None:
            self.before_write()
        settings = context.node.settings_snapshot
        path = Path(context.project.root_path) / memory_delta_output_relpath(context.node)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "node_id": context.node.id,
                    "project_id": context.project.id,
                    "binding_id": settings["project_context_binding_id"],
                    "planspace_id": settings["active_planspace_id"],
                    "created_at": 1234567890,
                    "terminal_state": "done",
                    "acceptance_state": "unreviewed",
                    "updates": [
                        {
                            "target": "STATUS.md",
                            "operation": "append_observation",
                            "policy": "auto",
                            "confidence": "observed",
                            "text": "Implemented memory delta v1 via project artifact.",
                        },
                        {
                            "target": "PLAN.md",
                            "operation": "propose_patch",
                            "policy": "proposed",
                            "reason": "Next step should remain manual.",
                            "patch": "Do not auto-apply this plan proposal.",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        yield AgentProviderEvent(kind="session", session_id="stub-session")
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class ContextSpaceRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_runner_snapshots_binding_and_injects_turn_context(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = Store(root=tmp / "store")
            project_root = tmp / "repo"
            project_root.mkdir()
            (project_root / "CONTEXT.md").write_text(
                "# Project Context\n\nRespect repo rules.\n",
                encoding="utf-8",
            )
            _write_contextspace(store.root, project_root)

            project = Project(
                root_path=str(project_root),
                project_context_binding_id="project.test",
            )
            store.create_project(project)
            node = store.create_node(Node(project_id=project.id, prompt="Do the work."))
            emitted: list[dict[str, object]] = []

            async def on_event(payload: dict[str, object]) -> None:
                emitted.append(payload)

            provider = _CaptureProvider()
            with patch("miniclaw2.runner._make_provider", return_value=provider):
                runner = NodeRunner(node, project, store, on_event)
                await runner.run()

            self.assertEqual(node.state, NodeState.DONE)
            self.assertEqual(
                node.system_context_snapshot,
                "# Project Context\n\nRespect repo rules.\n",
            )
            self.assertIsNotNone(node.context_bundle_id)
            self.assertEqual(len(provider.contexts), 1)
            captured = provider.contexts[0]
            self.assertEqual(
                captured.system_context,
                "# Project Context\n\nRespect repo rules.\n",
            )
            self.assertIn("Current status: implement contextspace.", captured.launch_instructions)
            self.assertIn("Current plan: ship the first slice.", captured.launch_instructions)
            self.assertIn("Use pytest for focused regression tests.", captured.launch_instructions)

            bundle = load_context_bundle_for_node(node, store_root=store.root)
            assert bundle is not None
            self.assertEqual(bundle["project_binding_id"], "project.test")
            self.assertEqual(bundle["active_planspace_id"], "planspaces.memory")
            source_paths = {source["path"] for source in bundle["sources"]}
            self.assertIn(str(project_root / "CONTEXT.md"), source_paths)
            self.assertIn("plugs/planspaces/memory/STATUS.md", source_paths)
            self.assertIn("plugs/planspaces/memory/PLAN.md", source_paths)
            self.assertIn("plugs/skills/python-testing/CONTEXT.md", source_paths)

    async def test_runner_omits_memory_delta_contract_when_auto_update_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = Store(root=tmp / "store")
            project_root = tmp / "repo"
            project_root.mkdir()
            ctx = _write_contextspace(store.root, project_root)
            binding_path = ctx / "bindings" / "projects" / "project.test.yaml"
            binding_path.write_text(
                binding_path.read_text(encoding="utf-8").replace(
                    "    auto_update: true",
                    "    auto_update: false",
                    1,
                ),
                encoding="utf-8",
            )

            project = Project(
                root_path=str(project_root),
                project_context_binding_id="project.test",
            )
            store.create_project(project)
            node = store.create_node(Node(project_id=project.id, prompt="Do the work."))

            async def on_event(payload: dict[str, object]) -> None:
                return None

            provider = _CaptureProvider()
            with patch("miniclaw2.runner._make_provider", return_value=provider):
                runner = NodeRunner(node, project, store, on_event)
                await runner.run()

            self.assertEqual(node.state, NodeState.DONE)
            self.assertEqual(len(provider.contexts), 1)
            launch_instructions = provider.contexts[0].launch_instructions
            self.assertIn("Current status: implement contextspace.", launch_instructions)
            self.assertNotIn("ContextSpace memory delta contract", launch_instructions)
            self.assertNotIn(memory_delta_output_relpath(node), launch_instructions)
            self.assertEqual(
                node.settings_snapshot["active_planspace_id"],
                "planspaces.memory",
            )
            self.assertNotIn("memory_delta_output_path", node.settings_snapshot)

            bundle = load_context_bundle_for_node(node, store_root=store.root)
            assert bundle is not None
            self.assertEqual(bundle["active_planspace_id"], "planspaces.memory")
            self.assertFalse(bundle["active_planspace_auto_update"])
            self.assertFalse(bundle["active_planspace"]["auto_update"])

    async def test_runner_applies_project_memory_delta_to_snapshot_planspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = Store(root=tmp / "store")
            project_root = tmp / "repo"
            project_root.mkdir()
            ctx = _write_contextspace(store.root, project_root)

            other = ctx / "plugs" / "planspaces" / "other"
            (other / "inbox").mkdir(parents=True)
            (other / "manifest.yaml").write_text(
                "\n".join(
                    [
                        "version: 1",
                        "id: planspaces.other",
                        "kind: planspace",
                        "title: Other Direction",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (other / "STATUS.md").write_text("Other status.\n", encoding="utf-8")
            (other / "PLAN.md").write_text("Other plan.\n", encoding="utf-8")

            binding_path = ctx / "bindings" / "projects" / "project.test.yaml"

            def write_binding(active: str) -> None:
                binding_path.write_text(
                    "\n".join(
                        [
                            "version: 1",
                            "id: project.test",
                            f"active_planspace_id: {active}",
                            "project:",
                            "  name: Test Project",
                            "  local_paths:",
                            f"    - {project_root}",
                            "plugs:",
                            "  - id: planspaces.memory",
                            "    role: status-plan",
                            "    injection: turn",
                            "    enabled: true",
                            "    auto_update: true",
                            "  - id: planspaces.other",
                            "    role: status-plan",
                            "    injection: turn",
                            "    enabled: true",
                            "    auto_update: true",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )

            write_binding("planspaces.memory")
            project = Project(
                root_path=str(project_root),
                project_context_binding_id="project.test",
            )
            store.create_project(project)
            node = store.create_node(Node(project_id=project.id, prompt="Do the work."))
            emitted: list[dict[str, object]] = []

            async def on_event(payload: dict[str, object]) -> None:
                emitted.append(payload)

            provider = _MemoryDeltaWritingProvider(
                before_write=lambda: write_binding("planspaces.other")
            )
            with patch("miniclaw2.runner._make_provider", return_value=provider):
                runner = NodeRunner(node, project, store, on_event)
                await runner.run()

            self.assertEqual(node.state, NodeState.DONE)
            self.assertEqual(len(provider.contexts), 1)
            launch_instructions = provider.contexts[0].launch_instructions
            self.assertIn(memory_delta_output_relpath(node), launch_instructions)
            self.assertIn('"target": "STATUS.md"', launch_instructions)
            self.assertIn('"target": "PLAN.md"', launch_instructions)
            self.assertIn("Do not write directly into the ContextSpace", launch_instructions)

            memory_status = (
                ctx / "plugs" / "planspaces" / "memory" / "STATUS.md"
            ).read_text(encoding="utf-8")
            memory_plan = (
                ctx / "plugs" / "planspaces" / "memory" / "PLAN.md"
            ).read_text(encoding="utf-8")
            other_status = (
                ctx / "plugs" / "planspaces" / "other" / "STATUS.md"
            ).read_text(encoding="utf-8")
            self.assertIn("Implemented memory delta v1 via project artifact.", memory_status)
            self.assertIn(f"node {node.id}", memory_status)
            self.assertIn("acceptance_state: unreviewed", memory_status)
            self.assertNotIn("Do not auto-apply this plan proposal.", memory_plan)
            self.assertNotIn("Implemented memory delta v1 via project artifact.", other_status)

            copied_delta = (
                ctx
                / "plugs"
                / "planspaces"
                / "memory"
                / "inbox"
                / f"{node.id}.memory-delta.json"
            )
            self.assertTrue(copied_delta.exists())
            self.assertTrue((project_root / memory_delta_output_relpath(node)).exists())

            events = (ctx / "plugs" / "planspaces" / "memory" / "events.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertIn("memory_delta_applied", events)
            self.assertIn("proposals", events)

            result = node.settings_snapshot.get("memory_delta")
            self.assertIsInstance(result, dict)
            assert isinstance(result, dict)
            self.assertEqual(result["applied"], 1)
            self.assertEqual(result["proposed"], 1)
            self.assertEqual(result["source"], "project_artifact")
            self.assertEqual(result["planspace_id"], "planspaces.memory")

    async def test_memory_delta_auto_applies_only_status_updates(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = Store(root=tmp / "store")
            project_root = tmp / "repo"
            project_root.mkdir()
            ctx = _write_contextspace(store.root, project_root)

            project = Project(
                root_path=str(project_root),
                project_context_binding_id="project.test",
            )
            node = Node(project_id=project.id, state=NodeState.DONE)
            inbox = ctx / "plugs" / "planspaces" / "memory" / "inbox"
            (inbox / f"{node.id}.memory-delta.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "node_id": node.id,
                        "project_id": project.id,
                        "binding_id": "project.test",
                        "updates": [
                            {
                                "target": "STATUS.md",
                                "operation": "append_observation",
                                "policy": "auto",
                                "text": "Implemented the ContextSpace loader.",
                            },
                            {
                                "target": "PLAN.md",
                                "operation": "propose_patch",
                                "policy": "proposed",
                                "patch": "do not apply",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = apply_memory_delta_inbox(project, node, store_root=store.root)

            self.assertEqual(result["applied"], 1)
            status = (ctx / "plugs" / "planspaces" / "memory" / "STATUS.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Implemented the ContextSpace loader.", status)
            self.assertIn(f"node {node.id}", status)
            self.assertNotIn("do not apply", status)
            events = (ctx / "plugs" / "planspaces" / "memory" / "events.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertIn("memory_delta_applied", events)

    async def test_memory_delta_routes_to_snapshotted_planspace_after_binding_change(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = Store(root=tmp / "store")
            project_root = tmp / "repo"
            project_root.mkdir()
            ctx = _write_contextspace(store.root, project_root)

            other = ctx / "plugs" / "planspaces" / "other"
            (other / "inbox").mkdir(parents=True)
            (other / "manifest.yaml").write_text(
                "\n".join(
                    [
                        "version: 1",
                        "id: planspaces.other",
                        "kind: planspace",
                        "title: Other Direction",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (other / "STATUS.md").write_text("Other status.\n", encoding="utf-8")
            (other / "PLAN.md").write_text("Other plan.\n", encoding="utf-8")

            binding_path = ctx / "bindings" / "projects" / "project.test.yaml"

            def write_binding(active: str) -> None:
                binding_path.write_text(
                    "\n".join(
                        [
                            "version: 1",
                            "id: project.test",
                            f"active_planspace_id: {active}",
                            "project:",
                            "  name: Test Project",
                            "  local_paths:",
                            f"    - {project_root}",
                            "plugs:",
                            "  - id: planspaces.memory",
                            "    role: status-plan",
                            "    injection: turn",
                            "    enabled: true",
                            "    auto_update: true",
                            "  - id: planspaces.other",
                            "    role: status-plan",
                            "    injection: turn",
                            "    enabled: true",
                            "    auto_update: true",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )

            write_binding("planspaces.memory")
            project = Project(
                root_path=str(project_root),
                project_context_binding_id="project.test",
            )
            node = Node(project_id=project.id, state=NodeState.DONE)
            bundle = compose_context_bundle(project, node, store_root=store.root)
            node.context_bundle_id = bundle.bundle_id
            node.context_bundle_path = str(
                bundle.bundle_path.relative_to(bundle.context_root)
            )

            write_binding("planspaces.other")

            memory_inbox = ctx / "plugs" / "planspaces" / "memory" / "inbox"
            other_inbox = ctx / "plugs" / "planspaces" / "other" / "inbox"
            (memory_inbox / f"{node.id}.memory-delta.json").write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "target": "STATUS.md",
                                "operation": "append_observation",
                                "policy": "auto",
                                "text": "Apply to the launch planspace.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (other_inbox / f"{node.id}.memory-delta.json").write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "target": "STATUS.md",
                                "operation": "append_observation",
                                "policy": "auto",
                                "text": "Wrong active planspace.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = apply_memory_delta_inbox(project, node, store_root=store.root)

            self.assertEqual(result["applied"], 1)
            self.assertEqual(result["planspace_id"], "planspaces.memory")
            memory_status = (
                ctx / "plugs" / "planspaces" / "memory" / "STATUS.md"
            ).read_text(encoding="utf-8")
            other_status = (
                ctx / "plugs" / "planspaces" / "other" / "STATUS.md"
            ).read_text(encoding="utf-8")
            self.assertIn("Apply to the launch planspace.", memory_status)
            self.assertNotIn("Wrong active planspace.", other_status)

    async def test_project_memory_delta_artifact_rejects_wrong_node_id(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = Store(root=tmp / "store")
            project_root = tmp / "repo"
            project_root.mkdir()
            _write_contextspace(store.root, project_root)

            project = Project(
                root_path=str(project_root),
                project_context_binding_id="project.test",
            )
            node = Node(project_id=project.id, state=NodeState.DONE)
            bundle = compose_context_bundle(project, node, store_root=store.root)
            node.context_bundle_id = bundle.bundle_id
            node.context_bundle_path = str(
                bundle.bundle_path.relative_to(bundle.context_root)
            )

            path = project_root / memory_delta_output_relpath(node)
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "node_id": "wrong",
                        "project_id": project.id,
                        "binding_id": "project.test",
                        "planspace_id": "planspaces.memory",
                        "updates": [
                            {
                                "target": "STATUS.md",
                                "operation": "append_observation",
                                "policy": "auto",
                                "text": "Should not apply.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = apply_memory_delta_artifact(project, node, store_root=store.root)

            self.assertEqual(result["applied"], 0)
            self.assertIn("node_id", result["reason"])
            status = (
                store.root / "contextspace" / "plugs" / "planspaces" / "memory" / "STATUS.md"
            ).read_text(encoding="utf-8")
            self.assertNotIn("Should not apply.", status)


if __name__ == "__main__":
    unittest.main()
