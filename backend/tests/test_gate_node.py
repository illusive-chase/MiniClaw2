from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from miniclaw2.domain import (
    AcceptanceState,
    Node,
    NodeKind,
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


if __name__ == "__main__":
    unittest.main()
