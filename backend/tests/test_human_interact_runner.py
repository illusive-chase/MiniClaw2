"""Tests for the human-interact review substate added in step 7."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from miniclaw2 import runner as runner_module
from miniclaw2.contextspace import create_planspace
from miniclaw2.domain import (
    Category,
    Node,
    NodeKind,
    NodeState,
    Project,
    ReviewBrief,
    ReviewSubtype,
)
from miniclaw2.providers import AgentProvider, AgentProviderContext, AgentProviderEvent
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)


class _StubProvider:
    """Bare-minimum provider: yields one done event and exits."""

    name = "stub"

    def __init__(self) -> None:
        self.last_context: AgentProviderContext | None = None

    async def run(self, context: AgentProviderContext):
        self.last_context = context
        # Yield a done event so the runner exits cleanly.
        yield AgentProviderEvent(kind="done", final_state="done")

    async def interrupt(self) -> None:
        return None


class HumanInteractRunnerTests(unittest.IsolatedAsyncioTestCase):
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
            self.project, title="review-lane", mode="manual"
        )

    async def asyncTearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def _human_review_node(self) -> Node:
        brief = ReviewBrief(
            check_what="check work",
            expected="all green",
            abnormal="anything red",
        )
        node = Node(
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            model_preset_id="opus-4-7",
            category=Category.REVIEW,
            subtype=ReviewSubtype.HUMAN_INTERACT_REVIEW,
            brief=brief,
            state=NodeState.QUEUED,
            planspace_id=self.plug_id,
            provider="claude",
            prompt="review please",
        )
        self.store.create_node(node)
        return node

    async def test_emits_human_review_prose_request_and_writes_files(self) -> None:
        node = self._human_review_node()

        emitted: list[dict] = []
        prose_id_holder: dict[str, str] = {}
        prose_seen_event = asyncio.Event()

        async def on_event(payload: dict) -> None:
            emitted.append(payload)
            if payload.get("type") == "interaction_request" and payload.get(
                "interaction_type"
            ) == "human_review_prose":
                prose_id_holder["id"] = payload["id"]
                prose_seen_event.set()

        runner = NodeRunner(node, self.project, self.store, on_event)
        stub = _StubProvider()

        with patch.object(runner_module, "_make_provider", return_value=stub):
            task = asyncio.create_task(runner.run())

            # Wait for the interaction request to fire.
            await asyncio.wait_for(prose_seen_event.wait(), timeout=2.0)

            # Verify substate transition + InteractionRequest payload.
            self.assertEqual(node.state, NodeState.AWAITING_HUMAN_INPUT)
            self.assertIn("id", prose_id_holder)
            request_payloads = [
                p for p in emitted if p.get("type") == "interaction_request"
            ]
            self.assertEqual(len(request_payloads), 1)
            payload = request_payloads[0]
            self.assertEqual(payload["interaction_type"], "human_review_prose")
            self.assertIn("brief", payload["tool_input"])
            self.assertEqual(
                payload["tool_input"]["brief"]["check_what"], "check work"
            )
            self.assertIn("human_review_path", payload["tool_input"])
            self.assertIn(
                self.plug_id, payload["tool_input"]["human_review_path"]
            )
            self.assertIn(node.id, payload["tool_input"]["human_review_path"])

            # Resolve with prose.
            prose_text = "I want the auth flow split into two parts."
            runner.resolve_gate(
                prose_id_holder["id"],
                allow=True,
                message=prose_text,
            )

            await asyncio.wait_for(task, timeout=5.0)

        # Durable + materialized human-review.md contain the prose.
        durable = self.store.node_dir(self.project.id, node.id) / "human-review.md"
        self.assertTrue(durable.exists())
        self.assertEqual(durable.read_text(encoding="utf-8"), prose_text)

        materialized = (
            Path(self.project.root_path)
            / ".miniclaw2"
            / "graph"
            / "lanes"
            / self.plug_id
            / "nodes"
            / node.id
            / "human-review.md"
        )
        self.assertTrue(materialized.exists())
        self.assertEqual(materialized.read_text(encoding="utf-8"), prose_text)

        # The provider was actually invoked after prose collection.
        self.assertIsNotNone(stub.last_context)

    async def test_empty_prose_cancels_the_node_without_calling_provider(
        self,
    ) -> None:
        node = self._human_review_node()

        prose_id_holder: dict[str, str] = {}
        prose_seen_event = asyncio.Event()

        async def on_event(payload: dict) -> None:
            if payload.get("type") == "interaction_request" and payload.get(
                "interaction_type"
            ) == "human_review_prose":
                prose_id_holder["id"] = payload["id"]
                prose_seen_event.set()

        runner = NodeRunner(node, self.project, self.store, on_event)
        stub = _StubProvider()

        with patch.object(runner_module, "_make_provider", return_value=stub):
            task = asyncio.create_task(runner.run())
            await asyncio.wait_for(prose_seen_event.wait(), timeout=2.0)
            # Submit empty prose — the runner should treat it as cancellation.
            runner.resolve_gate(
                prose_id_holder["id"],
                allow=True,
                message="   ",
            )
            await asyncio.wait_for(task, timeout=5.0)

        self.assertEqual(node.state, NodeState.CANCELLED)
        self.assertIn("no prose", node.error or "")
        # Provider was never invoked.
        self.assertIsNone(stub.last_context)
        # Durable human-review.md was NOT written.
        durable = self.store.node_dir(self.project.id, node.id) / "human-review.md"
        self.assertFalse(durable.exists())


if __name__ == "__main__":
    unittest.main()
