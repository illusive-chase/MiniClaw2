"""The runner's gate observer hook and answer-source bookkeeping.

The Feishu bridge relies on three runner facts: every inline gate is
announced to the observer when it opens and when it closes (answered, node
ended, cancelled), an observer that raises can never break the gate, and
when two answer channels race only one of them is told it won.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from miniclaw2 import runner as runner_module
from miniclaw2.contextspace import create_planspace
from miniclaw2.domain import Category, GateSubtype, Node, NodeKind, NodeState, Project
from miniclaw2.gate_observer import GateNotice, GateOutcome
from miniclaw2.providers import AgentProviderContext, AgentProviderEvent, GateRequest
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store

QUESTIONS = [{"id": "q", "question": "Which?", "options": [{"label": "A"}, {"label": "B"}]}]


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)


class _AskProvider:
    name = "stub"

    def __init__(self) -> None:
        self.response: dict[str, Any] | None = None
        self.hold = asyncio.Event()

    async def run(self, context: AgentProviderContext):
        yield AgentProviderEvent(kind="session", session_id="stub-session")
        # Ask only on the first turn; preview-repair turns just finish.
        if self.response is None:
            self.response = await context.request_gate(
                GateRequest(
                    subtype=GateSubtype.ASK_USER,
                    tool_name="AskUserQuestion",
                    tool_input={"questions": QUESTIONS},
                )
            )
            await self.hold.wait()
        yield AgentProviderEvent(kind="done")

    async def interrupt(self) -> None:
        self.hold.set()


class _RecordingObserver:
    def __init__(self, *, fail: bool = False) -> None:
        self.opened_notices: list[GateNotice] = []
        self.closed_outcomes: list[tuple[str, GateOutcome]] = []
        self.fail = fail

    def opened(self, notice: GateNotice) -> None:
        self.opened_notices.append(notice)
        if self.fail:
            raise RuntimeError("observer exploded on open")

    def closed(self, gate_id: str, outcome: GateOutcome) -> None:
        self.closed_outcomes.append((gate_id, outcome))
        if self.fail:
            raise RuntimeError("observer exploded on close")


class GateObserverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MINICLAW_CONTEXT_HOME"] = str(Path(self.tmp.name) / "ctx")
        self.store_root = Path(self.tmp.name) / "store"
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir(parents=True)
        _init_repo(self.repo)
        self.store = Store(root=self.store_root)
        self.project = Project(root_path=str(self.repo), name="demo")
        self.store.create_project(self.project)
        self.lane = create_planspace(self.project, title="gate-lane", mode="manual")
        self.store.update_project(self.project)

    async def asyncTearDown(self) -> None:
        os.environ.pop("MINICLAW_CONTEXT_HOME", None)
        self.tmp.cleanup()

    def _runner(self, observer: Any, provider: _AskProvider) -> tuple[NodeRunner, list[dict]]:
        node = Node(
            project_id=self.project.id,
            kind=NodeKind.AGENT,
            model_preset_id="opus-4-7",
            category=Category.REGULAR,
            state=NodeState.QUEUED,
            prompt="ship   the\nfeature",
            planspace_id=self.lane,
        )
        self.store.create_node(node)
        emitted: list[dict] = []

        async def on_event(payload: dict) -> None:
            emitted.append(payload)

        runner = NodeRunner(node, self.project, self.store, on_event, gate_observer=observer)
        return runner, emitted

    async def _wait_for_gate(self, observer: _RecordingObserver) -> GateNotice:
        for _ in range(500):
            if observer.opened_notices:
                return observer.opened_notices[-1]
            await asyncio.sleep(0.01)
        raise AssertionError("gate never opened")

    def _resolved_records(self, runner: NodeRunner) -> list[dict]:
        path = self.store._gates_file(self.project.id, runner.node.id)
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if json.loads(line)["action"] == "resolved"
        ]

    async def test_feishu_answer_is_recorded_with_its_source(self) -> None:
        observer, provider = _RecordingObserver(), _AskProvider()
        runner, emitted = self._runner(observer, provider)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            task = asyncio.create_task(runner.run())
            notice = await self._wait_for_gate(observer)
            self.assertEqual(notice.subtype, "ask_user")
            self.assertEqual(notice.questions, QUESTIONS)
            self.assertEqual(notice.project_name, "demo")
            self.assertEqual(notice.lane_title, "gate-lane")
            self.assertEqual(notice.node_label, "ship the feature")
            answers = {"answers": {"q": {"answers": ["B"]}}}
            self.assertTrue(
                runner.resolve_gate(
                    notice.gate_id, allow=True, response=answers, source="feishu", via="MBP-2"
                )
            )
            provider.hold.set()
            await asyncio.wait_for(task, timeout=5)

        self.assertEqual(provider.response["response"], answers)  # type: ignore[index]
        gate_id, outcome = observer.closed_outcomes[0]
        self.assertEqual(gate_id, notice.gate_id)
        self.assertEqual(outcome, GateOutcome(kind="answered", source="feishu", via="MBP-2", response=answers))
        record = self._resolved_records(runner)[0]["gate"]["response"]
        self.assertEqual((record["source"], record["via"]), ("feishu", "MBP-2"))
        activity = [ev for ev in emitted if ev.get("type") == "activity" and ev.get("name") == "feishu"]
        self.assertEqual(activity[0]["summary"], "通过飞书回答（经 MBP-2 中转）")

    async def test_wecom_answer_is_recorded_with_its_source(self) -> None:
        observer, provider = _RecordingObserver(), _AskProvider()
        runner, emitted = self._runner(observer, provider)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            task = asyncio.create_task(runner.run())
            notice = await self._wait_for_gate(observer)
            answers = {"answers": {"q": {"answers": ["A"]}}}
            self.assertTrue(
                runner.resolve_gate(notice.gate_id, allow=True, response=answers, source="wecom")
            )
            provider.hold.set()
            await asyncio.wait_for(task, timeout=5)

        self.assertEqual(observer.closed_outcomes[0][1].source, "wecom")
        record = self._resolved_records(runner)[0]["gate"]["response"]
        self.assertEqual(record["source"], "wecom")
        activity = [ev for ev in emitted if ev.get("type") == "activity" and ev.get("name") == "wecom"]
        self.assertEqual(activity[0]["summary"], "通过企业微信回答")

    async def test_racing_channels_only_one_wins(self) -> None:
        observer, provider = _RecordingObserver(), _AskProvider()
        runner, _ = self._runner(observer, provider)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            task = asyncio.create_task(runner.run())
            notice = await self._wait_for_gate(observer)
            first = runner.resolve_gate(notice.gate_id, allow=True, response={"answers": {}}, source="web")
            second = runner.resolve_gate(notice.gate_id, allow=True, response={"answers": {}}, source="feishu")
            provider.hold.set()
            await asyncio.wait_for(task, timeout=5)

        self.assertEqual((first, second), (True, False))
        self.assertEqual(observer.closed_outcomes[0][1].source, "web")

    async def test_node_end_reports_ended_and_failing_observer_is_harmless(self) -> None:
        observer, provider = _RecordingObserver(fail=True), _AskProvider()
        runner, _ = self._runner(observer, provider)
        with patch.object(runner_module, "_make_provider", return_value=provider):
            task = asyncio.create_task(runner.run())
            await self._wait_for_gate(observer)
            await runner.interrupt()
            runner._resolve_open_gates()
            await asyncio.wait_for(task, timeout=5)

        self.assertEqual(observer.closed_outcomes[0][1].kind, "ended")
        self.assertEqual(provider.response, {"allow": False, "message": "node ended"})


if __name__ == "__main__":
    unittest.main()
