"""Opt-in native protocol probe. No model turn or external host is contacted."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from miniclaw2.domain import Node, Project, RemoteAccessConfig, RemoteProjectIdentity
from miniclaw2.providers.base import AgentProviderContext
from miniclaw2.providers.codex import _CodexJsonRpcClient, _thread_params, _validate_remote_thread
from miniclaw2.remote_execution import _SUPERVISOR, register_environment, stop_process
from miniclaw2.remote_graph import RemoteGraphTools


@pytest.mark.skipif(os.environ.get("MINICLAW_TEST_CODEX_REMOTE") != "1", reason="显式启用本机原生 Codex 协议探针")
def test_native_environment_thread_and_dynamic_tools(tmp_path: Path) -> None:
    asyncio.run(_probe(tmp_path.resolve()))


async def _probe(root: Path) -> None:
    executable = shutil.which("codex")
    assert executable
    authority, local, profile = root / "authority", root / "local", root / "profile"
    for directory in (authority, local, profile):
        directory.mkdir()
    env = {**os.environ, "CODEX_HOME": str(profile)}
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-u", "-c", _SUPERVISOR, "executor", str(authority), executable,
        env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr = asyncio.create_task(process.stderr.read())
    try:
        raw = await asyncio.wait_for(process.stdout.readline(), 20)
        if not raw:
            raise AssertionError((await stderr).decode())
        info = json.loads(raw)
        executor = SimpleNamespace(url=f"ws://127.0.0.1:{info['port']}", check=lambda: None)
        project = Project(root_path=str(local), persistence_mode="remote", remote=RemoteProjectIdentity(target_id="probe", root_path=str(authority)))
        node = Node(project_id=project.id, model_preset_id="gpt-5.6", planspace_id="probe")
        context = AgentProviderContext(node=node, project=project, request_gate_handler=None,
            remote_access=RemoteAccessConfig(ssh_target="unused", codex_remote_experimental=True),
            remote_environment_id="probe", graph_tools=RemoteGraphTools(project, node))
        async with _CodexJsonRpcClient(cwd=str(local), env_overrides={"CODEX_HOME": str(profile)}) as client:
            initialized = await client.initialize()
            assert initialized["codexHome"] == str(profile)
            await register_environment(client, executor, "probe")
            thread = await client.request("thread/start", _thread_params(context, {}))
            _validate_remote_thread(context, thread)
            assert thread["thread"]["environments"][0]["cwd"] == str(authority)
    finally:
        await stop_process(process)
        await asyncio.wait_for(stderr, 5)
