from __future__ import annotations

import socket
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from miniclaw2.domain import RemoteAccessConfig
from miniclaw2.remote_transport import (
    SSH_TRANSPORT_FAILURE,
    RemoteTransportError,
    SSHProjectTransport,
    control_socket_path,
)


# macOS caps sockaddr_un.sun_path at 104 bytes and OpenSSH binds
# "<ControlPath>.<16 random chars>" before renaming it into place.
_SUN_PATH_LIMIT = 103
_OPENSSH_SUFFIX = 17


def _transport(**kwargs: object) -> SSHProjectTransport:
    return SSHProjectTransport(
        RemoteAccessConfig(ssh_target="gpu-box"),
        project_id="project-1",
        store_root=Path("/tmp/store"),
        **kwargs,  # type: ignore[arg-type]
    )


def test_control_path_fits_sun_path_under_long_private_tmpdir() -> None:
    """macOS TMPDIR is a ~48-char per-user folder; the socket must still fit."""
    long_tmp = "/var/folders/9g/1rqns07577q68nq5stpglns40000gn/T"

    with patch(
        "miniclaw2.remote_transport.tempfile.gettempdir", return_value=long_tmp
    ):
        path = control_socket_path("a" * 32)

    assert path is not None
    assert str(path).startswith(long_tmp)
    assert len(str(path)) + _OPENSSH_SUFFIX <= _SUN_PATH_LIMIT
    # The path must remain bindable in practice, not merely arithmetically short.
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.bind(f"{path}.{'x' * 16}")
    finally:
        probe.close()
        Path(f"{path}.{'x' * 16}").unlink(missing_ok=True)


def test_control_path_falls_back_to_shared_root_when_tmpdir_too_long() -> None:
    with patch(
        "miniclaw2.remote_transport.tempfile.gettempdir",
        return_value="/var/folders/" + "d" * 90,
    ):
        path = control_socket_path("b" * 32)

    assert path is not None
    assert str(path).startswith("/tmp/miniclaw2-ssh-")
    assert len(str(path)) + _OPENSSH_SUFFIX <= _SUN_PATH_LIMIT


def test_unusable_socket_dir_degrades_to_unmultiplexed_ssh() -> None:
    """No usable socket directory must not break SSH, only multiplexing."""
    with patch(
        "miniclaw2.remote_transport._prepare_socket_dir", return_value=False
    ):
        transport = _transport()

    assert transport.control_path is None
    args = transport._ssh_prefix()
    assert not any(arg.startswith("-oControlPath") for arg in args)
    assert not any(arg.startswith("-oControlMaster") for arg in args)
    assert "-oBatchMode=yes" in args
    transport.close()  # must not raise without a control socket


def test_probe_reports_ssh_failure_rather_than_missing_remote_directory() -> None:
    """A dead transport must not be reported as an absent remote path."""
    transport = _transport()

    with patch(
        "miniclaw2.remote_transport.subprocess.run",
        side_effect=OSError("unix_listener: path too long for Unix domain socket"),
    ), patch("miniclaw2.remote_transport.time.sleep"):
        with pytest.raises(RemoteTransportError) as excinfo:
            transport.probe_repository("/srv/project")

    message = str(excinfo.value)
    assert "无法通过 SSH 连接到 gpu-box" in message
    assert "远端目录不存在" not in message


def test_transport_failure_uses_ssh_exit_code_not_git_exit_one() -> None:
    """Exit 1 is a meaningful git result; a broken transport must differ."""
    transport = _transport()

    with patch(
        "miniclaw2.remote_transport.subprocess.run", side_effect=OSError("boom")
    ):
        text = transport.run(["git", "status"])
        binary = transport.run_bytes(["git", "status"])

    assert text.returncode == SSH_TRANSPORT_FAILURE
    assert binary.returncode == SSH_TRANSPORT_FAILURE
    assert SSH_TRANSPORT_FAILURE != 1


def test_readonly_command_retries_only_ssh_transport_failures() -> None:
    transport = _transport()
    responses = iter(
        [
            subprocess.CompletedProcess([], SSH_TRANSPORT_FAILURE, "", "lost"),
            subprocess.CompletedProcess([], SSH_TRANSPORT_FAILURE, "", "lost"),
            subprocess.CompletedProcess([], 0, "ok", ""),
        ]
    )
    with patch.object(transport, "run", side_effect=lambda *_args, **_kwargs: next(responses)) as run, patch(
        "miniclaw2.remote_transport.time.sleep"
    ) as sleep:
        result = transport.run_readonly(["git", "status"])

    assert result.stdout == "ok"
    assert run.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [1.0, 2.0]

    with patch.object(
        transport,
        "run",
        return_value=subprocess.CompletedProcess([], 1, "", "git failed"),
    ) as run, patch("miniclaw2.remote_transport.time.sleep") as sleep:
        result = transport.run_readonly(["git", "status"])
    assert result.returncode == 1
    run.assert_called_once()
    sleep.assert_not_called()


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
    assert "--hard-dereference" not in run.call_args_list[2].args[0][-1]
