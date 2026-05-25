from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from miniclaw2.domain import Node, NodeKind, NodeState, Project
from miniclaw2.providers.base import AgentProviderEvent
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


class _StubProvider:
    """Minimal AgentProvider: yields a session id then ``done`` immediately."""

    name = "stub"

    def __init__(self) -> None:
        self.interrupted = False

    async def run(self, _context: Any):
        yield AgentProviderEvent(kind="session", session_id="stub-session")
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        self.interrupted = True


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


def _setup_gate_run(tmp: Path, contract: str = "# Expected\nfoo\n"):
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
            prompt="check things",
            contract=contract,
        )
    )

    events: list[dict[str, Any]] = []

    async def on_event(payload: dict[str, Any]) -> None:
        events.append(payload)

    return store, project, node, events, on_event


class GateNodeRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_gate_node_transitions_to_awaiting_review_on_provider_done(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project, node, events, on_event = _setup_gate_run(tmp)

            with patch(
                "miniclaw2.runner._make_provider", return_value=_StubProvider()
            ):
                runner = NodeRunner(node, project, store, on_event)
                task = asyncio.create_task(runner.run())

                await _wait_for(lambda: len(_interaction_requests(events)) >= 1)
                self.assertEqual(node.state, NodeState.AWAITING_REVIEW)

                req = _interaction_requests(events)[0]
                self.assertEqual(req["interaction_type"], "checkpoint_review")
                self.assertEqual(req["tool_input"]["contract"], "# Expected\nfoo\n")

                runner.resolve_gate(req["id"], allow=True, decision="no-op")
                await task

            self.assertEqual(node.state, NodeState.DONE)

    async def test_gate_resolve_write_json_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project, node, events, on_event = _setup_gate_run(tmp)

            with patch(
                "miniclaw2.runner._make_provider", return_value=_StubProvider()
            ):
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

    async def test_gate_resolve_no_op_transitions_to_done(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project, node, events, on_event = _setup_gate_run(tmp)

            with patch(
                "miniclaw2.runner._make_provider", return_value=_StubProvider()
            ):
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

    async def test_gate_resolve_rejects_path_traversal_and_keeps_gate_open(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            store, project, node, events, on_event = _setup_gate_run(tmp)

            with patch(
                "miniclaw2.runner._make_provider", return_value=_StubProvider()
            ):
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
