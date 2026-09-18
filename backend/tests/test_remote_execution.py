from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from miniclaw2.domain import Node, Project, RemoteAccessConfig, RemoteProjectIdentity
from miniclaw2.providers.base import AgentProviderContext
from miniclaw2.providers.codex import CodexProvider, _thread_params, _turn_params
from miniclaw2.remote_execution import _EXECUTABLE_RESOLVER, _SUPERVISOR, RemoteExecutor, register_environment, ssh_command, start_verifier, stop_process
from miniclaw2.remote_graph import RemoteGraphTools
from miniclaw2.remote_transport import RemoteTransportError, SSHProjectTransport


def context(tmp_path: Path) -> AgentProviderContext:
    project = Project(root_path=str(tmp_path), persistence_mode="remote", remote=RemoteProjectIdentity(
        target_id="test", root_path="/srv/test project", root_commit="a" * 40,
    ))
    node = Node(project_id=project.id, model_preset_id="gpt-5.6", planspace_id="test")
    return AgentProviderContext(
        node=node, project=project, request_gate_handler=AsyncMock(),
        remote_access=RemoteAccessConfig(ssh_target="test", codex_remote_experimental=True),
        graph_tools=RemoteGraphTools(project, node),
    )


def test_environment_parameters_are_remote_only(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    with pytest.raises(RuntimeError, match="探针"):
        _thread_params(ctx, {})
    ctx.remote_environment_id = "remote"
    thread = _thread_params(ctx, {})
    turn = _turn_params(ctx, "thread", "work")
    assert thread["cwd"] == "/srv/test project"
    assert thread["environments"] == turn["environments"]
    assert turn["sandboxPolicy"]["writableRoots"] == ["/srv/test project"]
    assert {d["name"] for d in thread["dynamicTools"]} == {
        "ask_user", "lane_list", "lane_read", "publish_preview", "publish_artifact",
    }
    ctx.remote_access.sandbox = "externalSandbox"
    assert _turn_params(ctx, "thread", "work")["sandboxPolicy"] == {
        "type": "externalSandbox", "networkAccess": "restricted",
    }


def test_ready_probe_requires_shell_and_never_recovers() -> None:
    asyncio.run(_ready_probe())


async def _ready_probe() -> None:
    client = SimpleNamespace(request=AsyncMock(side_effect=[{}, {"status": "ready"}, {"shell": {"path": "/bin/bash"}}]))
    executor = Mock(url="ws://127.0.0.1:59591")
    await register_environment(client, executor, "remote")
    assert [call.args[0] for call in client.request.call_args_list] == ["environment/add", "environment/status", "environment/info"]
    client.request = AsyncMock(side_effect=[{}, {"status": "disconnected", "error": "lost"}])
    with pytest.raises(RemoteTransportError, match="lost"):
        await register_environment(client, executor, "remote")
    assert client.request.call_count == 2


class FakeClient:
    def __init__(self, ctx: AgentProviderContext, *, bad_environment: bool = False, disconnected: bool = False) -> None:
        self.ctx = ctx
        self.calls: list[tuple[str, dict]] = []
        self.bad_environment = bad_environment
        self.disconnected = disconnected

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def initialize(self):
        return {"userAgent": "codex/0.154.0", "codexHome": "/profiles/test"}

    async def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == "environment/status":
            return {"status": "ready"}
        if method == "environment/info":
            return {"shell": {"path": "/bin/bash"}}
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": "thread", "environments": [] if self.bad_environment else params["environments"]}, "modelProvider": "test"}
        if method == "turn/start":
            return {"turn": {"id": "turn"}}
        return {}

    async def receive(self):
        if self.disconnected:
            return {"method": "thread/environment/disconnected", "params": {}}
        return {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}


class FakeExecutor:
    version = "codex-cli 0.154.0"
    url = "ws://127.0.0.1:59591"

    def __init__(self):
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    def check(self):
        pass


@pytest.mark.parametrize("bad_environment,disconnected,terminal", [(False, False, "done"), (True, False, "error"), (False, True, "error")])
def test_provider_remote_lifecycle(tmp_path: Path, bad_environment: bool, disconnected: bool, terminal: str) -> None:
    asyncio.run(_provider_remote_lifecycle(tmp_path, bad_environment, disconnected, terminal))


async def _provider_remote_lifecycle(tmp_path: Path, bad_environment: bool, disconnected: bool, terminal: str) -> None:
    ctx = context(tmp_path)
    client = FakeClient(ctx, bad_environment=bad_environment, disconnected=disconnected)
    executor = FakeExecutor()
    with patch("miniclaw2.providers.codex._CodexJsonRpcClient", return_value=client), patch("miniclaw2.providers.codex.RemoteExecutor", return_value=executor):
        events = [ev async for ev in CodexProvider().run(ctx)]
    assert events[-1].kind == terminal
    assert executor.closed
    turns = [params for method, params in client.calls if method == "turn/start"]
    assert len(turns) == (0 if bad_environment else 1)
    assert all(turn["environments"] for turn in turns)


def test_profile_change_fails_before_executor(tmp_path: Path) -> None:
    asyncio.run(_profile_change(tmp_path))


async def _profile_change(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    ctx.node.settings_snapshot["resume_codex_home"] = "/profiles/other"
    with patch("miniclaw2.providers.codex._CodexJsonRpcClient", return_value=FakeClient(ctx)), patch("miniclaw2.providers.codex.RemoteExecutor") as executor:
        events = [ev async for ev in CodexProvider().run(ctx)]
    assert events[-1].kind == "error"
    executor.assert_not_called()


def test_experimental_flag_closed_by_default() -> None:
    asyncio.run(_experimental_flag())


async def _experimental_flag() -> None:
    with patch("asyncio.create_subprocess_exec") as spawn:
        with pytest.raises(RemoteTransportError, match="尚未启用"):
            async with RemoteExecutor(RemoteAccessConfig(ssh_target="test"), "/srv/test"):
                pass
    spawn.assert_not_called()


def test_executor_resolves_bare_codex_from_login_shell_path(tmp_path: Path) -> None:
    login_bin = tmp_path / "login-bin"
    login_bin.mkdir()
    shell = tmp_path / "login-shell"
    shell.write_text(
        "#!/bin/sh\n"
        "printf 'PATH=%s\\n' \"$MINICLAW_TEST_LOGIN_PATH\"\n"
    )
    shell.chmod(0o755)
    codex = login_bin / "codex"
    codex.write_text("#!/bin/sh\n")
    codex.chmod(0o755)
    result = subprocess.run(
        [sys.executable, "-c", (
            "import os, pwd, shutil, subprocess\n"
            + _EXECUTABLE_RESOLVER
            + "\nprint(resolve_executable('codex') or '')\n"
        )],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PATH": "/usr/bin:/bin",
            "SHELL": str(shell),
            "MINICLAW_TEST_LOGIN_PATH": f"{login_bin}:/usr/bin:/bin",
        },
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(codex)


def test_executor_missing_codex_reports_configuration_hint(tmp_path: Path) -> None:
    shell = tmp_path / "empty-login-shell"
    shell.write_text("#!/bin/sh\nprintf 'PATH=/usr/bin:/bin\\n'\n")
    shell.chmod(0o755)
    result = subprocess.run(
        [
            sys.executable, "-u", "-c", _SUPERVISOR,
            "executor", str(tmp_path), "missing-miniclaw-codex",
        ],
        input="",
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": "/usr/bin:/bin", "SHELL": str(shell)},
        timeout=5,
    )
    assert result.returncode == 127
    assert "填写绝对 codex_path" in result.stderr
    assert "Traceback" not in result.stderr


def _fake_codex(tmp_path: Path, *, sandbox_exit: int, stderr: str = "") -> Path:
    """A stand-in codex whose `sandbox` subcommand succeeds or fails on demand."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    codex = tmp_path / "codex"
    codex.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "codex-cli 0.154.0"; exit 0; fi\n'
        'if [ "$1" = "sandbox" ]; then\n'
        f'  printf %s "{stderr}" >&2\n'
        f"  exit {sandbox_exit}\n"
        "fi\n"
        # exec-server: hold the port open so the readiness probe succeeds.
        'if [ "$1" = "exec-server" ]; then\n'
        '  for a in "$@"; do last="$a"; done\n'
        '  port=$(echo "$last" | sed "s#.*:##")\n'
        f'  exec "{sys.executable}" -c "import socket,sys,time\n'
        "s=socket.socket(); s.bind((\'127.0.0.1\', int(sys.argv[1]))); s.listen(8); time.sleep(30)\" \"$port\"\n"
        "fi\n"
        "exit 0\n"
    )
    codex.chmod(0o755)
    return codex


def _supervisor_handshake(tmp_path: Path, codex: Path) -> dict:
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", _SUPERVISOR, "executor", str(tmp_path), str(codex)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        line = process.stdout.readline()
        assert line, process.stderr.read()
        return json.loads(line)
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def test_supervisor_reports_unusable_remote_sandbox(tmp_path: Path) -> None:
    """The handshake carries the sandbox verdict, not just the version."""
    bwrap_error = "bwrap: No permissions to create a new namespace"
    broken = _supervisor_handshake(tmp_path, _fake_codex(tmp_path / "broken", sandbox_exit=1, stderr=bwrap_error))
    assert broken["sandbox_ok"] is False
    assert bwrap_error in broken["sandbox_error"]
    healthy = _supervisor_handshake(tmp_path, _fake_codex(tmp_path / "healthy", sandbox_exit=0))
    assert healthy["sandbox_ok"] is True
    assert healthy["sandbox_error"] == ""


@pytest.mark.parametrize(
    "sandbox,sandbox_ok,expect_error",
    [
        ("workspaceWrite", False, True),
        # externalSandbox delegates isolation to the remote container, so a
        # missing bubblewrap sandbox is expected rather than fatal.
        ("externalSandbox", False, False),
        ("workspaceWrite", True, False),
    ],
)
def test_workspace_write_refuses_a_remote_without_a_usable_sandbox(
    sandbox: str, sandbox_ok: bool, expect_error: bool
) -> None:
    asyncio.run(_sandbox_gate(sandbox, sandbox_ok, expect_error))


async def _sandbox_gate(sandbox: str, sandbox_ok: bool, expect_error: bool) -> None:
    handshake = json.dumps({
        "port": 45671, "version": "codex-cli 0.154.0",
        "sandbox_ok": sandbox_ok,
        "sandbox_error": "" if sandbox_ok else "bwrap: No permissions to create a new namespace",
    }).encode()
    process = Mock(returncode=None)
    process.stdout.readline = AsyncMock(return_value=handshake)
    process.stderr.read = AsyncMock(return_value=b"")
    process.stdin = Mock()
    process.wait = AsyncMock(return_value=0)
    writer = Mock()
    writer.wait_closed = AsyncMock()
    access = RemoteAccessConfig(ssh_target="test", codex_remote_experimental=True, sandbox=sandbox)
    executor = RemoteExecutor(access, "/srv/test")
    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
        patch("asyncio.open_connection", AsyncMock(return_value=(Mock(), writer))),
    ):
        if expect_error:
            with pytest.raises(RemoteTransportError, match="每条命令都会要求人工授权"):
                await executor.__aenter__()
            return
        try:
            assert await executor.__aenter__() is executor
        finally:
            await executor.__aexit__()


def test_disconnect_cancels_waiting_gate(tmp_path: Path) -> None:
    async def run():
        ctx = context(tmp_path)
        cancelled = asyncio.Event()

        async def wait_for_user(_request):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        ctx.request_gate_handler = wait_for_user
        provider = CodexProvider()
        provider._remote_executor = Mock()
        provider._remote_executor.check.side_effect = [None, RemoteTransportError("连接断开")]
        message = {"id": 1, "method": "item/tool/requestUserInput", "params": {"questions": []}}
        with pytest.raises(RemoteTransportError):
            async for _event in provider._handle_message(message, ctx, Mock()):
                pass
        assert cancelled.is_set()

    asyncio.run(run())


def test_ssh_arguments_are_quoted_and_forwarding_is_disabled() -> None:
    command = ssh_command(RemoteAccessConfig(ssh_target="test", connect_via="jump"), ["test", "-d", "/srv/a; touch wrong"])
    assert "-oClearAllForwardings=yes" in command
    assert "-oForwardAgent=no" in command
    assert command[-1] == "test -d '/srv/a; touch wrong'"
    with pytest.raises(ValueError):
        RemoteAccessConfig(ssh_target="-oProxyCommand=bad")


def test_verifier_script_runs_in_authority_directory(tmp_path: Path) -> None:
    asyncio.run(_verifier_script(tmp_path))


async def _verifier_script(tmp_path: Path) -> None:
    authority = tmp_path / "authority"
    authority.mkdir()
    with patch("miniclaw2.remote_execution.ssh_command", side_effect=lambda access, args: [sys.executable, *args[1:]]):
        process = await start_verifier(RemoteAccessConfig(ssh_target="fake"), str(authority), "test", "printf verified > result; printf '%s' \"$CI\"")
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.gather(process.stdout.read(), process.stderr.read()), 5)
        assert await process.wait() == 0, stderr
        assert stdout == b"1"
        assert (authority / "result").read_text() == "verified"
        assert not (tmp_path / "result").exists()
    finally:
        await stop_process(process)


def local_transport(tmp_path: Path) -> SSHProjectTransport:
    transport = SSHProjectTransport(RemoteAccessConfig(ssh_target="unused"), project_id="test", store_root=tmp_path)
    transport.run = lambda args, **kwargs: subprocess.run([sys.executable, *args[1:]], capture_output=True, text=True, timeout=10)
    return transport


def test_init_has_distinct_fingerprint_and_preserves_nonempty_directory(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    transport = local_transport(root)
    transport.initialize_repository(str(root / "one"), "init", None)
    transport.initialize_repository(str(root / "two"), "init", None)
    def head(name):
        return subprocess.check_output(["git", "-C", str(root / name), "rev-parse", "HEAD"])
    assert head("one") != head("two")
    assert "/.miniclaw2/" in (root / "one/.git/info/exclude").read_text()
    with pytest.raises(RemoteTransportError, match="仅允许初始化空目录"):
        transport.initialize_repository(str(root / "one"), "init", None)
    assert (root / "one/.git").is_dir()


def test_clone_and_failed_clone_leave_remote_directory(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    transport = local_transport(root)
    transport.initialize_repository(str(root / "source"), "init", None)
    transport.initialize_repository(str(root / "clone"), "cloned", str(root / "source"))
    assert (root / "clone/.git").is_dir()
    with pytest.raises(RemoteTransportError, match="未删除目录"):
        transport.initialize_repository(str(root / "failed"), "cloned", str(root / "missing"))
    assert (root / "failed").is_dir()
    with pytest.raises(ValueError, match="凭据"):
        transport.initialize_repository(str(root / "credentials"), "cloned", "https://user:secret@host/repo")
    assert not (root / "credentials").exists()
