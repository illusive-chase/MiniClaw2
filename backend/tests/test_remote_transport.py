from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from miniclaw2.domain import RemoteAccessConfig
from miniclaw2.remote_transport import RemoteTransportError, SSHProjectTransport


def test_probe_uses_project_control_socket_and_quotes_remote_path(
    tmp_path: Path,
) -> None:
    sha = "a" * 40

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[-1]
        if command.startswith("test -d"):
            return subprocess.CompletedProcess(args, 0, "", "")
        if "--is-inside-work-tree" in command:
            return subprocess.CompletedProcess(args, 0, "true\n", "")
        if "--max-parents=0" in command:
            return subprocess.CompletedProcess(args, 0, f"{sha}\n", "")
        raise AssertionError(command)

    transport = SSHProjectTransport(
        RemoteAccessConfig(ssh_target="gpu-box", connect_via="jump-box"),
        project_id="project-1",
        store_root=tmp_path,
    )
    with patch("miniclaw2.remote_transport.subprocess.run", side_effect=run) as call:
        probe = transport.probe_repository("/srv/project with space")

    assert probe.root_commits == (sha,)
    first_args = call.call_args_list[0].args[0]
    assert "-oControlMaster=auto" in first_args
    assert "-oControlPersist=60" in first_args
    assert "-J" in first_args
    assert first_args[first_args.index("-J") + 1] == "jump-box"
    assert first_args[-2:] == ["gpu-box", "test -d '/srv/project with space'"]


def test_probe_rejects_empty_repository(tmp_path: Path) -> None:
    outputs = iter(
        [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "true\n", ""),
            subprocess.CompletedProcess([], 128, "", "fatal: bad revision 'HEAD'"),
        ]
    )
    transport = SSHProjectTransport(
        RemoteAccessConfig(ssh_target="gpu-box"),
        project_id="project-1",
        store_root=tmp_path,
    )

    with patch(
        "miniclaw2.remote_transport.subprocess.run",
        side_effect=lambda *_args, **_kwargs: next(outputs),
    ):
        with pytest.raises(RemoteTransportError, match="无法读取远端仓库根提交"):
            transport.probe_repository("/srv/project")


def test_export_tracked_files_pipes_nul_list_to_remote_tar(tmp_path: Path) -> None:
    responses = iter(
        [
            subprocess.CompletedProcess([], 0, b"src/app.py\0", b""),
            subprocess.CompletedProcess([], 0, b"", b""),
            subprocess.CompletedProcess([], 0, b"tar-data", b""),
        ]
    )
    transport = SSHProjectTransport(
        RemoteAccessConfig(ssh_target="gpu-box"),
        project_id="project-1",
        store_root=tmp_path,
    )

    with patch(
        "miniclaw2.remote_transport.subprocess.run",
        side_effect=lambda *_args, **_kwargs: next(responses),
    ) as run:
        archive = transport.export_tracked_files("/srv/project with space")

    assert archive == b"tar-data"
    assert run.call_args_list[0].args[0][-1] == (
        "git -C '/srv/project with space' ls-files --cached -z"
    )
    assert run.call_args_list[1].args[0][-1] == (
        "git -C '/srv/project with space' ls-files --deleted -z"
    )
    assert run.call_args_list[2].kwargs["input"] == b"src/app.py\0"
    assert "--no-recursion" in run.call_args_list[2].args[0][-1]
