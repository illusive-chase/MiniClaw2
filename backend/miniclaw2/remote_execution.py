"""Node-owned remote executors. Credentials and agent sessions stay local."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import socket
import uuid
from pathlib import Path
from typing import Any

from .domain import Node, NodeKind, RemoteAccessConfig, RemoteProjectIdentity, ReviewSubtype
from .remote_transport import RemoteTransportError, SSHProjectTransport, control_socket_path


def ssh_command(access: RemoteAccessConfig, command: list[str]) -> list[str]:
    args = [
        "ssh", "-T", "-oBatchMode=yes", "-oConnectTimeout=10",
        "-oForwardAgent=no", "-oControlMaster=no", "-oControlPath=none", "-oControlPersist=no",
        "-oServerAliveInterval=10", "-oServerAliveCountMax=2",
        "-oClearAllForwardings=yes",
    ]
    if access.connect_via:
        args.extend(["-J", access.connect_via])
    return [*args, access.ssh_target, shlex.join(command)]


# The supervisor owns a process group, never a name-matched process. Closing
# SSH stdin reaps the executor AND its children, including on cancellation.
_SUPERVISOR = r'''
import json, os, select, signal, socket, subprocess, sys, time
mode, root, executable = sys.argv[1:4]
os.chdir(root)
if mode == "executor":
    env = dict(os.environ)
    if os.path.dirname(executable):
        env["PATH"] = os.path.dirname(executable) + os.pathsep + env.get("PATH", "")
    version = subprocess.check_output([executable, "--version"], text=True, env=env).strip()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    args = [executable, "exec-server", "--listen", "ws://127.0.0.1:" + str(port)]
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=sys.stderr,
                               env=env, start_new_session=True)
else:
    script = json.loads(sys.stdin.buffer.readline())["script"]
    env = dict(os.environ, CI="1", MINICLAW_PROJECT_ID=executable)
    process = subprocess.Popen(["bash", "-c", script], env=env,
                               stdin=subprocess.DEVNULL, start_new_session=True)
def stop(signum, frame):
    raise SystemExit(128 + signum)
for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
    signal.signal(signum, stop)
try:
    if mode == "executor":
        deadline = time.monotonic() + 10
        while process.poll() is None:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("exec-server readiness timeout")
                time.sleep(0.1)
        if process.poll() is not None:
            raise RuntimeError("exec-server exited during startup")
        print(json.dumps({"port": port, "version": version}), flush=True)
    while process.poll() is None:
        if select.select([sys.stdin], [], [], 0.2)[0]:
            if not sys.stdin.buffer.read1(1):
                break
finally:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
sys.exit(process.returncode)
'''


async def stop_process(process: asyncio.subprocess.Process | None) -> None:
    if process is None:
        return
    if process.stdin:
        process.stdin.close()
    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), 4)
        except TimeoutError:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                process.kill()
                await process.wait()


async def start_verifier(
    access: RemoteAccessConfig, root: str, project_id: str, script: str,
    *, transport: SSHProjectTransport | None = None,
) -> asyncio.subprocess.Process:
    args = ["python3", "-u", "-c", _SUPERVISOR, "verifier", root, project_id]
    process = await asyncio.create_subprocess_exec(
        *(transport.command(args) if transport is not None else ssh_command(access, args)),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert process.stdin is not None
        process.stdin.write((json.dumps({"script": script}) + "\n").encode())
        await process.stdin.drain()
    except BaseException:
        await stop_process(process)
        raise
    return process


class RemoteExecutor:
    def __init__(self, access: RemoteAccessConfig, root: str) -> None:
        self.access = access
        self.root = root
        self.process: asyncio.subprocess.Process | None = None
        self.control_path: Path | None = None
        self.stderr_task: asyncio.Task[None] | None = None
        self.version = ""
        self.url = ""
        self.stderr_tail = ""

    async def __aenter__(self) -> "RemoteExecutor":
        if not self.access.codex_remote_experimental:
            raise RemoteTransportError("尚未启用 Codex 远端实验执行")
        try:
            self.control_path = control_socket_path(uuid.uuid4().hex)
            if self.control_path is None:
                raise RemoteTransportError("无法为节点创建独立 SSH 控制套接字")
            command = ssh_command(self.access, ["python3", "-u", "-c", _SUPERVISOR,
                                                "executor", self.root, self.access.codex_path])
            command[command.index("-oControlMaster=no")] = "-oControlMaster=yes"
            command[command.index("-oControlPath=none")] = f"-oControlPath={self.control_path}"
            self.process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert self.process.stdout is not None
            self.stderr_task = asyncio.create_task(self._drain_stderr())
            raw = await asyncio.wait_for(self.process.stdout.readline(), 20)
            if not raw:
                raise RemoteTransportError(f"远端执行器未启动：{self.stderr_tail}")
            info = json.loads(raw)
            self.version = str(info["version"])
            # Only this protocol family has been tested. Unknown versions fail
            # before an agent turn, not after dispatching a mutating command.
            if self.version not in {"codex-cli 0.149.1", "codex-cli 0.154.0"}:
                raise RemoteTransportError(f"未验证的远端 Codex 版本：{self.version}")
            remote_port = int(info["port"])
            if not 1024 <= remote_port <= 65535:
                raise RemoteTransportError("远端执行器端口无效")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                local_port = sock.getsockname()[1]
            # The master cleared ALL configured forwards. The control client
            # reads no user config and adds only this explicit local forward.
            forward = await asyncio.create_subprocess_exec(
                "ssh", "-F", "/dev/null", "-S", str(self.control_path),
                "-O", "forward", "-L",
                f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}",
                "miniclaw2-control", stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                if await asyncio.wait_for(forward.wait(), 10):
                    raise RemoteTransportError("无法建立节点 SSH 本地转发")
            finally:
                await stop_process(forward)
            for _ in range(100):
                self.check()
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", local_port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.1)
            else:
                raise RemoteTransportError("SSH 本地转发未就绪")
            self.url = f"ws://127.0.0.1:{local_port}"
            return self
        except BaseException:
            await self.__aexit__()
            raise

    async def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while chunk := await self.process.stderr.read(65536):
            self.stderr_tail = (self.stderr_tail + chunk.decode("utf-8", errors="replace"))[-4096:]

    def check(self) -> None:
        if self.process is not None and self.process.returncode is not None:
            raise RemoteTransportError(f"远端执行连接已断开；已发送操作的结果可能未知，不会自动重试：{self.stderr_tail}")

    async def __aexit__(self, *_exc: object) -> None:
        await stop_process(self.process)
        if self.stderr_task:
            await asyncio.gather(self.stderr_task, return_exceptions=True)
        if self.control_path:
            self.control_path.unlink(missing_ok=True)


def local_codex_home() -> str:
    return str(Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve())


def execution_binding(identity: RemoteProjectIdentity, access: RemoteAccessConfig) -> dict[str, Any]:
    # Audit changes to host-local access without syncing aliases or jump hosts.
    access_json = json.dumps(access.model_dump(exclude={"codex_remote_experimental"}), sort_keys=True)
    return {
        **identity.model_dump(), "sandbox": access.sandbox,
        "access_sha256": hashlib.sha256(access_json.encode()).hexdigest(),
    }


def execution_role(node: Node) -> str:
    if node.kind is NodeKind.VERIFIER:
        return "remote_verifier"
    if node.subtype is ReviewSubtype.CODE_REVIEW:
        return "local_patch_review"
    return "remote_codex" if node.provider == "codex" else "local_read_only"


async def register_environment(client: Any, executor: RemoteExecutor, environment_id: str) -> dict[str, Any]:
    await client.request("environment/add", {
        "environmentId": environment_id, "execServerUrl": executor.url,
        "connectTimeoutMs": 10000,
    })
    for _ in range(100):
        executor.check()
        status = await client.request("environment/status", {"environmentId": environment_id})
        if status.get("status") == "ready":
            info = await client.request("environment/info", {"environmentId": environment_id})
            shell = info.get("shell")
            if not isinstance(shell, dict) or not shell.get("path"):
                raise RemoteTransportError("远端环境未报告可用 shell")
            return info
        if status.get("status") in {"unknown", "disconnected"}:
            raise RemoteTransportError(f"远端环境未就绪：{status.get('error', status['status'])}")
        await asyncio.sleep(0.1)
    raise RemoteTransportError("远端环境就绪探针超时")
