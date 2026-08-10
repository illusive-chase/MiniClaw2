from __future__ import annotations

import asyncio
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx2 as httpx

from miniclaw2.app import create_app
from miniclaw2.domain import Node, NodeState, Project
from miniclaw2.registry import ProjectRegistry
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


class GitEndpointTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the git verbs through the ASGI app on a live event loop.

    Regression coverage for the sync-endpoint bug: ``def`` endpoints ran in
    a threadpool where ``_launch_node`` found no running loop and silently
    left ops QUEUED forever.
    """

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        self.initial = _init_repo(self.repo)
        self.store = Store(root=root / "store")
        self.project = Project(root_path=str(self.repo))
        self.store.create_project(self.project)
        self.registry = ProjectRegistry(store=self.store)
        transport = httpx.ASGITransport(app=create_app(self.registry))
        self.client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        runtime = self.registry._runtimes.get(self.project.id)
        if runtime is not None:
            pending = list(runtime.runner_tasks.values()) + list(runtime.background_tasks)
            await asyncio.gather(*pending, return_exceptions=True)
        self.temp.cleanup()

    async def _wait_for_state(
        self, node_id: str, state: NodeState, timeout: float = 10.0
    ) -> Node:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            node = self.store.load_node(self.project.id, node_id)
            if node is not None and node.state is state:
                return node
            await asyncio.sleep(0.02)
        self.fail(f"node {node_id} did not reach {state.value}")

    async def test_commit_endpoint_runs_op_to_done_with_message(self) -> None:
        (self.repo / "change.txt").write_text("hello\n", encoding="utf-8")

        response = await self.client.post(
            f"/sessions/{self.project.id}/git/commit",
            json={"message": "message from endpoint"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        node = await self._wait_for_state(response.json()["node"]["id"], NodeState.DONE)
        self.assertIsNotNone(node.commit_after)
        self.assertNotEqual(node.commit_after, self.initial)
        subject = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(subject, "message from endpoint")

    async def test_pull_endpoint_runs_serializes_and_recovers(self) -> None:
        release = threading.Event()

        def blocking_pull(cwd: str) -> tuple[str | None, str | None]:
            release.wait(timeout=10)
            return self.initial, None

        with patch("miniclaw2.runner.git_pull_rebase", blocking_pull):
            first = await self.client.post(f"/sessions/{self.project.id}/git/pull")
            self.assertEqual(first.status_code, 200, first.text)
            node_id = first.json()["node"]["id"]
            await self._wait_for_state(node_id, NodeState.RUNNING)

            second = await self.client.post(f"/sessions/{self.project.id}/git/pull")
            self.assertEqual(second.status_code, 409)

            release.set()
            await self._wait_for_state(node_id, NodeState.DONE)

        with patch("miniclaw2.runner.git_pull_rebase", lambda cwd: (self.initial, None)):
            third = await self.client.post(f"/sessions/{self.project.id}/git/pull")
            self.assertEqual(third.status_code, 200, third.text)
            await self._wait_for_state(third.json()["node"]["id"], NodeState.DONE)

    async def test_pull_409s_when_project_not_quiescent(self) -> None:
        self.store.create_node(
            Node(
                project_id=self.project.id,
                state=NodeState.QUEUED,
                model_preset_id="gpt-5.5",
            )
        )

        response = await self.client.post(f"/sessions/{self.project.id}/git/pull")

        self.assertEqual(response.status_code, 409)
        self.assertIn("idle", response.json()["detail"])

    async def test_push_failure_surfaces_409(self) -> None:
        # The repo has no remote, so the push itself fails.
        response = await self.client.post(f"/sessions/{self.project.id}/git/push")

        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
