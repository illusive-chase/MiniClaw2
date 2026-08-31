"""Contract and regression tests for cold-start agent nodes.

A cold start is an ordinary regular agent node that the framework injects
nothing into: no ContextSpace text, no category block, no preview contract, no
lane. These tests pin both halves of that claim — that the four injection
points really are empty, and that an ordinary regular node's ten-layer launch
composition is untouched by the branch that makes them empty.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from miniclaw2 import runner as runner_module
from miniclaw2.contextspace import contextspace_root, create_planspace
from miniclaw2.domain import (
    COLD_START_AGENT_OP_KIND,
    ArtifactMode,
    Category,
    Node,
    NodeKind,
    NodeState,
    Project,
)
from miniclaw2.events import TextDelta
from miniclaw2.launch_prompt import build_category_launch_block
from miniclaw2.providers import AgentProviderContext, AgentProviderEvent
from miniclaw2.registry import ProjectRegistry
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)


def _cold_node(**overrides) -> Node:
    fields = {
        "project_id": "p1",
        "kind": NodeKind.AGENT,
        "model_preset_id": "gpt-5.5",
        "state": NodeState.RUNNING,
        "planspace_id": "lane-A",
        "prompt": "do the thing",
        "agent_op_kind": COLD_START_AGENT_OP_KIND,
    }
    fields.update(overrides)
    return Node(**fields)


class ColdStartInvariantTests(unittest.TestCase):
    """Each rejected field is a channel the framework would inject through."""

    def test_category_must_be_regular(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires category=regular"):
            _cold_node(category=Category.PLANNING)

    def test_scheduled_deps_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "scheduled_deps"):
            _cold_node(scheduled_deps=["other"])

    def test_resume_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not resume"):
            _cold_node(resume_from_node_id="other")

    def test_artifact_mode_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "artifact_mode is not available"):
            _cold_node(artifact_mode=ArtifactMode.MARKDOWN)

    def test_pending_extra_principles_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "pending_extra_principles is not available"
        ):
            _cold_node(
                state=NodeState.VIRTUAL,
                prompt="",
                prompt_draft="draft",
                pending_extra_principles=["principles.evidence"],
            )

    def test_qa_mode_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "qa_mode is not available"):
            _cold_node(qa_mode=True)

    def test_skills_are_allowed(self) -> None:
        """Skills are capability supply, not injected prompt — see plan §6."""
        node = _cold_node(
            state=NodeState.VIRTUAL,
            prompt="",
            prompt_draft="draft",
            pending_extra_skills=[{"id": "skills.release"}],
        )
        self.assertEqual(node.agent_op_kind, COLD_START_AGENT_OP_KIND)

    def test_parent_node_id_is_allowed(self) -> None:
        """parent_node_id expresses graph shape only; it injects nothing."""
        node = _cold_node(parent_node_id="upstream")
        self.assertEqual(node.parent_node_id, "upstream")


class ColdStartCategoryBlockTests(unittest.TestCase):
    def test_cold_start_gets_no_category_block(self) -> None:
        block = build_category_launch_block(
            _cold_node(),
            outputs_path="/tmp/project/.miniclaw2/outputs/n1",
        )
        self.assertEqual(block, "")

    def test_a_plain_regular_node_still_gets_one(self) -> None:
        block = build_category_launch_block(
            _cold_node(agent_op_kind=None),
            outputs_path="/tmp/project/.miniclaw2/outputs/n1",
        )
        self.assertIn("regular execution node", block)


class _RecordingProvider:
    """Captures the launch context and replays a scripted turn."""

    name = "stub"

    def __init__(
        self,
        *,
        text: str = "",
        final_state: str | None = "done",
        error: str | None = None,
        side_effect: Callable[[AgentProviderContext], None] | None = None,
    ) -> None:
        self.text = text
        self.final_state = final_state
        self.error = error
        self.side_effect = side_effect
        self.contexts: list[AgentProviderContext] = []

    async def run(self, context: AgentProviderContext):
        self.contexts.append(context)
        if self.side_effect is not None:
            self.side_effect(context)
        if self.text:
            for chunk in self.text.split("|"):
                yield AgentProviderEvent(
                    kind="event",
                    event=TextDelta(text=chunk, node_id=context.node.id),
                )
        if self.error is not None:
            yield AgentProviderEvent(kind="error", error=self.error)
            return
        yield AgentProviderEvent(kind="done", final_state=self.final_state)

    async def interrupt(self) -> None:
        return None


class ColdStartRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.store_root = Path(self.tmp.name) / "store"
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        _init_repo(self.repo)
        # A CONTEXT.md on disk is the interesting case: the framework must stop
        # injecting it while the file itself stays readable by the agent.
        (self.repo / "CONTEXT.md").write_text(
            "# Project rules\n\nAlways do it this way.\n", encoding="utf-8"
        )
        self.store = Store(root=self.store_root)
        self.project = Project(root_path=str(self.repo))
        self.store.create_project(self.project)
        self.lane_id = create_planspace(
            self.project, title="cold-lane", mode="manual"
        )
        self.project.active_planspace_id = self.lane_id
        self.store.update_project(self.project)
        self.context_root = contextspace_root(self.store_root)

    async def asyncTearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def _node(self, *, cold: bool = True) -> Node:
        node = Node(
            project_id=self.project.id,
            model_preset_id="opus-4-7",
            category=Category.REGULAR,
            state=NodeState.QUEUED,
            planspace_id=self.lane_id,
            prompt="investigate the repo",
            agent_op_kind=COLD_START_AGENT_OP_KIND if cold else None,
        )
        self.store.create_node(node)
        return node

    async def _run(
        self, provider: _RecordingProvider, *, cold: bool = True
    ) -> Node:
        node = self._node(cold=cold)

        async def on_event(_payload: dict) -> None:
            return None

        runner = NodeRunner(node, self.project, self.store, on_event)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            await asyncio.wait_for(runner.run(), timeout=10.0)
        return node

    async def test_provider_context_carries_nothing_injected(self) -> None:
        provider = _RecordingProvider(text="done looking")
        node = await self._run(provider)
        self.assertEqual(node.state, NodeState.DONE)
        context = provider.contexts[0]
        self.assertEqual(context.launch_instructions, "")
        self.assertEqual(context.system_context, "")
        # Deliberate: minimal_mode bundles two codex-side effects a cold start
        # does not want (no ask_user, forced sandbox/approval). Suppressing the
        # system prompt needs only the empty system_context above.
        self.assertIs(context.minimal_mode, False)
        self.assertEqual(node.launch_instructions_snapshot, "")

    async def test_context_snapshot_is_empty_despite_context_md(self) -> None:
        node = await self._run(_RecordingProvider(text="ok"))
        self.assertEqual(node.system_context_snapshot, "")
        self.assertIsNone(node.context_bundle_id)
        self.assertIsNone(node.context_bundle_path)
        # The audit must not name a bundle, since none was composed.
        self.assertNotIn("context_bundle_id", node.settings_snapshot)
        # The cwd and skill audit are still recorded: they describe the launch
        # without being injected into it.
        self.assertEqual(node.settings_snapshot["cwd"], str(self.repo))
        self.assertIn("skill_audit", node.settings_snapshot)
        # The file is still on disk; the framework simply stopped reading it in.
        self.assertTrue((self.repo / "CONTEXT.md").exists())

    async def test_no_lane_is_materialized(self) -> None:
        """The agent must never see a lane subtree, and none may outlive the turn.

        The run workspace root itself does exist during the turn — skills mount
        under it — so the claim is specifically about ``lanes/``.
        """
        during: list[bool] = []

        def check(context: AgentProviderContext) -> None:
            during.append(
                (
                    Path(context.project.root_path)
                    / ".miniclaw2"
                    / "graph"
                    / "runs"
                    / context.node.id
                    / "lanes"
                ).exists()
            )

        node = await self._run(_RecordingProvider(text="ok", side_effect=check))
        self.assertEqual(during, [False])
        self.assertFalse(
            (self.repo / ".miniclaw2" / "graph" / "runs" / node.id).exists()
        )

    async def test_outputs_directory_is_created(self) -> None:
        """_materialize_lane() created this on the way past; the cold path must
        create it explicitly or the agent has nowhere to write artifacts."""
        seen: list[bool] = []
        node_holder: list[Node] = []

        def check(context: AgentProviderContext) -> None:
            node_holder.append(context.node)
            seen.append(
                (
                    Path(context.project.root_path)
                    / ".miniclaw2"
                    / "outputs"
                    / context.node.id
                ).is_dir()
            )

        await self._run(_RecordingProvider(text="ok", side_effect=check))
        self.assertEqual(seen, [True])

    async def test_framework_writes_the_preview(self) -> None:
        provider = _RecordingProvider(text="first part|final conclusion")
        node = await self._run(provider)
        raw = self.store.read_node_preview(self.project.id, node.id)
        self.assertIsNotNone(raw)
        assert raw is not None
        preview = json.loads(raw)
        self.assertEqual(preview["state"], "done")
        self.assertEqual(preview["category"], "regular")
        self.assertIn("final conclusion", preview["summary"])
        self.assertTrue(preview["motivation"])
        self.assertTrue(preview["next_implications"])

    async def test_summary_falls_back_when_no_text_arrives(self) -> None:
        node = await self._run(_RecordingProvider(text=""))
        self.assertEqual(node.state, NodeState.DONE)
        raw = self.store.read_node_preview(self.project.id, node.id)
        assert raw is not None
        self.assertTrue(json.loads(raw)["summary"].strip())

    async def test_summary_keeps_the_tail_when_text_is_long(self) -> None:
        tail = "THE ACTUAL CONCLUSION"
        provider = _RecordingProvider(text=("x" * 6000) + "|" + tail)
        node = await self._run(provider)
        raw = self.store.read_node_preview(self.project.id, node.id)
        assert raw is not None
        summary = json.loads(raw)["summary"]
        self.assertTrue(summary.endswith(tail))
        self.assertLessEqual(len(summary), runner_module._COLD_START_SUMMARY_LIMIT)

    async def test_provider_error_yields_a_stub_preview(self) -> None:
        node = await self._run(_RecordingProvider(error="provider exploded"))
        self.assertEqual(node.state, NodeState.ERROR)
        raw = self.store.read_node_preview(self.project.id, node.id)
        assert raw is not None
        self.assertEqual(json.loads(raw)["state"], "error")

    async def test_outputs_are_published_without_a_declaration(self) -> None:
        def write_files(context: AgentProviderContext) -> None:
            output_dir = (
                Path(context.project.root_path)
                / ".miniclaw2"
                / "outputs"
                / context.node.id
            )
            (output_dir / "report.md").write_text("# Report\n", encoding="utf-8")
            (output_dir / "data.json").write_text("{}\n", encoding="utf-8")
            (output_dir / "scratch.txt").write_text("noise\n", encoding="utf-8")
            (output_dir / "nested").mkdir()

        node = await self._run(
            _RecordingProvider(text="ok", side_effect=write_files)
        )
        published = [
            ref.name for ref in node.artifacts if ref.status == "published"
        ]
        self.assertEqual(published, ["data.json", "report.md"])
        raw = self.store.read_node_preview(self.project.id, node.id)
        assert raw is not None
        self.assertEqual(
            sorted(json.loads(raw)["artifacts"]), ["data.json", "report.md"]
        )

    async def test_empty_outputs_directory_is_not_an_error(self) -> None:
        node = await self._run(_RecordingProvider(text="ok"))
        self.assertEqual(node.state, NodeState.DONE)
        self.assertEqual(node.artifacts, [])

    async def test_regular_node_launch_composition_is_unchanged(self) -> None:
        """The value of the whole change rests on this staying true."""
        provider = _RecordingProvider(
            text="ok", side_effect=_write_own_preview
        )
        node = await self._run(provider, cold=False)
        self.assertEqual(node.state, NodeState.DONE)
        instructions = provider.contexts[0].launch_instructions
        for marker in (
            "regular execution node",
            "What you must write",
            "Publishing artifacts",
            "Long-running processes on a shared host",
            "Subagents must return within this turn",
            "Anti-self-poisoning guidance",
        ):
            self.assertIn(marker, instructions)
        self.assertEqual(node.launch_instructions_snapshot, instructions)
        self.assertTrue((self.repo / ".miniclaw2" / "outputs" / node.id).is_dir())


class ColdStartApiTests(unittest.TestCase):
    """The create/promote surface must accept a cold start and enforce §2.2."""

    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_HOME"] = self._home.name
        from miniclaw2.app import create_app

        self.client = TestClient(create_app())
        launched = self.client.post(
            "/templates/hello-text/run",
            json={"model_preset_id": "gpt-5.6"},
        )
        self.assertEqual(launched.status_code, 200, launched.text)
        self.sid = launched.json()["id"]

    def tearDown(self) -> None:
        self.client.close()
        os.environ.pop("MINICLAW_HOME", None)
        self._home.cleanup()

    def _create(self, **extra) -> "object":
        payload = {
            "prompt_draft": "Investigate with no framework context.",
            "category": "regular",
            "agent_op_kind": COLD_START_AGENT_OP_KIND,
        }
        payload.update(extra)
        return self.client.post(f"/sessions/{self.sid}/virtuals", json=payload)

    def test_create_and_promote_a_cold_start_virtual(self) -> None:
        created = self._create()
        self.assertEqual(created.status_code, 200, created.text)
        node = created.json()["node"]
        self.assertEqual(node["agent_op_kind"], COLD_START_AGENT_OP_KIND)
        self.assertEqual(node["category"], "regular")

        promoted = self.client.post(
            f"/sessions/{self.sid}/virtuals/{node['id']}/promote"
        )
        self.assertEqual(promoted.status_code, 200, promoted.text)
        self.assertEqual(
            promoted.json()["node"]["agent_op_kind"], COLD_START_AGENT_OP_KIND
        )

    def test_api_rejects_a_cold_start_with_injection_fields(self) -> None:
        for extra in (
            {"qa_mode": True},
            {"category": "planning"},
            {"artifact_mode": "markdown"},
            {"pending_extra_principles": ["principles.evidence"]},
        ):
            with self.subTest(extra=extra):
                response = self._create(**extra)
                self.assertEqual(response.status_code, 400, response.text)

    def test_create_rejects_dependencies_before_persisting(self) -> None:
        before = self.client.get(f"/sessions/{self.sid}/nodes")
        self.assertEqual(before.status_code, 200, before.text)
        before_nodes = before.json()
        dependency_id = before_nodes[0]["id"]

        created = self._create(scheduled_deps=[dependency_id])

        self.assertEqual(created.status_code, 400, created.text)
        self.assertIn("scheduled_deps", created.json()["detail"])
        after = self.client.get(f"/sessions/{self.sid}/nodes")
        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual(
            {node["id"] for node in after.json()},
            {node["id"] for node in before_nodes},
        )

    def test_editing_a_cold_start_into_an_injected_shape_is_rejected(self) -> None:
        created = self._create()
        self.assertEqual(created.status_code, 200, created.text)
        vid = created.json()["node"]["id"]
        patched = self.client.patch(
            f"/sessions/{self.sid}/virtuals/{vid}",
            json={"qa_mode": True},
        )
        self.assertEqual(patched.status_code, 400, patched.text)


class ColdStartResumeGuardTests(unittest.TestCase):
    """A direct launch resumes by carrying the source's provider session, which
    never sets ``resume_from_node_id`` — so the Node invariant cannot see it."""

    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.workspace = tempfile.TemporaryDirectory()
        self.registry = ProjectRegistry(Store(Path(self.home.name)))

    def tearDown(self) -> None:
        self.workspace.cleanup()
        self.home.cleanup()

    def test_start_node_refuses_a_resuming_cold_start(self) -> None:
        project = self.registry.create_project(self.workspace.name)
        source = self.registry.start_node(project.id, "first turn")
        self.assertIsNotNone(source)
        assert source is not None
        with self.assertRaisesRegex(ValueError, "must not resume"):
            self.registry.start_node(
                project.id,
                "cold turn",
                agent_op_kind=COLD_START_AGENT_OP_KIND,
                resume_from_node_id=source.id,
            )


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
                "ran_at": "2026-08-29T00:00:00+00:00",
                "lane": lane,
                "motivation": "the turn's motivation",
                "summary": "the turn's summary",
                "next_implications": "what follows",
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
