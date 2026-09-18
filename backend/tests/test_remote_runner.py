from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from miniclaw2.contextspace import create_planspace
from miniclaw2.domain import (
    Category,
    Node,
    NodeKind,
    NodeState,
    Project,
    RemoteAccessConfig,
    RemoteProjectBinding,
    RemoteProjectIdentity,
    ReviewBrief,
    ReviewSubtype,
)
from miniclaw2.providers.base import AgentProviderEvent
from miniclaw2.registry import ProjectRegistry
from miniclaw2.remote_execution import execution_binding, local_codex_home
from miniclaw2.runner import NodeRunner
from miniclaw2.store import Store


def setup_remote(root: Path):
    store = Store(root / "store")
    project = Project(root_path="/unused", persistence_mode="remote", remote=RemoteProjectIdentity(
        target_id="test", root_path="/srv/project", root_commit="a" * 40,
    ))
    project.root_path = str(store.root / "workspaces/remote" / project.id)
    binding = RemoteProjectBinding(projection_path=project.root_path, remote=RemoteAccessConfig(
        ssh_target="test", codex_remote_experimental=True,
    ))
    store.create_remote_project(project, binding, root_commits=("a" * 40,), initialized_at=1)
    lane = create_planspace(project, title="测试", store_root=store.root)
    store.update_project(project)
    return store, project, lane


class PublishingProvider:
    name = "test"

    def __init__(self):
        self.calls = 0
        self.closed = 0
        self.instructions = []

    async def run(self, context):
        self.calls += 1
        self.instructions.append(context.launch_instructions)
        try:
            assert context.remote_access.codex_remote_experimental
            if self.calls == 2:
                assert context.node.provider_session_id == "thread"
                assert context.graph_tools.call("publish_artifact", {"name": "result.md", "content": "交付结果"})["success"]
                preview = {"id": context.node.id, "kind": "agent", "category": "regular", "state": "done",
                           "ran_at": "2026-09-18T00:00:00Z", "lane": context.node.planspace_id,
                           "motivation": "执行", "summary": "完成闭环", "next_implications": "可审阅", "artifacts": ["result.md"]}
                assert context.graph_tools.call("publish_preview", {"preview": preview})["success"]
            yield AgentProviderEvent(kind="settings", settings={"observed_codex_home": local_codex_home(), "observed_model_provider": "test"})
            yield AgentProviderEvent(kind="session", session_id="thread")
            yield AgentProviderEvent(kind="done")
        finally:
            self.closed += 1


def test_remote_dynamic_preview_repair_reaps_and_publishes(tmp_path: Path) -> None:
    store, project, lane = setup_remote(tmp_path)
    node = store.create_node(Node(project_id=project.id, model_preset_id="gpt-5.6", planspace_id=lane,
                                  state="queued", artifact_mode="markdown", prompt="交付结果"))
    provider = PublishingProvider()
    with patch("miniclaw2.runner._make_provider", return_value=provider), patch("miniclaw2.runner.git_head", return_value="a" * 40):
        asyncio.run(NodeRunner(node, project, store, AsyncMock()).run())
    assert node.state is NodeState.DONE, node.error
    assert provider.calls == provider.closed == 2
    assert all("publish_preview" in instructions for instructions in provider.instructions)
    assert node.settings_snapshot["execution_role"] == "remote_codex"
    assert node.settings_snapshot["observed_codex_home"] == local_codex_home()
    assert (store.node_dir(project.id, node.id) / "artifacts/result.md").read_text() == "交付结果"
    assert node.artifacts[0].status == "published"


def test_claude_has_local_read_only_role(tmp_path: Path) -> None:
    store, project, lane = setup_remote(tmp_path)
    node = store.create_node(Node(project_id=project.id, model_preset_id="opus-4-8", planspace_id=lane))
    runner = NodeRunner(node, project, store, AsyncMock())
    bundle = runner._snapshot_context_bundle()
    runner._snapshot_launch_settings(bundle)
    instructions = runner._build_agent_launch_instructions(bundle)
    assert "本机只读设计分析" in instructions
    assert "不能" in instructions or "不修改源码" in instructions
    assert node.settings_snapshot["execution_role"] == "local_read_only"


def test_remote_verifier_using_miniclaw_home_runs_in_local_projection(
    tmp_path: Path,
) -> None:
    store, project, lane = setup_remote(tmp_path)
    projection = Path(project.root_path)
    projection.mkdir(parents=True, exist_ok=True)
    marker = projection / "verified"
    script = tmp_path / "verify-framework-state.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'test -f "$MINICLAW_HOME/projects/$MINICLAW_PROJECT_ID/project.json"\n'
        'printf "%s" "$MINICLAW_HOME" > verified\n',
        encoding="utf-8",
    )
    node = store.create_node(
        Node(
            project_id=project.id,
            kind=NodeKind.VERIFIER,
            category=Category.REVIEW,
            subtype=ReviewSubtype.PROGRAMMATIC_REVIEW,
            brief=ReviewBrief(check_what="框架状态", expected="可读取", abnormal="缺失"),
            verify_script_ref=str(script),
            planspace_id=lane,
            state=NodeState.QUEUED,
        )
    )

    with (
        patch("miniclaw2.runner.git_head", return_value="a" * 40),
        patch("miniclaw2.runner.start_verifier", new_callable=AsyncMock) as remote,
    ):
        asyncio.run(NodeRunner(node, project, store, AsyncMock()).run())

    assert node.state is NodeState.DONE, node.error
    remote.assert_not_awaited()
    assert marker.read_text(encoding="utf-8") == str(store.root)


def test_remote_verifier_without_framework_state_stays_remote(tmp_path: Path) -> None:
    store, project, lane = setup_remote(tmp_path)
    script = tmp_path / "verify-source.sh"
    script.write_text("#!/usr/bin/env bash\nprintf ok\n", encoding="utf-8")
    node = store.create_node(
        Node(
            project_id=project.id,
            kind=NodeKind.VERIFIER,
            category=Category.REVIEW,
            subtype=ReviewSubtype.PROGRAMMATIC_REVIEW,
            brief=ReviewBrief(check_what="源码", expected="通过", abnormal="失败"),
            verify_script_ref=str(script),
            planspace_id=lane,
            state=NodeState.QUEUED,
        )
    )
    process = SimpleNamespace(
        stdin=None,
        stdout=SimpleNamespace(read=AsyncMock(return_value=b"ok")),
        stderr=SimpleNamespace(read=AsyncMock(return_value=b"")),
        returncode=0,
        wait=AsyncMock(return_value=0),
    )

    with (
        patch("miniclaw2.runner.git_head", return_value="a" * 40),
        patch(
            "miniclaw2.runner.start_verifier",
            new=AsyncMock(return_value=process),
        ) as remote,
    ):
        asyncio.run(NodeRunner(node, project, store, AsyncMock()).run())

    assert node.state is NodeState.DONE, node.error
    remote.assert_awaited_once()
    assert remote.await_args.args[1:4] == (
        project.remote.root_path,
        project.id,
        script.read_text(encoding="utf-8"),
    )


@pytest.mark.parametrize("change", ["target", "cwd", "profile", "host", "backend", "access"])
def test_remote_resume_rejects_changed_identity(tmp_path: Path, change: str) -> None:
    store, project, lane = setup_remote(tmp_path)
    registry = ProjectRegistry(store)
    binding = store.read_remote_binding(project.id)
    source = Node(project_id=project.id, model_preset_id="gpt-5.6", state="done", provider_session_id="thread", planspace_id=lane,
                  origin_machine_id=store.machine.id, settings_snapshot={
                      "execution_binding": execution_binding(project.remote, binding.remote),
                      "execution_role": "remote_codex",
                      "observed_codex_home": local_codex_home(), "observed_model_provider": "test",
                  })
    assert registry._remote_resume_settings(project, source)["resume_codex_home"] == local_codex_home()
    if change == "target":
        source.settings_snapshot["execution_binding"]["target_id"] = "other"
    elif change == "cwd":
        source.settings_snapshot["execution_binding"]["root_path"] = "/other"
    elif change == "profile":
        source.settings_snapshot["observed_codex_home"] = "/other/profile"
    elif change == "backend":
        source.settings_snapshot.pop("observed_model_provider")
    elif change == "access":
        source.settings_snapshot["execution_binding"]["access_sha256"] = "changed"
    else:
        source.origin_machine_id = "another-device"
    with pytest.raises(ValueError):
        registry._remote_resume_settings(project, source)
    registry.close_remote_transports()


def test_remote_execution_migration_defaults_and_locality(tmp_path: Path) -> None:
    from miniclaw2.migrations.catalog import marker
    from miniclaw2.migrations.transaction import atomic_json
    store, project, lane = setup_remote(tmp_path)
    local_path = store.root / f"projects/{project.id}/hosts/{store.machine.id}/local.json"
    local = json.loads(local_path.read_text())
    for key in ("codex_remote_experimental", "codex_path", "sandbox"):
        local["remote"].pop(key)
    atomic_json(local_path, local)
    shared = store.root / f"projects/{project.id}/project.json"
    original = shared.read_bytes()
    atomic_json(store.root / "schema.json", marker(18))
    receipt = store.root / ".migration-local/state.json"
    atomic_json(receipt, {**json.loads(receipt.read_text()), **marker(18)})
    store.coordinator.apply(store.machine.id)
    assert shared.read_bytes() == original
    updated = json.loads(local_path.read_text())["remote"]
    assert updated["codex_remote_experimental"] is False
    assert updated["sandbox"] == "workspaceWrite"
