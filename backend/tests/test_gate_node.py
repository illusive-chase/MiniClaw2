from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from miniclaw2.contextspace import (
    compose_context_bundle,
    planspace_update_output_relpath,
    stage_planspace_update_artifact,
)
from miniclaw2.planspace_state import parse_planspace_status
from miniclaw2.domain import (
    AcceptanceState,
    Node,
    NodeKind,
    NodeOutputKind,
    NodeState,
    Project,
    VerdictSource,
)
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


class _UnexpectedProvider:
    """Sentinel: any attempt to construct a provider for a gate is a bug."""

    name = "unexpected"

    async def run(self, _context: Any):  # pragma: no cover - guarded by test
        raise AssertionError("provider must not be invoked for a passive gate")
        yield  # type: ignore[unreachable]

    async def interrupt(self) -> None:  # pragma: no cover - guarded by test
        raise AssertionError("provider.interrupt must not be invoked for a passive gate")


async def _wait_for(
    predicate, *, timeout: float = 2.0, interval: float = 0.02
) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError("predicate not satisfied in time")


def _interaction_requests(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("type") == "interaction_request"]


def _setup_gate_run(tmp: Path, brief: str = "# How to run\nfoo\n"):
    store = Store(root=tmp / "store")
    root = tmp / "project"
    root.mkdir()
    project = Project(root_path=str(root))
    store.create_project(project)

    node = store.create_node(
        Node(
            project_id=project.id,
            kind=NodeKind.GATE,
            state=NodeState.QUEUED,
            contract=brief,
        )
    )

    events: list[dict[str, Any]] = []

    async def on_event(payload: dict[str, Any]) -> None:
        events.append(payload)

    return store, project, node, events, on_event


def _write_contextspace(store_root: Path, project_root: Path) -> Path:
    ctx = store_root / "contextspace"
    (ctx / "bindings" / "projects").mkdir(parents=True)
    (ctx / "plugs" / "planspaces" / "review" / "inbox").mkdir(parents=True)
    (ctx / "plugs" / "planspaces" / "review" / "checkpoints").mkdir(parents=True)
    (ctx / "bindings" / "projects" / "project.review.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "id: project.review",
                "project:",
                "  name: Review Project",
                "  local_paths:",
                f"    - {project_root}",
                "plugs:",
                "  - id: planspaces.review",
                "    role: status-plan",
                "    injection: turn",
                "    enabled: true",
                "    auto_update: true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (ctx / "plugs" / "planspaces" / "review" / "manifest.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "id: planspaces.review",
                "kind: planspace",
                "title: Review Direction",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (ctx / "plugs" / "planspaces" / "review" / "STATUS.md").write_text(
        "\n".join(
            [
                "---",
                "goal: Review gate merge.",
                "current_state: Waiting for the first reviewed update.",
                "open_questions: []",
                "decisions: []",
                "out_of_scope: []",
                "---",
                "",
                "# Notes",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (ctx / "plugs" / "planspaces" / "review" / "PLAN.md").write_text(
        "# Plan\n\n",
        encoding="utf-8",
    )
    return ctx


class PassiveGateNodeRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_passive_gate_skips_provider_and_enters_awaiting_review(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project, node, events, on_event = _setup_gate_run(tmp)

            with patch(
                "miniclaw2.runner._make_provider", return_value=_UnexpectedProvider()
            ) as mk:
                runner = NodeRunner(node, project, store, on_event)
                task = asyncio.create_task(runner.run())

                await _wait_for(lambda: len(_interaction_requests(events)) >= 1)
                # Provider must not have been constructed at all.
                self.assertEqual(mk.call_count, 0)
                self.assertEqual(node.state, NodeState.AWAITING_REVIEW)

                req = _interaction_requests(events)[0]
                self.assertEqual(req["interaction_type"], "checkpoint_review")
                self.assertEqual(req["tool_input"]["contract"], "# How to run\nfoo\n")

                runner.resolve_gate(req["id"], allow=True, decision="no-op")
                await task

            self.assertEqual(node.state, NodeState.DONE)

    async def test_passive_gate_write_json_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project, node, events, on_event = _setup_gate_run(tmp)

            runner = NodeRunner(node, project, store, on_event)
            task = asyncio.create_task(runner.run())

            await _wait_for(lambda: len(_interaction_requests(events)) >= 1)
            req = _interaction_requests(events)[0]
            runner.resolve_gate(
                req["id"],
                allow=True,
                decision="write-json",
                response={
                    "path": "out/review.json",
                    "payload": {"approved": True, "notes": "ok"},
                },
            )
            await task

            self.assertEqual(node.state, NodeState.DONE)
            target = Path(project.root_path) / "out" / "review.json"
            self.assertTrue(target.exists())
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"approved": True, "notes": "ok"},
            )
            # Approved payload should stamp review_outcome="approved" so
            # downstream scenario `when:` predicates can branch on it.
            self.assertEqual(node.review_outcome, "approved")

    async def test_passive_gate_rejected_payload_stamps_review_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project, node, events, on_event = _setup_gate_run(tmp)

            runner = NodeRunner(node, project, store, on_event)
            task = asyncio.create_task(runner.run())

            await _wait_for(lambda: len(_interaction_requests(events)) >= 1)
            req = _interaction_requests(events)[0]
            runner.resolve_gate(
                req["id"],
                allow=True,
                decision="write-json",
                response={
                    "path": "out/review.json",
                    "payload": {"approved": False, "notes": "needs fix"},
                },
            )
            await task

            self.assertEqual(node.state, NodeState.DONE)
            self.assertEqual(node.review_outcome, "rejected")

    async def test_passive_gate_rejected_payload_marks_source_node_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = Store(root=tmp / "store")
            root = tmp / "project"
            root.mkdir()
            project = Project(root_path=str(root))
            store.create_project(project)
            source = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                )
            )
            gate = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.GATE,
                    state=NodeState.QUEUED,
                    contract="# Review\n",
                    parent_node_id=source.id,
                )
            )
            events: list[dict[str, Any]] = []

            async def on_event(payload: dict[str, Any]) -> None:
                events.append(payload)

            runner = NodeRunner(gate, project, store, on_event)
            task = asyncio.create_task(runner.run())

            await _wait_for(lambda: len(_interaction_requests(events)) >= 1)
            req = _interaction_requests(events)[0]
            runner.resolve_gate(
                req["id"],
                allow=True,
                decision="write-json",
                response={
                    "path": "out/review.json",
                    "payload": {"approved": False, "notes": "needs fix"},
                },
            )
            await task

            updated_source = store.load_node(project.id, source.id)
            assert updated_source is not None
            self.assertEqual(updated_source.acceptance_state, AcceptanceState.REJECTED)
            self.assertEqual(updated_source.verdict_source, VerdictSource.HUMAN)
            self.assertEqual(updated_source.verdict_artifact_path, "out/review.json")
            self.assertEqual(updated_source.verdict_thread_id, gate.id)
            self.assertIsNotNone(updated_source.rejected_at)

    async def test_passive_gate_no_op_transitions_to_done(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project, node, events, on_event = _setup_gate_run(tmp)

            runner = NodeRunner(node, project, store, on_event)
            task = asyncio.create_task(runner.run())

            await _wait_for(lambda: len(_interaction_requests(events)) >= 1)
            req = _interaction_requests(events)[0]
            runner.resolve_gate(req["id"], allow=True, decision="no-op")
            await task

            self.assertEqual(node.state, NodeState.DONE)
            # No file should have been written.
            self.assertEqual(
                list((Path(project.root_path)).iterdir()),
                [],
            )

    async def test_passive_gate_rejects_path_traversal_and_keeps_gate_open(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project, node, events, on_event = _setup_gate_run(tmp)

            runner = NodeRunner(node, project, store, on_event)
            task = asyncio.create_task(runner.run())

            await _wait_for(lambda: len(_interaction_requests(events)) >= 1)
            first = _interaction_requests(events)[0]
            runner.resolve_gate(
                first["id"],
                allow=True,
                decision="write-json",
                response={
                    "path": "../escape.json",
                    "payload": {"x": 1},
                },
            )

            # Wait for the re-emitted InteractionRequest (gate stays open).
            await _wait_for(lambda: len(_interaction_requests(events)) >= 2)

            # An ErrorEvent should have been emitted explaining the failure.
            errs = [e for e in events if e.get("type") == "error"]
            self.assertTrue(any("escapes project root" in (e.get("message") or "") for e in errs))

            # Node should still be in AWAITING_REVIEW.
            self.assertEqual(node.state, NodeState.AWAITING_REVIEW)

            # File was not written.
            self.assertFalse((tmp / "escape.json").exists())

            # Resolve no-op to let the task finish.
            second = _interaction_requests(events)[1]
            runner.resolve_gate(second["id"], allow=True, decision="no-op")
            await task

            self.assertEqual(node.state, NodeState.DONE)

    async def test_passive_gate_merges_staged_planspace_update_on_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = Store(root=tmp / "store")
            root = tmp / "project"
            root.mkdir()
            ctx = _write_contextspace(store.root, root)
            project = Project(
                root_path=str(root),
                project_context_binding_id="project.review",
            )
            store.create_project(project)

            source = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                    output_kind=NodeOutputKind.REVIEW_BRIEF,
                )
            )
            bundle = compose_context_bundle(project, source, store_root=store.root)
            source.context_bundle_id = bundle.bundle_id
            source.context_bundle_path = str(
                bundle.bundle_path.relative_to(bundle.context_root)
            )
            store.update_node(source)
            update_path = root / planspace_update_output_relpath(source)
            update_path.parent.mkdir(parents=True)
            update_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "node_id": source.id,
                        "project_id": project.id,
                        "binding_id": "project.review",
                        "planspace_id": "planspaces.review",
                        "updates": [
                            {
                                "target": "STATUS.md",
                                "operation": "append_body",
                                "policy": "auto",
                                "text": "Interim build claims the calculator is ready.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            staged = stage_planspace_update_artifact(project, source, store_root=store.root)
            self.assertTrue(staged["staged"])
            staged_path = (
                ctx
                / "plugs"
                / "planspaces"
                / "review"
                / "checkpoints"
                / f"{source.id}.interim-planspace-update.json"
            )
            self.assertTrue(staged_path.exists())
            status_before = (
                ctx / "plugs" / "planspaces" / "review" / "STATUS.md"
            ).read_text(encoding="utf-8")
            self.assertNotIn("Interim build claims", status_before)

            gate = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.GATE,
                    state=NodeState.QUEUED,
                    contract="# What to verify\n\nRun the calculator.",
                    parent_node_id=source.id,
                )
            )
            events: list[dict[str, Any]] = []

            async def on_event(payload: dict[str, Any]) -> None:
                events.append(payload)

            runner = NodeRunner(gate, project, store, on_event)
            task = asyncio.create_task(runner.run())
            await _wait_for(lambda: len(_interaction_requests(events)) >= 1)
            req = _interaction_requests(events)[0]
            self.assertEqual(req["tool_input"]["response_mode"], "freeform")
            self.assertEqual(req["tool_input"]["review_guidance"], gate.contract)

            runner.resolve_gate(
                req["id"],
                allow=True,
                decision="write-json",
                response={
                    "path": "out/review.json",
                    "payload": {"approved": False, "notes": "Display is clipped."},
                },
            )
            await task

            status_after = (
                ctx / "plugs" / "planspaces" / "review" / "STATUS.md"
            ).read_text(encoding="utf-8")
            self.assertIn("Interim build claims the calculator is ready.", status_after)
            self.assertIn("## Review (user)", status_after)
            self.assertIn("Display is clipped.", status_after)
            self.assertIn("controlling decision", status_after)
            self.assertFalse(staged_path.exists())
            self.assertIn("staged_discarded", gate.settings_snapshot["planspace_update"])

    async def test_review_merge_preserves_structured_staged_updates(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = Store(root=tmp / "store")
            root = tmp / "project"
            root.mkdir()
            ctx = _write_contextspace(store.root, root)
            project = Project(
                root_path=str(root),
                project_context_binding_id="project.review",
            )
            store.create_project(project)

            source = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.AGENT,
                    state=NodeState.DONE,
                    output_kind=NodeOutputKind.REVIEW_BRIEF,
                )
            )
            bundle = compose_context_bundle(project, source, store_root=store.root)
            source.context_bundle_id = bundle.bundle_id
            source.context_bundle_path = str(
                bundle.bundle_path.relative_to(bundle.context_root)
            )
            store.update_node(source)
            update_path = root / planspace_update_output_relpath(source)
            update_path.parent.mkdir(parents=True)
            update_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "node_id": source.id,
                        "project_id": project.id,
                        "binding_id": "project.review",
                        "planspace_id": "planspaces.review",
                        "updates": [
                            {
                                "target": "STATUS.md",
                                "operation": "add_open_question",
                                "policy": "auto",
                                "summary": "Does the gate run keep the structured slots?",
                            },
                            {
                                "target": "STATUS.md",
                                "operation": "add_decision",
                                "policy": "auto",
                                "summary": "Carry staged slots through review.",
                            },
                            {
                                "target": "STATUS.md",
                                "operation": "add_out_of_scope",
                                "policy": "auto",
                                "text": "Skip rewriting current_state for this checkpoint.",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            staged = stage_planspace_update_artifact(project, source, store_root=store.root)
            self.assertTrue(staged["staged"])

            gate = store.create_node(
                Node(
                    project_id=project.id,
                    kind=NodeKind.GATE,
                    state=NodeState.QUEUED,
                    contract="# Verify\n\nReview the staged slots.",
                    parent_node_id=source.id,
                )
            )
            events: list[dict[str, Any]] = []

            async def on_event(payload: dict[str, Any]) -> None:
                events.append(payload)

            runner = NodeRunner(gate, project, store, on_event)
            task = asyncio.create_task(runner.run())
            await _wait_for(lambda: len(_interaction_requests(events)) >= 1)
            req = _interaction_requests(events)[0]

            runner.resolve_gate(
                req["id"],
                allow=True,
                decision="write-json",
                response={
                    "path": "out/review.json",
                    "payload": {"approved": True, "notes": "Slots look correct."},
                },
            )
            await task

            status_after = (
                ctx / "plugs" / "planspaces" / "review" / "STATUS.md"
            ).read_text(encoding="utf-8")
            parsed = parse_planspace_status(status_after)
            self.assertEqual(
                [item["summary"] for item in parsed.open_questions],
                ["Does the gate run keep the structured slots?"],
            )
            self.assertEqual(
                [item["summary"] for item in parsed.decisions],
                ["Carry staged slots through review."],
            )
            self.assertIn(
                "Skip rewriting current_state for this checkpoint.",
                parsed.out_of_scope,
            )
            # The review note still appended as well.
            self.assertIn("## Review (user)", status_after)


if __name__ == "__main__":
    unittest.main()
